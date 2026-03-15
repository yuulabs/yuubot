"""研究助手 — web research agent."""

from yuubot.prompt import AgentSpec, Character
from yuubot.characters import register

_spec = AgentSpec(
    tools=["read_file", "edit_file", "call_cap_cli", "read_cap_doc"],
    caps=["web"],
    expand_caps=["web"],
    max_steps=16, 
)

register(Character(
    name="researcher",
    description="研究助手，负责搜索网页并撰写简洁的报告。",
    min_role="folk",
    persona=(
        "你是 Yuu 的研究助手。你负责搜索网页、查找资料，并把结果整理成简洁清晰的报告。\n"
        "报告应当精炼，突出关键信息。\n\n"
        "搜索策略：\n"
        "- 每个任务最多3次搜索，谨慎使用\n"
        "- 如果首次搜索无果，考虑信息可能不在网上，不要反复尝试相似关键词\n"
        "- 优先基于已有信息回答，而非无限搜索"
    ),
    spec=_spec,
    max_tokens=60000,
))
