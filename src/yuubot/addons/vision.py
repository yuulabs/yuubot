"""Vision addon — describe images using vision-capable LLM."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from uuid import uuid4

from loguru import logger

from yuubot.addons import ContentBlock, addon, text_block, uri_to_path

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
    """Build a YLLMClient for vision tasks."""
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
    """Call vision LLM to describe an image."""
    import yuullm
    import yuutrace as ytrace

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

    messages = [
        yuullm.system(_SYSTEM_PROMPT),
        yuullm.user(
            "请描述这张图片：",
            {"type": "image_url", "image_url": {"url": data_uri}},
        ),
    ]

    async def _call() -> str:
        stream, store = await client.stream(messages)
        text_parts: list[str] = []
        async for item in stream:
            if isinstance(item, yuullm.Response) and isinstance(item.item, str):
                text_parts.append(item.item)

        usage = store.get("usage")
        if usage is not None:
            ytrace.record_llm_usage(
                ytrace.LlmUsageDelta(
                    provider=usage.provider,
                    model=usage.model,
                    request_id=usage.request_id,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_write_tokens=usage.cache_write_tokens,
                    total_tokens=usage.total_tokens,
                )
            )
        cost = store.get("cost")
        if cost is not None:
            ytrace.record_cost(
                category="llm",
                currency="USD",
                amount=cost.total_cost,
                source=cost.source,
                llm_provider=usage.provider if usage else "",
                llm_model=usage.model if usage else "",
                llm_request_id=usage.request_id if usage else None,
            )
        return "".join(text_parts).strip()

    try:
        with ytrace.conversation(id=uuid4(), agent="vision", model=_MODEL) as chat:
            chat.system(_SYSTEM_PROMPT)
            chat.user("请描述这张图片：[image]")
            with chat.llm_gen() as gen:
                result = await _call()
                gen.log([{"type": "text", "text": result}])
                logger.info("Vision described {}: {}...", image_path, result[:100])
                return result
    except ytrace.TracingNotInitializedError:
        result = await _call()
        logger.info("Vision described {}: {}...", image_path, result[:100])
        return result


@addon("vision")
class VisionAddon:
    """Vision addon for describing images."""

    async def describe(
        self,
        *,
        _positional: list[str] | None = None,
        **_kw,
    ) -> list[ContentBlock]:
        """Describe an image using vision LLM."""
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
