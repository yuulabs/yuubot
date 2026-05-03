import yuuagents as ya

from yuubot.characters import register
from yuubot.prompt import (
    AgentSpec,
    Character,
    ExpandFunctionsSection,
    FileSection,
    InlineSection,
    PYTHON_RUNTIME_SECTION,
    PythonWorkerSection,
)

register(
    Character(
        name="general",
        description="通用任务执行：消息、记忆、网页、日程和文件操作。",
        spec=AgentSpec(
            tools=("execute_python", "read_file", "edit_file"),
            import_modules=(
                ya.PythonImport("yuubot.agent_fns.im", alias="im"),
                ya.PythonImport("yuubot.agent_fns.mem", alias="mem"),
                ya.PythonImport("yuubot.agent_fns.web", alias="web"),
                ya.PythonImport("yuubot.agent_fns.delegate", alias="delegate"),
            ),
            expand_functions=("*", "+im.*"),
            prompt_sections=(
                InlineSection("你是 yuubot 的通用任务 agent，以稳妥完成用户目标为先。"),
                PYTHON_RUNTIME_SECTION,
                ExpandFunctionsSection(),
                FileSection("shared/web_reading.md"),
                InlineSection(
                    "**大文档处理**：read_page 返回 full_size > 15000 时，"
                    "将 URL 和阅读目标用 delegate.delegate('general', task) 交给子 agent——"
                    "需要原文细节就让它清理（删废话/去噪后回传），只要结论就让它直接摘要。"
                ),
                PythonWorkerSection(),
            ),
            max_turns=32,
            max_context_tokens=128000,
            idle_agent_ttl_s=3600,
            preserve_python_session=True,
        ),
        bot_kind="master",
    )
)
