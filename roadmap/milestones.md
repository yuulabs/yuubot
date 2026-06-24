# yuubot Milestones

> 技术停止点的 chron 列表。top → bottom 读 `completed → WIP → draft`。
> 同时至多一个 `WIP`。`completed` 的判据：其 P1 Issue 全部 `implemented`。

## M-01 — Agent Runs Right + IM Reaches Actor

- **Status**: `WIP`
- **Builds**: 对齐 Charter Phase Goal 第 1 项（Agent Infra 功能正常）+ 第 3 项
  （集成框架稳定起点）。研究员可以：启动 yuubot → 在 Admin Conversation 让
  Agent 在 workspace 里跑 Python（venv 隔离、预装数据科学包、自检可用包、可装新包、
  可重启 kernel）→ 通过 QQ 或 Telegram 给 Actor 发消息 → Actor 回复。
- **Stopping point**: 至少一个 IM 发消息 → Actor 回复。两个 IM 都做完是 sprint 内延伸，
  不单列验收门槛。
- **Links**: ISSUE-0001, ISSUE-0002（核心 P1）；ISSUE-0003, ISSUE-0004（plumbing P2）。
