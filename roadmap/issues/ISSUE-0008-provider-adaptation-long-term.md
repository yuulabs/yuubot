---
id: ISSUE-0008
slug: provider-adaptation-long-term
status: approved
milestone: none
priority: P3
estimated_work_hours: unknown
---

# ISSUE-0008: Provider Adaptation (Long-Term)

长期 plumbing。对齐 Charter Phase Goal 第 3 项（集成框架稳定 — provider 侧）。
不绑任何 milestone：供应商总是在增加，ongoing，无硬停止点。

## Problem

新增 provider 时需要补两块：

1. **协议端**（`packages/yuullm/.../providers/`）：通常 OpenAI-compatible 的
   provider 已可复用 `OpenAIChatCompletionProvider`（如 DeepSeek），只需在
   预设里指 `runtimeProviderKey: "openai"` + `providerName` + `base_url`。
   非 OpenAI 兼容的（如 Anthropic 原生 Messages API）需独立 provider 文件。
2. **pricing list**：每个 provider 的每个模型的 `input_per_million` /
   `output_per_million`。ISSUE-0005 会先用 OpenAI / DeepSeek 的内置 pricing
   list 验证"开箱即用"路径；后续 provider 的 pricing 按需补入同一机制。

## User-System Scenario

```
研究员（或未来的某个时间点）想接入一个新 provider
  → 在「Provider」页选对应预设（如已接线）或 YuuDev 补协议端 + 预设
  → 填 API key → maintenance 该 provider 的 pricing list（输入/输出单价 per model）
    → 该 provider 进入"开箱即用"能力面（与 OpenAI/DeepSeek 同列）
```

## Scope

- 按需新增 provider 预设 + protocol（若非 OpenAI 兼容）+ pricing list。
- 复用 ISSUE-0005 建立的"内置 pricing + 自动 provision 链路"机制。

## Out of Scope

- 自动从 provider 官网抓 pricing — pricing 是运维数据，手工维护即可。
- 统一的 provider 适配测试矩阵 — 见 YuuDev 实现，非用户契约。
