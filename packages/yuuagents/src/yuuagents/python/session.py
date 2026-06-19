from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Literal, Protocol, TypeVar, cast
from uuid import uuid4

import msgspec
from attrs import define, field
from jupyter_client.kernelspec import KernelSpec, KernelSpecManager
import yuullm

from yuuagents.python.runtime import ResolvedPythonRuntime

from yuuagents.obs.entitylog import EntityLog
from yuuagents.types.values import (
    EventData,
    EventPayload,
    EventValue,
)


@define
class MimeBundle:
    data: Mapping[str, object]
    metadata: Mapping[str, object] = field(factory=dict)


@define
class PythonResultItem:
    kind: Literal["display_data", "execute_result"]
    mime: MimeBundle


@define
class PythonExecResult:
    status: Literal["ok", "error", "timeout", "interrupted", "crashed"]
    execution_count: int | None = None
    items: tuple[PythonResultItem, ...] = ()
    stdout: str = ""
    stderr: str = ""
    traceback: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class PythonSessionLike(Protocol):
    async def execute(
        self,
        code: str,
        *,
        timeout_s: float | None = None,
        entitylog: EntityLog | None = None,
    ) -> PythonExecResult: ...
    async def close(self) -> None: ...
    async def interrupt(self) -> PythonExecResult: ...


@define
class PythonSession:
    agent_id: str
    agent_name: str
    runtime: ResolvedPythonRuntime
    session_id: str = field(factory=lambda: f"session_{uuid4().hex[:12]}")
    emit: Callable[[str, EventPayload], Awaitable[None]] | None = field(
        default=None, repr=False
    )
    startup_timeout_s: float = 30.0
    _lock: asyncio.Lock = field(factory=asyncio.Lock, init=False, repr=False)
    _km: KernelManagerLike | None = field(default=None, init=False, repr=False)
    _client: KernelClientLike | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False)
    _closed: bool = field(default=False, init=False)
    _crash_error: str | None = field(default=None, init=False)
    _exec_buffers: dict[str, list[str]] = field(factory=dict, init=False, repr=False)
    _last_bound_state_json: str = field(default="", init=False, repr=False)

    async def execute(
        self,
        code: str,
        *,
        timeout_s: float | None = None,
        call_id: str | None = None,
        entitylog: EntityLog | None = None,
    ) -> PythonExecResult:
        async with self._lock:
            if self._closed:
                return PythonExecResult(
                    status="crashed", traceback=("Python session is closed.",)
                )
            if self._crash_error is not None:
                return PythonExecResult(
                    status="crashed", traceback=(self._crash_error,)
                )
            try:
                if not self._started:
                    await self._start()
                started_at = time.perf_counter()
                await self._emit("python.cell_started", {"timeout_s": timeout_s})
                result = await self._execute_with_timeout(
                    code,
                    timeout_s=timeout_s,
                    call_id=call_id,
                    entitylog=entitylog,
                )
                duration_s = time.perf_counter() - started_at
                event_name = (
                    "python.timeout"
                    if result.status == "timeout"
                    else "python.cell_finished"
                )
                await self._emit(
                    event_name,
                    {
                        "status": result.status,
                        "execution_count": result.execution_count,
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
            except Exception as exc:
                self._crash_error = f"{type(exc).__name__}: {exc}"
                await self._emit(
                    "python.cell_finished",
                    {"status": "crashed", "error": self._crash_error},
                )
                return PythonExecResult(
                    status="crashed", traceback=(self._crash_error,)
                )

    async def close(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                if self._client is not None:
                    self._client.stop_channels()
                if self._km is not None:
                    result = self._km.shutdown_kernel(now=True)
                    if asyncio.iscoroutine(result):
                        await result
            finally:
                await self._emit("python.session_closed", {})

    async def interrupt(self) -> PythonExecResult:
        if self._closed:
            return PythonExecResult(
                status="crashed", traceback=("Python session is closed.",)
            )
        if self._km is None:
            return PythonExecResult(
                status="interrupted", traceback=("Python session is not running.",)
            )
        await self._interrupt()
        await self._emit("python.interrupted", {})
        return PythonExecResult(status="interrupted")

    async def rebind(
        self,
        *,
        agent_id: str,
        agent_name: str,
        runtime: ResolvedPythonRuntime,
        emit: Callable[[str, EventPayload], Awaitable[None]] | None = None,
    ) -> None:
        async with self._lock:
            if self._closed:
                return
            self.agent_id = agent_id
            self.agent_name = agent_name
            self.runtime = runtime
            self.emit = emit
            state = dict(runtime.state)
            state.setdefault("session_id", self.session_id)
            state_json = json.dumps(state, ensure_ascii=False)
            if not self._started or state_json == self._last_bound_state_json:
                self._last_bound_state_json = state_json
                return
            result = await self._execute_with_timeout(
                self._state_rebind_code(state_json),
                timeout_s=self.startup_timeout_s,
                silent=True,
            )
            if result.status != "ok":
                detail = (
                    "\n".join(result.traceback)
                    or result.stderr
                    or result.stdout
                    or result.status
                )
                self._crash_error = f"Python kernel state rebind failed: {detail}"

    async def _start(self) -> None:
        try:
            from jupyter_client.manager import AsyncKernelManager
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Python execution requires ipykernel and jupyter-client to be installed"
            ) from exc

        config = self.runtime.config
        python = config.python or sys.executable
        kernel_cmd = [python, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
        kernel_spec = KernelSpec(
            argv=kernel_cmd, display_name="Python", language="python"
        )
        self._km = AsyncKernelManager(
            kernel_name="python3",
            kernel_spec_manager=_SingleKernelSpecManager(kernel_spec),
        )
        env = _build_env(config.inherit_envs, config.env_allowlist, config.extra_envs)
        await self._km.start_kernel(cwd=config.cwd, env=env)
        self._client = self._km.client()
        self._client.start_channels()
        await self._client.wait_for_ready(timeout=self.startup_timeout_s)
        self._started = True
        await self._emit(
            "python.session_started", {"cwd": config.cwd, "python": python}
        )
        bootstrap = self._bootstrap_code()
        result = await self._execute_with_timeout(
            bootstrap, timeout_s=self.startup_timeout_s, silent=True
        )
        if result.status != "ok":
            detail = (
                "\n".join(result.traceback)
                or result.stderr
                or result.stdout
                or result.status
            )
            raise RuntimeError(f"Python kernel bootstrap failed: {detail}")
        self._last_bound_state_json = json.dumps(self.runtime.state, ensure_ascii=False)

    async def _execute_with_timeout(
        self,
        code: str,
        *,
        timeout_s: float | None,
        silent: bool = False,
        call_id: str | None = None,
        entitylog: EntityLog | None = None,
    ) -> PythonExecResult:
        task = asyncio.create_task(
            self._execute_cell(
                code, silent=silent, call_id=call_id, entitylog=entitylog
            )
        )
        try:
            if timeout_s is None:
                return await task
            return await asyncio.wait_for(task, timeout=timeout_s)
        except TimeoutError:
            task.cancel()
            await self._interrupt()
            return PythonExecResult(status="timeout")

    async def _execute_cell(
        self,
        code: str,
        *,
        silent: bool = False,
        call_id: str | None = None,
        entitylog: EntityLog | None = None,
    ) -> PythonExecResult:
        assert self._client is not None
        msg_id = self._client.execute(
            code, allow_stdin=False, store_history=not silent, silent=silent
        )
        stdout: list[str] = []
        stderr: list[str] = []
        traceback: tuple[str, ...] = ()
        items: list[PythonResultItem] = []
        execution_count: int | None = None
        status: Literal["ok", "error", "timeout", "interrupted", "crashed"] = "ok"

        while True:
            try:
                msg = await self._client.get_iopub_msg(timeout=5)
            except queue.Empty:
                if not await self._kernel_is_alive():
                    return PythonExecResult(
                        status="crashed",
                        traceback=("Python kernel stopped unexpectedly.",),
                    )
                continue
            parent_header = _event_data(msg.get("parent_header"))
            if parent_header.get("msg_id") != msg_id:
                continue
            header = _event_data(msg.get("header"))
            msg_type = _string_value(header.get("msg_type"))
            content = _event_data(msg.get("content"))
            if msg_type == "status" and content.get("execution_state") == "idle":
                break
            if msg_type == "execute_input":
                execution_count = _int_value(content.get("execution_count"))
            elif msg_type == "stream":
                text = _string_value(content.get("text"))
                if content.get("name") == "stderr":
                    stderr.append(text)
                else:
                    stdout.append(text)
                if call_id is not None:
                    self._exec_buffers.setdefault(call_id, []).append(text)
                if entitylog is not None:
                    await entitylog.write(text)
            elif msg_type == "display_data":
                result_item = PythonResultItem(
                    kind="display_data",
                    mime=MimeBundle(
                        data=_json_object(content.get("data")),
                        metadata=_json_object(content.get("metadata")),
                    ),
                )
                items.append(result_item)
                if entitylog is not None:
                    await _write_rendered_mime(entitylog, result_item.mime)
            elif msg_type == "execute_result":
                execution_count = _int_value(
                    content.get("execution_count"),
                    default=execution_count,
                )
                result_item = PythonResultItem(
                    kind="execute_result",
                    mime=MimeBundle(
                        data=_json_object(content.get("data")),
                        metadata=_json_object(content.get("metadata")),
                    ),
                )
                items.append(result_item)
                if entitylog is not None:
                    await _write_rendered_mime(entitylog, result_item.mime)
            elif msg_type == "error":
                status = "error"
                traceback = _string_tuple(content.get("traceback"))
                if entitylog is not None and traceback:
                    await entitylog.write("\n".join(traceback))

        try:
            reply = await self._client.get_shell_msg(timeout=1)
            if _event_data(reply.get("parent_header")).get("msg_id") == msg_id:
                content = _event_data(reply.get("content"))
                execution_count = _int_value(
                    content.get("execution_count"),
                    default=execution_count,
                )
                if content.get("status") == "error" and status == "ok":
                    status = "error"
                    traceback = _string_tuple(content.get("traceback"))
        except Exception:
            pass

        return PythonExecResult(
            status=status,
            execution_count=execution_count,
            items=tuple(items),
            stdout="".join(stdout),
            stderr="".join(stderr),
            traceback=traceback,
        )

    async def _kernel_is_alive(self) -> bool:
        if self._km is None:
            return False
        result = self._km.is_alive()
        if asyncio.iscoroutine(result):
            result = await result
        return bool(result)

    async def _interrupt(self) -> None:
        if self._km is None:
            return
        result = self._km.interrupt_kernel()
        if asyncio.iscoroutine(result):
            await result
        await asyncio.sleep(0.05)

    def read_output(self, call_id: str, offset: int = 0) -> tuple[str, int]:
        lines = self._exec_buffers.get(call_id, [])
        text = "".join(lines[offset:])
        return text, len(lines)

    def _bootstrap_code(self) -> str:
        config = self.runtime.config
        package_src = str(Path(__file__).resolve().parents[1])
        sys_path = [package_src, *config.sys_path]
        imports = [(item.module, item.alias) for item in self.runtime.imports]
        state = dict(self.runtime.state)
        state.setdefault("session_id", self.session_id)
        state_json = json.dumps(state, ensure_ascii=False)
        startup_code = config.startup_code or ""
        return f"""
import importlib as _ya_importlib
import json as _ya_json
import sys as _ya_sys
for _ya_path in {sys_path!r}:
    if _ya_path and _ya_path not in _ya_sys.path:
        _ya_sys.path.insert(0, _ya_path)
_ya_state = _ya_json.loads({state_json!r})
SESSION_STATE = _ya_state
ACTOR_ID = str(_ya_state.get("actor_id", ""))
SESSION_ID = str(_ya_state.get("session_id", ""))
MAILBOX_ID = str(_ya_state.get("mailbox_id", ""))
def get_session_state():
    return SESSION_STATE
TASKS = {{}}
for _ya_module, _ya_alias in {imports!r}:
    _ya_mod = _ya_importlib.import_module(_ya_module)
    if _ya_alias:
        _ya_sys.modules[_ya_alias] = _ya_mod
{startup_code}
"""

    def _state_rebind_code(self, state_json: str) -> str:
        return f"""
import json as _ya_json
SESSION_STATE = _ya_json.loads({state_json!r})
ACTOR_ID = str(SESSION_STATE.get("actor_id", ""))
SESSION_ID = str(SESSION_STATE.get("session_id", ""))
MAILBOX_ID = str(SESSION_STATE.get("mailbox_id", ""))
"""

    async def _emit(self, name: str, data: EventPayload) -> None:
        if self.emit is not None:
            await self.emit(name, data)


_DEFAULT_CAPTURE_STREAMS = frozenset(("stdout", "stderr"))
_ALLOWED_CAPTURE_STREAMS = frozenset(("stdout", "stderr"))


def render_python_result(
    result: PythonExecResult,
    *,
    capture: frozenset[str] = _DEFAULT_CAPTURE_STREAMS,
) -> yuullm.ToolOutput:
    if result.status == "ok":
        content: list[yuullm.ContentItem] = []
        if "stdout" in capture and result.stdout:
            content.append(
                {
                    "type": "text",
                    "text": "Captured stdout:\n" + _bounded(_strip_ansi(result.stdout)),
                }
            )
        if "stderr" in capture and result.stderr:
            content.append(
                {
                    "type": "text",
                    "text": "Captured stderr:\n" + _bounded(_strip_ansi(result.stderr)),
                }
            )
        for item in result.items:
            rendered = _render_mime_bundle(item.mime)
            if isinstance(rendered, list):
                content.extend(rendered)
            else:
                content.append(rendered)
        if content:
            return content
        return "Python executed successfully with no visible output."

    if result.status == "error":
        text = "Python execution failed."
        if result.traceback:
            text += "\n" + "\n".join(_strip_ansi(line) for line in result.traceback)
        if "stdout" in capture and result.stdout:
            text += "\nCaptured stdout before the error:\n" + _bounded(
                _strip_ansi(result.stdout)
            )
        if "stderr" in capture and result.stderr:
            text += "\nCaptured stderr before the error:\n" + _bounded(
                _strip_ansi(result.stderr)
            )
        return text

    if result.status == "timeout":
        return "Python execution timed out and the kernel was interrupted."
    if result.status == "interrupted":
        return "Python execution was interrupted."
    text = "Python session is unavailable."
    if result.traceback:
        text += "\n" + "\n".join(result.traceback)
    return text


def _render_mime_bundle(
    bundle: MimeBundle,
) -> yuullm.ContentItem | list[yuullm.ContentItem]:
    data = bundle.data
    if "text/markdown" in data:
        return {"type": "text", "text": str(data["text/markdown"])}
    if "image/png" in data:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{data['image/png']}"},
        }
    if "image/jpeg" in data:
        return {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{data['image/jpeg']}"},
        }
    if "application/json" in data:
        import json

        return {
            "type": "text",
            "text": json.dumps(data["application/json"], ensure_ascii=False),
        }
    if "text/plain" in data:
        return {"type": "text", "text": str(data["text/plain"])}
    import json

    return {"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}


async def _write_rendered_mime(entitylog: EntityLog, bundle: MimeBundle) -> None:
    rendered = _render_mime_bundle(bundle)
    if isinstance(rendered, list):
        for item in rendered:
            await entitylog.write(item)
        return
    await entitylog.write(rendered)


_ANSI_ESCAPE = __import__("re").compile(r"\x1b\[[0-9;]*[mKJHABCDfsu]")


def _strip_ansi(text: str) -> str:
    return str(_ANSI_ESCAPE.sub("", text))


def _bounded(text: str, limit: int = 4000) -> str:
    return text[:limit]


def _build_env(
    inherit_envs: bool,
    env_allowlist: tuple[str, ...] | None,
    extra_envs: Mapping[str, str],
) -> dict[str, str]:
    env = dict(os.environ) if inherit_envs else {}
    if env_allowlist is not None:
        allowed = set(env_allowlist)
        env = {key: value for key, value in env.items() if key in allowed}
    env.update({key: str(value) for key, value in extra_envs.items()})
    return env


def _parse_capture_streams(raw: tuple[str, ...]) -> frozenset[str]:
    capture: set[str] = set()
    invalid: list[str] = []
    for item in raw:
        if item not in _ALLOWED_CAPTURE_STREAMS:
            invalid.append(item)
        else:
            capture.add(item)
    if invalid:
        allowed = ", ".join(sorted(_ALLOWED_CAPTURE_STREAMS))
        raise TypeError(
            f"execute_python capture contains invalid stream names: {invalid}; allowed: {allowed}"
        )
    return frozenset(capture)


class _SingleKernelSpecManager(KernelSpecManager):
    def __init__(self, kernel_spec: KernelSpec) -> None:
        super().__init__()
        self._kernel_spec = kernel_spec

    def get_kernel_spec(self, kernel_name: str) -> KernelSpec:
        del kernel_name
        return self._kernel_spec


T = TypeVar("T")
type MaybeAwaitable[T] = T | Awaitable[T]


class KernelClientLike(Protocol):
    def execute(
        self,
        code: str,
        *,
        allow_stdin: bool,
        store_history: bool,
        silent: bool,
    ) -> str: ...

    async def get_iopub_msg(self, *, timeout: int) -> EventData: ...

    async def get_shell_msg(self, *, timeout: int) -> EventData: ...

    def start_channels(self) -> None: ...

    def stop_channels(self) -> None: ...

    async def wait_for_ready(self, *, timeout: float) -> None: ...


class KernelManagerLike(Protocol):
    def client(self) -> KernelClientLike: ...

    def start_kernel(
        self,
        *,
        cwd: str | None,
        env: Mapping[str, str],
    ) -> MaybeAwaitable[None]: ...

    def shutdown_kernel(self, *, now: bool) -> MaybeAwaitable[None]: ...

    def interrupt_kernel(self) -> MaybeAwaitable[None]: ...

    def is_alive(self) -> MaybeAwaitable[bool]: ...


def _event_data(value: EventValue | None) -> EventData:
    if isinstance(value, Mapping):
        return cast(EventData, dict(value))
    return {}


def _string_value(value: EventValue | None) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value: EventValue | None, *, default: int | None = None) -> int | None:
    return value if isinstance(value, int) else default


def _string_tuple(value: EventValue | None) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _json_object(value: EventValue | None) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return msgspec.convert(value, type=dict[str, object], strict=False)
