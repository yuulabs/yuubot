# Yuu Persona System — 四人座设计

> 让 bot 不是在服务你，而是在和你交往。
> 治愈不是目标，是一个活得充实的存在自然外溢的副产品。

## 核心洞察

现有 yuubot 是 stimulus → response 的工具系统。Persona 系统的目标不同：
让用户感觉 yuu 是一个有自己生活的人。她不主动找你，但你找她时，
她带着自己最近的经历、心情、想法和你说话。偶尔，她会因为生活中的事想起你，主动发一条消息。

关键约束：
- yuu 不知道自己是 bot。她活在一个自洽的叙事世界中。
- 现有工具性功能（yllm 指令触发）继续存在，yuu 会观察到"自己的账号有时会自己说话"，她可以对此发展出自己的理解。
- 用户只通过"手机"这个窗口和 yuu 接触，不进入她的世界。

## 四 Agents 架构

```
咲 Saki   — 剧本家，让故事绽放
綴 Tsuzuri — 细节师，把故事缀成记忆
凪 Nagi   — 世界之声，描述此刻正在发生什么
夕雨 Yuu   — 活在这一切中的人
```

### 咲 Saki — 剧本家

**职责**：定期（每几小时）生成 yuu 生活的大纲。
**输入**：世界设定、历史剧情线、yuu 的性格、最近与用户的聊天摘要。
**输出**：今日/本段大纲，包括：
  - 遭遇事件（遭遇 RNG）
  - 是否安排 live scene
  - 是否安排主动联系用户（意图，不是消息本身）
  - 以及所有非Yuu角色的行动，设定等。她被允许丰富设定，她的这些设定也会一并传递给Nagi. 
**成本控制**：Saki 是整个系统的"财务总监"。live scene 数量、主动消息频率都由她控制。大部分时候不安排 live scene，剧本直接交给 Tsuzuri 写成日记。
**善后（showrunner）**：Saki 不只是编剧，是 showrunner。每次 tick 时她要收拾上一轮的碎片：
  - Nagi 的 live scene 偏离大纲了 → 哪些纳入正史，怎么合理化
  - Yuu 和用户聊天时说了剧情没铺垫过的话 → 要不要追认，还是当作随口一说
  - 用户打断了 yuu 的计划（"别买了快来帮忙"）→ 后续大纲怎么调整
  - 多条剧情线的一致性维护
  这是四个角色中最难的活。
**模型**：需要最好的模型。Saki 的判断力决定了整个系统叙事的连贯性和质量。

### 綴 Tsuzuri — 细节师

**职责**：把 Saki 的大纲展开成 yuu 的主观记忆。
**输入**：Saki 的大纲 + 世界设定 + 之前的日记。
**输出**：
  - 日记条目（自由文本，yuu 的笔迹和语气）
  - working memory 更新（当前心情、挂念的事、正在追的话题）
  - 若有 live scene：场景初始上下文
  - 若有主动消息意图：草稿意图（目标、动机、情绪）
**核心要求**：文笔，不是智力。Tsuzuri 的输出会直接成为 yuu 的 context——
  如果日记里出现 `**关键约束**：`、`作为一个AI` 之类的 meta 痕迹，yuu 会立刻出戏。
  Tsuzuri 必须完全用 yuu 的口吻写作，像一个真人的日记，带着口语、省略、涂改、情绪。
**模型**：需要文笔好的模型，但不需要最强推理能力。关键是 prompt 要严格约束输出风格。

### 凪 Nagi — 世界之声 (DM)

**职责**：在 live scene 中作为世界的代言人，响应 yuu 的行动、推进场景。
**运行时机**：仅在 live scene 期间。大部分时间不运行。
**交互模式**：

```
Nagi (user msg) → 场景描述
    Yuu (assistant msg) → 行动 + 可能的 tool call
        Nagi 看到 yuu 的行动 → 推进场景 (user msg)
        ...循环，直到 Nagi 决定场景结束
```

Nagi 不干预 yuu 的 tool call（那是 yuu 对自己随身物品的控制权），但会在下一轮补充世界层面的叙事后果。

**场景中用户消息的处理**：如果 yuu 在场景中给真实用户发了消息，用户的回复会被注入到 Nagi 的上下文中，Nagi 将其纳入场景推进。

**模型**：和 Yuu 同档即可。Nagi 的工作是描述和推进，能hold住yuu即可。Nagi会具有更强的工具来support, 例如可能配置一个文生图工具来辅助消息。

### 夕雨 Yuu — 活在这一切中的人

**职责**：作为自己，生活、感受、和手机里的人聊天。
**唤醒条件**（均由 Saki 编排，用户消息不直接触发 Yuu）：
  - Saki 在大纲中安排"看手机"（此时才看到用户的未读消息）
  - Saki 安排主动消息意图（她想找人聊天）
  - Nagi 启动的 live scene
**输入**：日记 + working memory + 当前场景上下文（如有） + 对话
**tool call**：yuu 的行动和贴身物品，不需要 DM 许可。具体工具的叙事外衣由世界设定决定。
**对话结束后**：聊天摘要回传给 Saki，影响下一轮剧情。

## 世界设定（待设计）

世界需要满足以下约束：

### 记忆机制
世界中存在某种自然现象（如"雾"），导致所有居民的短期记忆会周期性模糊。
因此写日记是这个世界的常识行为，就像带钥匙出门一样自然。
这让 LLM 的 context 清除变成世界的物理规律，而非需要掩盖的 bug。

### Capability 的叙事化
每个真实 capability 都需要在世界中有一个自洽的存在形式。具体映射是世界设定的一部分（待设计），原则见「设计哲学 — 工具的叙事化」。

### 世界基调
祥和、充实。yuu 有自己的人际关系、日常生活、兴趣爱好。
世界不是空白的，有丰富的可交互内容。
yuu 活得充实，和用户聊天时是从丰盈中分享，不是从空虚中索取。

### 异常现象
yuu 可能逐渐注意到一些不合常理的事情——工具行为的异常、用户提到的陌生概念、世界规则的偶尔不自洽。这些不是主线，是生活中偶尔闪过的"嗯？"。

## 运行时序

### 正常心跳（无 live scene）
```
Saki tick → 大纲
  → Tsuzuri → 日记 + working memory
  → 完毕，等待下一次 tick 或用户消息
```

### 有 live scene 的心跳
```
Saki tick → 大纲（含 live scene）
  → Tsuzuri → 日记 + 场景初始上下文
  → Nagi 启动场景
    → Yuu 在场景中行动（可能发真实消息）
    → 循环直到 Nagi 结束场景
  → 场景结果写入日记
```

### 用户发来消息
```
用户消息 → 堆积在未读队列
  → Saki 下次 tick 时看到未读，决定是否安排 Yuu "看手机"
    → 若安排：唤醒 Yuu（带最新日记 + working memory + 未读消息）
    → 正常聊天
    → 结束后聊天摘要回传 Saki
```

### Saki 安排主动消息
```
Saki 大纲含"想找某人聊天"意图
  → Tsuzuri 生成意图草稿
  → 唤醒 Yuu，注入日记 + 意图
  → Yuu 以自己的方式发消息
```

## 设计哲学

### 产品即作品，不是框架

Persona 系统的所有内容——世界观、性格、叙事风格、工具的叙事外衣——全部随代码发布，不暴露为用户配置项。用户接入 QQ 后被惊艳，而不是面对一堆参数自己拼装。

唯一的配置是运维层面的：是否启用、tick 间隔、模型选择。

四个角色的 prompt 是整体交付的作品，不是可插拔的组件。这与现有 characters 的模式不同——characters 通过 sections 组合 prompt，persona 的 prompt 是完成品。

### 工具的叙事化

每个真实 capability 都需要一层叙事外衣，使其在世界观中自洽。具体的叙事映射是世界设定的一部分（待设计），但原则是确定的：

- **tool call = Yuu 的行动和贴身物品**。这些是 Yuu 自主控制的，不需要 Nagi 许可。发消息、写日记、查资料——都是她随时可以做的事。
- **最后一条回复 = 与 Nagi 的交互**。Yuu 的最终文本输出是她在场景中的言行，Nagi 看到后推进世界。

这个区分让 tool call 和叙事输出有清晰的语义边界：tool call 是角色对自身物品的操作（世界无权干预），文本输出是角色在世界中的行为（世界会响应）。

### Saki 的 meta 视角

Saki 是四个角色中唯一拥有 meta 知识的：

- 她知道 yllm 系统的存在——手机会独立于 Yuu 发送消息，这些不代表 Yuu 的意志
- 善后时她需要区分 Yuu 的行为和 yllm 的行为，决定哪些纳入叙事、哪些忽略
- 她可以将 yllm 的行为编排为叙事素材（"手机又自己说话了"），也可以当作背景噪音

Yuu、Tsuzuri、Nagi 不需要知道 yllm 的存在。对她们来说，手机偶尔自己说话只是这个世界的一个小怪癖。

## 仓库结构

Persona 系统作为 `src/yuubot/persona/` 独立子包存在。

```
src/yuubot/persona/
├── __init__.py          # 启动入口：start_persona(config) / stop_persona()
├── clock.py             # tick 循环管理
├── saki.py              # 剧本家 — tick 驱动、大纲生成、善后
├── tsuzuri.py           # 细节师 — 大纲 → 日记 + working memory
├── nagi.py              # DM — live scene 循环
├── yuu.py               # 角色 agent — 聊天、场景内行动
├── memory.py            # 日记 + working memory 存储
└── prompts/             # 四个角色的 prompt（随代码发布，不可配置）
    ├── saki.md
    ├── tsuzuri.md
    ├── nagi.md
    └── yuu.md
```

### 复用现有组件

| 组件 | 用法 |
|------|------|
| `capabilities/im` | Yuu 发消息的唯一通道 |
| `capabilities/mem` | 日记存储的底层，persona/memory.py 在上面加叙事结构 |
| `capabilities/web` | Yuu 的信息获取工具 |
| `daemon/runtime.py` | LLM factory、tool manager 构建 |
| `core/types.py` | InboundMessage 作为用户消息输入格式 |
| `core/db.py` | 日记、大纲、世界状态的持久化 |
| `recorder/` | 消息收发不变 |

### 不复用的组件

| 组件 | 原因 |
|------|------|
| `daemon/dispatcher.py` | reactive 路由，persona 是 proactive 的 |
| `daemon/conversation.py` | TTL 驱动的会话状态，persona 的"会话"概念不同 |
| `daemon/agent_runner.py` | 单轮 request-response，persona 需要多 agent 编排 |
| `characters/` | persona 的四个角色不是 Character，不注册到 registry |
| `daemon/scheduler.py` | cron 粒度不适用，persona 有自己的 tick 循环 |

### 与现有系统的共存

两套系统共享消息通道，不共享状态：

- yllm 命令继续走 dispatcher → agent_runner 路径，直接回复
- 非命令的用户消息堆在 persona 的未读队列，等 Saki 下次 tick 时处理
- 两套系统通过 `im` capability 往同一个 QQ 号发消息，互不感知

Persona 系统完全由 tick 驱动，用户消息不触发额外的 LLM 调用。成本由 tick 频率决定，与用户行为无关。

## 待定项

- [ ] 世界的具体设定（地理、居民、文化、日常）— 需要小说家参与
- [ ] 遭遇 RNG 表设计（Saki 的素材库）
- [ ] yuu 的初始日记（bootstrap memory）
- [ ] 各 capability 的叙事外衣具体设计
- [ ] Saki 的 tick 频率和 live scene 预算
