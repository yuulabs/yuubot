# yuubot

一个基于大语言模型的 QQ 机器人，支持多轮对话、网页搜索、长期记忆、定时任务等功能。

---

## 目录

- [功能介绍](#功能介绍)
- [安装前准备](#安装前准备)
- [快速安装](#快速安装)
- [平台说明](#平台说明)
- [日常使用](#日常使用)
- [配置说明](#配置说明)
- [常见问题](#常见问题)

---

## 功能介绍

| 功能 | 说明 |
|------|------|
| 多轮对话 | 记住上下文，像真人一样聊天 |
| 网页搜索 | 实时搜索最新信息（需要 Tavily API Key） |
| 长期记忆 | 跨会话记住用户偏好和重要信息 |
| 图片理解 | 识别和描述图片内容（需要支持视觉的模型） |
| 定时任务 | 支持 cron 格式的定时提醒/任务 |
| 权限管理 | 区分 master / mod / folk 三级权限 |
| 多群支持 | 同时运行在多个 QQ 群 |

### 对话示例

```
用户：@bot 帮我搜一下今天的天气
Bot：[搜索中...] 北京今天晴，气温 18-26°C，空气质量良好。

用户：/y 记住我喜欢喝绿茶
Bot：好的，已记住你喜欢喝绿茶 ☕

用户：/yuu 下周一提醒我交报告
Bot：已设置定时提醒，下周一 09:00 会提醒你。
```

---

## 安装前准备

开始之前，请准备好以下东西：

### 必须

1. **一个 QQ 小号**（专用作 Bot 账号，不要用自己的主号）
   - 注册地址：https://ssl.zc.qq.com

2. **LLM API Key**（选一个即可）

   | 服务商 | 适合人群 | 费用 | 注册地址 |
   |--------|----------|------|----------|
   | **DeepSeek** ← 推荐新手 | 国内用户，中文对话 | 低，有免费额度 | https://platform.deepseek.com |
   | OpenAI (GPT-4o) | 需要最强效果 | 中等 | https://platform.openai.com |
   | OpenRouter | 想灵活切换模型 | 按模型定价 | https://openrouter.ai |

### 推荐（非必须）

3. **Tavily 搜索 API Key**（让 Bot 能搜索网页）
   - 免费套餐每月 1000 次搜索，完全够个人使用
   - 注册地址：https://tavily.com → 注册后进控制台复制 API Key

---

## 快速安装

### Linux / macOS / Windows (WSL2)

在终端中复制粘贴以下命令，然后按照提示操作：

```bash
curl -fsSL https://raw.githubusercontent.com/yuulabs/agent-kits/main/install.sh | bash
```

安装过程大约需要 **5~15 分钟**（取决于网速），脚本会自动：

1. 安装必要的系统工具
2. 安装 Python 环境（UV）
3. 下载 yuubot 代码
4. 引导你输入 API Key
5. 安装 NapCat（QQ 协议端）
6. 引导扫码登录 Bot QQ

### Windows 用户

Windows 需要先安装 WSL2（Linux 子系统），然后在 WSL2 终端中运行上面的命令。

**安装 WSL2 的方法（两步）：**

1. 以**管理员身份**打开 PowerShell，输入：
   ```powershell
   wsl --install
   ```

2. 重启电脑后，点击开始菜单 → Ubuntu，等待初始化完成

3. 在 Ubuntu 终端中运行安装命令即可

---

## 平台说明

| 平台 | 支持状态 | 备注 |
|------|----------|------|
| Linux (x86_64) | ✅ 完整支持 | 推荐，VPS 长期运行 |
| Linux (ARM64) | ✅ 完整支持 | 树莓派等 ARM 设备 |
| Windows (WSL2) | ✅ 支持 | 需要先安装 WSL2 |
| macOS | ⚠️ 部分支持 | NapCat 可能需要手动处理 |

**推荐使用 Linux VPS**（如腾讯云、阿里云的最低配置即可），可以 24 小时在线，稳定可靠。

---

## 日常使用

安装完成后，每次使用 Bot 的步骤：

```bash
# 第一步：启动 QQ 协议端（NapCat + Recorder）
ybot launch

# 第二步：启动 Bot 主程序
ybot up
```

关闭 Bot：

```bash
ybot down      # 只停止 Bot 主程序
ybot shutdown  # 关闭所有服务（包括 NapCat）
```

### 在 QQ 中使用

| 触发方式 | 说明 |
|----------|------|
| `@机器人 + 内容` | 在群里@Bot 进行对话 |
| `/y 内容` 或 `/yuu 内容` | 命令模式，触发各种功能 |
| 私聊 Bot | 直接发消息（需要在白名单中） |

### 管理员命令

管理员（master）可以在群里发送以下命令：

| 命令 | 说明 |
|------|------|
| `/y on` | 在当前群开启 Bot |
| `/y off` | 在当前群关闭 Bot |
| `/y auto on` | 开启自动回复（不需要@） |
| `/y auto off` | 关闭自动回复 |

---

## 配置说明

配置文件位置：`~/.local/share/yuubot-kit/yuubot/config.yaml`

### 常用配置项

```yaml
bot:
  qq: 123456789       # Bot 的 QQ 号
  master: 987654321   # 管理员 QQ 号
  entries:            # 触发命令的前缀
    - "/y"
    - "/yuu"

session:
  ttl: 300            # 对话超时时间（秒），超过后开始新对话
  max_tokens: 60000   # 单轮上下文最大 token 数

memory:
  forget_days: 90     # 多久后自动忘记旧记忆（天）

response:
  group_default: "at"  # 群里回复方式：at（@用户）或 reply（引用回复）
  dm_whitelist: []     # 允许私聊的 QQ 号列表，空列表=仅 master 可私聊
```

### 更换 API Key

编辑 `~/.local/share/yuubot-kit/yuubot/.env`：

```bash
DEEPSEEK_API_KEY=你的新key
TAVILY_API_KEY=你的新key
```

修改后重启 Bot（`ybot down && ybot up`）即可生效。

### 更换 AI 模型

编辑 `~/.local/share/yuubot-kit/yuubot/yuuagents.config.yaml`：

```yaml
agents:
  main:
    provider: deepseek     # 改为 openai 或 openrouter
    model: deepseek-chat   # 改为对应模型名称
```

常用模型名称：

| 服务商 | 模型名称 |
|--------|----------|
| DeepSeek | `deepseek-chat` |
| OpenAI | `gpt-4o`、`gpt-4o-mini` |
| OpenRouter | `google/gemini-3.1-flash-lite-preview`、`anthropic/claude-sonnet-4.6` |

---

## 常见问题

### Q: 安装后运行 `ybot` 提示"命令未找到"

执行以下命令重载配置：
```bash
source ~/.bashrc
# 或者
source ~/.zshrc
```

### Q: NapCat 启动失败

查看 NapCat 日志：
```bash
screen -r napcat
```
按 `Ctrl+A` 然后 `D` 退出查看（不会关闭 NapCat）。

常见原因：
- QQ 账号被风控：换一个小号，或者等几小时再试
- 端口被占用：检查 8765、8766、8767 端口是否被其他程序占用

### Q: Bot 收到消息但没有回复

1. 检查 Bot 是否在该群启用：发送 `/y on`
2. 查看日志：`tail -f ~/.yuubot/logs/daemon.log`
3. 确认 API Key 有效：检查 `.env` 文件中的 key 是否正确

### Q: 怎么让 Bot 一直在线（服务器重启后自动启动）

使用 `systemd` 设置开机自启：

```bash
# 创建 systemd 服务（需要 root）
sudo tee /etc/systemd/system/yuubot.service > /dev/null <<EOF
[Unit]
Description=yuubot QQ Bot
After=network.target

[Service]
Type=forking
User=$USER
ExecStart=$HOME/.local/bin/ybot launch && $HOME/.local/bin/ybot up
ExecStop=$HOME/.local/bin/ybot shutdown
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl enable yuubot
sudo systemctl start yuubot
```

### Q: 如何查看 Bot 的对话记录

```bash
# 查看最近的对话
cd ~/.local/share/yuubot-kit/yuubot
uv run python scripts/conv.py

# 查看最新一条对话的详细内容
uv run python scripts/conv.py -l
```

### Q: 如何更新 yuubot 到最新版本

```bash
cd ~/.local/share/yuubot-kit
git pull
uv sync
ybot down && ybot up
```

### Q: 怎么增加/减少 Bot 会说话的群

目前通过在群里发送 `/y on` 或 `/y off` 控制。只有管理员（master）可以执行此操作。

---

## 文件和目录

| 路径 | 说明 |
|------|------|
| `~/.local/share/yuubot-kit/` | yuubot 代码目录 |
| `~/.local/share/yuubot-kit/yuubot/config.yaml` | 主配置文件 |
| `~/.local/share/yuubot-kit/yuubot/.env` | API Key 存储（勿泄露） |
| `~/.yuubot/yuubot.db` | 消息数据库 |
| `~/.yuubot/logs/` | 日志文件 |
| `~/.yuubot/browser_profile/` | 浏览器登录状态 |

---

## 反馈问题

遇到 Bug 或有功能建议，请在 GitHub 提 Issue：
https://github.com/yuulabs/agent-kits/issues
