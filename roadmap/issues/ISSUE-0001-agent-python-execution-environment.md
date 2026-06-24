---
id: ISSUE-0001
slug: agent-python-execution-environment
status: in-progress
milestone: M-01
priority: P1
estimated_work_hours: 7
---

# ISSUE-0001: Agent Python Execution Environment

对齐 Charter Phase Goal 第 1 项（Agent Infra 功能正常 + 扩展点稳定）。

## Problem

当前 agent 的 ipykernel 跑在 daemon 自己的 venv 里（`session.py:218`
`python = config.python or sys.executable`，而 yuubot 从不设 `config.python`）。
后果：

- agent `import pandas` ↔ 导入 daemon 的 pandas，无隔离。
- agent 跑 `uv pip install` ↔ 污染 daemon 自己的环境。
- 无 `restart_kernel` 工具：agent 无法主动刷新自己的 kernel session。
- prompt 对 env / uv / 依赖管理只字未提 — agent 不知自己能 import 什么、
  怎么装包、怎么刷新 kernel。

## User-System Scenario

```
Researcher 在 Admin Conversation 让 Agent 做数据分析
  → Agent 调 execute_python 跑代码
    → System 在 actor workspace 的 .venv/bin/python 启动 ipykernel
      → pandas / numpy / matplotlib 已在 bootstrap 时 import 好
        (pd / np / plt 可直接用，版本由 daemon 锁定，uv cache 复用)
      → Agent 写代码、跑实验、出图
        → Agent 不确定自己能 import 什么
          → Agent 调 bash 跑 `uv pip list`（prompt 指示此路径）
            → System 返回 .venv 当前已装包列表
              → Agent 看到列表，决定是否需要装新包
        → Agent 发现缺某个包
          → Agent 调 bash 在 workspace 跑 `uv add <pkg>`（prompt 指示此路径）
            → .venv 更新，uv 复用 daemon 锁定版本的 cache
              → Agent 调 restart_kernel 工具
                → System 关闭旧 kernel，用更新后的 .venv 启新 kernel
                  → Agent 重新 import 新包，继续工作
  → Researcher 看到正确结果、图、数据
  → daemon 重启后：
      workspace 文件在，.venv 在，kernel 状态丢失（符合预期，不持久化 kernel）
```

## Prompt Transparency Principle 落点

System prompt 要明确告诉 agent（不是让 agent 猜）：

1. **能 import 什么** — `pd` / `np` / `plt` 已预装，可直接使用。
2. **怎么自检环境** — 不确定时跑 `uv pip list` 查看当前 .venv 装了什么。
3. **怎么装新包** — `uv add <pkg>` 在 workspace，不能 `pip install`
   （会绕过 uv 的 cache 隔离）。
4. **怎么刷新 kernel** — 装/卸包后调 `restart_kernel` 工具，让新环境生效。

## Scope (lazy: 列插入点，不复述实现)

- `PythonKernelConfig.python` 设为 `<workspace>/.venv/bin/python`
  （插入点：`_tools.py:108-122` `_python_tool_runtime`）。
- actor start 时 provision workspace `.venv` + `pyproject.toml`
  （插入点：`python_session.py:89-101` `ActorPythonSessionFactory.create` /
  `facade/workspace.py:49-100` `bind_actor`）。
- `FACADE_IMPORTS` 加 pandas/numpy/matplotlib（带 alias）
  （插入点：`_constants.py:20-27`）。
- daemon 依赖锁定 pandas/numpy/matplotlib 版本
  （插入点：`packages/yuubot/pyproject.toml` 或 apps/yuubot deps）。
- 新 `restart_kernel` tool factory + 注册
  （插入点：`tools/impls/` 新文件 + `registry.py:68-84`）。
- `PythonSession.restart()` 方法（close + null + force re-start）
  （插入点：`packages/yuuagents/.../python/session.py`）。
- system prompt 加 env 管理指引段落
  （插入点：`_prompt.py:118-154` `_render_system_instructions` /
  `_workspace_bullets`）。

## Out of Scope

- per-workspace venv 的 share/cache 策略调优（首次 uv sync 慢可接受）。
- kernel 跨 daemon restart 持久化（明确不做，kernel 是 in-memory only）。
- 通用 python package 沙箱 / 权限限制（MVP 信任 agent，不引入 RestrictedPython）。
