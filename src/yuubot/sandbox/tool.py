"""sandbox_python tool definition for yuutools/yuuagents."""

from __future__ import annotations

import yuutools as yt

from yuubot.sandbox.executor import execute_sandbox

_TOOL_DESC = (
    "Execute a RESTRICTED Python subset for pure computation only. "
    "This is NOT full Python — it is a crippled, sandboxed executor.\n\n"
    "Available: arithmetic, string ops, regex, list/dict/set transforms, "
    "sorting, counting, combinatorics, statistics, JSON encode/decode.\n\n"
    "Approved modules (use directly or import them): "
    "math, random, re, itertools, collections, functools, operator, "
    "statistics, json, string, textwrap, heapq, bisect.\n\n"
    "Rules:\n"
    "- Use return_result(value) to emit output (can call multiple times)\n"
    "- print() works for debug output but prefer return_result()\n"
    "- Only import approved modules listed above\n"
    "- NO file/network/env access, NO classes, NO try/except\n"
    "- NO dunder (__x__) or private (_x) attribute access\n"
    "- NO eval/exec/compile/getattr/type/open/globals\n"
    "- Keep code short and direct\n"
    "- If the task needs unsupported features, say so — do NOT attempt workarounds"
)


@yt.tool(
    params={
        "code": (
            "Python code to execute in the restricted sandbox. "
            "Must use return_result(value) to emit results."
        ),
    },
    description=_TOOL_DESC,
)
async def sandbox_python(code: str) -> str:
    """Execute restricted Python and return formatted results."""
    result = await execute_sandbox(code)

    if result.error:
        parts = ["[ERROR] " + result.error]
        if result.stdout:
            parts.append("[STDOUT]\n" + result.stdout)
        return "\n".join(parts)

    parts: list[str] = []
    if result.results:
        for i, item in enumerate(result.results):
            if len(result.results) == 1:
                parts.append(item)
            else:
                parts.append(f"[result {i + 1}]\n{item}")
    if result.stdout:
        parts.append("[stdout]\n" + result.stdout)
    if not parts:
        return "[ERROR] no result returned — call return_result(value) to emit output"
    return "\n".join(parts)
