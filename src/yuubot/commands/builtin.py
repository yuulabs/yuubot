"""Built-in commands: /bot, /help, /llm, /new, /cost, /ping."""

import re
import sqlite3
import time
from pathlib import Path


from yuubot.auth import is_master_user
from yuubot.commands.tree import Command, CommandRequest, RootCommand

from loguru import logger


def build_command_tree(entries: list[str], llm_executor=None) -> RootCommand:
    """Construct the full command tree with built-in commands."""
    # /bot sub-commands
    on_cmd = Command(
        prefix="on",
        executor=_exec_on,
        help_text="在当前群开启 bot",
    )
    off_cmd = Command(
        prefix="off",
        executor=_exec_off,
        help_text="关闭当前会话或在当前群关闭 bot",
    )
    set_cmd = Command(
        prefix="set",
        executor=_exec_set,
        help_text="设置入口映射: /bot set <entry> <route>",
    )
    allow_dm_cmd = Command(
        prefix="allow-dm",
        executor=_exec_allow_dm,
        scope="master",
        help_text="允许用户私聊: /bot allow-dm @user",
    )
    bot_cmd = Command(
        prefix="bot",
        subs=[on_cmd, off_cmd, set_cmd, allow_dm_cmd],
        help_text="Bot 管理命令",
    )

    help_cmd = Command(
        prefix="help",
        executor=_exec_help,
        help_text="显示帮助",
    )
    price_set_cmd = Command(
        prefix="set",
        executor=_exec_price_set,
        scope="master",
        help_text="设置模型定价: /ybot llm price set <provider>/<model> <input_mtok> <output_mtok> [<cache_read_mtok>] [<cache_write_mtok>]",
    )
    price_list_cmd = Command(
        prefix="list",
        executor=_exec_price_list,
        scope="master",
        help_text="列出手动定价记录: /ybot llm price list",
    )
    price_cmd = Command(
        prefix="price",
        subs=[price_set_cmd, price_list_cmd],
        scope="master",
        help_text="模型定价管理",
    )
    llm_cmd = Command(
        prefix="llm",
        executor=llm_executor,
        subs=[price_cmd],
        interactive=True,
        help_text="触发 Agent。用 #agent_name 指定；@bot 等价于 yllm continue。",
    )

    from yuubot.commands.hhsh import exec_hhsh

    hhsh_cmd = Command(
        prefix="hhsh",
        executor=exec_hhsh,
        help_text="能不能好好说话：翻译缩写/黑话",
    )

    close_cmd = Command(
        prefix="close",
        executor=_exec_close,
        help_text="关闭当前会话 session",
    )

    cost_cmd = Command(
        prefix="cost",
        executor=_exec_cost,
        help_text="查看近期 Agent 开销: /cost [--days N] [--all (Master)]",
    )

    ping_cmd = Command(
        prefix="ping",
        executor=_exec_ping,
        help_text="查看 bot/会话状态: 无会话→pong，运行中→session pong，就绪→session ready",
    )

    from yuubot.commands.ychar import (
        exec_char_alias,
        exec_char_alias_delete,
        exec_char_alias_refresh,
        exec_char_alias_show,
        exec_char_config,
        exec_char_list,
        exec_char_role_clear,
        exec_char_role_list,
        exec_char_role_refresh,
        exec_char_role_set,
        exec_char_role_show,
        exec_char_selector_list,
        exec_char_show_config,
        exec_char_show_prompt,
    )

    char_show_prompt = Command(
        prefix="prompt",
        executor=exec_char_show_prompt,
        scope="master",
        help_text="显示 Character 系统提示词: /ychar show prompt [name]",
    )
    char_show_config = Command(
        prefix="config",
        executor=exec_char_show_config,
        scope="master",
        help_text="显示 Character 配置: /ychar show config [name]",
    )
    char_show = Command(
        prefix="show",
        subs=[char_show_prompt, char_show_config],
        scope="master",
        help_text="查看 Character 详情",
    )
    char_config = Command(
        prefix="config",
        executor=exec_char_config,
        scope="master",
        help_text="运行时切换 Character 模型: /ychar config <name> llm=<provider>/<selector-or-model>",
    )
    char_alias_show = Command(
        prefix="show",
        executor=exec_char_alias_show,
        scope="master",
        help_text="显示 selector 状态: /ychar alias show <selector>",
    )
    char_alias_refresh = Command(
        prefix="refresh",
        executor=exec_char_alias_refresh,
        scope="master",
        help_text="刷新 selector 缓存: /ychar alias refresh <selector or provider/selector>",
    )
    char_alias_delete = Command(
        prefix="delete",
        executor=exec_char_alias_delete,
        scope="master",
        help_text="删除 selector 或 provider 绑定: /ychar alias delete <selector or provider/selector>",
    )
    char_alias = Command(
        prefix="alias",
        executor=exec_char_alias,
        subs=[char_alias_show, char_alias_refresh, char_alias_delete],
        scope="master",
        help_text="持久绑定 selector: /ychar alias <provider>/<model> as <selector>",
    )
    char_role_show = Command(
        prefix="show",
        executor=exec_char_role_show,
        scope="master",
        help_text="显示内部 LLM role 解析: /ychar role show <role>",
    )
    char_role_list = Command(
        prefix="list",
        executor=exec_char_role_list,
        scope="master",
        help_text="列出内部 LLM role: /ychar role list",
    )
    char_role_set = Command(
        prefix="set",
        executor=exec_char_role_set,
        scope="master",
        help_text="运行时切换 role: /ychar role set <role> <selector|provider|provider/selector>",
    )
    char_role_refresh = Command(
        prefix="refresh",
        executor=exec_char_role_refresh,
        scope="master",
        help_text="刷新 role 解析: /ychar role refresh <role>",
    )
    char_role_clear = Command(
        prefix="clear",
        executor=exec_char_role_clear,
        scope="master",
        help_text="清除 role runtime override: /ychar role clear <role>",
    )
    char_role = Command(
        prefix="role",
        subs=[char_role_list, char_role_show, char_role_set, char_role_refresh, char_role_clear],
        scope="master",
        help_text="内部 LLM role 热切换与检查",
    )
    char_selector_list = Command(
        prefix="selectors",
        executor=exec_char_selector_list,
        scope="master",
        help_text="列出已知 selector: /ychar selectors",
    )
    char_list = Command(
        prefix="list",
        executor=exec_char_list,
        scope="master",
        help_text="列出所有 Character",
    )
    char_cmd = Command(
        prefix="char",
        subs=[char_show, char_config, char_alias, char_role, char_selector_list, char_list],
        scope="master",
        help_text="Character/模型管理命令",
    )

    root = RootCommand(
        prefix="",
        subs=[bot_cmd, help_cmd, llm_cmd, hhsh_cmd, close_cmd, cost_cmd, ping_cmd, char_cmd],
        entries=entries,
    )
    return root


# ── Executor implementations ─────────────────────────────────────
# Each executor receives CommandRequest
# and returns a reply string (or None for no reply).

_AT_RE = re.compile(r"@(\d+)")


async def _exec_on(request: CommandRequest) -> str | None:
    """Enable bot in the current group."""
    from yuubot.core.models import GroupSetting

    gid = request.message.group_id
    if not gid:
        config = request.deps.get("config")
        master_id = config.bot.master if config else 0
        if is_master_user(request.message.sender.user_id, master_id):
            return "Master 私聊无需开启；直接发送消息即可对话"
        return "此命令仅限群聊使用"
    await GroupSetting.update_or_create(
        defaults={"bot_enabled": True},
        group_id=gid,
    )
    dispatcher = request.deps.get("dispatcher")
    if dispatcher and hasattr(dispatcher, "invalidate_group_settings_cache"):
        dispatcher.invalidate_group_settings_cache()
    return "Bot 已开启"


async def _exec_off(request: CommandRequest) -> str | None:
    """Disable bot in group, or close the private conversation.

    Emergency brake: recorder already muted this ctx on seeing /bot off.
    Daemon side cancels running flows, stops workers, and closes sessions.
    """
    ctx_id = request.message.ctx_id

    # ── Daemon-side cleanup ──
    agent_runner = request.deps.get("agent_runner")
    if agent_runner:
        agent_runner.cancel_ctx(ctx_id)

    session_mgr = request.deps.get("session_mgr")
    if session_mgr:
        session_mgr.close(ctx_id)

    if request.message.chat_type == "private":
        return "已执行紧急制动"

    from yuubot.core.models import GroupSetting

    gid = request.message.group_id
    if not gid:
        return "此命令仅限群聊使用"
    await GroupSetting.update_or_create(
        defaults={"bot_enabled": False},
        group_id=gid,
    )
    dispatcher = request.deps.get("dispatcher")
    if dispatcher and hasattr(dispatcher, "invalidate_group_settings_cache"):
        dispatcher.invalidate_group_settings_cache()
    return "Bot 已关闭（紧急制动已执行）"


async def _exec_set(request: CommandRequest) -> str | None:
    """Set entry mapping."""
    entry_mgr = request.deps["entry_mgr"]
    parts = request.remaining.split()
    if len(parts) < 2:
        return "用法: /bot set <entry> <route>"
    entry, route = parts[0], parts[1]
    scope = str(request.message.group_id or "global")
    await entry_mgr.set(entry, route, scope)
    return f"入口 {entry} → {route} (scope: {scope})"


async def _exec_allow_dm(request: CommandRequest) -> str | None:
    """Allow DM from user."""
    m = _AT_RE.search(request.remaining)
    if not m:
        return "用法: /bot allow-dm @user"
    target_uid = int(m.group(1))
    dm_whitelist: list[int] = request.deps.get("dm_whitelist", [])
    if target_uid not in dm_whitelist:
        dm_whitelist.append(target_uid)
    return f"已允许 {target_uid} 私聊"


async def _exec_help(request: CommandRequest) -> str | None:
    """Show help for a specific command route, or root if none given."""
    root: RootCommand = request.deps["root"]
    config = request.deps.get("config")
    master_id = config.bot.master if config else 0
    route = request.remaining.split() if request.remaining.strip() else []
    target = root.find(route)
    if target is None:
        return f"未知命令: {' '.join(route)}"
    if not target.is_visible_to(request.message, master_id):
        return None
    return target.help(request.message, master_id)


async def _exec_close(request: CommandRequest) -> str | None:
    """Close current session."""
    ctx_id = request.message.ctx_id
    agent_runner = request.deps.get("agent_runner")
    if agent_runner:
        agent_runner.cancel_ctx(ctx_id)
    session_mgr = request.deps.get("session_mgr")
    if session_mgr is None:
        return "Session 功能未启用"
    if request.message.raw_event.get("_session_closed") or session_mgr.close(ctx_id):
        return "会话已重置 ✨"
    return "当前没有活跃的会话"


def _sanitize_agent_name(raw: str) -> str:
    """Strip ctx_id / hex suffix from agent names for clean aggregation.

    ``yuubot-{name}-{ctx_id}`` → ``yuubot ({name})``,
    ``yuubot-cron-{name}-{ctx_id}`` → ``yuubot-cron ({name})``,
    ``agent-{name}-{ctx_id|hex8}`` / ``delegate-{name}-{...}`` → ``{prefix}-{name}``.
    Legacy: ``yuubot-{ctx_id}`` → ``yuubot``.
    """
    if not raw:
        return "unknown"

    def _strip_suffix(name: str) -> str:
        """Strip trailing -<digits> or -<hex8> suffix."""
        parts = name.rsplit("-", 1)
        if len(parts) == 2:
            suf = parts[1]
            if suf.isdigit() or (len(suf) == 8 and all(c in "0123456789abcdefABCDEF" for c in suf)):
                return parts[0]
        return name

    if raw.startswith("yuubot-cron-"):
        rest = raw[len("yuubot-cron-"):]
        return f"yuubot-cron ({_strip_suffix(rest)})"
    if raw.startswith("yuubot-"):
        rest = raw[len("yuubot-"):]
        stripped = _strip_suffix(rest)
        if stripped != rest:
            return f"yuubot ({stripped})"
        # legacy: rest = "{ctx_id}" with no agent name part
        if rest.isdigit():
            return "yuubot"
        return f"yuubot ({rest})"
    if raw.startswith(("agent-", "delegate-")):
        return _strip_suffix(raw)
    return raw


async def _exec_ping(request: CommandRequest) -> str | None:
    """Report bot liveness plus current conversation readiness."""
    session_mgr = request.deps.get("session_mgr")
    if session_mgr is None:
        return "pong"

    conv = session_mgr.get(request.message.ctx_id)
    if conv is None:
        return "pong"
    if conv.state == "running":
        return "session pong"
    return "session ready"


async def _exec_cost(request: CommandRequest) -> str | None:
    """Show recent agent cost summary from traces.db."""
    # Parse --days N and --all
    parts = request.remaining.strip().split()
    days = 7
    show_all = "--all" in parts
    for i, p in enumerate(parts):
        if p == "--days" and i + 1 < len(parts):
            try:
                days = int(parts[i + 1])
            except ValueError:
                return "用法: /cost [--days N] [--all]"

    if show_all:
        config = request.deps.get("config")
        master_id = config.bot.master if config else 0
        if not is_master_user(request.message.sender.user_id, master_id):
            return "--all 仅限 Master 使用"

    ctx_id = request.message.ctx_id

    # Find traces.db
    config = request.deps.get("config")
    db_path = ""
    if config is not None:
        yuutrace_cfg = config.yuuagents.get("yuutrace", {})
        db_path = yuutrace_cfg.get("db_path", "")
    if not db_path:
        db_path = str(Path.home() / ".yagents" / "traces.db")
    else:
        if not db_path.startswith("file:") and db_path != ":memory:":
            db_path = str(Path(db_path).expanduser())

    is_sqlite_uri = db_path.startswith("file:")
    if not is_sqlite_uri and db_path != ":memory:" and not Path(db_path).exists():
        return "traces.db 不存在，无法查询开销"

    try:
        cutoff_ns = int((time.time() - days * 86400) * 1_000_000_000)
        conn = sqlite3.connect(db_path, uri=is_sqlite_uri)
        conn.row_factory = sqlite3.Row

        if show_all:
            rows = conn.execute(
                """SELECT
                       parent.agent,
                       json_extract(e.attributes_json, '$."yuu.llm.model"') AS llm_model,
                       SUM(json_extract(e.attributes_json, '$."yuu.cost.amount"')) AS total_cost,
                       COUNT(DISTINCT parent.trace_id) AS task_count
                   FROM events e
                   JOIN spans s ON e.span_id = s.span_id
                   JOIN spans parent ON s.parent_span_id = parent.span_id
                   WHERE e.name = 'yuu.cost'
                     AND s.start_time_unix_nano >= ?
                   GROUP BY parent.agent, llm_model
                   ORDER BY total_cost DESC""",
                (cutoff_ns,),
            ).fetchall()
        else:
            # Filter to current ctx_id: agent names like yuubot-{ctx_id} or yuubot-cron-{ctx_id}
            like_pattern = f"%-{ctx_id}"
            rows = conn.execute(
                """SELECT
                       parent.agent,
                       json_extract(e.attributes_json, '$."yuu.llm.model"') AS llm_model,
                       SUM(json_extract(e.attributes_json, '$."yuu.cost.amount"')) AS total_cost,
                       COUNT(DISTINCT parent.trace_id) AS task_count
                   FROM events e
                   JOIN spans s ON e.span_id = s.span_id
                   JOIN spans parent ON s.parent_span_id = parent.span_id
                   WHERE e.name = 'yuu.cost'
                     AND s.start_time_unix_nano >= ?
                     AND parent.agent LIKE ?
                   GROUP BY parent.agent, llm_model
                   ORDER BY total_cost DESC""",
                (cutoff_ns, like_pattern),
            ).fetchall()
        conn.close()
    except Exception:
        logger.exception("Failed to query traces.db")
        return "查询开销失败"

    if not rows:
        return f"近 {days} 天没有开销记录（提示：需先用 /ybot llm price set 配置模型定价）"

    # Re-aggregate by sanitized agent name (collapse model variants)
    from collections import defaultdict

    agent_costs: dict[str, float] = defaultdict(float)
    agent_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        agent = _sanitize_agent_name(row["agent"])
        agent_costs[agent] += row["total_cost"] or 0.0
        agent_counts[agent] += row["task_count"] or 0

    sorted_agents = sorted(agent_costs.items(), key=lambda x: -x[1])
    total = sum(agent_costs.values())

    header = f"近 {days} 天开销" + (" (全局)" if show_all else "") + ":"
    lines = [header]
    top_n = 3
    for agent, cost in sorted_agents[:top_n]:
        count = agent_counts[agent]
        lines.append(f"  {agent}: ${cost:.4f} / {count} 次")
    rest = sorted_agents[top_n:]
    if rest:
        other_cost = sum(c for _, c in rest)
        lines.append(f"  Other ({len(rest)} 项): ${other_cost:.4f}")
    lines.append("  ──────────")
    lines.append(f"  合计: ${total:.4f}")
    return "\n".join(lines)


async def _exec_price_set(request: CommandRequest) -> str | None:
    """Set per-model pricing in DB and update in-memory cache immediately."""
    from yuubot.model_resolution import set_model_price

    config = request.deps.get("config")
    provider_aliases: dict[str, str] = {}
    if config:
        raw = config.yuuagents.get("provider_aliases", {})
        if isinstance(raw, dict):
            provider_aliases = {str(k).lower(): str(v) for k, v in raw.items()}

    parts = request.remaining.strip().split()
    if len(parts) < 3:
        return "用法: /ybot llm price set <provider>/<model> <input_mtok> <output_mtok> [<cache_read_mtok>] [<cache_write_mtok>]"

    ref = parts[0]
    if "/" not in ref:
        return "格式错误: 需要 provider/model，例如 deepseek/deepseek-v4-flash"
    provider, model = ref.split("/", 1)

    seen: set[str] = set()
    while provider.lower() in provider_aliases and provider.lower() not in seen:
        seen.add(provider.lower())
        provider = provider_aliases[provider.lower()]

    try:
        prices: dict[str, float] = {
            "input_mtok": float(parts[1]),
            "output_mtok": float(parts[2]),
        }
        if len(parts) > 3:
            prices["cache_read_mtok"] = float(parts[3])
        if len(parts) > 4:
            prices["cache_write_mtok"] = float(parts[4])
    except ValueError:
        return "价格必须是数字（单位: USD/百万tokens）"

    await set_model_price(provider, model, prices)
    lines = [f"已更新 {provider}/{model} 定价:"]
    for k, v in prices.items():
        lines.append(f"  {k}: ${v}")
    return "\n".join(lines)


async def _exec_price_list(request: CommandRequest) -> str | None:
    """List manually configured model prices."""
    from yuubot.model_resolution import _pricing_cache

    if not _pricing_cache:
        return "暂无手动定价记录（使用 /ybot llm price set 添加）"

    lines = ["手动定价 (USD/百万tokens):"]
    for (provider, model), prices in sorted(_pricing_cache.items()):
        parts = []
        for k in ("input_mtok", "output_mtok", "cache_read_mtok", "cache_write_mtok"):
            if k in prices:
                label = k.replace("_mtok", "")
                parts.append(f"{label}=${prices[k]}")
        lines.append(f"  {provider}/{model}: {', '.join(parts)}")
    return "\n".join(lines)
