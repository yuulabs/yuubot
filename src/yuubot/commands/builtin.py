"""Built-in commands: /bot, /help, /llm, /new, /cost."""

import json
import logging
import re
import sqlite3
import time
from pathlib import Path

import httpx

from yuubot.commands.roles import RoleManager
from yuubot.commands.tree import Command, RootCommand
from yuubot.core.models import Role

log = logging.getLogger(__name__)


def build_command_tree(entries: list[str]) -> RootCommand:
    """Construct the full command tree with built-in commands."""
    # /bot sub-commands
    grand_cmd = Command(
        prefix="grand",
        executor=_exec_grand,
        min_role=Role.MOD,
        help_text="变更用户角色: /bot grand @user <role> [--unlimited]",
    )
    on_cmd = Command(
        prefix="on",
        executor=_exec_on,
        min_role=Role.MOD,
        help_text="开启 bot (--free 开启 free 模式) | 私聊: --auto 开启自动响应模式",
    )
    off_cmd = Command(
        prefix="off",
        executor=_exec_off,
        min_role=Role.MOD,
        help_text="关闭 bot | 私聊: 关闭 auto 模式",
    )
    set_cmd = Command(
        prefix="set",
        executor=_exec_set,
        min_role=Role.MOD,
        help_text="设置入口映射: /bot set <entry> <route>",
    )
    allow_dm_cmd = Command(
        prefix="allow-dm",
        executor=_exec_allow_dm,
        min_role=Role.MASTER,
        help_text="允许用户私聊: /bot allow-dm @user",
    )
    bot_cmd = Command(
        prefix="bot",
        subs=[grand_cmd, on_cmd, off_cmd, set_cmd, allow_dm_cmd],
        help_text="Bot 管理命令",
    )

    help_cmd = Command(
        prefix="help",
        executor=_exec_help,
        min_role=Role.FOLK,
        help_text="显示帮助",
    )
    llm_cmd = Command(
        prefix="llm",
        executor=_exec_llm,
        min_role=Role.FOLK,
        help_text="触发 Agent 回答问题。使用yllm#agent_name 来指定 Agent。",
    )

    hhsh_cmd = Command(
        prefix="hhsh",
        executor=_exec_hhsh,
        min_role=Role.FOLK,
        help_text="能不能好好说话：翻译缩写/黑话",
    )

    close_cmd = Command(
        prefix="close",
        executor=_exec_close,
        min_role=Role.FOLK,
        help_text="关闭当前会话 session",
    )

    cost_cmd = Command(
        prefix="cost",
        executor=_exec_cost,
        min_role=Role.FOLK,
        help_text="查看近期 Agent 开销: /cost [--days N] [--all (Master)]",
    )

    # /char — character inspection and runtime config
    from yuubot.commands.ychar import (
        exec_char_config,
        exec_char_list,
        exec_char_show_config,
        exec_char_show_prompt,
    )
    char_show_prompt = Command(
        prefix="prompt",
        executor=exec_char_show_prompt,
        min_role=Role.MOD,
        help_text="显示 Character 的系统提示词结构: /char show prompt [name]",
    )
    char_show_config = Command(
        prefix="config",
        executor=exec_char_show_config,
        min_role=Role.MOD,
        help_text="显示 Character 配置: /char show config [name]",
    )
    char_show = Command(
        prefix="show",
        subs=[char_show_prompt, char_show_config],
        min_role=Role.MOD,
        help_text="查看 Character 详情",
    )
    char_config = Command(
        prefix="config",
        executor=exec_char_config,
        min_role=Role.MASTER,
        help_text="热更新 Character 配置: /char config <name> provider=x model=y",
    )
    char_list = Command(
        prefix="list",
        executor=exec_char_list,
        min_role=Role.MOD,
        help_text="列出所有已注册 Character",
    )
    char_cmd = Command(
        prefix="char",
        subs=[char_show, char_config, char_list],
        min_role=Role.MOD,
        help_text="Character 管理命令",
    )

    root = RootCommand(
        prefix="",
        subs=[bot_cmd, help_cmd, llm_cmd, hhsh_cmd, close_cmd, cost_cmd, char_cmd],
        entries=entries,
    )
    return root


# ── Executor implementations ─────────────────────────────────────
# Each executor receives (remaining: str, event: dict, deps: dict)
# and returns a reply string (or None for no reply).

_AT_RE = re.compile(r"@(\d+)")


async def _exec_grand(remaining: str, event: dict, deps: dict) -> str | None:
    """Grant role: /bot grand @user <role> [--unlimited]"""
    role_mgr: RoleManager = deps["role_mgr"]
    caller_role = await role_mgr.get(event["user_id"], str(event.get("group_id", "global")))

    m = _AT_RE.search(remaining)
    if not m:
        return "用法: /bot grand @user <role>"
    target_uid = int(m.group(1))
    rest = remaining[m.end():].strip()

    parts = rest.split()
    role_name = parts[0].lower() if parts else ""
    unlimited = "--unlimited" in rest

    role_map = {"master": Role.MASTER, "mod": Role.MOD, "folk": Role.FOLK, "deny": Role.DENY}
    target_role = role_map.get(role_name)
    if target_role is None:
        return f"未知角色: {role_name}. 可选: master, mod, folk, deny"

    # Mod can only grant folk/deny
    if caller_role == Role.MOD and target_role > Role.FOLK:
        return "Mod 只能授权 folk 或 deny"

    scope = "global" if unlimited else str(event.get("group_id", "global"))
    await role_mgr.set(target_uid, target_role, scope)
    return f"已将 {target_uid} 设为 {target_role.name} (scope: {scope})"


async def _exec_on(remaining: str, event: dict, deps: dict) -> str | None:
    """Enable bot in group, or enable auto mode in private chat (MOD+)."""
    if "--auto" in remaining:
        if event.get("message_type") != "private":
            return "auto 模式仅限私聊使用"
        ctx_id = event.get("ctx_id", 0)
        session_mgr = deps.get("session_mgr")
        if session_mgr:
            await session_mgr.enable_auto(ctx_id)
        return "已开启 auto 模式（每条消息自动响应，TTL 30min）"

    from yuubot.core.models import GroupSetting

    gid = event.get("group_id", 0)
    if not gid:
        return "此命令仅限群聊使用"
    mode = "free" if "--free" in remaining else "at"
    await GroupSetting.update_or_create(
        defaults={"bot_enabled": True, "response_mode": mode},
        group_id=gid,
    )
    return f"Bot 已开启 (模式: {mode})"


async def _exec_off(remaining: str, event: dict, deps: dict) -> str | None:
    """Disable bot in group, or disable auto mode in private chat (MOD+)."""
    if event.get("message_type") == "private":
        ctx_id = event.get("ctx_id", 0)
        session_mgr = deps.get("session_mgr")
        if session_mgr and session_mgr.is_auto(ctx_id):
            await session_mgr.disable_auto(ctx_id)
            return "已关闭 auto 模式"
        return "当前未开启 auto 模式"

    from yuubot.core.models import GroupSetting

    gid = event.get("group_id", 0)
    if not gid:
        return "此命令仅限群聊使用"
    await GroupSetting.update_or_create(
        defaults={"bot_enabled": False, "response_mode": "at"},
        group_id=gid,
    )
    return "Bot 已关闭"


async def _exec_set(remaining: str, event: dict, deps: dict) -> str | None:
    """Set entry mapping."""
    entry_mgr = deps["entry_mgr"]
    parts = remaining.split()
    if len(parts) < 2:
        return "用法: /bot set <entry> <route>"
    entry, route = parts[0], parts[1]
    unlimited = "--unlimited" in remaining
    scope = "global" if unlimited else str(event.get("group_id", "global"))
    await entry_mgr.set(entry, route, scope)
    return f"入口 {entry} → {route} (scope: {scope})"


async def _exec_allow_dm(remaining: str, event: dict, deps: dict) -> str | None:
    """Allow DM from user."""
    m = _AT_RE.search(remaining)
    if not m:
        return "用法: /bot allow-dm @user"
    target_uid = int(m.group(1))
    dm_whitelist: list[int] = deps.get("dm_whitelist", [])
    if target_uid not in dm_whitelist:
        dm_whitelist.append(target_uid)
    return f"已允许 {target_uid} 私聊"


async def _exec_help(remaining: str, event: dict, deps: dict) -> str | None:
    """Show help for a specific command route, or root if none given."""
    root: RootCommand = deps["root"]
    route = remaining.split() if remaining.strip() else []
    target = root.find(route)
    if target is None:
        return f"未知命令: {' '.join(route)}"
    return target.help()


async def _exec_hhsh(remaining: str, event: dict, deps: dict) -> str | None:
    """Translate abbreviation: /hhsh <text>"""
    text = remaining.strip()
    if not text:
        return "用法: /hhsh <缩写>，例如: /hhsh yyds"
    from yuubot.skills.hhsh.cli import guess

    try:
        result = await guess(text)
    except Exception:
        return "hhsh 查询失败"
    return result or "(无结果)"


async def _exec_llm(remaining: str, event: dict, deps: dict) -> str | None:
    """Trigger agent — returns None, dispatcher handles agent invocation."""
    # This is a marker; dispatcher checks for this command and runs agent
    return None


async def _exec_close(remaining: str, event: dict, deps: dict) -> str | None:
    """Close current session."""
    session_mgr = deps.get("session_mgr")
    if session_mgr is None:
        return "Session 功能未启用"
    ctx_id = event.get("ctx_id", 0)
    if event.get("_session_closed") or session_mgr.close(ctx_id):
        return "会话已重置 ✨"
    return "当前没有活跃的会话"


def _sanitize_agent_name(raw: str) -> str:
    """Strip ctx_id suffix from agent names to avoid leaking context info.

    New format: ``yuubot-{agent_name}-{ctx_id}`` → ``yuubot ({agent_name})``,
    ``yuubot-cron-{agent_name}-{ctx_id}`` → ``yuubot-cron ({agent_name})``,
    ``delegate-{name}-{hex8}`` → ``delegate-{name}``.
    Legacy format (no agent_name): ``yuubot-3`` → ``yuubot``.
    """
    if not raw:
        return "unknown"
    # yuubot-cron-{agent_name}-{ctx_id}
    if raw.startswith("yuubot-cron-"):
        rest = raw[len("yuubot-cron-"):]
        # new format: rest = "{agent_name}-{ctx_id}"
        parts = rest.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return f"yuubot-cron ({parts[0]})"
        # legacy: rest = "{ctx_id}"
        return "yuubot-cron"
    # yuubot-{agent_name}-{ctx_id}
    if raw.startswith("yuubot-"):
        rest = raw[len("yuubot-"):]
        # new format: rest = "{agent_name}-{ctx_id}"
        parts = rest.rsplit("-", 1)
        if len(parts) == 2 and parts[1].isdigit():
            return f"yuubot ({parts[0]})"
        # legacy: rest = "{ctx_id}" (just a number)
        if rest.isdigit():
            return "yuubot"
        return "yuubot"
    # delegate-{name}-{hex8} → delegate-{name}
    if raw.startswith("delegate-"):
        parts = raw.rsplit("-", 1)
        if len(parts) == 2 and len(parts[1]) == 8:
            return parts[0]
    return raw


async def _exec_cost(remaining: str, event: dict, deps: dict) -> str | None:
    """Show recent agent cost summary from traces.db."""
    # Parse --days N and --all
    parts = remaining.strip().split()
    days = 7
    show_all = "--all" in parts
    for i, p in enumerate(parts):
        if p == "--days" and i + 1 < len(parts):
            try:
                days = int(parts[i + 1])
            except ValueError:
                return "用法: /cost [--days N] [--all]"

    # --all requires Master role
    if show_all:
        role_mgr: RoleManager = deps["role_mgr"]
        scope = str(event.get("group_id", "global"))
        caller_role = await role_mgr.get(event["user_id"], scope)
        if caller_role < Role.MASTER:
            return "--all 仅限 Master 使用"

    ctx_id = event.get("ctx_id", 0)

    # Find traces.db
    config = deps.get("config")
    db_path = ""
    if config is not None:
        yuutrace_cfg = config.yuuagents.get("yuutrace", {})
        db_path = yuutrace_cfg.get("db_path", "")
    if not db_path:
        db_path = str(Path.home() / ".yagents" / "traces.db")
    else:
        db_path = str(Path(db_path).expanduser())

    if not Path(db_path).exists():
        return "traces.db 不存在，无法查询开销"

    try:
        cutoff_ns = int((time.time() - days * 86400) * 1_000_000_000)
        conn = sqlite3.connect(db_path)
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
        log.exception("Failed to query traces.db")
        return "查询开销失败"

    if not rows:
        return f"近 {days} 天没有开销记录"

    header = f"📊 近 {days} 天开销" + (" (全局)" if show_all else "") + ":"
    lines = [header]
    total = 0.0
    for row in rows:
        agent = _sanitize_agent_name(row["agent"])
        model = row["llm_model"] or "unknown"
        cost = row["total_cost"] or 0.0
        count = row["task_count"] or 0
        total += cost
        lines.append(f"  {agent} ({model}): ${cost:.4f} / {count} 次")
    lines.append("  ──────────")
    lines.append(f"  合计: ${total:.4f}")
    return "\n".join(lines)

