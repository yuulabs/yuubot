"""Restricted subprocess Python executor.

Best-effort restricted executor for LLM-generated helper code.
NOT safe for hostile / adversarial Python. See ast_check.py for the
AST-level validation policy.
"""

from __future__ import annotations

import asyncio
import io
import math
import multiprocessing
import textwrap
import time
from typing import Any

import attrs

from yuubot.sandbox.ast_check import normalize_imports, validate

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_SOURCE_BYTES: int = 8 * 1024
MAX_STDOUT_BYTES: int = 16 * 1024
MAX_RESULT_CALLS: int = 10
MAX_RESULT_BYTES: int = 16 * 1024
DEFAULT_TIMEOUT: float = 5.0
SANDBOX_PROCESS_NAME = "yuubot-sandbox"
_PROCESS_JOIN_TIMEOUT = 0.2
_ASYNC_POLL_INTERVAL = 0.01

# ---------------------------------------------------------------------------
# Allowed builtins
# ---------------------------------------------------------------------------

_ALLOWED_BUILTINS: dict[str, Any] = {
    # types / constructors
    "int": int,
    "float": float,
    "str": str,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "bool": bool,
    "bytes": bytes,
    # numeric
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
    "round": round,
    "divmod": divmod,
    # iteration
    "range": range,
    "len": len,
    "sorted": sorted,
    "reversed": reversed,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    # predicates
    "isinstance": isinstance,
    "all": all,
    "any": any,
    # misc
    "slice": slice,
    "chr": chr,
    "ord": ord,
    "repr": repr,
    "hash": hash,
    # output
    "print": print,  # will be monkey-patched per execution
    # constants
    "True": True,
    "False": False,
    "None": None,
}

# ---------------------------------------------------------------------------
# Allowed modules — injected as globals, NOT importable
# ---------------------------------------------------------------------------


def _load_allowed_modules() -> dict[str, Any]:
    """Import approved stdlib modules once."""
    import bisect
    import collections
    import functools
    import heapq
    import itertools
    import json
    import operator
    import random
    import re
    import statistics
    import string

    return {
        "math": math,
        "random": random,
        "re": re,
        "itertools": itertools,
        "collections": collections,
        "functools": functools,
        "operator": operator,
        "statistics": statistics,
        "json": json,
        "string": string,
        "textwrap": textwrap,
        "heapq": heapq,
        "bisect": bisect,
    }


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------


class _ResultCollector:
    """Collects return_result() calls during a single execution."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def __call__(self, value: Any) -> None:
        if len(self.items) >= MAX_RESULT_CALLS:
            raise RuntimeError(
                f"too many return_result() calls (max {MAX_RESULT_CALLS})"
            )
        text = str(value)
        if len(text) > MAX_RESULT_BYTES:
            text = text[:MAX_RESULT_BYTES] + f"\n... [truncated at {MAX_RESULT_BYTES} bytes]"
        self.items.append(text)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@attrs.define
class SandboxResult:
    """Outcome of a sandbox execution."""

    results: list[str] = attrs.Factory(list)
    stdout: str = ""
    error: str | None = None


# ---------------------------------------------------------------------------
# Core execution
# ---------------------------------------------------------------------------


def _run_sync(source: str) -> SandboxResult:
    """Compile and exec *source* in a restricted namespace (sync)."""
    violations = validate(source)
    if violations:
        return SandboxResult(error="Policy violation:\n" + "\n".join(f"- {v}" for v in violations))

    collector = _ResultCollector()
    stdout_buf = io.StringIO()

    builtins = dict(_ALLOWED_BUILTINS)
    builtins["print"] = lambda *a, **kw: print(*a, file=stdout_buf, **kw)

    ns: dict[str, Any] = {"__builtins__": builtins, "return_result": collector}
    ns.update(_load_allowed_modules())

    try:
        code = compile(normalize_imports(source), "<sandbox>", "exec")
    except SyntaxError as exc:
        return SandboxResult(error=f"syntax error: {exc.msg} (line {exc.lineno})")

    try:
        exec(code, ns)  # noqa: S102
    except Exception as exc:
        stdout_text = stdout_buf.getvalue()[:MAX_STDOUT_BYTES]
        return SandboxResult(stdout=stdout_text, error=f"{type(exc).__name__}: {exc}")

    stdout_text = stdout_buf.getvalue()
    if len(stdout_text) > MAX_STDOUT_BYTES:
        stdout_text = stdout_text[:MAX_STDOUT_BYTES] + f"\n... [truncated at {MAX_STDOUT_BYTES} bytes]"

    return SandboxResult(results=collector.items, stdout=stdout_text)


def _serialize_result(result: SandboxResult) -> dict[str, Any]:
    return {
        "results": result.results,
        "stdout": result.stdout,
        "error": result.error,
    }


def _deserialize_result(payload: object) -> SandboxResult:
    if not isinstance(payload, dict):
        return SandboxResult(error="sandbox process returned an invalid payload")

    results = payload.get("results")
    stdout = payload.get("stdout")
    error = payload.get("error")
    if not isinstance(results, list) or not all(isinstance(item, str) for item in results):
        return SandboxResult(error="sandbox process returned an invalid results payload")
    if not isinstance(stdout, str):
        return SandboxResult(error="sandbox process returned an invalid stdout payload")
    if error is not None and not isinstance(error, str):
        return SandboxResult(error="sandbox process returned an invalid error payload")
    return SandboxResult(results=results, stdout=stdout, error=error)


def _sandbox_worker(source: str, conn: Any) -> None:
    try:
        conn.send(_serialize_result(_run_sync(source)))
    except BaseException as exc:
        try:
            conn.send({
                "results": [],
                "stdout": "",
                "error": f"internal sandbox error: {type(exc).__name__}: {exc}",
            })
        except Exception:
            pass
    finally:
        conn.close()


def _stop_process(proc: multiprocessing.Process) -> None:
    if not proc.is_alive():
        proc.join(timeout=_PROCESS_JOIN_TIMEOUT)
        return

    proc.terminate()
    proc.join(timeout=_PROCESS_JOIN_TIMEOUT)
    if proc.is_alive() and hasattr(proc, "kill"):
        proc.kill()
        proc.join(timeout=_PROCESS_JOIN_TIMEOUT)


def _spawn_subprocess(source: str) -> tuple[Any, multiprocessing.Process]:
    ctx = multiprocessing.get_context("spawn")
    parent_conn, child_conn = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_sandbox_worker,
        args=(source, child_conn),
        name=SANDBOX_PROCESS_NAME,
    )
    proc.daemon = True
    proc.start()
    child_conn.close()
    return parent_conn, proc


async def _wait_for_subprocess(
    parent_conn: Any,
    proc: multiprocessing.Process,
    timeout: float,
) -> SandboxResult:
    deadline = time.monotonic() + timeout

    while True:
        if parent_conn.poll(0):
            try:
                payload = parent_conn.recv()
            except EOFError:
                proc.join(timeout=_PROCESS_JOIN_TIMEOUT)
                if proc.exitcode not in (0, None):
                    return SandboxResult(
                        error=f"sandbox process exited unexpectedly (code {proc.exitcode})"
                    )
                return SandboxResult(error="sandbox process exited without returning a result")

            proc.join(timeout=_PROCESS_JOIN_TIMEOUT)
            if proc.is_alive():
                _stop_process(proc)
            return _deserialize_result(payload)

        if not proc.is_alive():
            proc.join(timeout=_PROCESS_JOIN_TIMEOUT)
            if proc.exitcode not in (0, None):
                return SandboxResult(
                    error=f"sandbox process exited unexpectedly (code {proc.exitcode})"
                )
            return SandboxResult(error="sandbox process exited without returning a result")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(proc)
            return SandboxResult(error=f"execution timed out ({timeout}s)")

        await asyncio.sleep(min(_ASYNC_POLL_INTERVAL, remaining))


async def execute_sandbox(
    source: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> SandboxResult:
    """Execute *source* in a restricted Python sandbox."""
    if len(source.encode()) > MAX_SOURCE_BYTES:
        return SandboxResult(error=f"source too large (max {MAX_SOURCE_BYTES} bytes)")

    parent_conn, proc = _spawn_subprocess(source)
    try:
        return await _wait_for_subprocess(parent_conn, proc, timeout)
    finally:
        parent_conn.close()
        if proc.is_alive():
            _stop_process(proc)
        else:
            proc.join(timeout=_PROCESS_JOIN_TIMEOUT)
