# yuubot Product Constitution

> 产品宪法:yuubot 要达成什么、到什么程度算 done。
> 架构宪法(不变量/扩展点/SOP)见 `apps/yuubot/design/constitution.md`。
> 本文管"造什么、造到哪停",架构宪法管"怎么造"。

## Long-Term Vision

管理大数据是 yuubot 的主旋律。人类面对大量事务会遗忘,AI(磁盘)不会。
yuubot 是研究员的数据搭档,不替代人类思考。

三大支柱:

1. **数据集散中心** — 实验数据、项目进度管理数据、个人事务管理数据,都由 yuubot 管理。
2. **数据分析中心** — AI 是天然的非结构化大数据分析员。
3. **触须很长** — 例如 K8S cluster 分配一个 GPU node,可自主加入 yuubot 网络,
   让 bot 在上面实验、收集数据。

诚实的开发外包给人类或专业开发工具(OpenCode/Codex 等)。
yuubot 不做严肃开发,专注 scale、数据收集、分析与 scenario 解释。

## Prompt Transparency Principle

对 LLM 驱动的系统,"LLM 能看到什么"是产品契约,不是实现细节。
机械系统看不到的事件,对用户无影响;LLM 系统看不到的事件,会让 agent
产生与事实不符的行为,用户能观测到这个差异(LLM 不知 → 给研究员的报告
里漏掉该事实)。因此:凡影响 agent 行为的系统事件,其 prompt 可见性是
宪法级约束,必须在规划阶段明确,不允许"实现时再看"。

## Current Goal

实现一套面向研究员的数据管理 fabric,使 yuubot 能 (1) ingest 并记住
实验 / 项目进度 / 个人事务数据,(2) 通过 agent 分析非结构化数据,
(3) 自动征用算力(K8S GPU node 自主入网)运行实验并收集结果。

项目 done 当以下 5 项全部达成:

1. **Agent Infra 完成** — yuubot 自建的 runtime,目的是实现 Programming
   ToolCall(组合式调用比 bash/工具调用在大数据分析下高效很多)。done 当:
   功能正常 + 扩展点稳定 + 良好的可观测性。当前进度不错。
2. **前端完成** — Admin UI 跟住后端进度即可。
3. **集成框架完成** — 有一套稳定的集成接入方案(含第三方集成)。yuubot 进入
   长期维护期后慢慢加各种集成;done 当框架本身稳定。
4. **YNetwork 完成** — 任何机器通过 ynetwork client 加入网络后,yuubot 都能
   管理它。LLM 能看到机器的加入、变更、踢出事件(机器掉线 → agent 知道 →
   能告诉研究员)。可见性为产品契约;容错与否是架构实现决策。
5. **最终使用状态** — 一位研究员对某现象感兴趣(如 LLM 训练推理的 overhead),
   有相关数据来源(GPU resources)。他能:
   - 让 yuubot overnight 运行实验(人类晚上只能睡觉)。
     持久契约:实验在无消息窗口下保持运行,完成后结果保留至研究员回来查阅;
     实现载体(Actor / 后台任务 / 状态存储)是架构层决策,不在产品宪法约束。
   - 指示 yuubot 绘制流程图 & 交互式数据图,轻松过滤数据,定位真正的 Bottleneck。
   - 研究"某个算法为什么坏":以往需手动逐条对比 sample 轨迹、看 datapoint,
     现在由 AI 快速处理大量数据并去噪,聚焦核心 unhappy path。
   最终落到**可观测性**与**可解释性**。

每个 sprint milestone 必须朝这 5 项之一推进;不朝其推进的 REQ 属 backlog,不入 sprint。

## Scope Boundary (Non-Goals)

- 不做严肃编码 — 委托给 OpenCode/Codex 等专业 CLI。
- 不替代人类思考 — yuubot 是 scale / 收集 / 分析 / 解释的工具,判断与决策在人。
- 不做通用 workflow 引擎 / 低代码平台(与架构宪法一致)。
