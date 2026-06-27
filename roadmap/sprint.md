# yuubot Sprint

> 当前 sprint 的具体、可观测输出。每个 milestone 必须推进 `charter.md` 的 Phase Goal。
> Issue 文件是场景契约的唯一真相源 — 本文只引用 ISSUE-ID，不复述场景。

## Current Milestone

**M-03 "Grounded Research + Skills"**（见 `milestones.md`，WIP）。

对齐：`charter.md` Phase Goal 第 1 项（Agent Infra 扩展点稳定）与第 5 项
（研究员获得可解释分析）。目标是尽快赶出研究员自用核心功能：上网查实时消息、
上传/阅读资料、通过 skills 接入特定平台或信息收集策略。

> 上一轮 milestone（M-02）已结清：Admin UX 与后端能力面对齐，ISSUE-0005 /
> ISSUE-0007 implemented，ISSUE-0006 deprecated，结构收敛项 ISSUE-0010 /
> ISSUE-0011 implemented。

本轮 sprint 以可用闭环优先，不把部署、定时任务、IM 或 source archive/wiki
平台化能力作为前置。

## Blockers

_无。_

## Frozen Scope

落地顺序按信息获取闭环组织：先让 Actor 能加载 skills，再提供通用 web search/read，
最后补文件上传/资料进入 workspace。这样第三方平台 skills 和通用网页读取都能服务
同一个研究对话入口。

| Order | Issue | Pri | Est.h | 角色 |
|-------|-------|-----|-------|------|
| 1 | ISSUE-0015 | P1 | unknown | Skills v1：全局/局部 skill 加载与 Actor 选择 |
| 2 | ISSUE-0013 | P1 | unknown | Web search/read + citation metadata |
| 3 | ISSUE-0012 | P1 | unknown | 文件/PDF 上传到 workspace，Agent prompt 可见 |

验收闭环：研究员能配置带 skills 的 Actor，上传论文/PDF/资料，询问实时问题或文档
问题；Agent 能从 workspace 文件和 web/skill 来源收集信息，并在回答中保留引用。

## Trade-offs

- **不做 ISSUE-0014**（source archive/wiki reuse）— 先不平台化长期记忆。学习场景
  暂时由专门 Learning Actor 在 workspace `AGENTS.md` 跟踪进度。
- **不做 ISSUE-0016/0017**（定时/每日情报）— 上网和 skill 收集能力先按需可用，
  定时自唤醒等 M-06。
- **不做 ISSUE-0002/0003/0004**（IM）— IM 是外部触点增强，不阻塞 Admin 自用研究
  assistant。

## Velocity

最近已结清的 M-02 工作：

| Issue | Est.h | Act.h | Ratio | Note |
|-------|-------|-------|-------|------|
| ISSUE-0005 | 4 | cycle 29.6h | - | 无 timesheet；仅记录 wall-clock cycle |
| ISSUE-0007 | 8 | cycle 22.9h | - | 无 timesheet；视觉基线工作受 demo/迭代影响 |
| ISSUE-0010 | 4 | net 0.2h | 0.05 | 小范围结构收敛，估算样本不代表常规 feature |
| ISSUE-0011 | 1 | cycle 0.7h | - | 小范围结构收敛 |

M-03 的三个核心 Issue 仍为 `estimated_work_hours: unknown`，启动前需要按实际方案补估。
