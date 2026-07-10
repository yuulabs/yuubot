1. 转向LiteLLM支持多供应商/Chatgpt订阅 & 同一模型不同供应商降级策略。
2. 添加ask_gemini/grok方法，利用它们内置的网络搜索能力半替代search。此处基于之前的供应商切换方案。 web search限额（AI老乱搜）。prompt强化。
3. delegate路径复查。Agent需要能够fan out多个sub来并行完成工作。
4. 增加常用场景SKILL说明（写次抛式HTML（外加美化），解释各种东西，不要全部写到一个index中） /Workspace管理Prompt说明。（尽量projects内聚，识别一次性的内容和未来也需要的内容）