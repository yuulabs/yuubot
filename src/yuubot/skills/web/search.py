"""Web search via Tavily API."""

import json
import os
from pathlib import Path

import click
import httpx

from yuubot.config import load_config

_SEARCH_RATE_DIR = Path("/tmp/yuubot_rate")
_SEARCH_LIMIT = 3


def _check_search_quota() -> tuple[bool, int]:
    """Check and consume one search quota for the current task.

    Returns (allowed, remaining).
    If no task_id is set (human usage), always allows with remaining=-1.
    """
    task_id = os.environ.get("YUU_TASK_ID", "")
    if not task_id:
        return True, -1

    _SEARCH_RATE_DIR.mkdir(parents=True, exist_ok=True)
    counter_file = _SEARCH_RATE_DIR / f"web_search_{task_id}"

    try:
        count = int(counter_file.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0

    if count >= _SEARCH_LIMIT:
        return False, 0

    count += 1
    counter_file.write_text(str(count))
    return True, _SEARCH_LIMIT - count


async def tavily_search(query: str, limit: int, config_path: str | None) -> None:
    allowed, remaining = _check_search_quota()
    if not allowed:
        click.echo(f"错误: 本次任务搜索次数已达上限 ({_SEARCH_LIMIT}/{_SEARCH_LIMIT})")
        return

    cfg = load_config(config_path)
    api_key = cfg.api_keys.get("tavily", "")
    if not api_key:
        click.echo("错误: 未配置 TAVILY_API_KEY")
        return

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": limit,
        "include_answer": True,
    }
    data = None
    for no_proxy in (False, True):
        kwargs = {"timeout": 30}
        if no_proxy:
            kwargs["trust_env"] = False
        try:
            async with httpx.AsyncClient(**kwargs) as client:
                r = await client.post("https://api.tavily.com/search", json=payload)
                r.raise_for_status()
                data = r.json()
                break
        except httpx.ConnectError:
            if no_proxy:
                raise
    assert data is not None

    results = data.get("results", [])
    if not results:
        click.echo("未找到结果")
        return

    for i, item in enumerate(results, 1):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get("content", "")[:200]
        click.echo(f"{i}. [{title}] {url}")
        click.echo(f"   {snippet}")
        click.echo()

    if remaining >= 0:
        click.echo(f"(剩余搜索额度: {remaining}/{_SEARCH_LIMIT})")
