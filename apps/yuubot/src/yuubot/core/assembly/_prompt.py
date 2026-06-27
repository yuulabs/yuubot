"""System prompt construction helpers.

Renders a five-section system prompt contract:

    # Persona
    <Actor.persona_prompt verbatim>

    # System Instructions
    <tool-surface prose + workspace conventions + optional IM-mode guidance>

    # Integration SDKs
    <per-integration SDK prompt summaries for selected + running integrations;
     default line otherwise>

    # AGENTS.md Context
    <full AGENTS.md text + freeze note when present; default line otherwise>

    # Real-Time Data
    <platform, absolute ISO date/time, timezone>

Section order is part of the public contract. ``_render_extension_fragments``
exists as a code-only insertion point immediately before AGENTS.md Context;
it returns ``""`` today and the assembled prompt MUST NOT contain a
``# Extension Section`` header.

The ``# Integration SDKs`` section renders each visible
``VisibleIntegrationSurface``'s ``sdk.prompt_summary`` under a per-integration
sub-header (design §2.7.1, invariant 9): short per-integration summaries with
representative examples, not a mechanical expansion of every function schema.
"""

from __future__ import annotations

import platform
from datetime import datetime
from pathlib import Path
from typing import Literal

from yuubot.core.bindings import AgentBinding
from yuubot.core.facade import ActorFacadeBinding

from ._constants import IM_MODE_SYSTEM_GUIDANCE


# Public contract: section header markers in render order.
SECTION_HEADERS: tuple[str, ...] = (
    "Persona",
    "System Instructions",
    "Integration SDKs",
    "AGENTS.md Context",
    "Real-Time Data",
)

# Note appended to AGENTS.md Context body when an AGENTS.md file exists.
_AGENTS_MD_FREEZE_NOTE = (
    "Editing AGENTS.md only affects future agent instantiations. "
    "The current conversation will keep using the snapshot assembled at first send."
)

_NO_INTEGRATION_SDKS = "No integration SDKs configured."


def _system_prompt(
    binding: AgentBinding,
    mode: Literal["im", "conversation"],
    facade: ActorFacadeBinding | None = None,
) -> str:
    # Pair each visible header with its rendered body. Extension fragments
    # carry an empty header: the body is ``""`` today and the extension zone
    # MUST NOT produce a visible ``"# Extension Section"`` header.
    sections: list[tuple[str, str]] = [
        ("Persona", _render_persona(binding)),
        ("System Instructions", _render_system_instructions(binding, mode)),
        ("Integration SDKs", _render_integration_sections(binding, facade)),
        ("", _render_extension_fragments()),
        ("AGENTS.md Context", _render_agents_md_context(binding.workspace_path)),
        ("Real-Time Data", _render_realtime()),
    ]
    rendered: list[str] = []
    for header, body in sections:
        if not header:
            # Invisible insertion point; "" today.
            continue
        if body or header == "Integration SDKs":
            rendered.append(f"# {header}\n{body}" if body else f"# {header}")
    return "\n\n".join(rendered)


def _render_persona(binding: AgentBinding) -> str:
    return binding.actor.persona_prompt.strip()


def _render_system_instructions(
    binding: AgentBinding,
    mode: Literal["im", "conversation"],
) -> str:
    bullets: list[str] = [
        "- bash: shell command surface, one initialized command at a time; "
        "use it for shell-native workspace operations, not file edits.",
        "- read / edit / write: structured file surfaces; prefer them over bash "
        "for file operations; do not bypass them with bash to mutate files.",
        "- execute_python: integration-call surface. Integration calls go "
        "through execute_python. Do NOT call github.* capability ids as "
        "top-level tools unless they also appear under Tools.",
    ]
    if binding.workspace_path:
        workspace_url_segment = binding.capability_set.workspace_path.strip() or None
        bullets.extend(
            _workspace_bullets(binding.workspace_path, workspace_url_segment)
        )
    # Unconditional math guidance: text blocks already render KaTeX (Phase A-4),
    # so the agent may emit inline ``$...$`` and block ``$$...$$`` LaTeX.
    bullets.append(
        "- You may emit LaTeX math in text blocks: inline $...$ and block $$...$$."
    )
    if mode == "im":
        bullets.append("")
        bullets.append(IM_MODE_SYSTEM_GUIDANCE)
    return "\n".join(bullets)


def _workspace_bullets(
    workspace_path: Path,
    workspace_url_segment: str | None,
) -> list[str]:
    absolute = str(workspace_path.resolve())
    bullets: list[str] = [
        "",
        "Workspace:",
        f"- Absolute workspace path: {absolute}",
        "- This workspace path IS the current working directory; relative paths in execute_python resolve against it.",
        "- Create subfolders for project work under the workspace.",
        "- tmp/ is for scratch output; artifacts/ is the local artifact store "
        "(create it first if missing: os.makedirs('artifacts', exist_ok=True)).",
        "- AGENTS.md at the workspace root is the project map; update it when projects change (create / remove / rename).",
        "",
        "Python execution environment:",
        "- execute_python runs in the workspace's isolated .venv; `pd`, `np`, `plt` are pre-imported there — use them directly.",
        "- To check what is installed: run `uv pip list` (via bash) — it lists the workspace .venv packages.",
        "- To add a package: run `uv add <pkg>` (via bash, in the workspace). Do NOT use `pip install` (it bypasses uv cache isolation).",
        "- After `uv add`/`uv remove`, call the `restart_kernel` tool so the next execute_python starts a fresh kernel in the same .venv and picks up the change.",
    ]
    bullets.extend(_file_delivery_bullets(workspace_url_segment))
    return bullets


def _file_delivery_bullets(
    workspace_url_segment: str | None,
) -> list[str]:
    bullets: list[str] = [
        "",
        "Delivering files to the user:",
        "- The runtime is headless: plt.show() and inline auto-display do NOT "
        "reach the user, and you cannot see rendered images either.",
        "- Save any output files under the workspace (e.g. artifacts/).",
    ]
    if workspace_url_segment:
        bullets.append(
            "- Saved files under the workspace are served by the workspace "
            f"browser at /workspace/{workspace_url_segment}/."
        )
    else:
        bullets.append(
            "- Reference saved files by their relative path under the "
            "workspace, e.g. artifacts/<name>.png."
        )
    bullets.extend([
        "",
        "Image files (png, jpg, gif, svg, etc.):",
        "- Embed the saved file in your reply as a markdown image so the user "
        "sees it inline.",
    ])
    if workspace_url_segment:
        bullets.append(
            f"  Example: ![chart](/workspace/{workspace_url_segment}/artifacts/<name>.png)."
        )
    else:
        bullets.append(
            "  Example: ![chart](artifacts/<name>.png)."
        )
    bullets.extend([
        "",
        "Non-image files (pdf, txt, csv, html, zip, etc.):",
        "- The frontend only renders images inline. For all other file types, "
        "state the file's path and instruct the user to find it in the workspace.",
        "",
        "General rules:",
        "- Do NOT fabricate external URLs (e.g. quickchart.io) or claim "
        "a file was created when it was not. Only reference files you actually "
        "saved.",
        "- Label charts in English by default (titles, axis labels, legends). "
        "Only switch to Chinese text when the user explicitly asks for it — "
        "headless host font coverage for CJK and emoji is not guaranteed and "
        "will emit glyph-missing warnings.",
    ])
    return bullets


def _render_integration_sections(
    binding: AgentBinding,
    facade: ActorFacadeBinding | None = None,
) -> str:
    """Render the ``# Integration SDKs`` section (§2.7.1, invariant 9).

    Each visible ``VisibleIntegrationSurface`` (selected ∩ running, derived at
    facade-bind time and carried on the facade) with a non-empty
    ``sdk.prompt_summary`` is rendered under a ``## {integration_name}``
    sub-header. Surfaces without a prompt summary (e.g. inbound-only kinds)
    contribute nothing. When the facade is absent or no surface has a summary,
    the section renders the empty default.
    """
    _ = binding
    if facade is None:
        return _NO_INTEGRATION_SDKS
    sub_sections: list[str] = []
    for surface in facade.integration_surfaces:
        summary = surface.sdk.prompt_summary.strip()
        if not summary:
            continue
        header = f"{surface.integration_name} ({surface.integration_id})"
        imports = ", ".join(surface.sdk.import_paths)
        instance_line = f"- Integration id: `{surface.integration_id}`"
        if imports:
            instance_line += f"\n- Import: `{imports}`"
        sub_sections.append(f"## {header}\n{instance_line}\n{summary}")
    if not sub_sections:
        return _NO_INTEGRATION_SDKS
    return "\n\n".join(sub_sections)


def _render_extension_fragments() -> str:
    # Documented code-only insertion point immediately before AGENTS.md Context.
    # Returns "" today; the assembled prompt MUST NOT contain a
    # "# Extension Section" header.
    return ""


def _render_agents_md_context(workspace_path: Path | None) -> str:
    if workspace_path is None:
        return "No AGENTS.md found at the workspace root."
    agents_md = workspace_path / "AGENTS.md"
    if not agents_md.exists():
        return "No AGENTS.md found at the workspace root."
    body = agents_md.read_text(encoding="utf-8").strip()
    return f"{body}\n\n{_AGENTS_MD_FREEZE_NOTE}"


def _render_realtime() -> str:
    now = datetime.now().astimezone()
    tz = now.strftime("%Z") or "UTC"
    platform_token = platform.system().lower() or "unknown"
    return (
        f"- Platform: {platform_token}\n"
        f"- Datetime: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"- Timezone: {tz}"
    )
