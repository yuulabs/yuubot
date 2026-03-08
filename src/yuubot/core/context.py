"""ctx_id ↔ (type, target_id) bidirectional mapping."""

import attrs

from yuubot.core.models import Context, CtxInfo


@attrs.define
class ContextManager:
    """Manages ctx_id mapping. Hot-loaded from DB on startup."""

    _by_id: dict[int, CtxInfo] = attrs.field(factory=dict)
    _by_target: dict[tuple[str, int], int] = attrs.field(factory=dict)

    async def load(self) -> None:
        """Hot-load all ctx mappings from DB."""
        for row in await Context.all():
            info = CtxInfo(ctx_id=row.id, type=row.type, target_id=row.target_id)
            self._by_id[info.ctx_id] = info
            self._by_target[(info.type, info.target_id)] = info.ctx_id

    async def get_or_create(self, ctx_type: str, target_id: int) -> int:
        """Return existing ctx_id or create a new one."""
        key = (ctx_type, target_id)
        if key in self._by_target:
            return self._by_target[key]
        obj, _ = await Context.get_or_create(
            type=ctx_type, target_id=target_id,
        )
        info = CtxInfo(ctx_id=obj.id, type=ctx_type, target_id=target_id)
        self._by_id[obj.id] = info
        self._by_target[key] = obj.id
        return obj.id

    def resolve(self, ctx_id: int) -> CtxInfo | None:
        """Resolve ctx_id to CtxInfo."""
        return self._by_id.get(ctx_id)

    def all(self) -> list[CtxInfo]:
        return list(self._by_id.values())
