# Convention: Python Execution Environment

> 术语 + pseudocode 风格约定，用于讨论 yuubot agent 的 Python 执行环境
> （ISSUE-0001 起源）。后续 YuuDev / YuuCoder 沟通均用此术语。

## 1. 术语

| 术语 | 含义 | 不要混淆为 |
|------|------|-----------|
| **kernel session** | 一个活的 ipykernel 进程 + jupyter client channels。in-memory only，不跨 daemon 重启。 | 不是 "actor session"、不是 "LLM session" |
| **PythonKernelConfig** | 启 kernel 所需的静态配置（解释器路径、cwd、env、sys_path、startup_code）。yuubot 装配层在 `_tools.py:_python_tool_runtime` 组装。 | 不是 runtime state |
| **facade binding** | `ActorFacadeBinding`：actor workspace 的 facade 上下文快照（sys_path、startup_code、session_state、capabilities）。shared 装配产物，两条 kernel 路径都过它。 | 不是 kernel 本身；binding 是"如何拉起 kernel 的配方" |
| **venv provisioning** | 在 actor workspace 建 `.venv/` + `pyproject.toml` 并 `uv sync` 的动作。挂在 shared `FacadeWorkspace.bind_actor`，**不是**挂 kernel 启动逻辑。 | 不是"启动 kernel"；provision 完，kernel 还没起 |
| **execute_python 工具（路径 A）** | LLM agent 调的 `execute_python` 工具，`ExecutePythonTool`。kernel 在 `_get_session` lazy 拉起。ISSUE-0001 目标。 | 不是 echo test harness |
| **ExecutePythonSession（路径 B）** | `core/actors/impls/python_session.py` 的 `ExecutePythonSession`，只被 `EchoOnceActor`（example/test harness）用。ISSUE-0001 不改它。 | 不是 agent 路径 |
| **restart_kernel 工具** | agent 可调的工具。语义 = lazy：close 当前 kernel session 句柄 + 置空，下次 `execute_python` 用同一 `config.python`（=.venv/bin/python，路径不变）自动重生平新 kernel。**不**立即 `_start`。 | 不是 kernel 进程热重启 |
| **版本真值** | daemon `apps/yuubot/pyproject.toml` 声明的 pandas/numpy/matplotlib 版本。actor workspace `pyproject.toml` 的 pin **从这里复制**。workspace 不是独立版本源。 | 不是 workspace 自行声明 |

## 2. 两条 kernel 路径的职责边界（核心共识）

yuubot 有两条 ipykernel 路径，**职责不同，不可混用**：

```
路径 A（ISSUE-0001 目标）:
  LLM agent 调 execute_python 工具
    → ExecutePythonTool._get_session
      → config.python = <workspace>/.venv/bin/python
        → 真正的研究环境，isolation 在此生效

路径 B（test harness，不改）:
  EchoOnceActor（"example for testing actor/integration communication"）
    → python_sessions.create(binding)
      → ExecutePythonSession
        → 临时目录 test harness
```

**provisioning 挂 shared 层**（`FacadeWorkspace.bind_actor`），两边 workspace 都顺手建 .venv；但 **`config.python` 真正设值只在路径 A**。B 顺手有 venv 却不参与验收——是否用由 echo 自己决定。

## 3. Pseudocode 风格约定

讨论 yuubot 设计时按此风格写：

1. **先写 ought-to-be 数据流**，不写实现细节。用 `→` 串调用，每层一行。例：
   ```
   Actor start
     → python_sessions.bind_facade(binding)
       → FacadeWorkspace.bind_actor:
           mkdir actor_root
           write _facade_context.py
           ★ NEW: provision workspace venv (uv sync)
           return ActorFacadeBinding(..., venv_python=...)
   ```
2. **改动边界用表格**，按"模块 / 改动 / 性质"三列。不写代码，只写"在哪一层加什么"。
3. **凡加新动作用 `★ NEW:` 标记**，便于一眼看出新增面。
4. **场景驱动**：先假设功能已存在，写"用户代码 / agent 会怎么用"，再反推设计。例 ISSUE-0001 的 agent 数据分析场景。
5. **Out of Scope 显式列出**，对齐 Issue 的边界，防止 scope creep。
6. **验收锚点用行为可验证的句子**（"agent 跑 X → 看到Y"），不用"实现 X 方法"这种实现层描述。
7. **选 wiring/形状时倾向 W1**（复用已有 registry / 机制优先，少一个新概念），与 Ponytail 一致。引入新共享类型前先论证已有机制为何不够。

## 4. Kernel 隔离的不变量

- `ExecutePythonTool` 的 kernel **必须**跑在 `<workspace>/.venv/bin/python`，**绝不**回落 `sys.executable`（daemon venv）。回落即 bug。
- workspace `.venv` 由 `uv sync` 建，**不**通过 `pip install` 直接装包（绕过 uv cache 隔离）。
- `restart_kernel` 的语义是 **lazy**（close + null），**不**是 eager（立即 reboot）。与现有 crash-reset 路径一致。
- kernel 是 in-memory only，**不**跨 daemon 重启持久化。这是有意设计，不是缺陷。
