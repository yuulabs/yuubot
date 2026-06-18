# yuubot 架构宪法

> 本文档定义 yuubot 的架构不变量、扩展模式和开发 SOP。
> 它是**强制执行**的约束——任何违反这些规则的实现都应被拒绝。
> AI 行为准则请见 `ai-guidelines.md`。

---

## 1. 项目定位

### yuubot 是什么

yuubot 是一个 **AI Agent 编排宿主**。它管理 Agent 的生命周期、消息路由、LLM 调用、预算控制，
并通过 Integration 插件系统为 Agent 暴露外部平台和工具的能力。

具体功能范围见 `checklist.md`。本文档聚焦于**边界约束**。

### yuubot 不是什么

以下需求**不属于本项目范围**。如果被提出，AI 必须拒绝并指向正确方向：

| # | 不做什么 | 为什么 | 正确方向 |
|---|---------|--------|---------|
| 1 | **不是编码工具** | 编码委托给 OpenCode/Codex 等专业 CLI | Agent 调用 OpenCode Integration |
| 2 | **不是 IM 产品** | Admin Conversation 是独立工作区，不走 IM 路径 | 配置 Telegram/Discord Integration |
| 3 | **不是通用 workflow 引擎** | 不做 DAG 编排、条件分支图、并行任务图 | Actor 循环是简单的 while-loop + mailbox |
| 4 | **不是 LLM 网关** | 不做请求级 LLM 路由、fallback 链、负载均衡 | 由 yuuagents 层处理 |
| 5 | **不做实时协作** | Actor 之间完全隔离，无共享状态、无群组对话 | 通过 yb.delegate 委托子任务 |
| 6 | **不是低代码平台** | 不做可视化 workflow 编辑、拖拽构建 | — |

---

## 2. 架构不变量

以下规则**在任何情况下都不能被违反**。违反即为 bug。

### 2.1 消息流向

```
外部消息：   Integration → Gateway.ingest() → Actor mailbox → Agent loop
系统消息：   system_ingress.send() → Actor mailbox → Agent loop
```

- System 消息（Admin、定时任务、bridge、后台任务通知）**绝不**走 Gateway glob 路由
- Gateway **只**处理外部 Integration 消息
- `system_ingress.send()` 是系统消息的**唯一入口**

### 2.2 分类边界

| 概念 | 分类 | 不属于 |
|------|------|--------|
| Admin Conversation | yuubot 系统能力 | 不是 Integration，不出现在插件列表、Gateway source 选择器、Actor 集成启用列表 |
| `yb` facade | 手写，暴露 yuubot 系统能力 | 不放入生成目录，不与 `yext` 合并 |
| `yext` facade | 生成，暴露 Integration capability | 不承载 yuubot 系统 helper |
| System 通道 | yuubot 运行时自身 | 不是 Integration，不被视为"插件" |

### 2.3 不可逾越的抽象层

以下入口点是各自领域的**唯一合法访问路径**。禁止绕过：

| 领域 | 唯一入口 | 禁止绕过方式 |
|------|---------|------------|
| Integration 生命周期 | `IntegrationCore` | 不直接操作 Integration 实例 |
| Actor 生命周期 | `ActorManager` | 不直接操作 Actor 实例 |
| 资源持久化 | `ResourceService` | 不直接操作 `ResourceRepository` |
| 外部消息投递 | `Gateway.ingest()` | 不直接向 Actor mailbox 投递外部消息 |
| 系统消息投递 | `system_ingress.send()` | 不通过 Gateway 发送系统消息 |

### 2.4 扩展点

新增能力时，必须使用以下扩展点，不得通过修改核心逻辑来"支持"新功能：

| 新增内容 | 扩展点 | 注册位置 |
|---------|--------|---------|
| 内置 Integration | 实现 `IntegrationFactory` + `IntegrationInstance` | `integrations/registry.py` → `default_integration_factories()` |
| 外部 Integration | 编写 `manifest.yaml` | Admin API 上传安装 |
| Actor 类型 | 实现 `Actor` + `ActorFactory` | `actors/registry.py` → `ActorFactoryRegistry` |
| Resource 类型 | 定义 `msgspec.Struct` → 注册 `ResourceTypeRegistry` | `daemon/commands/_app.py` |
| 系统能力（Agent 可见） | 扩展 `yb` facade | `core/facade/` 手写模块 |

---

## 3. yuubot 特定拒绝清单

以下需求模式在 yuubot 上下文中**必须被拒绝**：

| 触发模式 | 为什么危险 | 正确回应 |
|---------|-----------|---------|
| "让 Admin Conversation 接收 XX 平台消息" | Admin Conversation 不是 Integration，不走 Gateway 路由 | 为该 Actor 配置对应的 Integration |
| "给 Gateway 加条件分支配发" | Gateway 只做 glob 匹配路由，不做 workflow | 在 Actor 内部用 Agent 逻辑处理条件分发 |
| "把两个 Actor 的 mailbox 合并" | Actor 之间完全隔离 | 通过 yb.delegate 委托子任务 |
| "做一个请求级 LLM fallback 链" | yuubot 不是 LLM 网关 | 由 yuuagents 的 ProviderPool 处理 |
| "在 Gateway 里直接调用 Integration.response()" | Gateway 不持有 Integration 实例 | response() 是 Actor 通过 facade 调用的 |
| "给 integration response 加一个延迟发送/定时发送" | response 语义是即时回复 | 用后台任务（yb.tasks）+ response 组合 |
| "能不能直接往 Actor 的 mailbox 里塞一条消息" | 绕过 Gateway 破坏路由完整性 | 外部消息走 Gateway，系统消息走 system_ingress |

---

## 4. 开发 SOP

### 4.1 内置集成开发（6 步）

以 `echo` 集成为参考模板（`src/yuubot/core/integrations/impls/echo.py`）。

**步骤：**

1. **创建目录** — `src/yuubot/core/integrations/impls/<name>/`
2. **定义数据模型** — 使用 `msgspec.Struct` 定义 payload 类型（输入、输出、HTTP ingress）
3. **实现 IntegrationInstance** — 实现 `capabilities()`、`response(react, msg)`、`close()`
4. **实现 IntegrationFactory** — 实现 `create()`、`kind_info()`、`declare_capabilities()`、`routes()`（如需要）
5. **注册工厂** — 在 `integrations/registry.py` 的 `default_integration_factories()` 中添加
6. **编写测试** — 参考 `tests/test_integration_actor_echo.py` 的模式

**关键约束：**
- 每条 capability 对应一个 `CapabilitySpec`（typed input/output）
- `response()` 只负责"对该消息 ID 的原路回复"，不做额外逻辑
- HTTP routes 的 prefix 由 `IntegrationFactoryRegistry.collect_routes()` 自动添加，工厂只需返回相对路径

### 4.2 外部集成开发（5 步）

1. **编写 manifest.yaml** — 声明 capability specs、routes、facade specs、ingress specs
2. **实现集成进程** — HTTP server 处理 `/invoke`、`/response`、`/health` 端点
3. **打包为 zip** — 包含 manifest + 所有依赖
4. **通过 Admin API 上传安装** — Admin 负责解压、校验、启动子进程
5. **编写测试** — 参考 `tests/test_external_plugin.py`

### 4.3 新增 Resource 类型（4 步）

1. **定义 record** — 在 `resources/records.py` 中定义 `msgspec.Struct`
2. **生成 ORM model** — 在 `resources/store/models.py` 中通过 `resource_model()` 生成
3. **注册类型** — 在 `daemon/commands/_app.py` 的 `build_default_resource_type_registry()` 中注册
4. **编写测试** — 参考 `tests/test_daemon_commands.py`

### 4.4 新增 Actor 类型（4 步）

1. **实现 Actor 协议** — 在 `actors/impls/` 下实现 `Actor` 接口（`start/stop/handle_message/handle_resource_changed`）
2. **实现 ActorFactory** — 实现 `ActorFactory` 接口
3. **注册工厂** — 在 `actors/registry.py` 中注册
4. **编写测试** — 参考 `tests/test_actor_lifecycle.py`

### 4.5 扩展 yb facade（3 步）

1. **定义函数** — 在手写的 `yb` 模块中添加新的 system 能力函数
2. **注入到 Agent 可见范围** — 确保函数在 `yb/__init__.py` 中导出
3. **编写测试** — 用 LLM Prompt Visibility 测试验证 Agent 确实能看到新函数

**约束：**
- `yb` 中的函数**不得**通过 Integration capability schema 暴露
- `yb` 函数不能绕过 Actor 权限边界

---

## 5. 编码指令模板

Phase 1（设计讨论）结束后，AI 应生成以下格式的编码指令文件，
经用户确认后交给 Phase 2 编码子代理执行。

```markdown
# Task: {任务名称}

## 背景
{1-2 句话说明为什么要做这个改动}

## 参考上下文
- 架构约束：design/constitution.md
- AI 行为准则：design/ai-guidelines.md
- 参考实现：{类似的已实现模块路径}

## 实现清单

### 新建文件
- [ ] `{path}` — {说明}

### 修改文件
- [ ] `{path}` — {说明改动内容}

### 不改动的文件（明确排除）
- [ ] `{path}` — {为什么不改}

## 约束（从 constitution.md 派生）
- {具体约束 1}
- {具体约束 2}

## 验证
- [ ] `uv run pytest {test_path}` 通过
- [ ] `uv run ruff check src tests` 通过
- [ ] `uv run ty check` 通过
```

**编码子代理收到此文件后：**
- 严格按清单执行，不增加、不删除、不修改范围
- 遇到清单外问题 → 停止并报告，不自行解决
- 完成后运行验证命令，报告结果

---

## 6. 修改本文件的流程

本文档定义的是**硬约束**，不是建议。修改需要：

1. 在 `ai-guidelines.md` 的规则下进行充分讨论（Scenario-First 解释、疑虑公开）
2. 明确变更的理由和影响范围
3. 更新本文档后，同步检查 AGENTS.md 是否需要更新

禁止：因为"某个需求做不了"而临时放宽约束。
正确做法：讨论需求本身是否合理，或是否应该通过已有的扩展点实现。
