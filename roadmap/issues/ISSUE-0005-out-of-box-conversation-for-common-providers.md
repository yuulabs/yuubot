---
id: ISSUE-0005
slug: out-of-box-conversation-for-common-providers
status: implemented
milestone: M-02
priority: P1
estimated_work_hours: 4
cycle_hours: 29.6
implemented_by: HEAD
regression_test: manual: user confirmed M-02 complete
---

# ISSUE-0005: Out-of-Box Conversation for Common Providers

对齐 Charter Phase Goal 第 2 项（前端完成）。推进 M-02 stopping point 第 (1) 项。

## Problem

当前跑通一个 Conversation 需要研究员手工串四-五张表，链路太长：

```
LLMBackend (name, provider, base_url, api_key, default_model, models, pricing[])
  └→ Character         (system_prompt — 决定 agent 人格)
       └→ CapabilitySet (integration_capability_ids, workspace_path,
                         runtime_policy, resource_policy)
            └→ Actor    (把上面三个串起来 + default_model + budget)
                 └→ Conversation
```

recon 确认：OpenAI / DeepSeek 在协议端已就绪（DeepSeek 走 OpenAI-compatible
端点，`packages/yuullm/.../providers/openai.py` 已处理其 `reasoning_content`）。
UI 也有预设入口（`apps/yuubot/web/src/routes/providers.tsx` 的 8 个预设含
openai / deepseek）。真正的障碍有两块：

1. **链路无预设**：Character / CapabilitySet / Actor 当前全靠用户在各自 CRUD
   页手工建，没有任何预设/bundle 机制把它们串起来。用户必须先去四张表各建一条
   记录、互引 FK，才能跑通一个 Conversation。
2. **pricing 阻断**：所有 provider 的 `pricing: []` 为空（`config.yaml` 第
   70-91 行）。一旦 Actor 设了非零 USD budget，后端在三处守卫直接报 400
   拒绝：
   - `_validate_actor_pricing`（`runtime/daemon/commands/_codec.py:303-325`）
     — Actor 创建时即拒绝；
   - `_check_pricing_for_budget`（`core/assembly/_stage.py:124-147`）—
     stage 启动时拒绝；
   - `send_message` 的 `ConfigurationError`（`runtime/daemon/handlers.py:84-93`）
     — 发消息时拒绝，hint 让用户去配 pricing。
   `yuullm` 层虽有 `genai-prices` 库回退（`packages/yuullm/.../pricing.py`），
   但 yuubot 的 `core/costing.py` 严格只用本地 `PricingTable`，不走回退。
   "没有 pricing 就无法启动"的根即在此。

## User-System Scenario

```
研究员打开 Admin（空配置 / 首次使用）
  → 在「Provider」页点「New Backend」→ 选 OpenAI 或 DeepSeek 预设
  → 表单只问一项：API key
  → 研究员填入 API key → 保存
    → System 持久化 LLMBackendRecord（预设的 base_url / default_model /
      models.names 已自动填充），并写入该 provider 的内置 pricing entries
      （input_per_million / output_per_million per model）
    → System 自动 provision 默认 Character（预设 system_prompt）+
      默认 CapabilitySet（预设 integration capability ids / workspace_path /
      runtime_policy / resource_policy）+ 默认 Actor（串起上述三者 +
      default_model + 默认 budget）
      → 这三条默认记录在各自 CRUD 列表页对用户可见、可编辑、可删
        （与研究员手建的记录混在一起，无视觉/语义区分 — 走向 A：隐式预设）
  → 研究员进入该默认 Actor → 点「与该 Actor 对话」（见 ISSUE-0010 入口契约）
  → 发一条消息 → Agent 回复
    → 全程不碰 LLMBackend/Character/CapabilitySet/Actor 四张表的手工新建，
      不打开 pricing 页面查输入/输出单价

研究员之后想改预设内容（如 default Character 的 system_prompt）
  → 在 Character 页直接编辑那条默认记录 → 保存即生效
    （预设内容对用户可编辑，但系统初始已经给了一个能用的默认值）
```

## Scope (lazy: 契约只列用户观测点，不复述实现)

- **Provider 预设内置 pricing**：OpenAI / DeepSeek 的常用模型维护一份内置
  pricing list，随 LLMBackend 创建自动写入 `pricing.entries`。pricing list 的
  具体内容（哪些模型、单价多少）是运维数据，非用户契约；更新方式见
  ISSUE-0008。
- **链路默认预设**：系统持有默认 Character / CapabilitySet / Actor。用户只填
  API key 时系统自动 provision 这三条记录并把它们串起来。预设的具体内容
  （system_prompt 写什么、默认含哪些 integration capability、workspace_path
  指哪、runtime_policy/resource_policy 取什么）是设计决策，属 YuuDev 实现域，
  本 Issue 不约束。
- **预设可见可改**：默认 Character/CapabilitySet/Actor 在各自列表页对研究员可见、
  可编辑、可删 — 与手建记录无视觉/语义区分（走向 A）。

## Out of Scope

- 显式 bundle / "Quick Start" UI 概念（走向 B，本 Issue 不做 — A 更简，
  预设记录就混在列表里）。
- 自动发现 provider 可用模型列表（现已有 `GET /llm-backends/{id}/models`，
  非本 Issue 阻塞点）。
- 更多 provider（OpenRouter / Anthropic / aihubmix 已有预设但不在本 Issue
  "开箱即用"范围；新增 provider 见 ISSUE-0008）。
