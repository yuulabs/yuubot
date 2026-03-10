"""夕雨 (Yuu) — main QQ bot agent."""

from yuubot.prompt import AgentSpec, Character, FileRef, Section
from yuubot.characters import register

_spec = AgentSpec(
    tools=[
        "execute_skill_cli", "read_skill",
        "check_running_tool", "cancel_running_tool",
    ],
    sections=[
        Section("safety", FileRef("prompts/main/safety.md")),
        Section("messaging", FileRef("prompts/main/messaging.md")),
        Section("memes", FileRef("prompts/main/memes.md")),
    ],
    skills=["*"],
    expand_skills=["im"],
    max_steps=16,
    silence_timeout=120,
)

register(Character(
    name="main",
    description="yuubot QQ 机器人主代理 — 夕雨(Yuu)",
    min_role="folk",
    persona=FileRef("prompts/main/persona.md"),
    spec=_spec,
))
