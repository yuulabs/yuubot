"""Capability framework — in-process abilities exposed as CLI-style tools.

Capabilities are yuubot's built-in abilities (im, mem, web, etc.) that run
inside the daemon process. They look like CLI commands to the LLM but execute
as direct function calls.

Tool interface for LLM:
    call_cap_cli("im send --ctx 5 -- [{...}]")
    read_cap_doc("mem")

The `--` separator splits CLI args from structured JSON data.
Left side: shlex-parsed arguments. Right side: raw JSON (no escaping needed).
"""

from __future__ import annotations

import inspect
import json
import shlex
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

import attrs
from loguru import logger
from yuullm import ImageItem, TextItem

if TYPE_CHECKING:
    from yuubot.config import Config

from yuubot.capabilities.contract import ActionFilter

# ContentBlock covers the common content shapes capabilities return.
# TextItem and ImageItem are TypedDicts from yuullm; dict[str, Any] is a
# fallback for other structured shapes that may appear as capabilities evolve.
ContentBlock = TextItem | ImageItem | dict[str, Any]


def uri_to_path(s: str) -> str:
    """Convert ``file:///path`` URI to local path. Bare paths pass through."""
    if s.startswith("file:///"):
        return s[len("file://"):]
    if s.startswith("file://"):
        return s[len("file://"):]
    return s


def path_to_uri(s: str) -> str:
    """Ensure a local path is in ``file:///`` URI form."""
    if s.startswith("file://"):
        return s
    return f"file://{s}" if s.startswith("/") else s


def text_block(text: str) -> TextItem:
    """Convenience: create a text content block."""
    return TextItem(type="text", text=text)


def image_block(url: str) -> ImageItem:
    """Convenience: create an image content block."""
    return ImageItem(type="image_url", image_url={"url": url})


# ── Context ──────────────────────────────────────────────────────


@attrs.define
class CapabilityContext:
    """Runtime context for capability execution."""

    config: Config | None = None
    ctx_id: int | None = None
    user_id: int | None = None
    user_role: str = ""
    agent_name: str = ""
    task_id: str = ""
    runtime_id: str = ""
    bot_name: str = ""
    allowed_caps: frozenset[str] | None = None  # None = all caps allowed
    action_filters: dict[str, ActionFilter] | None = None
    docker_host_mount: str = ""
    docker_home_host_dir: str = ""
    docker_home_dir: str = ""


_ctx_var: ContextVar[CapabilityContext | None] = ContextVar(
    "capability_context", default=None
)


def get_context() -> CapabilityContext:
    """Get the current capability execution context. Raises if not in a call."""
    ctx = _ctx_var.get()
    if ctx is None:
        raise RuntimeError("not inside a capability call")
    return ctx


# ── Registry ─────────────────────────────────────────────────────

_REGISTRY: dict[str, type] = {}
_INSTANCES: dict[str, object] = {}


def capability(name: str):
    """Class decorator to register a capability.

    Usage::

        @capability("im")
        class ImCapability:
            async def send(self, ctx_id: int, ...) -> list[ContentBlock]: ...
    """
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_capability(name: str) -> object:
    """Get or create a singleton capability instance."""
    if name not in _INSTANCES:
        if name not in _REGISTRY:
            raise KeyError(f"unknown capability: {name!r}")
        _INSTANCES[name] = _REGISTRY[name]()
    return _INSTANCES[name]


def registered_capabilities() -> list[str]:
    """Return sorted list of registered capability names."""
    return sorted(_REGISTRY.keys())


# ── Command parsing ──────────────────────────────────────────────


def _parse_command(raw: str) -> tuple[str, str, list[str], Any]:
    """Parse "cap_name subcommand --flags ... [-- json_data]".

    Returns (cap_name, subcommand, args_list, data).
    data is None if no `--` separator, otherwise parsed JSON.
    """
    data = None
    cli_part = raw
    sep = " -- "
    idx = raw.find(sep)
    if idx >= 0:
        cli_part = raw[:idx]
        data_str = raw[idx + len(sep):]
        try:
            data = json.loads(data_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON data after '--' is invalid: {e}") from e

    tokens = shlex.split(cli_part)
    if len(tokens) < 2:
        raise ValueError(
            f"command must be 'capability subcommand [args...]', got: {raw!r}"
        )
    return tokens[0], tokens[1], tokens[2:], data


def _parse_args(args: list[str]) -> dict[str, Any]:
    """Parse CLI-style args into a dict.

    Supports: --key value, --key=value, --flag (bool True).
    Positional args collected under '_positional'.
    """
    result: dict[str, Any] = {}
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("--"):
            if "=" in arg:
                key, val = arg[2:].split("=", 1)
                result[key.replace("-", "_")] = _coerce(val)
            elif i + 1 < len(args) and not args[i + 1].startswith("--"):
                key = arg[2:].replace("-", "_")
                result[key] = _coerce(args[i + 1])
                i += 1
            else:
                result[arg[2:].replace("-", "_")] = True
        else:
            positional.append(arg)
        i += 1
    if positional:
        result["_positional"] = positional
    return result


def _coerce(val: str) -> Any:
    """Try to coerce string to int/float, else keep as string."""
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


# ── Execution ────────────────────────────────────────────────────


async def execute(
    command: str,
    *,
    context: CapabilityContext | None = None,
) -> list[ContentBlock]:
    """Execute a capability command string.

    Args:
        command: Full command like "im send --ctx 5 -- [{...}]"
        context: Runtime context. If None, uses ContextVar.

    Returns:
        Multimodal content blocks.
    """
    cap_name, subcommand, args, data = _parse_command(command)
    parsed = _parse_args(args)

    if context is not None and context.allowed_caps is not None and cap_name not in context.allowed_caps:
        raise ValueError(
            f"capability {cap_name!r} is not available to this agent "
            f"(allowed: {', '.join(sorted(context.allowed_caps))})"
        )
    if (
        context is not None
        and context.action_filters is not None
        and cap_name in context.action_filters
    ):
        action_filter = context.action_filters[cap_name]
        if action_filter.mode == "include" and subcommand not in action_filter.actions:
            raise ValueError(
                f"capability {cap_name!r} command {subcommand!r} is not available to this agent. "
                f"Call read_cap_doc('{cap_name}') to see allowed commands."
            )
        if action_filter.mode == "exclude" and subcommand in action_filter.actions:
            raise ValueError(
                f"capability {cap_name!r} command {subcommand!r} is not available to this agent. "
                f"Call read_cap_doc('{cap_name}') to see allowed commands."
            )

    logger.debug("Capability execute: {} {} (ctx_id={}, agent={})",
                 cap_name, subcommand,
                 context.ctx_id if context else None,
                 context.agent_name if context else None)

    instance = get_capability(cap_name)
    method = getattr(instance, subcommand, None)
    if method is None:
        available = [
            m for m in dir(instance)
            if not m.startswith("_") and callable(getattr(instance, m))
        ]
        raise ValueError(
            f"capability {cap_name!r} has no command {subcommand!r}. "
            f"Available: {', '.join(available)}"
        )

    if data is not None:
        parsed["data"] = data

    # Detect unknown arguments
    sig = inspect.signature(method)
    has_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if has_var_kw:
        known = {
            name for name, p in sig.parameters.items()
            if p.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        }
        unknown = set(parsed.keys()) - known
        if unknown:
            raise ValueError(
                f"capability {cap_name!r} command {subcommand!r} got unknown arguments: "
                f"{', '.join(sorted(unknown))}. "
                f"Call read_cap_doc('{cap_name}') to see correct usage."
            )

    # Set context via ContextVar for the duration of the call
    token = _ctx_var.set(context)
    try:
        result = await method(**parsed)
        logger.debug("Capability done: {} {}", cap_name, subcommand)
    except TypeError as e:
        raise ValueError(
            f"{e}. "
            f"Call read_cap_doc('{cap_name}') to see correct usage."
        ) from e
    except Exception as e:
        logger.error("Capability failed: {} {} - {}", cap_name, subcommand, e)
        raise
    finally:
        _ctx_var.reset(token)

    # Normalize return
    if result is None:
        return [text_block("done")]
    if isinstance(result, str):
        return [text_block(result)]
    if isinstance(result, list):
        return result
    return [text_block(str(result))]


# ── Doc loading ──────────────────────────────────────────────────

def load_capability_doc(
    name: str,
    *,
    action_filter: ActionFilter | None = None,
) -> str:
    """Render capability documentation from the YAML contract."""
    from yuubot.capabilities.contract import (
        filter_contract_actions,
        load_all_contracts,
        render_contract_doc,
    )

    contracts = load_all_contracts()
    contract = contracts.get(name)
    if contract is None:
        raise FileNotFoundError(f"no documentation for capability {name!r}")

    return render_contract_doc(filter_contract_actions(contract, action_filter))


def capability_summary(name: str) -> str:
    """One-line description from the contract."""
    from yuubot.capabilities.contract import load_all_contracts
    contracts = load_all_contracts()
    if name in contracts:
        return contracts[name].summary
    return ""


# ── Import capability modules to trigger registration ────────────

from yuubot.capabilities import im      # noqa: E402, F401
from yuubot.capabilities import mem     # noqa: E402, F401
from yuubot.capabilities import web     # noqa: E402, F401
from yuubot.capabilities import img     # noqa: E402, F401
from yuubot.capabilities import schedule as _schedule  # noqa: E402, F401
from yuubot.capabilities import hhsh    # noqa: E402, F401
from yuubot.capabilities import vision  # noqa: E402, F401
