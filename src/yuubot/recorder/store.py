"""Message storage — parse OneBot events and write to DB."""

from datetime import datetime, timezone

import httpx
from loguru import logger

from yuubot.core.context import ContextManager
from yuubot.core.models import (
    AtSegment,
    ForwardSegment,
    ImageSegment,
    Message,
    MessageEvent,
    MessageRecord,
    segments_to_plain,
    segments_to_json,
)
from yuubot.core.onebot import parse_segments
from yuubot.recorder.downloader import MediaDownloader
from yuubot.recorder.forward import ForwardResolver


class Store:
    def __init__(
        self,
        ctx_mgr: ContextManager,
        downloader: MediaDownloader,
        forward_resolver: ForwardResolver | None = None,
        bot_qq: int = 0,
        napcat_http: str = "",
    ) -> None:
        self.ctx_mgr = ctx_mgr
        self.downloader = downloader
        self.forward_resolver = forward_resolver
        self._bot_qq_str = str(bot_qq) if bot_qq else ""
        self._napcat_http = napcat_http.rstrip("/") if napcat_http else ""
        self._bot_name: str = ""

    async def save(self, event: MessageEvent) -> tuple[int, list[str], list[dict]]:
        """Parse and persist a message event.

        Returns (ctx_id, media_local_paths, forward_logs) — one local path per image
        segment, in order, so the caller can enrich the raw event.
        """
        if not self._bot_name and self._napcat_http:
            await self._try_fetch_bot_name()
        ctx_id = await self.ctx_mgr.get_or_create(event.ctx_type, event.target_id)
        segments = parse_segments(event.message)
        self._enrich_bot_at(segments)

        # Download media files
        media_files: list[str] = []
        for seg in segments:
            if isinstance(seg, ImageSegment) and seg.url:
                local_path = await self.downloader.download(seg.url)
                if local_path:
                    media_files.append(local_path)
                    seg.local_path = local_path

        plain = segments_to_plain(segments)
        raw_json = segments_to_json(segments)
        ts = datetime.fromtimestamp(event.time, tz=timezone.utc)
        forward_logs: list[dict] = []

        if self.forward_resolver is not None:
            for seg in segments:
                if not isinstance(seg, ForwardSegment):
                    continue
                resolved = await self.forward_resolver.resolve(
                    seg.id,
                    source_message_id=event.message_id,
                    source_ctx_id=ctx_id,
                )
                if resolved is None:
                    continue
                seg.summary = resolved["summary"]
                forward_logs.append({"forward_id": seg.id, "nodes": resolved["log_nodes"]})
            plain = segments_to_plain(segments)
            raw_json = segments_to_json(segments)

        await MessageRecord.create(
            message_id=event.message_id,
            ctx_id=ctx_id,
            user_id=event.user_id,
            nickname=event.nickname,
            display_name=event.display_name,
            content=plain,
            raw_message=raw_json,
            timestamp=ts,
            media_files=media_files,
        )
        return ctx_id, media_files, forward_logs

    async def _try_fetch_bot_name(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{self._napcat_http}/get_login_info")
                data = r.json().get("data", r.json())
                if isinstance(data, dict):
                    nickname = data.get("nickname", "")
                    if nickname:
                        self._bot_name = nickname
                        logger.info("Recorder: bot name fetched: {}", nickname)
        except Exception:
            pass

    def _enrich_bot_at(self, segments: Message) -> None:
        if not self._bot_qq_str or not self._bot_name:
            return
        for seg in segments:
            if isinstance(seg, AtSegment) and seg.qq == self._bot_qq_str:
                if not seg.name or seg.name == self._bot_qq_str:
                    seg.name = self._bot_name
