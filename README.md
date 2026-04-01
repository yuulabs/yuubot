# yuubot

<img src="imgs/yuu-youfuku.png" align="right" width="220" alt="夕雨 Yuu">

夕雨（Yuu）是一个住在 QQ 群里的 agent。

和大多数 QQ bot 不同，她不只是"收到消息 → 调 LLM → 回复"。她跑的是完整的 agent 循环——可以翻历史记录、全文搜索几个月前的消息、在 Docker 沙箱里写代码执行、记住群友的偏好。她自己决定要做什么，然后做。

```
用户：@bot 帮我看看上周大家讨论的那个框架叫什么
Bot：[搜索历史消息] 上周三讨论的是 FastMCP，小明当时贴了文档链接。

用户：/yllm 统计一下这个月大家发言次数
Bot：[Docker 跑了一段 Python] 本月发言排行：小明 312 条、张三 289 条…

用户：/yllm 周五晚上十点提醒我交作业
Bot：好，周五 22:00 提醒你。
```

---

## 快速开始

准备：一个 QQ 小号 + 一个 LLM API Key

```bash
curl -fsSL https://raw.githubusercontent.com/yuulabs/yuubot/main/install.sh | bash
```

安装脚本引导你选择服务商、填写 API Key、配置 QQ 号，自动生成所有配置文件。

```bash
source ~/.bashrc
ybot launch   # 启动 QQ 协议端（扫码登录）
ybot up       # 启动 Bot 主程序
```

群里发 `/ybot on` 启用。加 `--free` 参数后无需 `@bot` 也能触发命令和 `/yllm`。

---

## 她能做什么

| 能力 | 说明 |
|------|------|
| 翻历史 | `im browse` 浏览任意时间段聊天记录，`im search "关键词"` 全文检索 |
| 发消息 | 普通文字、贴表情反应——不一定要回复，有时 👍 就够了 |
| 读图 | 调用视觉模型描述图片，结果自动缓存 |
| 联网搜索 | Tavily 实时搜索（可选，免费套餐 1000 次/月） |
| 跑代码 | Docker 隔离 Python 沙箱，每次独立容器，网络和文件系统都隔离 |
| 记忆 | 长期记忆跨会话保留，curator agent 自动整理，语义召回 |
| 定时任务 | cron 格式，重启后恢复 |

---

## 记忆

对话轮次超出上限后，`mem_curator` 会审查整轮对话，决定哪些内容值得存下来。

保留标准很窄：必须是"脱离当前对话仍然成立"的信息。流水账、一次性截图描述、只靠上下文才能理解的内容一律丢掉。能存下来的大概长这样：

```
小明不喜欢被 @，有事 dm 更好（QQ:12345）
排班查询：https://example.com/schedule
本群说的"团长"指王大明（QQ:67890）
```

整理完后自动更新该群的话题摘要。

---

## 成本参考

44 天真实运行数据（8 个活跃群聊）：

| 指标 | 数值 |
|------|------|
| 总 LLM 请求 | ~13,800 次 |
| 总费用 | ~$9.50 USD（日均 $0.22） |
| Cache 命中率 | 93.6%（输入 token 中） |
| 处理消息 | 36,098 条 |
| 错误率 | 0.99% |

93.6% 不是意外。每一条消息的渲染方式、每一个工具的返回格式、错误处理的措辞，都经过反复打磨，目的只有一个：让尽可能长的前缀在下一轮保持不变。常用的 capability 合约固定在 system prompt 里，而不是动态拼入——这一条单独就省掉了大量重复 token。DeepSeek 的 cache 窗口长达 24 小时，意味着即使群里沉默一整晚，第二天早上第一条消息还是能命中缓存。

这些优化一点一点积累起来，最终 93.6% 的输入 token 走缓存，缓存价格是正常价格的 1/10，实际输入成本约为不优化时的 16%。

---

## 选择 LLM 服务商

| 服务商 | 用途 | 备注 |
|--------|------|------|
| DeepSeek（推荐） | 对话主力 | 国内直连，24h cache 窗口，新用户有免费额度 |
| AiHubMix（推荐搭配） | 图片理解 | DeepSeek 不支持多模态，视觉模型走这里，支持支付宝 |
| OpenRouter | 海外用户备选 | 可灵活切换主流模型 |

**热切换与自动降级**：模型和服务商可以在运行时切换，无需重启。配置多个 provider 后，某个服务商出现故障或限速时会自动降级到下一个——对群里的用户完全无感。

Tavily 搜索 Key 可选，没有也能跑，只是联网搜索用不了。

---

## 多角色

除了夕雨，yuubot 内置了几个专职 agent：

| Character | 职责 |
|-----------|------|
| main（夕雨/Yuu） | 默认群聊 agent，有完整人格，16 步上限 |
| general | 无人设的通用助手 |
| researcher | 专注网页搜索和信息整合 |
| mem_curator | 记忆整理，会话 rollover 后自动触发 |

`/yllm #researcher 帮我找一下…` 可以指定角色。每个 character 有独立的工具集和 token 预算。

---

## 配置

```yaml
# ~/.local/share/yuubot-kit/yuubot/config.yaml
bot:
  qq: 123456789
  master: 987654321

session:
  ttl: 300          # 对话超时（秒）
  max_tokens: 60000

memory:
  forget_days: 90
```

```bash
# .env
DEEPSEEK_API_KEY=sk-...
TAVILY_API_KEY=tvly-...   # 可选
```

更换模型：编辑 `llm.yaml` 中的 `agent_llm_refs`：

```yaml
agent_llm_refs:
  main: "deepseek/deepseek-chat"
  # main: "aihubmix/claude-sonnet-4-6"
```

改完 `ybot down && ybot up` 重启生效。

---

## 常用命令

| 命令 | 说明 |
|------|------|
| `/ybot on` / `/ybot off` | 在当前群启用/关闭 Bot |
| `/ybot on --free` | free 模式，无需 `@bot` 也能触发命令和 `/yllm` |
| `/yllm 消息` | 触发 agent；`#角色名` 可指定 character |
| `/yhelp` | 查看可用命令列表 |

`/y` 是命令前缀，不是独立的对话入口。

---

## 平台支持

| 平台 | 状态 |
|------|------|
| Linux x86_64 / ARM64 | 完整支持，推荐 VPS 长期运行 |
| Windows (WSL2) | 支持 |
| macOS | NapCat 可能需要手动处理 |

---

## 常见问题

**Bot 没有回复**

1. 确认已启用：`/ybot on`
2. 检查日志：`tail -f ~/.yuubot/logs/daemon.log`
3. 确认 `.env` 里的 API Key 有效

**`ybot` 找不到**

```bash
source ~/.bashrc   # 或 ~/.zshrc
```

**NapCat 启动失败**

```bash
screen -r napcat   # 查看日志，Ctrl+A D 退出
```

常见原因：账号被风控（换号或等几小时）、端口 8765-8767 被占用。

**开机自启**

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

**更新**

```bash
cd ~/.local/share/yuubot-kit
git pull && uv sync
ybot down && ybot up
```

---

遇到问题或有功能建议：https://github.com/yuulabs/yuubot/issues
