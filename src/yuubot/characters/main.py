"""夕雨 (Yuu) — main QQ bot agent."""

from yuubot.prompt import AgentSpec, CapVisibility, Character, FileRef, Section
from yuubot.characters import register

_spec = AgentSpec(
    tools=[
        "call_cap_cli", "read_cap_doc",
    ],
    sections=[
        Section("safety", FileRef("prompts/main/safety.md")),
        Section("messaging", FileRef("prompts/main/messaging.md")),
        Section("memes", FileRef("prompts/main/memes.md")),
    ],
    caps=["*"],
    expand_caps=["im"],
    cap_visibility={
        "mem": CapVisibility(mode="include", actions=("recall", "show", "config")),
    },
    max_steps=16, 
    silence_timeout=120,
)

register(Character(
    name="main",
    description="yuubot QQ 机器人主代理 — 夕雨(Yuu)",
    min_role="folk",
    persona=FileRef("prompts/main/persona.md"),
    spec=_spec,
    max_tokens=30000,
))
