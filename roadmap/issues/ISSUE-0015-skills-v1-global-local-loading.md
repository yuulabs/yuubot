---
id: ISSUE-0015
slug: skills-v1-global-local-loading
status: approved
milestone: none
priority: P1
estimated_work_hours: unknown
---

# ISSUE-0015: Skills v1 Global/Local Loading

对齐 Charter Phase Goal 第 1 项（Agent Infra 扩展点稳定）。第一版 skills 不做
复杂 registry，只做两层文件系统目录和 actor 级加载选择。

## Problem

yuubot 需要一种低成本方式扩展 agent 能力。复杂 skill market / registry 暂时
不是必要条件；当前需要的是 actor 能稳定加载全局 skills 和 workspace 局部 skills，
并让用户在 Actor 页面进行最小管理。

## User-System Scenario

```text
全局 skills 位于 <data_dir>/skills/
局部 skills 位于 <actor_workspace>/.agents/skills/

研究员打开 Actor 页面
  → 选择 skill_scope:
      - local_only：只加载局部 skills
      - global_and_local：加载全局 + 局部 skills
  → 从全局 skills 导入一个 skill 到该 actor
  → 或删除该 actor 的某个局部 skill
  → 保存 Actor
    → 后续对话 prompt 只注入该 actor 实际加载到的 skills
```

## Scope

- Actor profile 增加 skill loading 选项：仅局部 / 全局+局部。
- 加载规则只认包含 `SKILL.md` 的 skill 目录。
- 同名冲突时局部 skill 覆盖全局 skill。
- yuubot 只支持安装全局 skills；安装路径为 `<data_dir>/skills/`。
- Actor 页面支持从全局导入局部 skill、删除局部 skill。

## Out of Scope

- 复杂 marketplace / registry / dependency solver。
- per-conversation skill 临时开关。
- 全局 skill 删除入口；可另排管理页。
- workspace web editor；见 ISSUE-0019。
