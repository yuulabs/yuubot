"""Entry mapping — manage custom command entry prefixes."""

from yuubot.core.models import EntryMapping


class EntryManager:
    async def get_route(self, entry: str, scope: str = "global") -> str | None:
        for s in (scope, "global"):
            row = await EntryMapping.filter(entry=entry, scope=s).first()
            if row:
                return row.route
        return None

    async def set(self, entry: str, route: str, scope: str = "global") -> None:
        await EntryMapping.update_or_create(
            defaults={"route": route},
            entry=entry,
            scope=scope,
        )

    async def remove(self, entry: str, scope: str = "global") -> None:
        await EntryMapping.filter(entry=entry, scope=scope).delete()
