"""Schedule capability — create, list, update, delete cron tasks."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
from loguru import logger

from yuubot.capabilities import capability, get_context, text_block, ContentBlock
from yuubot.config import load_config
from yuubot.core.models import ScheduledTask
from .cron import is_long_cycle, validate_cron

type ContentBlocks = list[ContentBlock]


def _get_config():
    ctx = get_context()
    if ctx.config is not None:
        return ctx.config
    return load_config(None)


def _caller_agent() -> str:
    return get_context().agent_name


def _is_master() -> bool:
    return get_context().user_role == "MASTER"


def _caller_ctx() -> int | None:
    return get_context().ctx_id


def _resolve_agent(explicit: str | None) -> str:
    if explicit:
        return explicit
    return _caller_agent() or "main"


def _check_agent_permission(cfg, target_agent: str) -> str | None:
    caller = _caller_agent()
    if not caller:
        return None
    if target_agent == caller:
        return None
    agents_cfg = cfg.yuuagents.get("agents", {})
    caller_cfg = agents_cfg.get(caller, {})
    allowed = caller_cfg.get("subagents", [])
    if "*" in allowed or target_agent in allowed:
        return None
    return (
        f"错误: agent {caller!r} 无权为 {target_agent!r} 创建定时任务。"
        f"只能调度自身或 subagents 列表中的 agent。"
    )


def _check_role_guard(cfg, caller_agent: str, task_agent: str) -> str | None:
    """Block if caller agent's min_role < task agent's min_role."""
    if not caller_agent or not task_agent:
        return None
    caller_role = cfg.agent_min_role(caller_agent)
    task_role = cfg.agent_min_role(task_agent)
    if caller_role < task_role:
        return (
            f"错误: agent {caller_agent!r} (权限={caller_role.name}) "
            f"无法操作 agent {task_agent!r} (权限={task_role.name}) 的定时任务。"
        )
    return None


async def _notify_reload(cfg) -> None:
    try:
        api = f"http://{cfg.daemon.api.host}:{cfg.daemon.api.port}"
        async with httpx.AsyncClient() as client:
            await client.post(f"{api}/schedule/reload", timeout=5)
    except Exception:
        logger.debug("schedule reload notification failed (non-critical)")


@capability("schedule")
class ScheduleCapability:

    async def create(
        self,
        *,
        _positional: Sequence[str] | None = None,
        agent: str | None = None,
        ctx: int | None = None,
        recurring: bool = False,
        **_kw,
    ) -> ContentBlocks:
        if not _positional or len(_positional) < 2:
            return [text_block("错误: 用法: schedule create <cron> <task> [--agent ...] [--ctx ...] [--recurring]")]

        cron_expr = _positional[0]
        task = " ".join(_positional[1:])

        cfg = _get_config()
        validate_cron(cron_expr)
        resolved_agent = _resolve_agent(agent)

        err = _check_agent_permission(cfg, resolved_agent)
        if err:
            return [text_block(err)]

        err = _check_role_guard(cfg, _caller_agent(), resolved_agent)
        if err:
            return [text_block(err)]

        once = not recurring
        if not once and is_long_cycle(cron_expr):
            existing_long = 0
            for t in await ScheduledTask.filter(enabled=True, once=False).all():
                if is_long_cycle(t.cron):
                    existing_long += 1
            if existing_long >= cfg.schedule.max_long_cycle:
                return [text_block(
                    f"错误: 长周期定时任务已达上限 ({cfg.schedule.max_long_cycle})。"
                    f"请先删除或禁用已有的长周期任务。"
                )]

        created_by = _caller_agent() or str(get_context().user_id or "")
        obj = await ScheduledTask.create(
            cron=cron_expr,
            task=task,
            agent=resolved_agent,
            ctx_id=ctx,
            once=once,
            created_by=created_by,
        )
        lines = [
            f"已创建定时任务 [id: {obj.id}]",
            f"  cron: {cron_expr}",
            f"  task: {task}",
            f"  agent: {resolved_agent}",
        ]
        if ctx is not None:
            lines.append(f"  ctx: {ctx}")
        if once:
            lines.append("  once: yes (触发一次后自动禁用)")
        caller = _caller_agent()
        if caller and resolved_agent != caller:
            lines.append(
                f"⚠️ 注意: 当前 agent 为 {caller!r}，但任务将由 {resolved_agent!r} 执行。"
            )
        await _notify_reload(cfg)
        return [text_block("\n".join(lines))]

    async def list(
        self,
        *,
        all: bool = False,
        **_kw,
    ) -> ContentBlocks:
        filters: dict = {} if all else {"enabled": True}
        ctx = _caller_ctx()
        if ctx is not None and not _is_master():
            filters["ctx_id"] = ctx
        if filters:
            tasks = await ScheduledTask.filter(**filters).order_by("id")
        else:
            tasks = await ScheduledTask.all().order_by("id")
        if not tasks:
            msg = "暂无定时任务" if all else "暂无活跃定时任务（使用 --all 查看全部）"
            return [text_block(msg)]
        lines = []
        for t in tasks:
            status = "enabled" if t.enabled else "disabled"
            once_tag = " [once]" if t.once else " [recurring]"
            ctx_str = f" ctx={t.ctx_id}" if t.ctx_id is not None else ""
            lines.append(
                f"[{t.id}] ({status}{once_tag}) cron=\"{t.cron}\" "
                f"agent={t.agent}{ctx_str}"
            )
            lines.append(f"     task: {t.task}")
        label = "全部" if all else "活跃"
        lines.append(f"共 {len(tasks)} 条{label}定时任务")
        return [text_block("\n".join(lines))]

    async def update(
        self,
        *,
        _positional: Sequence[str] | None = None,
        cron: str | None = None,
        task: str | None = None,
        agent: str | None = None,
        enable: bool | None = None,
        disable: bool | None = None,
        **_kw,
    ) -> ContentBlocks:
        if not _positional:
            return [text_block("错误: 请提供任务 ID")]
        task_id = int(_positional[0])

        enable_val = None
        if enable:
            enable_val = True
        elif disable:
            enable_val = False

        cfg = _get_config()
        obj = await ScheduledTask.get_or_none(id=task_id)
        if obj is None:
            return [text_block(f"错误: 任务 {task_id} 不存在")]

        ctx = _caller_ctx()
        if ctx is not None and not _is_master() and obj.ctx_id != ctx:
            return [text_block(f"错误: 无权修改其他会话的定时任务 [{task_id}]")]

        err = _check_role_guard(cfg, _caller_agent(), obj.agent)
        if err:
            return [text_block(err)]

        if cron is not None:
            validate_cron(cron)
            if not obj.once and is_long_cycle(cron):
                existing_long = 0
                for t in await ScheduledTask.filter(enabled=True, once=False).exclude(id=task_id).all():
                    if is_long_cycle(t.cron):
                        existing_long += 1
                if existing_long >= cfg.schedule.max_long_cycle:
                    return [text_block(f"错误: 长周期定时任务已达上限 ({cfg.schedule.max_long_cycle})。")]
            obj.cron = cron
        if task is not None:
            obj.task = task
        if agent is not None:
            err = _check_agent_permission(cfg, agent)
            if err:
                return [text_block(err)]
            err = _check_role_guard(cfg, _caller_agent(), agent)
            if err:
                return [text_block(err)]
            obj.agent = agent
        if enable_val is not None:
            obj.enabled = enable_val

        await obj.save()
        await _notify_reload(cfg)
        return [text_block(f"已更新定时任务 [{task_id}]")]

    async def delete(
        self,
        *,
        _positional: Sequence[str] | None = None,
        **_kw,
    ) -> ContentBlocks:
        if not _positional:
            return [text_block("错误: 请提供任务 ID")]
        task_id = int(_positional[0])

        cfg = _get_config()
        obj = await ScheduledTask.get_or_none(id=task_id)
        if obj is None:
            return [text_block(f"错误: 任务 {task_id} 不存在")]

        ctx = _caller_ctx()
        if ctx is not None and not _is_master() and obj.ctx_id != ctx:
            return [text_block(f"错误: 无权删除其他会话的定时任务 [{task_id}]")]

        err = _check_role_guard(cfg, _caller_agent(), obj.agent)
        if err:
            return [text_block(err)]

        await obj.delete()
        await _notify_reload(cfg)
        return [text_block(f"已删除定时任务 [{task_id}]")]
