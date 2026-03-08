"""Schedule skill CLI implementations."""

import logging

import click
import httpx

from yuubot.config import load_config
from yuubot.core import env
from yuubot.core.db import init_db, close_db
from yuubot.core.models import ScheduledTask
from yuubot.skills.schedule.cron import is_long_cycle, validate_cron

log = logging.getLogger(__name__)


def _daemon_api(cfg) -> str:
    return f"http://{cfg.daemon.api.host}:{cfg.daemon.api.port}"


def _notify_reload(cfg) -> None:
    """POST /schedule/reload to daemon. Silent on failure."""
    try:
        httpx.post(f"{_daemon_api(cfg)}/schedule/reload", timeout=5)
    except httpx.ConnectError:
        pass


def _caller_agent() -> str:
    """Return the name of the agent invoking this skill, or empty string."""
    return env.get(env.AGENT_NAME)


def _resolve_agent(explicit: str | None) -> str:
    """Resolve target agent: explicit > caller's own name > 'main'."""
    if explicit:
        return explicit
    return _caller_agent() or "main"


def _check_agent_permission(cfg, target_agent: str) -> str | None:
    """Check that the caller can schedule *target_agent*.

    Returns an error message if denied, or None if allowed.
    An agent may schedule itself or any agent in its subagents list.
    """
    caller = _caller_agent()
    if not caller:
        # Not running inside a bot agent (manual CLI invocation) — allow all
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


async def create_task(
    cron_expr: str,
    task: str,
    agent: str | None,
    ctx_id: int | None,
    once: bool,
    config_path: str | None,
) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        validate_cron(cron_expr)

        resolved_agent = _resolve_agent(agent)

        # Permission check
        err = _check_agent_permission(cfg, resolved_agent)
        if err:
            click.echo(err)
            return

        # Long-cycle limit: only applies to recurring tasks
        if not once and is_long_cycle(cron_expr):
            existing_long = 0
            for t in await ScheduledTask.filter(enabled=True, once=False).all():
                if is_long_cycle(t.cron):
                    existing_long += 1
            if existing_long >= cfg.schedule.max_long_cycle:
                click.echo(
                    f"错误: 长周期定时任务已达上限 ({cfg.schedule.max_long_cycle})。"
                    f"请先删除或禁用已有的长周期任务。"
                )
                return

        created_by = _caller_agent() or env.get(env.USER_ID) or ""
        obj = await ScheduledTask.create(
            cron=cron_expr,
            task=task,
            agent=resolved_agent,
            ctx_id=ctx_id,
            once=once,
            created_by=created_by,
        )
        click.echo(f"已创建定时任务 [id: {obj.id}]")
        click.echo(f"  cron: {cron_expr}")
        click.echo(f"  task: {task}")
        click.echo(f"  agent: {resolved_agent}")
        if ctx_id is not None:
            click.echo(f"  ctx: {ctx_id}")
        if once:
            click.echo("  once: yes (触发一次后自动禁用)")
        _notify_reload(cfg)
    except ValueError as e:
        click.echo(f"错误: {e}")
    finally:
        await close_db()


async def list_tasks(config_path: str | None, *, show_all: bool = False) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        if show_all:
            tasks = await ScheduledTask.all().order_by("id")
        else:
            tasks = await ScheduledTask.filter(enabled=True).order_by("id")
        if not tasks:
            click.echo("暂无定时任务" if show_all else "暂无活跃定时任务（使用 --all 查看全部）")
            return
        for t in tasks:
            status = "enabled" if t.enabled else "disabled"
            once_tag = " [once]" if t.once else " [recurring]"
            ctx_str = f" ctx={t.ctx_id}" if t.ctx_id is not None else ""
            click.echo(
                f"[{t.id}] ({status}{once_tag}) cron=\"{t.cron}\" "
                f"agent={t.agent}{ctx_str}"
            )
            click.echo(f"     task: {t.task}")
        label = "全部" if show_all else "活跃"
        click.echo(f"共 {len(tasks)} 条{label}定时任务")
    finally:
        await close_db()


async def update_task(
    task_id: int,
    cron_expr: str | None,
    task: str | None,
    agent: str | None,
    enable: bool | None,
    config_path: str | None,
) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        obj = await ScheduledTask.get_or_none(id=task_id)
        if obj is None:
            click.echo(f"错误: 任务 {task_id} 不存在")
            return

        if cron_expr is not None:
            validate_cron(cron_expr)
            # Re-check long-cycle limit if cron changes and task is recurring
            if not obj.once and is_long_cycle(cron_expr):
                existing_long = 0
                for t in await ScheduledTask.filter(enabled=True, once=False).exclude(id=task_id).all():
                    if is_long_cycle(t.cron):
                        existing_long += 1
                if existing_long >= cfg.schedule.max_long_cycle:
                    click.echo(
                        f"错误: 长周期定时任务已达上限 ({cfg.schedule.max_long_cycle})。"
                    )
                    return
            obj.cron = cron_expr

        if task is not None:
            obj.task = task
        if agent is not None:
            err = _check_agent_permission(cfg, agent)
            if err:
                click.echo(err)
                return
            obj.agent = agent
        if enable is not None:
            obj.enabled = enable

        await obj.save()
        click.echo(f"已更新定时任务 [{task_id}]")
        _notify_reload(cfg)
    except ValueError as e:
        click.echo(f"错误: {e}")
    finally:
        await close_db()


async def delete_task(task_id: int, config_path: str | None) -> None:
    cfg = load_config(config_path)
    await init_db(cfg.database.path, simple_ext=cfg.database.simple_ext)
    try:
        obj = await ScheduledTask.get_or_none(id=task_id)
        if obj is None:
            click.echo(f"错误: 任务 {task_id} 不存在")
            return
        await obj.delete()
        click.echo(f"已删除定时任务 [{task_id}]")
        _notify_reload(cfg)
    except Exception as e:
        click.echo(f"错误: {e}")
    finally:
        await close_db()
