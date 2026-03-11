# 测试记录（yuubot）

本文描述 yuubot 当前测试策略，以及本地运行方式。

## 测试分层

### 仓内默认测试

`tests/flows/` 里的测试是 **需求驱动的行为测试**。它们验证：

- 哪些消息会被 bot 接受或忽略
- 权限是否符合设计
- session 是否被创建、延续、关闭
- `execute_skill_cli -> ybot im send -> recorder_api/send_msg` 这类用户可感知链路是否真的发生
- soft timeout 是否能及时给出 handle，并允许后续轮询进度/结果

这些测试可以默认运行：

```bash
uv run pytest tests/flows
```

### 不在这里测的内容

以下内容不应放在 yuubot 的 flow 测试里：

- yuuagents 的 `OutputBuffer`
- `RunningToolRegistry`
- `ToolsContext.gather()` 的内部 soft-timeout 语义

这些属于上游库实现细节，应由对应仓库负责。

## 当前约束

- Recorder API 使用本地 mock，只替换 HTTP 边界
- LLM 输出使用 mock，只替换 provider stream
- 如果某条测试声称“工具链真的执行了”，必须断言 recorder API 确实收到了 `send_msg`
- soft timeout 测试允许在 `Dispatcher / AgentRunner` 这一层注入自定义慢工具，但断言仍然面向 handle、进度文本、最终结果这些产品语义

## 常用命令

```bash
uv run pytest tests/flows -q
```
