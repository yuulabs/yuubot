from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Callable, Iterator, Sequence
from fnmatch import fnmatchcase
from types import ModuleType
from typing import Literal

import msgspec

from yuuagents.types.values import validate_json_value


def _validate_json(value: object, path: str = "$") -> object:
    return validate_json_value(value, path)


class PythonImport(msgspec.Struct):
    module: str
    alias: str | None = None
    state_hook: dict[str, Callable[[], object]] = msgspec.field(default_factory=dict)

    @property
    def import_name(self) -> str:
        return self.alias or self.module


class PythonKernelConfig(msgspec.Struct):
    python: str | None = None
    cwd: str | None = None
    inherit_envs: bool = True
    env_allowlist: tuple[str, ...] | None = None
    extra_envs: dict[str, str] = msgspec.field(default_factory=dict)
    sys_path: tuple[str, ...] = ()
    startup_code: str = ""


class PythonRuntime(msgspec.Struct):
    config: PythonKernelConfig = msgspec.field(default_factory=PythonKernelConfig)
    imports: tuple[PythonImport, ...] = ()
    state: dict[str, object] = msgspec.field(default_factory=dict)
    expand_functions: tuple[str, ...] | None = None

    @property
    def import_modules(self) -> tuple[PythonImport, ...]:
        return self.imports


class PythonFunctionDoc(msgspec.Struct):
    name: str
    signature: str
    doc: str = ""
    is_async: bool = False


class PythonImportDoc(msgspec.Struct):
    module: str
    alias: str | None
    doc: str = ""
    functions: tuple[PythonFunctionDoc, ...] = ()
    error: str | None = None

    @property
    def import_name(self) -> str:
        return self.alias or self.module


class ResolvedPythonRuntime(msgspec.Struct):
    config: PythonKernelConfig
    imports: tuple[PythonImport, ...]
    state: dict[str, object]
    import_docs: tuple[PythonImportDoc, ...] = ()
    expand_functions: tuple[str, ...] | None = None

    def tool_description_suffix(self) -> str:
        parts: list[str] = [
            "Python session rules:",
            "- State persists for this live agent until the agent or session closes.",
            "- Use SESSION_STATE for host-provided JSON state.",
        ]
        if self.state:
            keys = ", ".join(sorted(self.state))
            parts.append(f"Session state keys: {keys}.")
        if self.import_docs:
            parts.append("Available Python packages:")
            for doc in self.import_docs:
                import_line = f"- import {doc.import_name}"
                if doc.module != doc.import_name:
                    import_line += f"  # alias for {doc.module}"
                parts.append(import_line)
                if doc.doc:
                    parts.append(f"  {doc.doc}")
                if doc.error:
                    parts.append(f"  [metadata unavailable: {doc.error}]")
                for fn in doc.functions:
                    prefix = "async def" if fn.is_async else "def"
                    summary = f"  {prefix} {doc.import_name}.{fn.name}{fn.signature}"
                    if fn.doc:
                        if "\n" in fn.doc:
                            summary += ":\n" + _indent(fn.doc, "    ")
                        else:
                            summary += f": {fn.doc}"
                    parts.append(summary)
        return "\n".join(parts)


def _resolve_python(
    runtime: PythonRuntime,
    *,
    default_doc_mode: Literal["summary", "full"] = "summary",
) -> ResolvedPythonRuntime:
    hook_state = _collect_hook_state(runtime.imports)
    state = {**runtime.state, **hook_state} if hook_state else runtime.state
    return ResolvedPythonRuntime(
        config=runtime.config,
        imports=runtime.imports,
        state=state,
        expand_functions=runtime.expand_functions,
        import_docs=tuple(
            _describe_imports(
                runtime.imports,
                runtime.config.sys_path,
                runtime.expand_functions,
                default_doc_mode=default_doc_mode,
            )
        ),
    )


def _collect_hook_state(imports: tuple[PythonImport, ...]) -> dict[str, object]:
    extra: dict[str, object] = {}
    for imp in imports:
        for key, hook in imp.state_hook.items():
            extra[key] = _validate_json(hook())
    return extra


def _describe_imports(
    imports: Sequence[PythonImport],
    sys_path: Sequence[str],
    expand_functions: Sequence[str] | None,
    *,
    default_doc_mode: Literal["summary", "full"] = "summary",
) -> Iterator[PythonImportDoc]:
    old_path = list(sys.path)
    for entry in reversed(tuple(sys_path)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    try:
        for item in imports:
            try:
                module = importlib.import_module(item.module)
            except Exception as exc:
                yield PythonImportDoc(
                    module=item.module,
                    alias=item.alias,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            functions = _describe_functions(
                module,
                item,
                expand_functions,
                default_doc_mode=default_doc_mode,
            )
            yield PythonImportDoc(
                module=item.module,
                alias=item.alias,
                doc=_first_line(module.__doc__),
                functions=tuple(functions),
            )
    finally:
        sys.path[:] = old_path


def _describe_functions(
    module: ModuleType,
    item: PythonImport,
    expand_functions: Sequence[str] | None,
    *,
    default_doc_mode: Literal["summary", "full"] = "summary",
) -> list[PythonFunctionDoc]:
    public_names = getattr(module, "__all__", None)
    if isinstance(public_names, Sequence) and not isinstance(public_names, str | bytes):
        functions = [
            (name, value)
            for name in public_names
            if isinstance(name, str)
            for value in [getattr(module, name, None)]
            if inspect.isfunction(value)
        ]
    else:
        functions = [
            (name, value)
            for name, value in inspect.getmembers(module, inspect.isfunction)
            if not name.startswith("_")
        ]
    selected: dict[str, Literal["summary", "full"]] = {}
    if expand_functions is None:
        selected.update({name: default_doc_mode for name, _value in functions[:24]})
    else:
        for raw_pattern in expand_functions:
            mode: Literal["summary", "full"] = "summary"
            pattern = raw_pattern
            if pattern.startswith("+"):
                mode = "full"
                pattern = pattern[1:]
            elif pattern.startswith("-"):
                pattern = pattern[1:]
                for name, _value in functions:
                    if _matches_function_pattern(item, name, pattern):
                        selected.pop(name, None)
                continue
            for name, _value in functions:
                if _matches_function_pattern(item, name, pattern):
                    selected[name] = mode

    docs: list[PythonFunctionDoc] = []
    for name, value in functions:
        doc_mode = selected.get(name)
        if doc_mode is None:
            continue
        try:
            signature = str(inspect.signature(value))
        except TypeError, ValueError:
            signature = "(...)"
        docs.append(
            PythonFunctionDoc(
                name=name,
                signature=signature,
                doc=_doc_text(value.__doc__, full=doc_mode == "full"),
                is_async=inspect.iscoroutinefunction(value)
                or inspect.isasyncgenfunction(value),
            )
        )
    return docs


def _matches_function_pattern(item: PythonImport, name: str, pattern: str) -> bool:
    candidates = (name, f"{item.module}.{name}", f"{item.import_name}.{name}")
    return any(fnmatchcase(candidate, pattern) for candidate in candidates)


def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return text.strip().splitlines()[0][:240]


def _doc_text(text: str | None, *, full: bool) -> str:
    if not text:
        return ""
    if not full:
        return _first_line(text)
    return text.strip()[:4000]


def _indent(text: str, prefix: str) -> str:
    return "\n".join(
        f"{prefix}{line}" if line else prefix.rstrip() for line in text.splitlines()
    )
