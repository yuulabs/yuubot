"""研究助手 — web research agent."""

from yuubot.prompt import AgentSpec, Character
from yuubot.characters import register

_spec = AgentSpec(
    tools=["read_file", "write_file", "edit_file"],
    skills=["web"],
    max_steps=16,
)

register(Character(
    name="researcher",
    description="研究助手，负责搜索网页并撰写简洁的报告。",
    min_role="folk",
    persona=(
        "你是 Yuu 的研究助手。你负责搜索网页、查找资料，并把结果整理成简洁清晰的报告。\n"
        "报告应当精炼，突出关键信息。"
    ),
    spec=_spec,
))
