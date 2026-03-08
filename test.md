# 测试记录（yuubot）

本文记录 yuubot 仓库中与 “live 测试 / 半 E2E 测试” 相关的运行方式与约束，便于本地复现。

## 测试入口

- Provider smoke（真实 LLM 可连通）：
  - tests/test_live_provider.py
- 半 E2E（真实 LLM 触发 execute_skill_cli 并执行 ybot）：
  - tests/test_live_half_e2e.py

## 默认行为（不会误跑）

两条 live 测试都需要显式开启：

- 只有当环境变量 `YUUBOT_LIVE_TESTS=1` 时才会执行
- 否则会 `pytest.skip(...)`

同时都带 `@pytest.mark.live`，可通过 `-m live` 过滤。

## 常用命令

- 运行全部测试（live 用例会 skip）：

```bash
uv run pytest
```

- 仅运行 live 测试：

```bash
YUUBOT_LIVE_TESTS=1 uv run pytest -m live
```

## Live 测试环境变量

- `YUUBOT_LIVE_TESTS=1`
  - 启用 live 测试

- `YUUBOT_TEST_YUUAGENTS_CONFIG=/abs/path/to/yuuagents.config.yaml`
  - 覆盖 yuuagents 配置文件路径
  - 未设置时默认使用仓库根目录的 `yuuagents.config.yaml`

- `YUUBOT_TEST_AGENT=main`
  - 选择 yuuagents agent 名称
  - 未设置时默认尝试 `main`，若不存在则取 `agents:` 中第一个

- `YUUBOT_TEST_MODEL=gpt-4o`
  - 覆盖模型名（优先级高于 agent/model 与 provider/default_model）

- Provider API Key
  - 从 `providers.<name>.api_key_env` 指定的环境变量中读取
  - 例如 `api_key_env: OPENAI_API_KEY` 则需设置 `OPENAI_API_KEY=...`

## Provider smoke 测试说明

- 读取 `yuuagents.config.yaml` 的 providers/agents 配置
- 根据 `api_type` 创建对应的 yuullm provider：
  - openai-chat-completion
  - openai-responses（如果 yuullm 版本提供 OpenAIResponsesProvider）
  - anthropic-messages
- 发送一次最小请求并断言返回内容包含 `OK_SMOKE`

## 半 E2E 测试说明

目标：验证 “LLM 产出工具调用 → execute_skill_cli 真正执行 ybot → 发送消息 HTTP 请求发出” 的整条链路。

核心点：

- 测试会启动一个本地 fake recorder_api（FastAPI+uvicorn）：
  - `GET /health`：健康检查
  - `POST /send_msg`：记录请求体到内存列表 `received`

- 测试会生成一个临时 `config.yaml`：
  - 仅设置 `daemon.recorder_api` 指向 fake recorder_api

- 测试构造一个 ybot 命令（必须被执行）：
  - `ybot --config <temp_config> im send '<msg_json>' --uid 123`
  - `<msg_json>` 里包含随机 marker（用于断言链路确实走通）

- Agent 侧只注册一个工具：`execute_skill_cli`
  - 由 LLM 生成工具调用来执行上述 ybot 命令
  - 执行成功后 ybot 会向 fake recorder_api 调用 `/send_msg`
  - 测试最终断言：
    - `received` 非空
    - `message_type == "private"`
    - 请求体里包含 marker

### 前置条件

- `ybot` 必须在 PATH 中
  - 若缺失，测试会 skip（提示需要安装 yuubot 使 entrypoint 可用）

### 注意事项

- execute_skill_cli 有安全限制（见 yuuagents 的实现）：
  - 禁止 shell 控制符（`;`, `|`, `&&` 等）
  - 禁止 `$()`/反引号等 shell expansion
  - 禁止 `bash/sh/python/node` 等程序
  - 因此测试命令必须是单条、无拼接、无重定向

