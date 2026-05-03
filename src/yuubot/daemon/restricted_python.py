"""Best-effort restricted Python backend for group-chat agents."""

from __future__ import annotations

import ast
import asyncio
import contextlib
import functools
import inspect
import importlib
import io
import multiprocessing as mp
import queue
import sys
import time
import traceback
import types
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import attrs
from RestrictedPython.Eval import default_guarded_getitem, default_guarded_getiter
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safe_builtins,
    safer_getattr,
)
from RestrictedPython.transformer import RestrictingNodeTransformer
from loguru import logger
from yuuagents.python_runtime import ResolvedPythonRuntime
from yuuagents.python_session import MimeBundle, PythonExecResult, PythonResultItem


_ALLOWED_IMPORTS = {
    "bisect",
    "collections",
    "copy",
    "datetime",
    "decimal",
    "enum",
    "fractions",
    "functools",
    "heapq",
    "itertools",
    "json",
    "math",
    "operator",
    "random",
    "re",
    "statistics",
    "string",
    "textwrap",
}
_DENIED_CALLS = {"compile", "eval", "exec", "globals", "locals", "open", "__import__"}
_LAST_EXPR_NAME = "YUUBOT_LAST_EXPR"

_SESSION_STATE: dict[str, Any] = {}


class SessionStateView(dict[str, Any]):
    """Mapping with attribute access for agent-written SESSION_STATE code."""

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _init_session_state(state: dict[str, Any]) -> None:
    global _SESSION_STATE
    _SESSION_STATE = dict(state) if state else {}


def get_session_state() -> dict[str, Any]:
    return _SESSION_STATE


class _AsyncToSyncTransformer(ast.NodeTransformer):
    """Rewrite await/async def to sync equivalents before RestrictedPython sees them."""

    def visit_Await(self, node: ast.Await) -> ast.Call:
        self.generic_visit(node)
        return ast.copy_location(
            ast.Call(
                func=ast.Name(id="YUUBOT_SYNC", ctx=ast.Load()),
                args=[node.value],
                keywords=[],
            ),
            node,
        )

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.FunctionDef:
        self.generic_visit(node)
        return ast.copy_location(
            ast.FunctionDef(
                name=node.name,
                args=node.args,
                body=node.body,
                decorator_list=node.decorator_list,
                returns=node.returns,
            ),
            node,
        )

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        raise SyntaxError("async for is not supported in restricted mode")

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        raise SyntaxError("async with is not supported in restricted mode")


def YUUBOT_SYNC(coro: Any) -> Any:
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


def _compile_restricted(tree: ast.AST, filename: str) -> Any:
    transformer = RestrictingNodeTransformer()
    new_tree = transformer.visit(tree)
    if transformer.errors:
        raise SyntaxError(transformer.errors)
    ast.fix_missing_locations(new_tree)
    return compile(new_tree, filename, "exec")


@attrs.define
class RestrictedPythonSession:
    worker: RestrictedPythonWorker
    session_id: str
    runtime: ResolvedPythonRuntime
    agent_id: str = ""
    agent_name: str = ""
    emit: Callable[[str, Mapping[str, Any]], Awaitable[None]] | None = attrs.field(default=None, repr=False)
    _closed: bool = attrs.field(default=False, init=False, repr=False)

    async def execute(self, code: str, *, timeout_s: float | None = None) -> PythonExecResult:
        if self._closed:
            return PythonExecResult(status="crashed", traceback=("Restricted Python session is closed.",))
        started_at = time.perf_counter()
        await self._emit("python.cell_started", {"backend": "restricted", "timeout_s": timeout_s})
        result = await self.worker.execute(
            session_id=self.session_id,
            runtime=self.runtime,
            code=code,
            timeout_s=timeout_s,
        )
        duration_s = time.perf_counter() - started_at
        await self._emit(
            "python.timeout" if result.status == "timeout" else "python.cell_finished",
            {
                "backend": "restricted",
                "status": result.status,
                "duration_s": duration_s,
                "stdout_len": len(result.stdout),
                "stderr_len": len(result.stderr),
                "traceback_len": len(result.traceback),
                "stdout": _bounded(result.stdout),
                "stderr": _bounded(result.stderr),
                "traceback": tuple(result.traceback),
                "item_count": len(result.items),
            },
        )
        return result

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.worker.close_session(self.session_id)
        await self._emit("python.session_closed", {"backend": "restricted"})

    async def interrupt(self) -> PythonExecResult:
        return PythonExecResult(status="interrupted", traceback=("Restricted Python worker is process-scoped.",))

    async def rebind(
        self,
        *,
        agent_id: str,
        agent_name: str,
        runtime: ResolvedPythonRuntime,
        emit: Callable[[str, Mapping[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        if self._closed:
            return
        self.agent_id = agent_id
        self.agent_name = agent_name
        self.runtime = runtime
        self.emit = emit

    async def _emit(self, name: str, data: Mapping[str, Any]) -> None:
        if self.emit is not None:
            await self.emit(name, data)


@attrs.define
class RestrictedPythonWorker:
    default_timeout_s: float = 8.0
    _lock: asyncio.Lock = attrs.field(factory=asyncio.Lock, init=False, repr=False)
    _requests: Any = attrs.field(default=None, init=False, repr=False)
    _responses: Any = attrs.field(default=None, init=False, repr=False)
    _process: Any = attrs.field(default=None, init=False, repr=False)

    async def execute(
        self,
        *,
        session_id: str,
        runtime: ResolvedPythonRuntime,
        code: str,
        timeout_s: float | None = None,
    ) -> PythonExecResult:
        async with self._lock:
            self._ensure_started()
            request_id = uuid.uuid4().hex
            effective_timeout = float(timeout_s or self.default_timeout_s)
            request = {
                "id": request_id,
                "session_id": session_id,
                "code": code,
                "state": runtime.state.to_dict(),
                "imports": [(item.module, item.alias) for item in runtime.imports],
                "sys_path": tuple(runtime.config.sys_path),
                "startup_code": runtime.config.startup_code,
            }
            self._requests.put(request)
            try:
                response = await asyncio.to_thread(
                    self._responses.get,
                    True,
                    effective_timeout,
                )
            except queue.Empty:
                self._restart("timeout")
                return PythonExecResult(
                    status="timeout",
                    traceback=("Restricted Python worker timed out and was restarted.",),
                )
            if not isinstance(response, dict) or response.get("id") != request_id:
                self._restart("protocol_error")
                return PythonExecResult(
                    status="crashed",
                    traceback=("Restricted Python worker returned an invalid response.",),
                )
            return _result_from_dict(response.get("result", {}))

    async def close_session(self, session_id: str) -> None:
        async with self._lock:
            if self._process is None and self._requests is None:
                return
            if self._process is None or not self._process.is_alive():
                self._terminate()
                self._close_queues()
                return
            request_id = uuid.uuid4().hex
            assert self._requests is not None
            assert self._responses is not None
            self._requests.put({"id": request_id, "op": "close_session", "session_id": session_id})
            try:
                response = await asyncio.to_thread(self._responses.get, True, 2.0)
            except queue.Empty:
                self._restart("close_session_timeout")
                return
            if not isinstance(response, dict) or response.get("id") != request_id:
                self._restart("protocol_error")

    def stop(self) -> None:
        process = self._process
        if self._requests is not None:
            with contextlib.suppress(Exception):
                self._requests.put(None)
        if process is not None and process.is_alive():
            process.join(timeout=1)
        self._terminate()
        self._close_queues()

    def _ensure_started(self) -> None:
        if self._process is not None and self._process.is_alive():
            return
        ctx = mp.get_context("spawn")
        self._requests = ctx.Queue()
        self._responses = ctx.Queue()
        process = ctx.Process(
            target=_worker_main,
            args=(self._requests, self._responses),
            name="yuubot-restricted-python",
            daemon=True,
        )
        self._process = process
        process.start()
        logger.info("Restricted Python worker started: pid={}", process.pid)

    def _restart(self, reason: str) -> None:
        logger.warning("Restricted Python worker restarted: reason={}", reason)
        self._terminate()
        self._close_queues()

    def _terminate(self) -> None:
        process = self._process
        if process is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        self._process = None

    def _close_queues(self) -> None:
        for q in (self._requests, self._responses):
            if q is None:
                continue
            with contextlib.suppress(Exception):
                q.close()
            with contextlib.suppress(Exception):
                q.join_thread()
        self._requests = None
        self._responses = None


def _worker_main(requests: Any, responses: Any) -> None:
    sessions: dict[str, dict[str, Any]] = {}
    last_prune = time.time()
    while True:
        try:
            request = requests.get(timeout=60)
        except queue.Empty:
            # Periodic idle cleanup: prune sessions unused for > 10 minutes
            now = time.time()
            if now - last_prune > 60:
                stale_cutoff = now - 600
                stale = [
                    sid for sid, ns in sessions.items()
                    if ns.get("_last_used", 0) < stale_cutoff
                ]
                for sid in stale:
                    sessions.pop(sid, None)
                last_prune = now
            continue
        if request is None:
            return
        request_id = request.get("id", "")
        if request.get("op") == "close_session":
            sessions.pop(str(request.get("session_id", "")), None)
            responses.put({"id": request_id, "result": {"status": "ok"}})
            continue
        try:
            result = _execute_request(sessions, request)
        except BaseException as exc:
            result = PythonExecResult(
                status="crashed",
                traceback=tuple(traceback.format_exception(exc)),
            )
        responses.put({"id": request_id, "result": _result_to_dict(result)})


def _execute_request(sessions: dict[str, dict[str, Any]], request: Mapping[str, Any]) -> PythonExecResult:
    session_id = str(request.get("session_id", ""))
    namespace = sessions.get(session_id)
    if namespace is None:
        namespace = _new_namespace(request)
        sessions[session_id] = namespace
    namespace["_last_used"] = time.time()
    _refresh_namespace(namespace, request)
    return _execute_restricted(str(request.get("code", "")), namespace)


def _new_namespace(request: Mapping[str, Any]) -> dict[str, Any]:
    namespace: dict[str, Any] = {
        "__name__": "__restricted_python__",
        "__builtins__": _restricted_builtins({}),
        "_print_": _StdoutPrintCollector,
        "_getattr_": safer_getattr,
        "_getitem_": default_guarded_getitem,
        "_getiter_": default_guarded_getiter,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "YUUBOT_SYNC": YUUBOT_SYNC,
        "TASKS": {},
    }
    namespace["__builtins__"] = _restricted_builtins(namespace)
    _refresh_namespace(namespace, request)
    startup_code = str(request.get("startup_code", "") or "")
    if startup_code.strip():
        _execute_restricted(startup_code, namespace)
    return namespace


def _refresh_namespace(namespace: dict[str, Any], request: Mapping[str, Any]) -> None:
    for entry in request.get("sys_path", ()):
        path = str(entry)
        if path and path not in sys.path:
            sys.path.insert(0, path)
    state = request.get("state")
    _init_session_state(state if isinstance(state, Mapping) else {})
    namespace["SESSION_STATE"] = SessionStateView(get_session_state())
    namespace["__builtins__"] = _restricted_builtins(namespace)
    for module_name, alias in request.get("imports", ()):
        module_name = str(module_name)
        module = _load_runtime_module(module_name)
        import_name = str(alias or module_name)
        namespace[import_name] = module


def _load_runtime_module(module_name: str) -> types.ModuleType:
    module = importlib.import_module(module_name)
    if module_name == "yuubot.agent_fns" or module_name.startswith("yuubot.agent_fns."):
        module = importlib.reload(module)
        return _sync_agent_fns_facade(module)
    return module


def _sync_agent_fns_facade(module: types.ModuleType) -> types.ModuleType:
    facade = types.ModuleType(module.__name__)
    facade.__doc__ = module.__doc__
    facade.__package__ = module.__package__
    facade.__file__ = getattr(module, "__file__", None)
    for name, value in vars(module).items():
        if name.startswith("__") and name not in {"__all__"}:
            continue
        setattr(facade, name, _sync_callable(value) if inspect.iscoroutinefunction(value) else value)
    return facade


def _sync_callable(func: Callable[..., Awaitable[Any]]) -> Callable[..., Any]:
    @functools.wraps(func)
    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        return YUUBOT_SYNC(func(*args, **kwargs))

    return _wrapped


def _execute_restricted(code: str, namespace: dict[str, Any]) -> PythonExecResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        tree = ast.parse(code, mode="exec")
        _validate_tree(tree)
        tree = _AsyncToSyncTransformer().visit(tree)
        tree = _LastExpressionCapture().visit(tree)
        ast.fix_missing_locations(tree)
        compiled = _compile_restricted(tree, "<restricted-python>")
        namespace.pop(_LAST_EXPR_NAME, None)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exec(compiled, namespace)
        items = _items_for_value(namespace.get(_LAST_EXPR_NAME))
        return PythonExecResult(
            status="ok",
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            items=items,
        )
    except Exception:
        return PythonExecResult(
            status="error",
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
            traceback=tuple(traceback.format_exc().splitlines()),
        )


def _validate_tree(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            raise SyntaxError("while loops are disabled in group restricted Python")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SyntaxError("dunder attribute access is disabled")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise SyntaxError("dunder names are disabled")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _DENIED_CALLS:
            raise SyntaxError(f"{node.func.id}() is disabled")
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.partition(".")[0]
                if root != "yb" and root not in _ALLOWED_IMPORTS:
                    raise SyntaxError(f"import {alias.name!r} is disabled")
        if isinstance(node, ast.ImportFrom):
            root = (node.module or "").partition(".")[0]
            if root not in _ALLOWED_IMPORTS:
                raise SyntaxError(f"from {node.module!r} import ... is disabled")


class _LastExpressionCapture(ast.NodeTransformer):
    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)
        if node.body and isinstance(node.body[-1], ast.Expr):
            expr = node.body[-1]
            node.body[-1] = ast.copy_location(
                ast.Assign(
                    targets=[ast.Name(id=_LAST_EXPR_NAME, ctx=ast.Store())],
                    value=expr.value,
                ),
                expr,
            )
        return node


class _StdoutPrintCollector:
    def __init__(self, _getattr_: Callable[..., Any] | None = None) -> None:
        del _getattr_

    def _call_print(self, *objects: object, **kwargs: object) -> None:
        del kwargs
        print(*objects)


def _restricted_builtins(namespace: Mapping[str, Any]) -> dict[str, Any]:
    builtins = dict(safe_builtins)
    builtins.update(
        {
            "__import__": _restricted_import(namespace),
            "enumerate": enumerate,
            "filter": filter,
            "len": len,
            "list": list,
            "map": map,
            "max": max,
            "min": min,
            "range": range,
            "reversed": reversed,
            "set": set,
            "sorted": sorted,
            "sum": sum,
            "tuple": tuple,
            "zip": zip,
        }
    )
    return builtins


def _restricted_import(namespace: Mapping[str, Any]) -> Callable[..., Any]:
    def _import(name: str, globals_: object = None, locals_: object = None, fromlist: object = (), level: int = 0) -> Any:
        del globals_, locals_
        if level:
            raise ImportError("relative imports are disabled")
        root = name.partition(".")[0]
        if root == "yb" and "yb" in namespace:
            return namespace["yb"]
        if root not in _ALLOWED_IMPORTS:
            raise ImportError(f"import {name!r} is disabled")
        module = importlib.import_module(name)
        if fromlist:
            return module
        return importlib.import_module(root)

    return _import



def _items_for_value(value: Any) -> tuple[PythonResultItem, ...]:
    if value is None:
        return ()
    return (
        PythonResultItem(
            kind="execute_result",
            mime=MimeBundle(data={"text/plain": repr(value)}),
        ),
    )


def _result_to_dict(result: PythonExecResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "execution_count": result.execution_count,
        "items": [
            {"kind": item.kind, "mime": {"data": dict(item.mime.data), "metadata": dict(item.mime.metadata)}}
            for item in result.items
        ],
        "stdout": result.stdout,
        "stderr": result.stderr,
        "traceback": tuple(result.traceback),
    }


def _result_from_dict(payload: Any) -> PythonExecResult:
    if not isinstance(payload, Mapping):
        return PythonExecResult(status="crashed", traceback=("invalid worker payload",))
    items = []
    for raw in payload.get("items", ()):
        if not isinstance(raw, Mapping):
            continue
        mime = raw.get("mime", {})
        if not isinstance(mime, Mapping):
            continue
        items.append(
            PythonResultItem(
                kind=raw.get("kind", "execute_result"),
                mime=MimeBundle(
                    data=dict(mime.get("data", {})),
                    metadata=dict(mime.get("metadata", {})),
                ),
            )
        )
    return PythonExecResult(
        status=payload.get("status", "crashed"),
        execution_count=payload.get("execution_count"),
        items=tuple(items),
        stdout=str(payload.get("stdout", "")),
        stderr=str(payload.get("stderr", "")),
        traceback=tuple(str(item) for item in payload.get("traceback", ())),
    )


def _bounded(text: str, limit: int = 4000) -> str:
    del limit
    return text
