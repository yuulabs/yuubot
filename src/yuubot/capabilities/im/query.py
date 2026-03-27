"""Message query logic — FTS search on SQLite."""

from datetime import datetime, timedelta, timezone

from tortoise import connections


async def search_messages(
    keywords: str,
    ctx_id: int | None,
    limit: int,
    days: int,
) -> list[dict]:
    """Search messages using FTS5. keywords is space-separated."""
    words = keywords.strip().split()
    if not words:
        return []

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    fts_query = " OR ".join(words)

    conn = connections.get("default")

    if ctx_id is not None:
        sql = """
            SELECT m.message_id, m.timestamp, m.user_id, m.nickname, m.display_name, m.ctx_id, m.content, m.raw_message, m.media_files
            FROM messages m
            JOIN messages_fts f ON f.rowid = m.id
            WHERE messages_fts MATCH ? AND m.ctx_id = ? AND m.timestamp >= ?
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        params = [fts_query, ctx_id, since, limit]
    else:
        sql = """
            SELECT m.message_id, m.timestamp, m.user_id, m.nickname, m.display_name, m.ctx_id, m.content, m.raw_message, m.media_files
            FROM messages m
            JOIN messages_fts f ON f.rowid = m.id
            WHERE messages_fts MATCH ? AND m.timestamp >= ?
            ORDER BY m.timestamp DESC
            LIMIT ?
        """
        params = [fts_query, since, limit]

    _, rows = await conn.execute_query(sql, params)

    return [
        {
            "message_id": row[0],
            "timestamp": row[1],
            "user_id": row[2],
            "nickname": row[3],
            "display_name": row[4],
            "ctx_id": row[5],
            "content": row[6],
            "raw_message": row[7],
            "media_files": row[8] if row[8] else [],
        }
        for row in rows
    ]


async def browse_messages(
    msg_id: int | None = None,
    ctx_id: int | None = None,
    before: int = 0,
    after: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    qq_ids: list[int] | None = None,
    name_pattern: str | None = None,
) -> list[dict]:
    """Browse messages around a specific message or within a time range.

    Args:
        msg_id: Center message ID (optional)
        ctx_id: Filter by context ID (optional)
        before: Number of messages before the center message
        after: Number of messages after the center message
        since: Start timestamp (optional)
        until: End timestamp (optional)
        limit: Maximum number of messages to return
    """
    conn = connections.get("default")

    # If msg_id is provided, fetch messages around it
    # msg_id is always OneBot message_id (the ID visible to LLM)
    if msg_id is not None:
        # Get the center message timestamp
        sql_center = "SELECT timestamp, ctx_id FROM messages WHERE message_id = ?"
        _, center_rows = await conn.execute_query(sql_center, [msg_id])
        if not center_rows:
            return []

        center_time = center_rows[0][0]
        msg_ctx_id = center_rows[0][1]

        # Use msg's context if ctx_id not specified
        if ctx_id is None:
            ctx_id = msg_ctx_id

        # Fetch messages before
        sql_before = """
            SELECT message_id, timestamp, user_id, nickname, display_name, ctx_id, content, raw_message, media_files
            FROM messages
            WHERE ctx_id = ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        _, before_rows = await conn.execute_query(sql_before, [ctx_id, center_time, before + 1])

        # Fetch messages after
        sql_after = """
            SELECT message_id, timestamp, user_id, nickname, display_name, ctx_id, content, raw_message, media_files
            FROM messages
            WHERE ctx_id = ? AND timestamp > ?
            ORDER BY timestamp ASC
            LIMIT ?
        """
        _, after_rows = await conn.execute_query(sql_after, [ctx_id, center_time, after])

        # Combine and sort
        all_rows = list(before_rows) + list(after_rows)
        all_rows.sort(key=lambda r: r[1])  # Sort by timestamp

    else:
        # Time range query
        conditions = []
        params = []

        if ctx_id is not None:
            conditions.append("ctx_id = ?")
            params.append(ctx_id)

        if since is not None:
            conditions.append("timestamp >= ?")
            params.append(since.isoformat())

        if until is not None:
            conditions.append("timestamp <= ?")
            params.append(until.isoformat())

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        # When no time range is specified, fetch the newest N messages
        # then reverse to display oldest-first (top-to-bottom).
        has_time_range = since is not None or until is not None
        order = "ASC" if has_time_range else "DESC"

        sql = f"""
            SELECT message_id, timestamp, user_id, nickname, display_name, ctx_id, content, raw_message, media_files
            FROM messages
            WHERE {where_clause}
            ORDER BY timestamp {order}
            LIMIT ?
        """
        params.append(limit)

        _, all_rows = await conn.execute_query(sql, params)

        if not has_time_range:
            all_rows = list(reversed(all_rows))

    results = [
        {
            "message_id": row[0],
            "timestamp": row[1],
            "user_id": row[2],
            "nickname": row[3],
            "display_name": row[4],
            "ctx_id": row[5],
            "content": row[6],
            "raw_message": row[7],
            "media_files": row[8] if row[8] else [],
        }
        for row in all_rows
    ]

    # Post-query filtering by QQ IDs or name (OR logic)
    if qq_ids or name_pattern:
        qq_set = set(qq_ids) if qq_ids else set()
        pattern = name_pattern.lower() if name_pattern else ""
        filtered = []
        for msg in results:
            if qq_set and msg["user_id"] in qq_set:
                filtered.append(msg)
                continue
            if pattern:
                names = f"{msg['nickname'] or ''} {msg['display_name'] or ''}".lower()
                if pattern in names:
                    filtered.append(msg)
        results = filtered

    return results
