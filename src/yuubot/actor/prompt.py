import inspect
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Literal

from ..domain.messages import ContentItem, InputMessage
from ..integrations.registry import Integration
from ..runtime.skills import SkillSummary
from .prompt_docs import ADMIN_PAGES

_RUNTIME_FACADE_PACKAGES = ("yb.fixer", "yb.tasks", "yb.tasks.cron", "yb.mcps", "yb.skills")

SessionMode = Literal["conversation", "actor"]
REAL_TIME_CONTEXT_MARKER = "[yuubot-real-time-context]"
_REAL_TIME_CONTEXT_SEPARATOR = "\n---\n"


def developer_prompt(
    persona: str,
    workspace: Path,
    integrations: list[Integration],
    *,
    actor_id: str = "",
    has_python: bool,
    global_skills: list[SkillSummary] | None = None,
) -> str:
    sections = [
        "# Persona\n" + (persona.strip() or "You are a yuubot actor."),
        "# System Instructions\n" + _system_instructions(has_python),
        "# Workspace Instructions\n" + _workspace_instructions(workspace, actor_id),
    ]
    if has_python:
        sections.append("# Tool Suggestions\n" + _tool_suggestions())
    extra_packages = _RUNTIME_FACADE_PACKAGES if has_python else ()
    integration_docs = _integration_docs(integrations, extra_packages)
    if integration_docs:
        sections.append("# Integration SDKs\n" + integration_docs)
    sections.append("# Skills\n" + _skills(workspace, global_skills or []))
    sections.append("# AGENTS.md\n" + _agents_context(workspace))
    sections.append("# Real-Time Data\n" + _real_time_data())
    return "\n\n".join(sections)


_YUUBOT_FRAMEWORK_PROMPT = """"
You are running inside an AI agent framework called Yuubot. You have access to a workspace served in a cloud server.
The user is chatting with you through a web interface. You may also receive messages from other sources (if so, relevant context will be provided in the message).
"""

def _system_instructions(has_python: bool) -> str:
    lines = [
        _YUUBOT_FRAMEWORK_PROMPT,
    ]
    if has_python:
        lines.append("execute_python resets after each user turn; a developer notice appears when a prior session is gone.")
    return "\n".join(lines)


def _workspace_instructions(workspace: Path, actor_id: str = "") -> str:
    lines = [f"Workspace path: {workspace}"]
    if actor_id:
        lines.append(f"Actor id: {actor_id}")
    lines.extend(
        [
            "- Put one-time reports, web pages, charts, and exports in `artifacts/<slug>/`.",
            "- `uploads/`: uploaded files grouped by MIME type.",
            "- Put code and documentation that will be developed or maintained over time in a cohesive `projects/<slug>/` directory.",
            "- `notes/`: durable actor notes.",
            "- `scripts/`: helper scripts.",
            "- `.agents/skills/`: skill files you may inspect with the read tool.",
            "- User and assistant messages may reference workspace files as `[[ relative/path ]]`; use the read tool to inspect referenced files before relying on their contents.",
            "- Show images with Markdown: `![short alt](relative/path)` or `![short alt](https://...)`. Prefer an external URL when the image is already online.",
            "- Reference non-image workspace files as `[[ relative/path ]]`. Do not nest `[[...]]` inside Markdown image or link URLs.",
            "- Use `uv` to manage python dependencies and run shell commmands(uv run) instead of raw python."
        ]
    )
    lines.append("- Keep implementation files inside their artifact or project directory; reserve the workspace root for established entry points and workspace conventions.")
    lines.append("- `AGENTS.md`: the concise workspace map and durable-constraint entry point loaded for each session. Keep project details, run instructions, and design notes in the corresponding project directory, and record their location here.")
    return "\n".join(lines)


def _tool_suggestions() -> str:
    execute_python_example = """Example execute_python code block:
```python
results = await yext.web.search(query)
print(results[:3])

page = await yext.web.read(results[0].url)
print(page[:2000])

repo = yext.github.repo()
issues = await repo.issues.list_recent()
print([{"number": issue.number, "title": issue.title} for issue in issues[:10]])

async def fetch_data(url):
    return await yext.web.read(url)
results = asyncio.gather(*(fetch_data(url) for url in ["https://example.com", "https://example.org"]))
for r in results:
    print(r[:1000])
```"""
    return "\n".join(
        [
            "Prefer one execute_python call for multi-step local work, data shaping, and integration facade calls. It runs an IPython session with native top-level await; execute_python calls are not concurrent (but you can write concurrent code via it!), so orchestrate multiple facade calls inside one submitted code block.",
            execute_python_example,
            "Keep execute_python output quiet. Store uncertain or large intermediate results in variables, print a small slice or summary first, and only print the full value if that sample is useful.",
            "Use the `bash` tool for commands that may prompt, block, or need stdin. It runs in a PTY, streams output, detaches when idle, and returns a task id for `task.output()`, `task.write(text)`, and `task.cancel()`.",
            "Register background shell work with `await yb.tasks.submit(name, shell, intro, delivery=...)`. `manual` — poll with `task.output()` / `task.status()` yourself (`ttl_s <= 3600`). `conversation` — completion wakes this chat. `actor` — completion goes to the actor mailbox.",
            "Task output is an expiring offload buffer. For long jobs, write resumable workspace scripts that persist their own state and artifacts.",
            "MCP data sources: use `yb.mcps` (see Integration SDKs). Durable schedules: use `yb.tasks.cron`.",
            "Research routing is cost-sensitive and ordered: (1) when confident, answer directly without search or fixer; (2) for ordinary current facts, first use `yext.web.search`, then `yext.web.read` only when full text is needed; (3) for uncertain but stable knowledge such as mathematics, scientific principles, classic anime, or literature, use `await yb.fixer.ask_gemini(prompt)` with its default `enable_web_search=False`; (4) for X/Twitter questions, or after ordinary search has no useful result or a page blocks `read`, use `await yb.fixer.ask_grok(prompt, enable_web_search=True)`—X/Twitter may go directly to Grok, and blocked-page extraction prompts must include the URL and the information sought; (5) for heavyweight multi-source synthesis, deep research, or complex fact-checking, directly use `ask_gemini(prompt, enable_web_search=True)` without spending the one call on a no-search preflight. Treat fixer output as evidence to assess, not guaranteed success, and mark remaining uncertainty.",
            "Each enabled Gemini and Grok fixer allows only one provider-completed request per user turn, so include all related subquestions in one prompt. Ordinary web search is cheaper: when `yext.web` appears in Integration SDKs, `yext.web.search` provides up to three successful searches per user turn; `read` and `download` remain available for source inspection and files.",
            "`pass_through_options` on fixer calls is a vendor-specific escape hatch whose fields the framework does not interpret. Before sending a non-empty object, inspect the current system prompt: the Persona must provide the field and value rules, or the injected AGENTS.md must record the configuration. If neither source does, its instructions are incomplete, or the sources conflict, ask the user; never guess a handle, date, plugin ID, or other vendor value. Example vendor values show structure only and must never be reused. `enable_web_search` is a separately supported framework parameter and requires no user confirmation.",
            ADMIN_PAGES,
            "After `uv add` or `uv remove`, call the `restart_kernel` tool before expecting new imports in execute_python.",
        ]
    )


def _integration_docs(integrations: list[Integration], extra_packages: tuple[str, ...]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for integration in integrations:
        prompt_doc = getattr(integration, "prompt_doc", None)
        if callable(prompt_doc):
            doc = prompt_doc()
        else:
            doc = inspect.getdoc(import_module(integration.package_path)) or ""
        if doc:
            parts.append(f"{integration.package_path}:\n{doc}")
        seen.add(integration.package_path)
    for package_path in extra_packages:
        if package_path in seen:
            continue
        doc = inspect.getdoc(import_module(package_path)) or ""
        if doc:
            parts.append(f"{package_path}:\n{doc}")
    return "\n\n".join(parts)


def _skills(workspace: Path, global_skills: list[SkillSummary]) -> str:
    skills_dir = workspace / ".agents" / "skills"
    entries: list[str] = []
    for skill in global_skills:
        description = skill.description or "No description provided."
        entries.append(f"- {skill.name} ({skill.id}): {description}. Inspect full instructions via {skill.inspect_hint}.")
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        text = skill_path.read_text(encoding="utf-8")
        entries.append(f"- {skill_path.parent.name}: {_skill_description(text)}. Inspect full instructions with the read tool at `{skill_path}`.")
    if not entries:
        return "No workspace skills are currently installed."
    return "The following skills are summaries only; inspect full instructions on demand:\n" + "\n".join(entries)


def _skill_description(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            key, separator, value = line.partition(":")
            if separator and key.strip() == "description" and value.strip():
                return value.strip().strip('"').strip("'")
    inside_frontmatter = bool(lines and lines[0].strip() == "---")
    for line in lines[1:] if inside_frontmatter else lines:
        stripped = line.strip()
        if inside_frontmatter:
            if stripped == "---":
                inside_frontmatter = False
            continue
        if stripped and not stripped.startswith("#"):
            return stripped
    return "No description provided."


def _agents_context(workspace: Path) -> str:
    agents = workspace / "AGENTS.md"
    if not agents.exists():
        return "No AGENTS.md is present."
    return agents.read_text(encoding="utf-8")


def _real_time_data() -> str:
    tz = datetime.now().astimezone().tzname() or "local"
    return "\n".join(
        [
            "platform: local",
            f"timezone: {tz}",
            "",
            "## Session modes",
            "Conversation (User): The user message is from a real person in this chat. Reply in this conversation; the user sees your responses here.",
            "Actor: The user message may come from any source (webhook, schedule, inbound API, etc.). For each outbound action, decide whether it belongs in this Conversation (visible in this thread) or to Actor (your future self: workspace notes, cron actor_message, inbound without binding to this conversation, KV, and similar durable channels).",
            "",
            "Per-turn `mode` and `now` are appended to each incoming user message; do not expect them in this section.",
        ]
    )


def real_time_turn_context(mode: SessionMode) -> str:
    now = datetime.now().astimezone()
    return "\n".join(
        [
            REAL_TIME_CONTEXT_MARKER,
            f"mode: {mode}",
            f"now: {now.isoformat()}",
        ]
    )


def augment_user_message(message: InputMessage, mode: SessionMode) -> InputMessage:
    prefix = real_time_turn_context(mode) + _REAL_TIME_CONTEXT_SEPARATOR
    content: list[ContentItem] = []
    for item in message.content:
        if item.kind == "text" and item.text and not content:
            content.append(ContentItem("text", prefix + item.text, meta=item.meta))
            continue
        content.append(item)
    if not content:
        content.append(ContentItem("text", prefix.rstrip()))
    return InputMessage(message.role, message.name, content)


def user_visible_text(message: InputMessage) -> str:
    parts: list[str] = []
    for item in message.content:
        if item.kind != "text" or not item.text:
            continue
        text = item.text
        if text.startswith(REAL_TIME_CONTEXT_MARKER):
            _, _, remainder = text.partition(_REAL_TIME_CONTEXT_SEPARATOR)
            text = remainder
        parts.append(text)
    return "\n\n".join(parts)
