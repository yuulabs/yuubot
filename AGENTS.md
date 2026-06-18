# AGENTS.md

## ⚠️ Constitutional Documents — READ FIRST

**These two files govern ALL AI behavior in this project.**
Read them before any code exploration or implementation.

- **`design/ai-guidelines.md`** — AI behavior rules: Scenario-First explanations, when to reject, how to communicate.
- **`design/constitution.md`** — Project-specific: what yuubot IS/NOT, architectural invariants, development SOPs.

When these documents conflict with a user request, **the documents win.**

## ⚠️ Direct Over Indirect — 先跑命令，不要空想

> 这是 `design/ai-guidelines.md` 第 5 节在此项目的具体落地。该节是权威来源。

**核心规则：当一份 PR、指令、或文档明确告诉你可以跑什么命令来验证时 —— 跑命令是你理解任务的第一动作，不是最后一步。** 代码阅读排在其后。

### 为什么这条规则存在

在一次 Docker PR 的 review 中，AI 只阅读了 Dockerfile、entrypoint、docker-compose.yml 的代码，认为"都写对了"，标记完成。随后人工执行 `docker compose up` —— **立刻炸出 6 个错误**。而这行命令 PR 文档自己就写清楚了（"The New Scenario" 一节），甚至不需要你看代码就能发现。

下面是那次事故暴露的 6 个 bug。注意：每一个都是 **运行命令立刻可见的**，没有一个需要 code review 才能发现：

1. **Admin UI 根本没构建** —— Dockerfile 缺少 frontend build stage；`curl :8781` 返回 404
2. **`web_dist_dir` 路径不匹配** —— 就算构建了，admin 进程在默认 `web/dist` 路径下也找不到资源
3. **`-c` 短标志 Click 不支持** —— entrypoint 用了短标志，容器启动立刻报 "No such option: -c" 退出
4. **缺失 `cryptography` 包** —— monorepo root 的 `uv.lock` 是 v1 的，v2 需要 `cryptography>=48.0.0`
5. **`uv.sources` workspace 冲突** —— `{ path = "../yuuagents" }` 在 workspace 内非法，必须 `{ workspace = true }`
6. **pnpm 10+ `approve-builds` 阻止 esbuild** —— 新版本 pnpm 对有 build script 的包要求显式批准

**这 6 个 bug 浪费了大量时间用于"理解代码文件"，而本质上一个命令就能全部暴露。**

### 按任务类型选择第一动作

不同任务类型有不同的最佳第一动作。**Feature request 是唯一应该直接从读代码开始的场景。**

| 任务类型 | 第一动作 | 说明 |
|---------|---------|------|
| **Review / 审阅** | `uv run ybot dev` 起临时实例 | 用临时数据库/API 起一个 dev 实例，直接 curl / 浏览器看接口行为对不对，确认真实行为后再针对差异看代码 |
| **Debug / 调试** | 跑复现命令 | 先尝试复现。**例外**：如果复现路径涉及 UI 操作无法直接用脚本表达（例如"点击某个按钮后出现异常"），则先读前端代码把操作步骤翻译成可脚本化的请求，再跑命令 |
| **Refactor / 重构** | 先读测试代码 | 确定哪些测试是已有的回归测试，这些测试在新结构下必须继续通过。理解测试覆盖的 contract 之后再动代码 |
| **Feature / 新功能** | 读代码 | **唯一直接读代码的场景。**先理解现有架构、扩展点、已有模块的边界，再设计新功能怎么接入 |

#### 通用场景速查

以下场景同样遵循"先验证，后讨论代码"：

| 场景 | ❌ 错误做法 | ✅ 正确做法 |
|------|-----------|-----------|
| PR 文档给了使用命令 | 读代码，推演逻辑 | **先跑 PR 给的命令**，看到错误再反查代码 |
| 怀疑有 bug | 自己想 5000 tokens 推理 bug 在哪 | 插 print / 写最小复现脚本，跑一遍看输出 |
| 不确定配置是否生效 | 读 config 解析代码，推演变量替换 | 启动服务，curl endpoint，看实际响应 |
| 不确定 API 行为 | 猜/查文档/讨论 | 写一行调用的脚本跑一遍 |

### Docker 具体操作

当涉及 Docker 文件变更时，以下是标准验证流程（命令来自 PR 文档 — 永远先跑这些，再读代码）：

```bash
# 1. Build the image
docker compose -f yuubot-v2/docker-compose.yml build

# 2. Start the container with real secrets
YUU_SECRET_KEY=$(python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())") \
YUU_ADMIN_SECRET=admin-secret \
YUU_DAEMON_SECRET=daemon-secret \
docker compose -f yuubot-v2/docker-compose.yml up -d

# 3. Verify both services respond
curl -sf http://127.0.0.1:8780/healthz   # daemon
curl -sf http://127.0.0.1:8781/healthz   # admin
curl -sf http://127.0.0.1:8781/ | head -5  # admin UI HTML

# 4. Check container logs for errors
docker compose -f yuubot-v2/docker-compose.yml logs --tail 30

# 5. Clean up
docker compose -f yuubot-v2/docker-compose.yml down
```

任何一步失败 → 改动未完成。不要仅凭代码阅读标记为 verified。

### 真实 API E2E — Push Back 规则

**真实 API key 跑完整对话链路是最强的回归测试。** 但每次修改都跑会烧钱，所以只在特定时机执行：

| 场景 | 跑真实 API E2E？ | 理由 |
|------|-----------------|------|
| Feature request 收尾（合并前） | ✅ **必须** | fake provider (PromptCapture) 掩盖了真实 provider 才会暴露的 bug。Docker PR 的 `StreamOptions.model` 泄漏就是例子 — 87 个 pytest 全绿，但真实验证立刻炸 |
| Bug fix / Refactor | ❌ | pytest 回归测试足够。不应破坏已有路径 |
| 日常 commit | ❌ | 烧钱且没必要 |

**Golden Test 定义**：由**人类通过 Admin UI** 手动走通 `LLM API key → Character → Actor → Conversation → Agent 回复` 完整链路。API 脚本不能替代 — 脚本会绕过前端表单校验，已有案例：Docker PR 的 API E2E 通过但 Admin UI 炸在 Actor 表单字段名不匹配。Golden Test 跑通后在 PR 文档中记录。

**Push Back 规则**（AI 做 review 时必须执行）：

> 当 review 一个 Feature PR，且 PR 文档（`prs/*.md`）的 Verification 节中**没有真实 API E2E 记录**时，reviewer **必须 Push Back**：
>
> > ⚠️ 该 PR 涉及 LLM → Agent 对话链路，未找到真实 API E2E 验证记录。  
> > 请用临时 API key 跑通完整链路：`LLM Backend → Character → Actor → Conversation → Agent 回复`  
> > 然后将结果记录在 PR 文档的 Verification 节中。
>
> 其他问题（代码风格、架构、逻辑）正常 review，但 **真实 E2E 缺失是 blocking 条件**。

**测试记录格式**（写在 PR 文档 Verification 节）：

```
✅ 真实 API E2E — deepseek-v4-flash @ api.deepseek.com
   LLM Backend → Character → Actor → Conversation → Agent 回复: "E2E test passed"
   总消息: 2 (user: 1, assistant: 1), 耗时 ~3s
   测试脚本: .tmp/landing-plan/docker/e2e_test.py
```

## Commands

```bash
uv run ruff check src tests
uv run ty check
uv run pytest
uv run ybot check
uv run ybot daemon
uv run ybot admin
uv run ybot dev
```

Python is 3.14. Type checking uses `ty`.

## Navigation Map

### Product Direction

- `design/checklist.md` — current hand-written product and architecture checklist.
- `design/ai-guidelines.md` — (see ⚠️ section above) AI behavior rules and communication standards.
- `design/constitution.md` — (see ⚠️ section above) architectural invariants, scope boundaries, and SOPs.
- `demo/` — static Web Admin UI exploration pages.
- `config.example.yaml` — bootstrap config shape for local runtime startup.

### Entrypoints

- `src/yuubot/cli.py` — `ybot` command group: config validation, daemon/admin processes, dev launcher, archive import/export.
- `src/yuubot/runtime/daemon/app.py` — daemon assembly, ASGI routes, service lifecycle, resource refresh, plugin ingest.
- `src/yuubot/runtime/admin/app.py` — admin ASGI routes, integration kind metadata, secret reveal, plugin install/uninstall, trace UI mount.
- `src/yuubot/runtime/process.py` — local service host, uvicorn server wrapper, trace service, shared resource opening.
- `src/yuubot/runtime/archive.py` — data directory archive import/export.
- `src/yuubot/runtime/plugin_manager.py` — external integration plugin manifest, install, subprocess lifecycle, HTTP facade calls.

### Bootstrap And Layout

- `src/yuubot/bootstrap/config.py` — typed bootstrap config, env substitution, path expansion, startup validation.
- `src/yuubot/bootstrap/layout.py` — canonical `data_dir` layout for database, traces, logs, facades, plugins, integrations, workspaces, and skills.

### Resource Storage

- `src/yuubot/resources/records.py` — persisted msgspec resource records.
- `src/yuubot/resources/store/models.py` — Tortoise ORM models generated from resource records.
- `src/yuubot/resources/repository.py` — CRUD boundary with table-level resource change events.
- `src/yuubot/resources/service.py` — domain operations for resource CRUD and runtime reconciliation.
- `src/yuubot/resources/registry.py` — resource type registry and event-driven refresh dispatcher.
- `src/yuubot/resources/orm.py` — record/ORM conversion, references, encrypted secret handling.
- `src/yuubot/core/secrets.py` — `Secret` wrapper, master key validation, AES-GCM codec, schema hooks, config secret wrapping.

### Messages And Routing

- `src/yuubot/core/messages.py` — `IncomingMessage`, `MessageSource`, system source helpers.
- `src/yuubot/core/gateway.py` — integration ingress, actor mailboxes, message fanout.
- `src/yuubot/core/routing.py` — source glob route projection and resolution.
- `src/yuubot/core/bindings.py` — actor resource binding loader.

### Integrations

- `src/yuubot/core/integrations/contracts.py` — integration factory/instance/storage protocols and admin-facing kind metadata.
- `src/yuubot/core/integrations/registry.py` — builtin and external integration factory registry and route collection.
- `src/yuubot/core/integrations/core.py` — integration enable/disable/reconcile, capability indexing, actor capability authorization, storage cleanup.
- `src/yuubot/core/integrations/context.py` — invocation context for actor-visible capability calls.
- `src/yuubot/core/integrations/impls/echo.py` — loopback integration used by runtime and HTTP E2E tests.
- `src/yuubot/core/integrations/impls/echo_routes.py` — HTTP ingress routes for the echo integration.

### Actors And Agents

- `src/yuubot/core/actors/contracts.py` — actor and actor factory protocols.
- `src/yuubot/core/actors/manager.py` — actor lifecycle, reconciliation, mailbox ownership.
- `src/yuubot/core/actors/registry.py` — actor factory registry.
- `src/yuubot/core/actors/workspace.py` — actor workspace path resolution.
- `src/yuubot/core/actors/impls/simple_loop.py` — default yuuagents-backed actor runtime.
- `src/yuubot/core/actors/impls/python_session.py` — generated facade package lifecycle and execute_python bridge.
- `src/yuubot/core/actors/impls/echo.py` — test actor for integration/facade plumbing.
- `src/yuubot/core/assembly.py` — yuuagents `Stage` and `AgentDefinition` assembly from an `ActorBinding`.
- `src/yuubot/core/llm.py` — bound LLM data shape.
- `src/yuubot/core/costing.py` — pricing-aware LLM client wrapper.
- `src/yuubot/core/observability.py` — trace context registration.

### Actor Facade

- `src/yuubot/core/facade/codegen.py` — generated Python facade source for actor capability access.
- `src/yuubot/core/facade/bridge.py` — facade RPC server and background task mailbox messages.
- `src/yuubot/core/facade/client.py` — generated facade client request helpers.
- `src/yuubot/core/facade/workspace.py` — generated package paths and startup code.

### Daemon APIs

- `src/yuubot/runtime/daemon/commands.py` — `/api/resources` CRUD and lifecycle HTTP handlers.
- `src/yuubot/runtime/daemon/validators.py` — resource reference and deletion validation.
- `src/yuubot/runtime/http_utils.py` — shared JSON error response helper.

### Tests

- `tests/__init__.py` — makes `tests` a proper Python package (required for relative imports across subdirectories).
- `tests/test_daemon_commands.py` — resource API CRUD and validation.
- `tests/test_daemon_refresh_api.py` — admin-triggered daemon refresh.
- `tests/test_actor_lifecycle.py` — actor start/stop/reconcile behavior.
- `tests/test_actor_workspace.py` — workspace path behavior.
- `tests/test_integration_actor_echo.py` — integration-to-actor facade path.
- `tests/test_echo_http_e2e.py` — HTTP ingress round trip.
- `tests/test_route_bindings.py` — Gateway route matching.
- `tests/test_trace_cost_e2e.py` — trace and pricing plumbing.
- `tests/test_external_plugin.py` — external plugin manifest and subprocess integration.
- `tests/test_archive_export_import.py` — data archive import/export.

#### LLM Prompt Visibility Tests

These tests verify that the LLM sees the right information in its prompt — tool specs, system prompt content, capability imports — by running **scripted scenarios** where a fake LLM records every invocation while the test asserts on the captured content.

```
tests/llm_prompt/
├── scenario.py             # Core types: PromptScenario, ScenarioStep, ToolCall, PromptSnapshot, assertion builders
├── framework.py            # PromptCapture (scripted LLM provider) + ScenarioRunner
├── scenarios/              # One file per feature being tested
│   └── execute_python_visibility.py
└── test_all_scenarios.py   # Parameterized entry: add new scenarios to ALL_SCENARIOS list
```

**Pattern** — Each scenario declares an **Assert → Action → Assert → …** chain:

- **Assert** checks the current prompt snapshot (e.g. `AssertToolExists("execute_python")`).
- **Action** simulates the LLM calling a tool (e.g. `ToolCall("execute_python", {"code": "..."})`). The daemon processes it, appends the result to the LLM history, and the next assert checks the new state.

Format a new scenario:

```python
class MyFeatureVisibility(PromptScenario):
    @property
    def name(self) -> str: ...

    async def setup(self, ctx: ScenarioContext) -> None:
        # start daemon, insert resources, start actor, send trigger message
        ctx.daemon = daemon  # runner stops it after assertions

    def steps(self) -> list[ScenarioStep]:
        return [
            ScenarioStep(assertion=AssertToolExists("execute_python")),
            ScenarioStep(
                assertion=AssertHistoryContains("expected text"),
                action=ToolCall("execute_python", {"code": "print(...)"}),
            ),
        ]
```

Then add `MyFeatureVisibility()` to `ALL_SCENARIOS` in `test_all_scenarios.py` — the test runner picks it up automatically.

**Run**: `uv run pytest tests/llm_prompt/`
