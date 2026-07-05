# Design: Python Kernel Workers

**实现顺序：8**（可观测性 [09-observability.md](09-observability.md) 的前置依赖）

## Scenario

```text
Researcher 让 Agent 跑 execute_python
  → daemon Harness 调 ExecutePythonTool
    → ActorKernelPool.acquire(conversation_id, workspace)
      → GlobalKernelLimiter 有 worker 槽才 lazy 启动；否则等待/超时
      → 复用当前 actor 的 idle ipykernel 或 lazy 启动（workspace/.venv/bin/python）
        → jupyter execute_request(code)
  → 同 turn 多次 execute_python：同一 leased worker，namespace 保留
  → run_loop 结束 harness.close() → release → RPC reset_or_recycle()
  → 同 actor 的下一条 user message：可复用 actor idle worker；developer notice（现有逻辑保留）

Agent 代码 segfault / os._exit（非 75）
  → 仅 kernel 子进程死亡；daemon 存活
  → execute_request 失败 → hard_recycle + 一次 transparent retry

Agent 调 restart_kernel（uv add 后换 import cache）
  → 立即杀：① 当前 conversation leased worker  ② 当前 actor pool 内全部 idle worker
  → 不杀同 actor 其他 conversation 的 busy leased worker；不影响其他 actor
  → 不预启；下次 acquire lazy 冷启动

idle 6h 无 lease
  → daemon SIGTERM 杀 idle worker；lazy 重建
```

## Concepts

```text
GlobalKernelLimiter = Runtime 组合成员；全局最多 max_workers=4 子进程，只限制资源数量
ActorKernelPool     = Actor 组合成员；只在同 actor 的 conversations 间复用 worker
KernelWorker        = ipykernel + AsyncKernelClient；idle | leased
KernelLease         = acquire 占 worker，服务一 workspace 的一轮 turn
worker_runtime      = 子进程内 reset / recycle 入口（.yuubot/worker_runtime.py）
reset_or_recycle    = turn 结束 RPC：%reset -sf + gc + RSS 自检（可能 exit 75）
purge_for_restart   = restart_kernel：杀 leased(conversation) + actor pool 全部 idle
RECYCLE_EXIT_CODE   = 75
```

**Worker 复用（预期内）**：warm worker 保留 import cache；仅同 actor 的不同 conversation 可复用 idle worker。

**全局简化边界**：全局层不理解 actor、conversation、workspace、env 或 restart 语义，只用 semaphore 限制活 kernel 子进程数量，避免耗尽服务器资源。

## Configuration

```yaml
python_kernels:
  max_workers: 4
  acquire_timeout_s: 30
  max_rss_bytes: 2147483648
  idle_ttl_s: 21600
```

## Worker runtime

子进程 ipykernel startup 加载 `.yuubot/worker_runtime.py`：

```py
def reset_worker_namespace(): ...
def maybe_recycle_worker(): ...  # RSS > max → os._exit(75)
def reset_or_recycle(): ...
```

| RPC | 时机 |
|-----|------|
| `reset_or_recycle` | harness.close() |
| `purge_for_restart` | daemon 侧；杀进程，非 RPC |

## reset_or_recycle vs restart_kernel

| | turn 结束 | restart_kernel |
|--|--|--|
| 目的 | 清 namespace、RSS 自检 | 换 import cache / 新依赖 |
| 动作 | RPC reset + maybe exit 75 | 杀当前 conversation leased + 当前 actor **全部 idle** |
| 重建 | 存活则留 idle 池 | lazy 冷启动 |

## Kernel 启动

| 字段 | 值 |
|------|-----|
| `python` | `<workspace>/.venv/bin/python`（禁止回落 daemon venv） |
| `cwd` | workspace root |
| `env` | integrations + `YUUBOT_*` + `YUUBOT_WORKER_MAX_RSS_BYTES` |
| `sys_path` | `.yuubot/facade` |

首次 acquire 缺 `.venv` → 若缺 workspace `pyproject.toml`，复制静态 `workspace.pyproject.toml` → `uv sync` → 再启 kernel。

## Runtime lifecycle

```text
startup → GlobalKernelLimiter(config)
enable_actor → ActorKernelPool(config, limiter)
run_loop → acquire(conversation_id) → jupyter execute_request × N → release(conversation_id) → reset_or_recycle
restart_kernel → actor_pool.purge_for_restart(conversation_id)
disable_actor / shutdown → actor_pool.shutdown()
```

## Implementation

| Phase | 内容 | 验收 |
|-------|------|------|
| **A** | KernelPool + ExecutePythonTool + Runtime | e2e execute_python / yb.tasks 通过 |
| **B** | workspace venv provision | import 来自 workspace venv |
| **C** | worker_runtime + max_workers | 两 workspace 并行 |
| **D** | restart_kernel + idle_ttl 6h | uv add → restart → 新 import |
| **E** | config.example.yaml | 默认值可 review |

## Out of Scope

- OTEL / yb.observe（09）
- kernel 跨 daemon restart 持久化

## Related

- [python-execution-env.md](../conventions/python-execution-env.md)
- [`src/yuubot/python/`](../../src/yuubot/python/)
- [09-observability.md](09-observability.md)
