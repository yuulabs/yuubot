"""Image library storage — CRUD + FTS5 search."""

from tortoise import connections

from yuubot.core.db import has_simple
from yuubot.core.models import ImageEntry


async def _build_fts_query(words: list[str]) -> str:
    """Build FTS5 MATCH expression, reusing mem skill's pattern."""
    if not has_simple():
        return " OR ".join(words)
    conn = connections.get("default")
    parts: list[str] = []
    for w in words:
        rows = await conn.execute_query_dict("SELECT simple_query(?) AS q", [w])
        sq = rows[0]["q"] if rows else w
        parts.append(f"({sq})")
    return " OR ".join(parts)


async def save(
    local_path: str,
    description: str = "",
    tags: list[str] | None = None,
    source_msg_id: int | None = None,
) -> int:
    """Save an image entry. Returns image id."""
    entry, created = await ImageEntry.get_or_create(
        local_path=local_path,
        defaults={
            "description": description,
            "tags": tags or [],
            "source_msg_id": source_msg_id,
        },
    )
    if not created:
        # Update existing entry
        entry.description = description
        entry.tags = tags or []
        if source_msg_id is not None:
            entry.source_msg_id = source_msg_id
        await entry.save()
    return entry.id


async def search(
    query: str = "",
    tags: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search images by description (FTS5) and/or tags."""
    results: list[ImageEntry] = []

    if query:
        fts_expr = await _build_fts_query(query.split())
        conn = connections.get("default")
        rows = await conn.execute_query_dict(
            "SELECT rowid FROM images_fts WHERE images_fts MATCH ?",
            [fts_expr],
        )
        fts_ids = [r["rowid"] for r in rows]
        if fts_ids:
            results = await ImageEntry.filter(id__in=fts_ids).limit(limit)
        elif not tags:
            return []

    if tags:
        # Filter by tags using JSON contains (SQLite JSON)
        qs = ImageEntry.all()
        if results:
            qs = qs.filter(id__in=[r.id for r in results])
        # tags is a JSON array; filter entries that contain any of the given tags
        all_entries = await qs.limit(limit * 3)
        tag_set = set(tags)
        tag_filtered = [e for e in all_entries if tag_set & set(e.tags)]
        results = tag_filtered[:limit]
    elif not query:
        # No query and no tags — return recent
        results = await ImageEntry.all().order_by("-created_at").limit(limit)

    return [
        {
            "id": e.id,
            "local_path": e.local_path,
            "description": e.description,
            "tags": e.tags,
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }
        for e in results
    ]


async def delete(image_id: int) -> bool:
    """Delete an image entry by ID."""
    count = await ImageEntry.filter(id=image_id).delete()
    return count > 0


async def list_tags() -> list[tuple[str, int]]:
    """Return all tags with counts."""
    entries = await ImageEntry.all()
    tag_counts: dict[str, int] = {}
    for e in entries:
        for tag in (e.tags or []):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(tag_counts.items(), key=lambda x: -x[1])
