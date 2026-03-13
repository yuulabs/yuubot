"""Vision capability — describe images using vision-capable LLM."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import yuutools as yt
from loguru import logger
from yuuagents import Agent, AgentContext, start_agent
from yuuagents.agent import AgentConfig, SimplePromptBuilder

from yuubot.capabilities import ContentBlock, capability, text_block, uri_to_path

_SYSTEM_PROMPT = (
    "你是一个图片描述助手。请用中文简洁地描述图片内容。\n"
    "要求：\n"
    "- 纯文本，不要使用markdown格式、编号、标题\n"
    "- 描述画面内容、情绪氛围、适用场景、是否适合用作表情包\n"
    "- 简洁但信息完整，让人能通过描述搜索到这张图\n"
    "- 直接描述，不要以'这张图片'开头"
)

_MODEL = "google/gemini-3.1-flash-lite-preview"


def _make_vision_llm():
    import yuullm
    from yuullm.providers import OpenAIChatCompletionProvider

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set in environment")

    provider = OpenAIChatCompletionProvider(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    return yuullm.YLLMClient(
        provider=provider,
        default_model=_MODEL,
        price_calculator=yuullm.PriceCalculator(),
    )


async def _describe_image(image_path: str) -> str:
    from uuid import uuid4

    p = Path(image_path)
    if not p.is_file():
        raise FileNotFoundError(f"Image file not found: {image_path}")

    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime = mime_map.get(p.suffix.lower(), "image/jpeg")
    data = base64.b64encode(p.read_bytes()).decode()
    data_uri = f"data:{mime};base64,{data}"

    client = _make_vision_llm()
    task_id = str(uuid4())
    builder = SimplePromptBuilder()
    builder.add_section(_SYSTEM_PROMPT)

    agent = Agent(
        config=AgentConfig(
            task_id=task_id,
            agent_id=f"vision-{task_id[:8]}",
            persona=_SYSTEM_PROMPT,
            tools=yt.ToolManager(),
            llm=client,
            prompt_builder=builder,
            max_steps=1,
        )
    )
    ctx = AgentContext(
        task_id=task_id,
        agent_id=agent.agent_id,
        workdir="",
        docker_container="",
    )
    await start_agent(
        agent,
        "请描述这张图片：",
        ctx,
        extra_items=[{"type": "image_url", "image_url": {"url": data_uri}}],
    )

    for msg in reversed(agent.history):
        if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "assistant":
            text = "".join(item for item in msg[1] if isinstance(item, str)).strip()
            if text:
                logger.info("Vision described {}: {}...", image_path, text[:100])
                return text
    return ""


@capability("vision")
class VisionCapability:

    async def describe(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        if not _positional:
            return [text_block("错误: 请提供图片路径")]

        image_path = uri_to_path(_positional[0])
        try:
            description = await _describe_image(image_path)
            return [text_block(description)]
        except FileNotFoundError as e:
            return [text_block(f"错误: {e}")]
        except ValueError as e:
            return [text_block(f"错误: {e}")]
        except Exception as e:
            logger.exception(f"Vision describe failed for {image_path}")
            return [text_block(f"错误: 图片描述失败 - {e}")]
