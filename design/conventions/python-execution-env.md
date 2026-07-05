# Convention: Python Execution Environment

> 术语 + pseudocode 风格约定。权威 wire doc：[08-python-kernel.md](../services/08-python-kernel.md)。

## 1. 术语

| 术语 | 含义 | 不要混淆为 |
|------|------|-----------|
| **kernel worker** | 一个活的 ipykernel 子进程 + daemon 侧 jupyter client。in-memory only，不跨 daemon 重启。 | 不是 "actor session"、不是 "LLM session" |
| **GlobalKernelLimiter** | Runtime 组合成员；最多 `max_workers` 个子进程；只限制全局资源数量 | 不持有 workspace/env/restart 语义 |
| **ActorKernelPool** | Actor 组合成员；同 actor conversations 间复用 idle worker；acquire / release / purge_for_restart | 不是 execute_python 工具本身 |
| **PythonKernelConfig** | 启 kernel 的静态配置（python 路径、cwd、env、facade sys.path）。由 `ExecutePythonTool` factory 组装。 | 不是 runtime state |
| **worker_runtime** | 子进程内 reset / recycle 入口（`.yuubot/worker_runtime.py`）：`reset_or_recycle` | 不是 daemon 模块 |
| **execute_python** | LLM 调的 `ExecutePythonTool`；经 ActorKernelPool lazy 启 / 复用 worker | — |
| **restart_kernel** | agent 工具。语义 = **立即**杀当前 conversation leased worker + 当前 actor 的全部 idle worker；lazy 冷启动。用于 `uv add` 后换 import cache。 | 不是 `%reset`；不是 lazy 置空句柄 |
| **版本真值** | 根目录 `pyproject.toml` 声明的包版本；workspace `pyproject.toml` 从这里复制 pin | 不是 workspace 自行声明 |

## 2. 数据流

```text
execute_python
  → ActorKernelPool.acquire(conversation_id, workspace)
    → 复用当前 actor 的 idle worker 或经 GlobalKernelLimiter lazy 启动 <workspace>/.venv/bin/python
      → jupyter execute_request(code)
  → harness.close() → release → reset_or_recycle()

restart_kernel
  → ActorKernelPool.purge_for_restart(conversation_id)
    → 杀当前 conversation leased worker + 当前 actor 的全部 idle worker
    → 下次 execute_python lazy 冷启动
```

## 3. Kernel 隔离的不变量

- kernel **必须**跑在 `<workspace>/.venv/bin/python`，**绝不**回落 `sys.executable`（daemon venv）。
- workspace `.venv` 由 `uv sync` 建，**不**通过 `pip install` 绕过 uv。
- turn 内 namespace 保留；turn 结束 `reset_or_recycle`；同 actor 后续 conversation 可复用 idle worker；换依赖用 `restart_kernel` hard kill。
- kernel 不跨 daemon restart 持久化。

## 4. Pseudocode 风格约定

1. 先写 ought-to-be 数据流，用 `→` 串调用。
2. 改动边界用表格（模块 / 改动 / 性质）。
3. 新动作用 `★ NEW:` 标记。
4. 验收锚点用行为可验证的句子。
