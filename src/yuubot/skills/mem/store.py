"""Memory storage — CRUD operations on memories table."""

import re
from datetime import datetime, timezone

from tortoise import connections
from tortoise.expressions import Q
from tortoise.functions import Count

from yuubot.core.db import has_simple
from yuubot.core.models import Memory, MemoryTag

# Extract individual terms from jieba_query output.
# jieba_query returns things like: "张三" AND "周五" AND ( p+n* OR pn* )
# We want to extract: ["张三", "周五", "pn"]
_JIEBA_TERM_RE = re.compile(r'"([^"]+)"')
_JIEBA_ASCII_RE = re.compile(r'\b([a-zA-Z0-9_]{2,})\*')


async def _build_fts_query(words: list[str]) -> str:
    """Build an FTS5 MATCH expression from a list of words.

    With simple tokenizer: calls simple_query() per word, joins with OR.
    Without: joins words with OR directly (default FTS5 syntax).
    """
    if not has_simple():
        return " OR ".join(words)
    conn = connections.get("default")
    parts: list[str] = []
    for w in words:
        rows = await conn.execute_query_dict("SELECT simple_query(?) AS q", [w])
        sq = rows[0]["q"] if rows else w
        parts.append(f"({sq})")
    return " OR ".join(parts)


async def _jieba_tokenize(text: str) -> list[str]:
    """Use jieba_query() to segment text into meaningful tokens.

    Returns deduplicated list of Chinese words and ASCII terms (2+ chars).
    Filters out single-char Chinese stopwords (是, 的, 了, etc.).
    """
    if not has_simple():
        # Fallback: split ASCII words and Chinese bigrams
        ascii_words = re.findall(r'[a-zA-Z0-9_]{2,}', text)
        cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
        bigrams = [cn_chars[i] + cn_chars[i + 1] for i in range(len(cn_chars) - 1)]
        return list(dict.fromkeys(ascii_words + bigrams))

    conn = connections.get("default")
    rows = await conn.execute_query_dict("SELECT jieba_query(?) AS q", [text])
    if not rows:
        return []

    jq = rows[0]["q"]
    # Extract quoted Chinese terms and ASCII terms from jieba_query output
    cn_terms = _JIEBA_TERM_RE.findall(jq)
    ascii_terms = _JIEBA_ASCII_RE.findall(jq)

    # Filter: keep Chinese terms with 2+ chars, all ASCII terms
    tokens = [t for t in cn_terms if len(t) >= 2]
    tokens.extend(ascii_terms)
    return list(dict.fromkeys(tokens))


async def save(
    content: str,
    tags: list[str],
    ctx_id: int | None,
    max_length: int = 500,
    source_user_id: int | None = None,
    scope: str = "private",
) -> int:
    """Save a memory. Returns memory id."""
    if len(content) > max_length:
        raise ValueError(f"记忆内容过长 ({len(content)} > {max_length})")

    mem = await Memory.create(
        content=content, ctx_id=ctx_id, source_user_id=source_user_id,
        scope=scope,
    )

    for tag in tags:
        tag = tag.strip()
        if tag:
            await MemoryTag.get_or_create(memory=mem, tag=tag)

    return mem.id


async def recall(
    words: list[str],
    tags: list[str],
    ctx_id: int | None,
    limit: int,
) -> list[dict]:
    """Recall memories matching words and/or tags.

    Uses FTS5 with simple tokenizer (Chinese-aware) when available,
    falls back to default FTS5 tokenizer otherwise.
    """
    q = Q()

    if words:
        fts_expr = await _build_fts_query(words)
        conn = connections.get("default")
        rows = await conn.execute_query_dict(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?",
            [fts_expr],
        )
        fts_ids = [r["rowid"] for r in rows]

        if fts_ids:
            q &= Q(id__in=fts_ids)
        elif not tags:
            return []

    if tags:
        tag_mem_ids = await MemoryTag.filter(tag__in=tags).values_list("memory_id", flat=True)
        if not tag_mem_ids:
            if not words:
                return []
        else:
            q &= Q(id__in=tag_mem_ids)

    if ctx_id is not None:
        q &= (Q(ctx_id=ctx_id, scope="private") | Q(scope="public"))
    else:
        # No ctx — only public memories visible
        q &= Q(scope="public")

    if not words and not tags:
        return []

    memories = await Memory.filter(q).order_by("-last_accessed").limit(limit)

    results = []
    for m in memories:
        mem_tags = await MemoryTag.filter(memory_id=m.id).values_list("tag", flat=True)
        results.append({
            "id": m.id,
            "content": m.content,
            "ctx_id": m.ctx_id,
            "created_at": m.created_at.isoformat() if m.created_at else "",
            "last_accessed": m.last_accessed.isoformat() if m.last_accessed else "",
            "tags": ", ".join(mem_tags) if mem_tags else "",
        })

    # Update last_accessed for matched memories
    if results:
        now = datetime.now(timezone.utc)
        ids = [r["id"] for r in results]
        await Memory.filter(id__in=ids).update(last_accessed=now)

    return results


async def probe(tokens: list[str], ctx_id: int | None = None) -> list[str]:
    """Probe which tokens have matching memories via FTS5.

    Returns the subset of *tokens* that hit at least one memory row
    visible to the given ctx_id (private in that ctx + all public).
    """
    if not tokens:
        return []
    conn = connections.get("default")
    hits: list[str] = []
    for token in tokens:
        fts_expr = await _build_fts_query([token])
        if ctx_id is not None:
            rows = await conn.execute_query_dict(
                "SELECT f.rowid FROM memories_fts f "
                "JOIN memories m ON f.rowid = m.id "
                "WHERE f.memories_fts MATCH ? "
                "AND ((m.ctx_id = ? AND m.scope = 'private') OR m.scope = 'public') "
                "LIMIT 1",
                [fts_expr, ctx_id],
            )
        else:
            rows = await conn.execute_query_dict(
                "SELECT f.rowid FROM memories_fts f "
                "JOIN memories m ON f.rowid = m.id "
                "WHERE f.memories_fts MATCH ? AND m.scope = 'public' "
                "LIMIT 1",
                [fts_expr],
            )
        if rows:
            hits.append(token)
    return hits


async def probe_text(text: str, ctx_id: int | None = None) -> list[str]:
    """Probe raw text for memory hits using jieba segmentation.

    Tokenizes the text with jieba, then probes each token individually.
    Returns tokens that have matching memories visible to ctx_id.
    """
    tokens = await _jieba_tokenize(text)
    if not tokens:
        return []
    return await probe(tokens, ctx_id=ctx_id)


async def delete(ids: list[int]) -> int:
    """Delete memories by IDs. Returns count deleted."""
    if not ids:
        return 0
    count = await Memory.filter(id__in=ids).count()
    if count:
        await Memory.filter(id__in=ids).delete()
    return count


async def show_tags(ctx_id: int | None) -> list[tuple[str, int]]:
    """Return (tag, count) pairs for memories visible to ctx_id."""
    if ctx_id is not None:
        qs = MemoryTag.filter(
            Q(memory__ctx_id=ctx_id, memory__scope="private") | Q(memory__scope="public")
        )
    else:
        qs = MemoryTag.filter(memory__scope="public")

    rows = await qs.annotate(cnt=Count("id")).group_by("tag").values("tag", "cnt")
    return [(r["tag"], r["cnt"]) for r in sorted(rows, key=lambda x: -x["cnt"])]
