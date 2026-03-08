# yuubot traces.db 分析报告

分析日期: 2026-02-14
数据库路径: `~/.yagents/traces.db` (14MB)

## 1. 数据库概况

数据库使用 OpenTelemetry 格式，包含两张表：
- `spans`: 7778 行，记录每个 LLM 调用、工具调用、对话的 span
- `events`: 5506 行，记录 token 用量 (`yuu.llm.usage`)、费用 (`yuu.cost`)、异常 (`exception`)

### 总体统计

| 指标 | 数值 |
|------|------|
| 总 traces (对话) | 344 |
| 总 LLM 调用 | 2,700 次 |
| 总 tool 调用 | 2,365 次 |
| 总 input tokens | 9,376,868 |
| 总 output tokens | 225,745 |
| 总 cache read tokens | 8,478,656 |
| cache 命中率 | ~47% |
| 总费用 | $2.6346 |
| 总异常数 | 162 |

所有费用均为 LLM 调用费用（deepseek-chat），无独立的 tool 计费。

## 2. 各 Agent 费用分布

| Agent | Traces | 总费用 | 平均费用/trace | Input Tokens | Output Tokens |
|-------|--------|--------|---------------|-------------|--------------|
| yuubot-2 | 297 | $2.2357 (84.9%) | $0.00753 | 7,945,161 | 182,301 |
| yuubot-1 | 24 | $0.3160 (12.0%) | $0.01317 | 1,053,320 | 28,711 |
| yuubot-3 | 7 | $0.0419 (1.6%) | $0.00598 | 168,027 | 5,848 |
| yuubot-cron-1 | 3 | $0.0320 (1.2%) | $0.01067 | 187,453 | 8,115 |
| delegate-researcher-* | 4 | $0.0263 (1.0%) | $0.00658 | 81,460 | 3,924 |
| yuubot-cron-2 | 3 | $0.0070 (0.3%) | $0.00234 | 22,906 | 769 |

yuubot-2 是绝对主力，占总费用的 85%。以下分析主要针对 yuubot-2。

## 3. yuubot-2 对话复杂度分布

按每个 trace 的 LLM 调用步数分桶：

| LLM 步数 | Trace 数 | 总费用 | 平均费用 |
|----------|---------|--------|---------|
| 2-3 步 | 46 | $0.055 | $0.0012 |
| 4-6 步 | 83 | $0.264 | $0.0032 |
| 7-10 步 | 102 | $0.875 | $0.0086 |
| 11-16 步 | 64 | $0.977 | $0.0153 |
| 17+ 步 | 3 | $0.066 | $0.0218 |

按费用分桶：

| 费用区间 | Trace 数 | 总费用 |
|----------|---------|--------|
| < $0.002 | 62 | $0.081 |
| $0.002 - $0.005 | 88 | $0.311 |
| $0.005 - $0.01 | 84 | $0.591 |
| $0.01 - $0.02 | 50 | $0.696 |
| $0.02 - $0.05 | 10 | $0.243 |
| ≥ $0.05 | 3 | $0.313 |

Top 3 最贵的 trace 合计 $0.313，占 yuubot-2 总费用的 14%。

## 4. Top 10 最贵 Trace

| 费用 | LLM 步数 | 用户消息 |
|------|---------|---------|
| $0.168 | 9 | "今天arxiv cs.ai板块有啥看上去很强的论文" |
| $0.085 | 14 | "超时空系列是什么" |
| $0.060 | 16 | "这是什么意思[回复:187521675]" |
| $0.036 | 10 | "玩机器 是哪部漫画" |
| $0.028 | 23 | "安装python3.14并告诉我如下命令的结果..." |
| $0.025 | 16 | "用ASCII ART 画一个中国自然手语的K" |
| $0.024 | 14 | "其中有一个被命名为 Ao, 为什么" |
| $0.024 | 16 | "yamibo的一般来信不需要有更新吗" |
| $0.022 | 16 | "用ASCII ART 画一个安和昴" |
| $0.022 | 14 | "看看yamibo今天更新了什么小说" |

## 5. 主要问题分析

### 5.1 im send 格式试错 — 最大的浪费源

**现象**: 88 次 `im send` 调用失败，其中 73 次是因为使用了 `--content` 参数（正确格式是位置参数 `'<msg_json>'`）。

**影响**: 297 个 trace 中有 246 个（83%）读取了 SKILL.md 文件：
- 0 次读取: 52 traces，平均费用 $0.00687
- 1 次读取: 154 traces，平均费用 $0.00501
- 2+ 次读取: 92 traces，平均费用 $0.0121

2+ 次读取的 trace 平均费用是 1 次读取的 2.4 倍。

**典型流程**（超时空系列 trace，$0.085）：

```
步骤 1: LLM → "我需要先查看记忆" → mem recall "系列"
步骤 2: LLM → "让我搜索一下" → web search "超时空系列"
步骤 3: LLM → "让我查看详细信息" → web read (维基百科-时空系列)
步骤 4: LLM → "让我也查看超时空要塞" → web read (维基百科-超时空要塞)
步骤 5: LLM → "让我回复" → im send --ctx 2 --content "..."
                                ↑ 错误！No such option: --content
步骤 6: LLM → "让我查看im skill" → cat im/SKILL.md
步骤 7: LLM → "现在我知道了" → im send '[...]' --ctx 2  ✓
步骤 8-10: 继续发送更多消息...
步骤 11: LLM → "保存记忆" → mem save --ctx 2 --content "..."
                                ↑ 又错了！No such option: --content
步骤 12: LLM → "查看mem skill" → cat mem/SKILL.md
步骤 13: LLM → "现在知道了" → mem save "..." --tags ... --ctx 2  ✓
步骤 14: LLM → "完成！"
```

步骤 5-6 和 11-12 完全是浪费。而且由于对话历史累积，到步骤 12 时 input tokens 已经很高，每多一步都很贵。

### 5.2 hhsh guess 命令不可用 — 反复重试

**现象**: `ybot hhsh guess` 命令被安全策略拦截（"command not allowed"），但 agent 反复尝试不同格式。

**影响**: 16 个 trace 受影响，总费用 $0.16，平均每个 $0.01。

**典型流程**（yllm 查询 trace，$0.0097）：

```
步骤 1: LLM → mem recall "知道 用来 du cn"
步骤 2: LLM → ybot hhsh guess yllm          ← command not allowed
步骤 3: LLM → cat hhsh/SKILL.md             ← 读文档
步骤 4: LLM → ybot hhsh guess "yllm"        ← 加引号重试，还是不行
步骤 5: LLM → ybot hhsh guess "yllm"        ← 再试
步骤 6: LLM → ybot hhsh guess "yllm"        ← 再试
步骤 7: LLM → ybot hhsh guess yllm          ← 去引号再试
步骤 8: LLM → "算了，直接回复吧" → cat im/SKILL.md
步骤 9-12: 发送消息 + 保存记忆
```

步骤 2-7 全部浪费（5 次重试），agent 没有理解"command not allowed"意味着这个命令根本不能用。

### 5.3 网页内容导致 input tokens 爆炸

**现象**: `web read` 返回的大段网页内容留在对话历史中，后续每一步 LLM 调用都要重新处理。

**最贵 trace**（arxiv 论文，$0.168）的 input tokens 增长曲线：

```
步骤 1:   1,281 tokens  (初始 prompt)
步骤 2:   1,605 tokens  (+324, web search 结果)
步骤 3:   2,375 tokens  (+770, web read --summary)
步骤 4:   2,878 tokens  (+503)
步骤 5: 120,715 tokens  (+117,837! web read 完整页面)
步骤 6: 121,165 tokens  (累积)
步骤 7: 121,357 tokens
步骤 8: 121,955 tokens
步骤 9: 122,431 tokens
```

步骤 5 读取了 arxiv 完整页面（无 --summary），input tokens 从 2.8K 暴涨到 120K。之后每一步都要重新处理这 120K tokens。

### 5.4 shell 安全策略异常

**现象**: 24 次 "shell expansions are not allowed" + 2 次 "dangerous shell control operators"

agent 在命令中使用了 `$`、`*`、`|` 等 shell 特殊字符，被安全策略拦截后需要额外步骤修正。

### 5.5 httpx.RemoteProtocolError — 网络不稳定

**现象**: 33 次连接断开异常，影响 6 个 trace，额外费用约 $0.014。

这不是 agent 逻辑问题，是 deepseek API 连接不稳定导致的。

### 5.6 异常汇总

| 异常类型 | 次数 | 说明 |
|---------|------|------|
| ValueError | 80 | 其中 24 次 shell expansion，40+ 次 command not allowed |
| httpx.RemoteProtocolError | 33 | API 连接断开 |
| AssertionError | 24 | 内部断言失败 |
| tavily.errors.MissingAPIKeyError | 11 | Tavily API key 缺失 |
| AttributeError | 8 | 代码属性错误 |
| openai.BadRequestError | 4 | API 请求格式错误 |
| FileNotFoundError | 2 | 文件未找到 |

## 6. 改进建议

### 6.1 把工具格式写进 system prompt（预计节省 30-40%）

im send 的正确格式 `ybot im send '<msg_json>' --ctx <id>` 和 mem save 的正确格式 `ybot mem save "<content>" --tags ... --ctx <id>` 应该直接写在 system prompt 里，附带 few-shot example。

当前 83% 的 trace 需要读 SKILL.md，其中 31% 需要读 2 次以上。每次读取 = 1 次 tool 调用 + 1 次 LLM 调用 + SKILL.md 内容进入对话历史。

### 6.2 对 "command not allowed" 快速失败（预计节省 $0.16+）

在 agent loop 中检测到 "command not allowed" 错误时，应该直接告诉 LLM "此命令不可用，请换其他方式"，而不是让 LLM 自己反复尝试。可以在 tool 执行层加一个 error classifier：

```python
if "command not allowed" in error_msg:
    return "此命令不可用。请直接回复用户，不要重试此命令。"
```

### 6.3 网页内容摘要后丢弃原文（预计节省 50%+ 对于 web 类任务）

`web read` 返回的完整网页内容不应该留在对话历史里。建议：
- 强制使用 `--summary` 模式，或
- 在 tool 返回后只保留前 N tokens 的摘要，或
- 用一个独立的 context window 处理网页内容，只把结论传回主对话

arxiv trace 的 input tokens 从 2.8K 暴涨到 122K，如果网页内容被摘要压缩，后续步骤的 input tokens 可以控制在 5-10K。

### 6.4 在 system prompt 中声明不可用的命令

明确列出 `hhsh guess` 等被安全策略禁止的命令，避免 agent 尝试调用。

### 6.5 错误重试上限

对同一类错误设置 max_retry（建议 1-2 次）。当前有 trace 对同一个命令重试 5 次。

## 7. 查询方法

以下是本次分析使用的主要 SQL 查询，供后续复现：

### 每个 agent 的 token 用量
```sql
SELECT c.agent,
       SUM(json_extract(e.attributes_json, '$."yuu.llm.usage.input_tokens"')) as total_input,
       SUM(json_extract(e.attributes_json, '$."yuu.llm.usage.output_tokens"')) as total_output,
       SUM(json_extract(e.attributes_json, '$."yuu.llm.usage.cache_read_tokens"')) as total_cache_read
FROM events e
JOIN spans s ON e.span_id = s.span_id
JOIN spans c ON s.trace_id = c.trace_id AND c.name = 'conversation'
WHERE e.name = 'yuu.llm.usage'
GROUP BY c.agent
ORDER BY total_input DESC;
```

### 每个 trace 的费用和步数
```sql
SELECT c.trace_id,
  (SELECT COUNT(*) FROM spans s2 WHERE s2.trace_id=c.trace_id AND s2.name='llm_gen') as llm_steps,
  SUM(json_extract(e.attributes_json, '$."yuu.cost.amount"')) as cost,
  SUBSTR(json_extract(c.attributes_json, '$."yuu.context.user.content"'), 1, 120) as user_msg
FROM spans c
JOIN spans s ON s.trace_id = c.trace_id
JOIN events e ON e.span_id = s.span_id AND e.name='yuu.cost'
WHERE c.name='conversation' AND c.agent='yuubot-2'
GROUP BY c.trace_id
ORDER BY cost DESC;
```

### 某个 trace 的完整流程
```sql
SELECT s.name,
  json_extract(s.attributes_json, '$."yuu.llm_gen.items"') as llm_items,
  json_extract(s.attributes_json, '$."yuu.tool.input"') as tool_input,
  json_extract(s.attributes_json, '$."yuu.tool.output"') as tool_output
FROM spans s
WHERE s.trace_id = '<trace_id>'
ORDER BY s.start_time_unix_nano;
```

### 异常分布
```sql
SELECT json_extract(e.attributes_json, '$."exception.type"') as exc_type,
       json_extract(e.attributes_json, '$."exception.message"') as msg,
       COUNT(*) as cnt
FROM events e
WHERE e.name='exception'
GROUP BY exc_type, SUBSTR(msg, 1, 60)
ORDER BY cnt DESC;
```

### SKILL.md 读取频率
```sql
SELECT
  CASE
    WHEN tool_input LIKE '%im/SKILL%' THEN 'im/SKILL.md'
    WHEN tool_input LIKE '%web/SKILL%' THEN 'web/SKILL.md'
    WHEN tool_input LIKE '%mem/SKILL%' THEN 'mem/SKILL.md'
    ELSE tool_input
  END as skill_file,
  COUNT(*) as read_count
FROM (
  SELECT json_extract(s.attributes_json, '$."yuu.tool.input"') as tool_input
  FROM spans s
  WHERE json_extract(s.attributes_json, '$."yuu.tool.input"') LIKE '%SKILL.md%'
)
GROUP BY skill_file
ORDER BY read_count DESC;
```
