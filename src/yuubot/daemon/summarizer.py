"""Context summarizer — compress session history into a handoff note on rollover."""

from __future__ import annotations

from typing import Any

import attrs
import yuutools as yt
from loguru import logger
from yuuagents import AgentContext, Session
from yuuagents.agent import AgentConfig


_SYSTEM_PROMPT = (
    "你是一个对话摘要助手。根据提供的对话历史，生成一份简洁的工作交接摘要，"
    "让新会话能够无缝继续当前任务。\n"
    "摘要结构：\n"
    "1. 任务目标（一句话说清楚用户想要什么）\n"
    "2. 当前进展（已完成了什么，结论是什么）\n"
    "3. 下一步（如果对话中有明确的下一步，列出来）\n"
    "不超过300字，用中文，不要废话。"
)


def _items_text(items: list[Any]) -> str:
    """Extract concatenated text from a list of yuullm Items."""
    return "".join(
        item["text"] for item in items if item.get("type") == "text"
    )


def extract_original_task(history: list[Any]) -> str:
    """Return text of the first user message in history (the original task)."""
    for role, items in history:
        if role == "user":
            return _items_text(items)[:600]
    return ""


def compact_original_task(history: list[Any]) -> str:
    """Return a compact original request suitable for handoff-first rendering."""
    original = extract_original_task(history).strip()
    return original[:1200]


def build_summary_prompt(task: str, summary: str, *, should_continue: bool) -> str:
    """Build the first user message for a post-rollover session."""
    status_line = (
        "请继续未完成的工作。"
        if should_continue
        else "前述任务已完成。若用户有新消息，再基于以上上下文继续处理。"
    )
    return (
        f"<原任务>\n{task.strip()}\n</原任务>\n\n"
        f"<压缩摘要>\n{summary.strip()}\n</压缩摘要>\n\n"
        f"{status_line}"
    )


def render_recent(history: list[Any], n: int = 8) -> str:
    """Render the last N non-system messages as readable narrative."""
    msgs = [(role, items) for role, items in history if role != "system"]
    recent = msgs[-n:]
    parts: list[str] = []

    for role, items in recent:
        if role == "assistant":
            texts: list[str] = []
            tool_names: list[str] = []
            for item in items:
                if item.get("type") == "text" and item["text"].strip():
                    texts.append(item["text"])
                elif item.get("type") == "tool_call":
                    name = item.get("name", "?")
                    args = item.get("arguments", "")
                    tool_names.append(f"{name}({str(args)[:200]})")
            if texts:
                parts.append(f"[助手]: {''.join(texts)[:400]}")
            if tool_names:
                parts.append(f"[调用工具]: {', '.join(tool_names)}")

        elif role == "user":
            joined = _items_text(items)[:400]
            if joined.strip():
                parts.append(f"[用户]: {joined}")

        elif role == "tool":
            snippets: list[str] = []
            for item in items:
                if item.get("type") == "tool_result":
                    content = str(item.get("content", ""))[:200]
                    snippets.append(content)
            if snippets:
                parts.append(f"[工具结果]: {' | '.join(snippets)}")

    return "\n\n".join(parts) if parts else "（无记录）"


def render_for_curator(history: list[Any]) -> str:
    """Extract only QQ-level message exchanges for the curator.

    Pulls out:
    1. User turns: raw QQ message text (stripped of XML wrappers)
    2. im_send calls: the actual message content sent to QQ
    3. Last assistant text (may contain URL summaries)
    """
    parts: list[str] = []
    for msg in history:
        if not (isinstance(msg, tuple) and len(msg) == 2):
            continue
        role, items = msg
        if role == "system":
            continue

        if role == "user":
            joined = _items_text(items).strip()
            if not joined:
                continue
            # Extract content from <msg> XML if present
            import re
            msg_matches = re.findall(r"<content>(.*?)</content>", joined, re.DOTALL)
            if msg_matches:
                for m in msg_matches:
                    parts.append(f"[用户]: {m.strip()[:400]}")
            else:
                # Skip system injections (silence pings, etc.)
                if not joined.startswith("[system]"):
                    parts.append(f"[用户]: {joined[:400]}")

        elif role == "assistant":
            if not isinstance(items, list):
                continue
            # Extract im send calls
            for item in items:
                if item.get("type") == "tool_call":
                    name = item.get("name", "")
                    args = item.get("arguments", "")
                    if "im" in name and "send" in name:
                        parts.append(f"[bot发送]: {str(args)[:400]}")
                    elif name == "execute_skill_cli":
                        try:
                            import json
                            parsed = json.loads(args) if isinstance(args, str) else args
                            cmd = parsed.get("command", "")
                            if isinstance(cmd, str) and "im send" in cmd:
                                parts.append(f"[bot发送]: {cmd[:400]}")
                        except Exception:
                            pass
            # Last assistant text block
            text = _items_text(items).strip()
            if text:
                parts.append(f"[助手思考]: {text[:400]}")

    return "\n\n".join(parts) if parts else "（无记录）"


async def _run_summarizer(llm: Any, user_content: str, agent_id: str) -> str:
    """Run a temporary single-step agent to generate a summary.

    Delegates trace ownership entirely to yuuagents — no manual ytrace calls.
    """
    from uuid import uuid4

    task_id = str(uuid4())
    runtime_id = f"{agent_id}-{task_id[:8]}"

    session = Session(
        config=AgentConfig(
            agent_id=runtime_id,
            system=_SYSTEM_PROMPT,
            tools=yt.ToolManager(),
            llm=llm,
            max_steps=1,
        ),
        context=AgentContext(
            task_id=task_id,
            agent_id=runtime_id,
            workdir="",
            docker_container="",
        ),
    )
    session.start(user_content)
    async for _step in session.step_iter():
        pass

    for msg in reversed(session.history):
        role, items = msg
        if role != "assistant":
            continue
        text = _items_text(items).strip()
        if text:
            return text
    return ""


_FORK_SUMMARY_INSTRUCTION = (
    "你已达到步骤上限，会话即将交接给新的 agent 继续处理。"
    "请直接用文字回复以下内容，不要调用任何工具。\n\n"
    "为接手的 agent 生成工作交接摘要，目标是让它不需要重复你已经做过的事情：\n\n"
    "1. **已读文件**：列出读过的文件路径和从中获取的关键信息\n"
    "2. **已执行命令**：列出运行过的命令和结果\n"
    "3. **已确认事实**：你已经弄清楚的结论\n"
    "4. **未完成任务**：还需要做什么才能回复用户"
)


async def summarize_via_fork(runtime_session: Session) -> str:
    """Fork the agent session to generate a structured handoff summary.

    Reuses the agent's config (system prompt + tools + LLM) so all history
    tokens hit the prompt cache at 0.1x rate.  The synthetic user message
    instructs the model to produce text only; max_steps=1 is a safety net.
    """
    from uuid import uuid4

    task_id = str(uuid4())
    agent_id = f"summarizer-{runtime_session.config.agent_id}"

    config = attrs.evolve(runtime_session.config, agent_id=agent_id, max_steps=1)
    context = runtime_session.context.evolve(
        task_id=task_id,
        agent_id=agent_id,
    )

    fork = Session(config=config, context=context)
    fork.resume(
        task=_FORK_SUMMARY_INSTRUCTION,
        history=list(runtime_session.history),
        conversation_id=runtime_session.conversation_id,
    )
    async for _ in fork.step_iter():
        pass

    # Extract text from the last assistant message
    for msg in reversed(fork.history):
        role, items = msg
        if role != "assistant":
            continue
        text = _items_text(items).strip()
        if text:
            return text
    return ""


async def compress_summary(history_slice: list[Any], llm: Any, steps_span: int = 8) -> str:
    """Summarize a history slice for mid-step compression.

    steps_span controls how many recent messages to render for context.
    """
    narrative = render_recent(history_slice, n=steps_span)
    user_content = (
        f"对话记录：\n{narrative}\n\n"
        "请生成工作交接摘要。"
    )
    try:
        return await _run_summarizer(llm, user_content, "compressor")
    except Exception:
        logger.exception("Compress summary LLM call failed")
        return render_recent(history_slice, n=4)


async def summarize(history: list[Any], llm: Any) -> str:
    """Call the LLM to produce a compact handoff note from session history."""
    original_task = extract_original_task(history)
    recent_narrative = render_recent(history, n=4)
    user_content = (
        f"原始任务：\n{original_task}\n\n"
        f"最近对话（最后4条消息）：\n{recent_narrative}\n\n"
        "请生成工作交接摘要。"
    )
    try:
        return await _run_summarizer(llm, user_content, "summarizer")
    except Exception:
        logger.exception("Summary LLM call failed, using fallback excerpt")
        return f"（原任务摘要：{original_task[:200]}）"
