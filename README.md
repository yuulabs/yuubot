# yuubot

> **不是聊天机器人。是一个在 QQ 里活着的 Agent。**

夕雨（Yuu）能主动翻聊天记录、搜历史消息、发表情反应、在 Docker 沙箱里写代码执行——她不是在等你问问题，她在真正地参与对话。

**一行命令安装，5 分钟上线：**

```bash
curl -fsSL https://raw.githubusercontent.com/yuulabs/yuubot/main/install.sh | bash
```

---

## 和其他 QQ bot 的本质区别

大多数 QQ bot 的模型是：**收到消息 → 调 LLM → 回复**。

yuubot 的模型是：**收到消息 → 启动 Agent → Agent 自主决定做什么**。

这意味着夕雨可以：

- **翻回去看** — `im browse` 让她浏览任意时间段的聊天记录，不只是当前轮
- **主动搜索** — `im search "关键词"` 在几个月的历史消息里全文检索
- **贴表情反应** — 不一定要回复，贴个 👍 或 🤔 也是行动
- **读图** — 调用专用视觉模型描述图片，结果缓存备用
- **写代码、跑代码** — Docker 沙箱隔离，能力全开，无需为安全妥协
- **管理记忆** — 跨会话记住用户偏好，并由专门的 curator agent 自动整理

她有工具，不是只有嘴。

---

## Docker 沙箱：能力全开，安全无忧

夕雨可以在群里写 Python 脚本并立刻执行——这不是受限的"仅显示代码"模式，而是真正运行代码、返回结果。

安全性由 Docker 容器隔离保证，而不是靠限制 LLM 的能力来回避风险。每次执行都在隔离的容器里：

- 网络访问受控
- 文件系统隔离
- 容器崩了不影响主程序

这意味着**不必为安全而阉割能力，也不必为了性能而走捷径**。安全和能力可以同时拥有。

---

## 成本极低：$0.22/天跑一个活跃群聊 Agent

这不是宣传数字，是 44 天真实运行的生产数据：

| 指标 | 数值 |
|------|------|
| 运行时长 | 44 天连续在线 |
| 总 LLM 请求 | ~13,800 次 |
| 总费用 | **~$9.50 USD**（日均 $0.22） |
| Cache 命中率 | **93.6%**（输入 token 中） |
| 处理消息 | 36,098 条 |
| 错误率 | 0.99% |
| 活跃群聊 | 8 个 |

93.6% 的输入 token 命中缓存，缓存读取价格是正常价格的 1/10。实际输入成本只有不缓存时的约 **16%**。

这不是偶然：yuubot 的每一个工具返回、每一段 system prompt 都经过精心设计，最大化前缀重用。对话历史是递增的，system prompt 是固定的——DeepSeek 和 Claude 的自动 prompt cache 机制天然命中。

---

## 记忆系统

这是 yuubot 最不一样的地方。

记忆不是简单地把用户说的话存进数据库。当对话上下文满载后，**记忆整理员（mem_curator）** 会被自动触发，审查整轮对话，按照严格标准决定：

**值得保留的**：
- 能改变未来行为的事实（偏好、约定、关系）
- 有稳定锚点的信息（QQ 号、URL、绝对日期）

**会被清除的**：
- 从消息元数据就能推导的信息
- 只靠"他/她/那个"等相对指代才能理解的记忆
- 流水账、一次性快照、纯文字的表情包描述

每条被保留的记忆都必须"脱离当前对话还能独立成立"。整理完后自动更新该群的话题摘要。

```
# 群里存着的记忆可能长这样：
"小明不喜欢被 @，有事 dm 更好（QQ:12345）"
"排班查询网站：https://example.com/schedule"
"本群常说的'团长'指王大明（QQ:67890）"
```

---

## 多 Character 架构

yuubot 内置多个专职 agent：

| Character | 职责 |
|-----------|------|
| **main（夕雨/Yuu）** | 默认 QQ 群聊 agent，有完整人格，16 步上限 |
| **general** | 无人设的通用助手，适合纯任务场景 |
| **researcher** | 专注网页搜索和信息整合 |
| **mem_curator** | 记忆整理员，会话 rollover 后自动触发 |

每个 character 有独立的工具集、cap 权限和 token 预算，互不干扰。

---

## 快速开始

**准备**：一个 QQ 小号 + 一个 LLM API Key（下方任选一）

```bash
curl -fsSL https://raw.githubusercontent.com/yuulabs/yuubot/main/install.sh | bash
```

安装脚本引导你选择 LLM 服务商、填写 API Key、配置 QQ 号，自动生成所有配置文件。

```bash
source ~/.bashrc   # 刷新 PATH
ybot launch        # 启动 QQ 协议端（扫码登录）
ybot up            # 启动 Bot 主程序
```

然后在群里发 `/y on` 启用 Bot，`@bot` 开始对话。

---

## 选择 LLM 服务商

| 服务商 | 用途 | 费用 |
|--------|------|------|
| **DeepSeek**（推荐） | 对话主力，国内直连，cache 命中率极高 | 极低，新用户有免费额度 |
| **AiHubMix**（推荐搭配） | 图片理解（视觉模型），DeepSeek 不支持多模态 | 按用量计费，支持支付宝 |
| **OpenRouter** | 海外用户，灵活切换所有主流模型 | 按用量计费 |

Tavily 搜索 Key 是可选的（免费套餐每月 1000 次），没有也能用，只是不能联网搜索。

---

## 在群里的样子

```
用户：@bot 帮我看一下上周大家讨论的那个框架叫什么来着
Bot：[搜索历史消息] 上周三你们讨论的是 FastMCP，@小明 贴了文档链接。

用户：/y 帮我写个脚本统计一下这个月大家发言次数
Bot：[在 Docker 里跑了一段 Python] 本月发言排行：小明 312 条、张三 289 条...

用户：/y 记住我不喜欢被 @ 打扰
Bot：好的，记住了。有事我会直接发消息给你。

用户：/y 周五晚上十点提醒我交作业
Bot：已设置，周五 22:00 提醒你交作业。
```

---

## 能力一览

| 能力 | 说明 |
|------|------|
| **im** | 发消息、搜索历史、浏览上下文、emoji 反应、读合并转发 |
| **mem** | 长期记忆存取，curator 自动整理，语义召回 |
| **web** | Tavily 实时搜索（可选，免费套餐 1000 次/月） |
| **vision** | 图片内容描述，结果自动缓存 |
| **img** | 图片搜索与发送 |
| **schedule** | cron 格式定时任务，重启后恢复 |
| **sandbox** | Docker 隔离 Python 沙箱，安全执行任意代码 |

---

## 配置

```yaml
# ~/.local/share/yuubot-kit/yuubot/config.yaml
bot:
  qq: 123456789       # Bot QQ 号
  master: 987654321   # 管理员 QQ 号

session:
  ttl: 300            # 对话超时（秒）
  max_tokens: 60000   # 单轮上下文上限

memory:
  forget_days: 90     # 自动永久删除旧记忆的天数
```

```bash
# ~/.local/share/yuubot-kit/yuubot/.env
DEEPSEEK_API_KEY=sk-...      # 或 AIHUBMIX_API_KEY / OPENROUTER_API_KEY
TAVILY_API_KEY=tvly-...      # 可选
```

更换模型：编辑 `llm.yaml` 中的 `agent_llm_refs`：

```yaml
agent_llm_refs:
  main: "deepseek/deepseek-chat"        # 极低成本（推荐）
  main: "aihubmix/claude-sonnet-4-6"    # 更强推理
```

修改后运行 `ybot down && ybot up` 重启生效。

---

## 常用管理命令

| 命令 | 说明 |
|------|------|
| `/y on` / `/y off` | 在当前群启用/关闭 Bot |
| `/y auto on` / `/y auto off` | 开启/关闭无需 @ 的自动回复 |
| `/y 消息` | 直接和夕雨说话 |

---

## 技术栈

yuubot 构建于同一仓库的 agent 框架上：

```
yuubot → yuuagents → { yuullm · yuutools · yuutrace }
```

- **yuuagents** — Flow 执行模型，每个 agent 运行都有完整事件日志、异步信箱和取消支持
- **yuullm** — 统一流式 LLM 接口，支持 Anthropic / OpenAI / DeepSeek / OpenRouter / AiHubMix 等任意 OpenAI 兼容 provider
- **yuutrace** — 基于 OpenTelemetry 的可观测性，对话历史落库可查

```bash
# 查看最近的对话记录
uv run python scripts/conv.py -l
```

---

## 平台支持

| 平台 | 状态 |
|------|------|
| Linux x86_64 / ARM64 | ✅ 完整支持（推荐 VPS 长期运行） |
| Windows (WSL2) | ✅ 支持 |
| macOS | ⚠️ NapCat 可能需要手动处理 |

---

## 常见问题

**Bot 没有回复**

1. 确认已启用：`/y on`
2. 查看日志：`tail -f ~/.yuubot/logs/daemon.log`
3. 确认 `.env` 里的 API Key 正确

**`ybot` 命令找不到**

```bash
source ~/.bashrc   # 或 source ~/.zshrc
```

**NapCat 启动失败**

```bash
screen -r napcat   # 查看日志，Ctrl+A D 退出
```
常见原因：账号被风控（换号或等几小时）、端口 8765-8767 被占用。

**开机自启（服务器）**

```bash
sudo tee /etc/systemd/system/yuubot.service > /dev/null <<'EOF'
[Unit]
Description=yuubot
After=network.target

[Service]
Type=simple
User=YOUR_USER
ExecStartPre=/home/YOUR_USER/.local/bin/ybot launch
ExecStart=/home/YOUR_USER/.local/bin/ybot up
ExecStop=/home/YOUR_USER/.local/bin/ybot down
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl enable --now yuubot
```

**更新到最新版本**

```bash
cd ~/.local/share/yuubot-kit
git pull && uv sync
ybot down && ybot up
```

---

遇到问题或有功能建议：https://github.com/yuulabs/yuubot/issues
