"""Memory storage — CRUD operations on memories table."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from tortoise import connections
from tortoise.expressions import Q
from tortoise.functions import Count

from yuubot.core.db import has_simple
from yuubot.core.models import Memory, MemoryRecallTerm, MemoryTag

# Extract individual terms from jieba_query output.
# jieba_query returns things like: "张三" AND "周五" AND ( p+n* OR pn* )
# We want to extract: ["张三", "周五", "pn"]
_JIEBA_TERM_RE = re.compile(r'"([^"]+)"')
_JIEBA_ASCII_RE = re.compile(r'\b([a-zA-Z0-9_]{2,})\*')

# High-frequency Chinese 2-char words with low information value.
# These are filtered during probe (not during explicit mem recall).
_COMMON_WORDS = frozenset({
    # 代词/指示词
    "我们", "他们", "她们", "它们", "自己", "大家", "别人",
    "这个", "那个", "这些", "那些", "这里", "那里", "这样", "那样",
    # 副词/助词
    "不要", "不是", "不会", "不能", "不了", "已经", "可能", "应该",
    "可以", "需要", "现在", "然后", "还是", "或者", "但是", "因为",
    "所以", "如果", "虽然", "而且", "就是", "只是", "其实", "当然",
    # 动词（高频泛义）
    "使用", "知道", "觉得", "认为", "看到", "听到", "告诉", "开始",
    "继续", "进行", "表示", "发现", "出来", "出去", "回来", "过来",
    # 名词（高频泛义）
    "内容", "更新", "任务", "时候", "东西", "事情", "地方", "问题",
    "方法", "情况", "结果", "部分", "方面", "关系", "工作", "时间",
    "什么", "怎么", "没有", "的人", "一个", "一下", "一些", "一点",
})


def _is_common_word(token: str) -> bool:
    """Check if a token is a high-frequency word with low probe value."""
    return token in _COMMON_WORDS


def parse_ids(parts: list[str] | None) -> list[int]:
    """Parse memory IDs from positional args, allowing comma/space mixing."""
    if not parts:
        return []
    ids: list[int] = []
    for part in parts:
        for token in part.split(","):
            token = token.strip()
            if token:
                ids.append(int(token))
    return ids


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
        sq = (rows[0]["q"] if rows else w).strip()
        if not sq:
            continue
        parts.append(f"({sq})")
    if not parts:
        return " OR ".join(words)
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
    recall_terms: list[str] | None = None,
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

    if recall_terms:
        for term in recall_terms:
            term = term.strip()
            if term:
                await MemoryRecallTerm.get_or_create(memory=mem, term=term)

    return mem.id


async def recall(
    words: list[str],
    tags: list[str],
    ctx_id: int | None,
    limit: int,
    show_all: bool = False,
) -> list[dict]:
    """Recall memories matching words and/or tags.

    Uses FTS5 with simple tokenizer (Chinese-aware) when available,
    falls back to default FTS5 tokenizer otherwise.
    """
    q = Q()

    words = [w for w in words if w.strip()]
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

    # Always exclude trashed memories
    q &= Q(trashed_at__isnull=True)

    if not show_all:
        if ctx_id is not None:
            q &= (Q(ctx_id=ctx_id, scope="private") | Q(scope="public"))
        else:
            q &= Q(scope="public")

    if not words and not tags:
        return []

    memories = await Memory.filter(q).order_by("-last_accessed").limit(limit)

    results = []
    for m in memories:
        mem_tags: list[str] = list(await MemoryTag.filter(memory_id=m.id).values_list("tag", flat=True))  # type: ignore[arg-type]
        results.append({
            "id": m.id,
            "content": m.content,
            "ctx_id": m.ctx_id,  # type: ignore[attr-defined]
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


def _visibility_q(ctx_id: int | None) -> Q:
    """Build a Q filter for memory visibility (private in ctx + all public)."""
    q = Q(trashed_at__isnull=True)
    if ctx_id is not None:
        q &= (Q(ctx_id=ctx_id, scope="private") | Q(scope="public"))
    else:
        q &= Q(scope="public")
    return q


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
                "AND m.trashed_at IS NULL "
                "AND ((m.ctx_id = ? AND m.scope = 'private') OR m.scope = 'public') "
                "LIMIT 1",
                [fts_expr, ctx_id],
            )
        else:
            rows = await conn.execute_query_dict(
                "SELECT f.rowid FROM memories_fts f "
                "JOIN memories m ON f.rowid = m.id "
                "WHERE f.memories_fts MATCH ? AND m.trashed_at IS NULL AND m.scope = 'public' "
                "LIMIT 1",
                [fts_expr],
            )
        if rows:
            hits.append(token)
    return hits


async def _recall_term_hits(
    text: str, ctx_id: int | None,
) -> list[int]:
    """Find memory IDs whose recall_terms appear as substrings in text.

    Returns deduplicated list of memory IDs visible to ctx_id.
    """
    all_terms = await MemoryRecallTerm.all().values("term", "memory_id")
    if not all_terms:
        return []

    text_lower = text.lower()
    candidate_ids = list({
        t["memory_id"] for t in all_terms
        if t["term"].lower() in text_lower
    })
    if not candidate_ids:
        return []

    # Filter by visibility
    vis = _visibility_q(ctx_id)
    visible = await Memory.filter(vis & Q(id__in=candidate_ids)).values_list("id", flat=True)
    return list(visible)


async def probe_with_snippets(
    text: str,
    ctx_id: int | None = None,
    limit: int = 5,
) -> list[dict]:
    """Probe raw text for memory hits, returning memory snippets.

    Combines two matching strategies:
    1. FTS5 via jieba tokenization (with high-frequency word filtering)
    2. MemoryRecallTerm substring matching (bypasses tokenizer)

    Returns up to `limit` memories as dicts with {id, content, tags}.
    Does NOT update last_accessed (this is a probe, not a recall).
    """
    hit_ids: set[int] = set()

    # Strategy 1: jieba tokens → FTS5 (filtered)
    tokens = await _jieba_tokenize(text)
    filtered_tokens = [t for t in tokens if not _is_common_word(t)]
    if filtered_tokens:
        fts_hits = await probe(filtered_tokens, ctx_id=ctx_id)
        if fts_hits:
            fts_expr = await _build_fts_query(fts_hits)
            conn = connections.get("default")
            vis = _visibility_q(ctx_id)
            rows = await conn.execute_query_dict(
                "SELECT rowid FROM memories_fts WHERE memories_fts MATCH ?",
                [fts_expr],
            )
            fts_ids = {r["rowid"] for r in rows}
            if fts_ids:
                visible = await Memory.filter(
                    vis & Q(id__in=list(fts_ids)),
                ).values_list("id", flat=True)
                hit_ids.update(visible)

    # Strategy 2: recall_term substring matching
    term_ids = await _recall_term_hits(text, ctx_id)
    hit_ids.update(term_ids)

    if not hit_ids:
        return []

    # Fetch memories with tags, ordered by recency
    memories = await (
        Memory.filter(id__in=list(hit_ids))
        .order_by("-last_accessed")
        .limit(limit)
    )

    results = []
    for m in memories:
        mem_tags: list[str] = list(
            await MemoryTag.filter(memory_id=m.id).values_list("tag", flat=True)
        )  # type: ignore[arg-type]
        results.append({
            "id": m.id,
            "content": m.content,
            "tags": ", ".join(mem_tags) if mem_tags else "",
        })

    return results


async def probe_text(text: str, ctx_id: int | None = None) -> list[str]:
    """Probe raw text for memory hits using jieba segmentation.

    Tokenizes the text with jieba, then probes each token individually.
    Returns tokens that have matching memories visible to ctx_id.

    .. deprecated:: Use probe_with_snippets() for richer results.
    """
    tokens = await _jieba_tokenize(text)
    if not tokens:
        return []
    return await probe(tokens, ctx_id=ctx_id)


async def get_group_topic(ctx_id: int) -> str | None:
    """Get the _group_topic memory content for a ctx, if any."""
    tag_mems = await MemoryTag.filter(
        tag="_group_topic",
        memory__ctx_id=ctx_id,
        memory__trashed_at__isnull=True,
        memory__scope="private",
    ).values_list("memory_id", flat=True)
    if not tag_mems:
        return None
    mem = await Memory.filter(id__in=list(tag_mems)).order_by("-last_accessed").first()
    return mem.content if mem else None


async def trash(ids: list[int]) -> int:
    """Soft-delete memories by moving them to trash. Returns count trashed.

    Trashed memories are invisible to recall/probe but can be restored until
    the forget period expires, after which cleanup_stale() hard-deletes them.
    """
    if not ids:
        return 0
    now = datetime.now(timezone.utc)
    count = await Memory.filter(id__in=ids, trashed_at__isnull=True).update(trashed_at=now)
    return count


async def restore(ids: list[int]) -> int:
    """Restore trashed memories back to active. Returns count restored."""
    if not ids:
        return 0
    count = await Memory.filter(id__in=ids, trashed_at__isnull=False).update(trashed_at=None)
    return count


async def list_memories(
    ctx_id: int | None,
    *,
    show_all: bool = False,
    trash: bool = False,
    limit: int = 100,
) -> list[dict]:
    """List memories visible to ctx_id, optionally from trash."""
    q = Q(trashed_at__isnull=not trash)
    if not show_all:
        if ctx_id is not None:
            q &= (Q(ctx_id=ctx_id, scope="private") | Q(scope="public"))
        else:
            q &= Q(scope="public")

    memories = await Memory.filter(q).order_by("-last_accessed", "-id").limit(limit)
    results = []
    for m in memories:
        mem_tags: list[str] = list(
            await MemoryTag.filter(memory_id=m.id).values_list("tag", flat=True)
        )  # type: ignore[arg-type]
        results.append({
            "id": m.id,
            "content": m.content,
            "ctx_id": m.ctx_id,  # type: ignore[attr-defined]
            "scope": m.scope,
            "created_at": m.created_at.isoformat() if m.created_at else "",
            "last_accessed": m.last_accessed.isoformat() if m.last_accessed else "",
            "trashed_at": m.trashed_at.isoformat() if m.trashed_at else "",
            "tags": ", ".join(mem_tags) if mem_tags else "",
        })
    return results


async def hard_delete(ids: list[int]) -> int:
    """Permanently delete memories (used by cleanup_stale only). Returns count deleted."""
    if not ids:
        return 0
    count = await Memory.filter(id__in=ids).count()
    if count:
        await Memory.filter(id__in=ids).delete()
    return count


async def show_tags(ctx_id: int | None, show_all: bool = False) -> list[tuple[str, int]]:
    """Return (tag, count) pairs for memories visible to ctx_id."""
    if show_all:
        qs = MemoryTag.all()
    elif ctx_id is not None:
        qs = MemoryTag.filter(
            Q(memory__ctx_id=ctx_id, memory__scope="private") | Q(memory__scope="public")
        )
    else:
        qs = MemoryTag.filter(memory__scope="public")

    rows = await qs.annotate(cnt=Count("id")).group_by("tag").values("tag", "cnt")
    return [(r["tag"], r["cnt"]) for r in sorted(rows, key=lambda x: -x["cnt"])]
