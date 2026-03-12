"""BotInfo — cached bot identity and group name resolution.

Extracted from AgentRunner._get_bot_name() and _resolve_group_name().
"""

from __future__ import annotations

import attrs
from loguru import logger

from yuubot.config import Config


@attrs.define
class BotInfo:
    """Cached bot identity and group name lookups."""

    config: Config
    _bot_name: str | None = None
    _group_names: dict[int, str] = attrs.field(factory=dict)

    async def bot_name(self) -> str:
        """Get bot's display name, with caching.

        Falls back to bot QQ number if nickname fetch fails.
        """
        if self._bot_name is not None:
            return self._bot_name
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.config.daemon.recorder_api}/get_login_info",
                )
                data = r.json().get("data", r.json())
                if isinstance(data, dict):
                    nickname = data.get("nickname", "")
                    if nickname:
                        self._bot_name = nickname
                        logger.info("Bot name fetched: {}", nickname)
                        return self._bot_name
        except Exception:
            logger.opt(exception=True).warning("Failed to fetch bot nickname from API")

        self._bot_name = str(self.config.bot.qq)
        logger.info("Using bot QQ as name: {}", self._bot_name)
        return self._bot_name

    async def group_name(self, group_id: int) -> str:
        """Resolve group_id to group_name, with caching."""
        if group_id in self._group_names:
            return self._group_names[group_id]
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.config.daemon.recorder_api}/get_group_list",
                )
                data = r.json().get("data", r.json())
                if isinstance(data, list):
                    for g in data:
                        self._group_names[g.get("group_id", 0)] = g.get(
                            "group_name", ""
                        )
        except Exception:
            logger.warning("Failed to fetch group list for name resolution")
        return self._group_names.get(group_id, "")
