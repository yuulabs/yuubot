"""Skill installation — copy SKILL.md to yuuagents skills directory."""

import shutil
from pathlib import Path

import click

from yuubot.config import load_config

# Built-in skills with their source directories
_BUILTIN_SKILLS = ["im", "web", "mem", "hhsh", "schedule"]


def install_skill(skill_name: str | None, config_path: str | None) -> None:
    """Install skill SKILL.md to yuuagents skills directory.

    If *skill_name* is None, install all built-in skills.
    """
    cfg = load_config(config_path)

    names = _BUILTIN_SKILLS if skill_name is None else [skill_name]

    for name in names:
        if name not in _BUILTIN_SKILLS:
            click.echo(f"未知 skill: {name}. 可选: {', '.join(_BUILTIN_SKILLS)}")
            continue

        # Read SKILL.md from the skill's source directory
        source_skill_md = Path(__file__).parent / name / "SKILL.md"
        if not source_skill_md.exists():
            click.echo(f"错误: {name} 的 SKILL.md 不存在于 {source_skill_md}")
            continue

        skill_content = source_skill_md.read_text(encoding="utf-8")

        for skill_dir in cfg.skill_paths:
            dest = Path(skill_dir) / name
            dest.mkdir(parents=True, exist_ok=True)
            skill_md = dest / "SKILL.md"
            skill_md.write_text(skill_content, encoding="utf-8")
            click.echo(f"已安装 {name} → {skill_md}")
            break
        else:
            click.echo("错误: 未配置 skill_paths")
