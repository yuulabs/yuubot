---
name: write-tests
description: the guidelines for writing E2E yuubot tests
---

# 测试应当尽可能E2E，不要测试中间细节。

E2E和中间细节的含义如下：
1. User-facing details. 即命令系统和预期响应，这属于用户在qq侧能看到什么。检测方法为mock onebot api确认是否真的收到这些消息。
2. LLM-facing prompts. 即LLM api真正收到了什么内容。因为LLM具有概率性且为外部依赖，我们无法在测试时准确测试其返回，只能测试它收到的内容是否符合预期（并相信如果符合预期那么LLM会正确回答）。
中间不要Mock，除非涉及到外部网络依赖（例如web 搜索）。

编写E2E测试用例涵盖以下方面：
1. 命令系统。命令系统分为yllm和其他。对于确定性命令系统，检测qq端返回值是否符合设计预期。yllm（涉及到LLM交互）见下
2. 对于llm交互，测试完整的交互流程，并且考虑多种因素（如超时，错误，etc）。一个例子如下：

1) 建立一个模拟的私聊 2) 用户发送消息 （此处检测llm api是否得到了预期渲染的消息（包括system prompt是否符合预期。tool spec是否符合预期，expand functions是否符合预期，etc））3) mock llm的细节行为（即yuullm stream返回值）。分别测试 1. llm单纯文字回复 2. llm思考再回复 3. llm思考，调用工具，思考，调用工具，再回复。注意，在第三种情况中，需要编写多个场景，测试多个工具的可用性（例如，mock llm返回了一个execute_python调用，内部是访问导入的模块，TASKS，SESSION STATE等通用状态；mock llm返回对于特定工具的调用，例如web read pages, 测试特定工具的可用性，etc）. 

对于具有上下文依赖的命令（例如yclose, yping），也需要先运行一些上下文再断言测试。
内部RPC不属于mock范畴。处理它们的方式类似于in-memory db，需要启动临时的测试daemon. 
