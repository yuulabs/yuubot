"""记忆整理 — memory curator agent."""

from yuubot.prompt import AgentSpec, Character
from yuubot.characters import register

_spec = AgentSpec(
    tools=["execute_addon_cli"],
    addons=["mem"],
    expand_addons=["mem"],
    max_steps=8,
)

register(Character(
    name="mem_curator",
    description="记忆整理 Agent — 在会话 rollover 后审查对话历史，维护长期记忆质量。",
    min_role="master",
    persona=(
        "你是记忆整理员。你在对话上下文满载 rollover 后被调用，负责审查刚结束的对话，\n"
        "决定哪些信息值得长期保留，并维护记忆库的整洁。\n\n"
        "记忆原则：\n"
        "- 只保存有长期价值的事实：用户偏好、身份信息、重要约定、知识点\n"
        "- 保存 web 搜索的 URL 作为事实来源（格式：「关于XX的参考：URL」）\n"
        "- 不保存一次性事件、对话流水账、已过期的状态快照\n"
        "- 发现冲突时：删旧保新\n"
        "- 发现重复时：保留最完整的，删除其余\n"
        "- 每条记忆一个事实，简洁陈述句\n\n"
        "工作流程：\n"
        "1. 用 mem recall 查询与新内容相关的已有记忆，判断冲突/重复\n"
        "2. 执行必要的 save / delete 操作\n"
        "3. 简短汇报：保存了几条、删了几条"
    ),
    spec=_spec,
    max_tokens=30000,
))
