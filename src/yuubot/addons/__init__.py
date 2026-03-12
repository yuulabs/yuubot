"""Addon framework — in-process capabilities exposed as a single LLM tool.

Addons are yuubot's built-in abilities (im, mem, web, etc.) that run inside
the daemon process. They look like CLI commands to the LLM but execute as
direct function calls — no subprocess, no path translation, multimodal returns.

Tool interface for LLM:
    execute_addon_cli("im send --ctx 5 -- [{...}]")
    read_addon_doc("mem")

The `--` separator splits CLI args from structured JSON data.
Left side: shlex-parsed arguments. Right side: raw JSON (no escaping needed).
"""

from __future__ import annotations

import json
import inspect
import shlex
from pathlib import Path
from typing import Any

from loguru import logger

# ContentBlock: reuse yuullm's dict-based content items.
# A tool result is list[ContentBlock] — text, images, etc.
ContentBlock = dict[str, Any]


def uri_to_path(s: str) -> str:
    """Convert ``file:///path`` URI to local path. Bare paths pass through."""
    if s.startswith("file:///"):
        return s[len("file://"):]  # file:///home/x → /home/x
    if s.startswith("file://"):
        return s[len("file://"):]
    return s


def path_to_uri(s: str) -> str:
    """Ensure a local path is in ``file:///`` URI form."""
    if s.startswith("file://"):
        return s
    return f"file://{s}" if s.startswith("/") else s


def text_block(text: str) -> ContentBlock:
    """Convenience: create a text content block."""
    return {"type": "text", "text": text}


def image_block(url: str) -> ContentBlock:
    """Convenience: create an image content block."""
    return {"type": "image_url", "image_url": {"url": url}}


# ── Registry ────────────────────────────────────────────────────

_REGISTRY: dict[str, type] = {}
_INSTANCES: dict[str, object] = {}


def addon(name: str):
    """Class decorator to register an addon.

    Usage::

        @addon("im")
        class ImAddon:
            async def send(self, ctx_id: int, ...) -> list[ContentBlock]: ...
    """
    def decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return decorator


def get_addon(name: str) -> object:
    """Get or create a singleton addon instance."""
    if name not in _INSTANCES:
        if name not in _REGISTRY:
            raise KeyError(f"unknown addon: {name!r}")
        _INSTANCES[name] = _REGISTRY[name]()
    return _INSTANCES[name]


def registered_addons() -> list[str]:
    """Return sorted list of registered addon names."""
    return sorted(_REGISTRY.keys())


# ── Command parsing ─────────────────────────────────────────────


def _parse_command(raw: str) -> tuple[str, str, list[str], Any]:
    """Parse "addon_name subcommand --flags ... [-- json_data]".

    Returns (addon_name, subcommand, args_list, data).
    data is None if no `--` separator, otherwise parsed JSON.
    """
    # Split on first ` -- ` (with spaces) to separate CLI from data
    data = None
    cli_part = raw
    # Find standalone -- separator (not inside quotes)
    # We split on ' -- ' first, then handle edge cases
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
            f"command must be 'addon_name subcommand [args...]', got: {raw!r}"
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
                # Boolean flag
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


# ── Execution ───────────────────────────────────────────────────


class AddonContext:
    """Runtime context passed to addon methods during execution.

    Populated by the daemon before each call with per-request info.
    """

    def __init__(
        self,
        *,
        config: Any = None,
        ctx_id: int | None = None,
        user_id: int | None = None,
        user_role: str = "",
        agent_name: str = "",
        task_id: str = "",
    ):
        self.config = config
        self.ctx_id = ctx_id
        self.user_id = user_id
        self.user_role = user_role
        self.agent_name = agent_name
        self.task_id = task_id


# Thread-local-ish context for the current addon call.
# Set before dispatch, cleared after.
_current_context: AddonContext | None = None


def get_context() -> AddonContext:
    """Get the current addon execution context. Raises if not in a call."""
    if _current_context is None:
        raise RuntimeError("not inside an addon call")
    return _current_context


async def execute(
    command: str,
    *,
    context: AddonContext | None = None,
) -> list[ContentBlock]:
    """Execute an addon command string.

    Args:
        command: Full command like "im send --ctx 5 -- [{...}]"
        context: Runtime context (config, env). If None, uses module-level.

    Returns:
        Multimodal content blocks.
    """
    global _current_context

    addon_name, subcommand, args, data = _parse_command(command)
    parsed = _parse_args(args)

    logger.debug("Addon execute: {} {} (ctx_id={}, agent={})",
                 addon_name, subcommand,
                 context.ctx_id if context else None,
                 context.agent_name if context else None)

    instance = get_addon(addon_name)
    method = getattr(instance, subcommand, None)
    if method is None:
        available = [
            m for m in dir(instance)
            if not m.startswith("_") and callable(getattr(instance, m))
        ]
        raise ValueError(
            f"addon {addon_name!r} has no command {subcommand!r}. "
            f"Available: {', '.join(available)}"
        )

    # Inject data if present
    if data is not None:
        parsed["data"] = data

    # Detect unknown arguments that would be silently swallowed by **_kw
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
                f"addon {addon_name!r} command {subcommand!r} got unknown arguments: "
                f"{', '.join(sorted(unknown))}. "
                f"Call read_addon_doc('{addon_name}') to see correct usage."
            )

    # Set context for the duration of the call
    prev = _current_context
    _current_context = context
    try:
        result = await method(**parsed)
        logger.debug("Addon done: {} {}", addon_name, subcommand)
    except TypeError as e:
        # Wrong/missing arguments — nudge LLM to read the doc
        raise ValueError(
            f"{e}. "
            f"Call read_addon_doc('{addon_name}') to see correct usage."
        ) from e
    except Exception as e:
        logger.error("Addon failed: {} {} - {}", addon_name, subcommand, e)
        raise
    finally:
        _current_context = prev

    # Normalize return
    if result is None:
        return [text_block("done")]
    if isinstance(result, str):
        return [text_block(result)]
    if isinstance(result, list):
        return result
    return [text_block(str(result))]


# ── Doc loading ─────────────────────────────────────────────────

_DOC_DIR = Path(__file__).parent / "docs"


def load_addon_doc(name: str) -> str:
    """Load addon documentation markdown."""
    # Try docs/ directory first
    doc_path = _DOC_DIR / f"{name}.md"
    if doc_path.is_file():
        return doc_path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"no documentation for addon {name!r}")


def addon_summary(name: str) -> str:
    """One-line description for on-demand addon listing."""
    doc_path = _DOC_DIR / f"{name}.md"
    if not doc_path.is_file():
        return ""
    # Parse YAML frontmatter for description
    lines = doc_path.read_text(encoding="utf-8").splitlines()
    in_frontmatter = False
    desc_lines: list[str] = []
    collecting_desc = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if in_frontmatter:
                break  # end of frontmatter
            in_frontmatter = True
            continue
        if not in_frontmatter:
            continue
        if stripped.startswith("description:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest.startswith(">"):
                collecting_desc = True
            elif rest:
                return rest
        elif collecting_desc:
            if stripped and not stripped.startswith(("name:", "---")):
                desc_lines.append(stripped)
            else:
                break
    return " ".join(desc_lines).strip()


# ── Import all addon modules to trigger registration ────────────

from yuubot.addons import im      # noqa: E402, F401
from yuubot.addons import mem     # noqa: E402, F401
from yuubot.addons import web     # noqa: E402, F401
from yuubot.addons import img     # noqa: E402, F401
from yuubot.addons import schedule as _schedule  # noqa: E402, F401
from yuubot.addons import hhsh    # noqa: E402, F401
from yuubot.addons import vision  # noqa: E402, F401
