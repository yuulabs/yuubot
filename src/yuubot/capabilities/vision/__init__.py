"""Vision capability — describe images using vision-capable LLM."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import yuutools as yt
from loguru import logger
from yuuagents import ConversationInput
from yuuagents.agent import AgentConfig
from yuuagents.context import AgentContext
from yuuagents.core.flow import Agent as FlowAgent

from yuubot.capabilities import ContentBlock, capability, get_context, text_block, uri_to_path
from yuubot.core.media_paths import MediaPathContext, MediaPathError, input_to_host

_SYSTEM_PROMPT = (
    "你是一个图片描述助手。请用中文描述图片内容。\n"
    "要求：\n"
    "- 纯文本，不要使用markdown格式、编号、标题\n"
    "- 按顺序描述：画面中有什么角色/物体、他们在做什么、表情/动作/姿态、"
    "画面构图与色调、文字内容（如有）、整体情绪氛围\n"
    "- 如果是表情包/梗图，描述其传达的情绪和适用场景\n"
    "- 信息完整，让人能通过描述准确还原画面并搜索到这张图\n"
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


async def _get_cached(host_path: str) -> str | None:
    from yuubot.core.models import VisionCache

    entry = await VisionCache.filter(host_path=host_path).first()
    return entry.description if entry else None


async def _set_cached(host_path: str, description: str) -> None:
    from yuubot.core.models import VisionCache

    await VisionCache.update_or_create(
        host_path=host_path,
        defaults={"description": description},
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
    agent_id = f"vision-{task_id[:8]}"

    import yuullm

    config = AgentConfig(
        agent_id=agent_id,
        system=_SYSTEM_PROMPT,
        tools=yt.ToolManager(),
        llm=client,
        max_steps=1,
    )
    ctx = AgentContext(
        task_id=task_id,
        agent_id=agent_id,
        workdir="",
        docker_container="",
    )
    agent = FlowAgent(config=config, ctx=ctx)
    agent.start(
        ConversationInput(
            messages=[
                yuullm.user(
                    "请描述这张图片：",
                    {"type": "image_url", "image_url": {"url": data_uri}},
                )
            ]
        )
    )
    async for _step in agent.steps():
        pass
    history = agent.messages

    for msg in reversed(history):
        role, items = msg
        if role != "assistant":
            continue
        text = "".join(
            item["text"] for item in items if item.get("type") == "text"
        ).strip()
        if text:
            logger.info("Vision described {}: {}...", image_path, text[:100])
            return text
    logger.warning("Vision describe returned empty for {}", image_path)
    return ""


@capability("vision")
class VisionCapability:

    async def describe(
        self,
        *,
        _positional: list[str] | None = None,
        refresh: bool = False,
        **_kw,
    ) -> list[ContentBlock]:
        if not _positional:
            return [text_block("错误: 请提供图片路径")]

        image_path = uri_to_path(_positional[0])
        try:
            actx = get_context()
            media_ctx = MediaPathContext.from_values(
                docker_host_mount=actx.docker_host_mount,
                host_home_dir=actx.docker_home_host_dir,
                container_home_dir=actx.docker_home_dir,
            )
            image_path = input_to_host(image_path, ctx=media_ctx)

            if not refresh:
                cached = await _get_cached(image_path)
                if cached is not None:
                    logger.debug("Vision cache hit for {}", image_path)
                    return [text_block(cached)]

            description = await _describe_image(image_path)
            if not description:
                return [text_block("错误: 图片描述为空，可能是模型未返回结果")]
            await _set_cached(image_path, description)
            return [text_block(description)]
        except FileNotFoundError as e:
            return [text_block(f"错误: {e}")]
        except MediaPathError as e:
            return [text_block(f"错误: {e}")]
        except ValueError as e:
            return [text_block(f"错误: {e}")]
        except Exception as e:
            logger.exception(f"Vision describe failed for {image_path}")
            return [text_block(f"错误: 图片描述失败 - {e}")]
