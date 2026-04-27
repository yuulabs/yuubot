import yuuagents as ya

from yuubot.characters import register
from yuubot.prompt import (
    AgentSpec,
    Character,
    DelegatesSection,
    ExpandFunctionsSection,
    FileSection,
    PYTHON_RUNTIME_SECTION,
    PythonWorkerSection,
)

register(
    Character(
        name="yuu",
        description="日常群聊、消息浏览、记忆检索、网页阅读、图片理解。",
        spec=AgentSpec(
            tools=("execute_python", "read_chat_file"),
            import_modules=(
                ya.PythonImport("yuubot.agent_fns.im", alias="im"),
                ya.PythonImport("yuubot.agent_fns.mem", alias="mem"),
                ya.PythonImport("yuubot.agent_fns.web", alias="web"),
                ya.PythonImport("yuubot.agent_fns.vision", alias="vision"),
            ),
            expand_functions=("+im.*", "*"),
            prompt_sections=(
                FileSection("main/persona.md"),
                PYTHON_RUNTIME_SECTION,
                ExpandFunctionsSection(),
                FileSection("main/messaging.md"),
                FileSection("main/safety.md"),
                FileSection("main/context_awareness.md"),
                FileSection("main/memes.md"),
                FileSection("shared/web_reading.md"),
                DelegatesSection(),
                PythonWorkerSection(),
            ),
            delegate_policy=None,
            max_turns=16,
            max_context_tokens=32000,
        ),
        bot_kind="group"
    )
)
