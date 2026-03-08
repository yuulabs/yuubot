# 配置文件格式设计

yuubot 使用 YAML 配置文件，默认路径 `config.yaml`，可通过 `--config` 参数指定。

## 完整配置示例

```yaml
# ============================================================
# yuubot 配置文件
# ============================================================

# Bot 基本信息
bot:
  qq: 123456789                    # Bot 的 QQ 号
  master: 987654321                # Master 的 QQ 号
  entries:                         # 命令入口前缀
    - "/y"
    - "/yuu"

# ============================================================
# Recorder 进程配置
# ============================================================
recorder:
  # NapCat 反向 WS 服务器（NapCat 连接到这里）
  napcat_ws:
    host: "0.0.0.0"
    port: 8765

  # 内部 WS（转发给 daemon）
  relay_ws:
    host: "127.0.0.1"
    port: 8766

  # HTTP API（供 skills 和 daemon 调用）
  api:
    host: "127.0.0.1"
    port: 8767

  # NapCat HTTP API 地址（Recorder 代理发送消息用）
  napcat_http: "http://127.0.0.1:3000"

# ============================================================
# Daemon 进程配置
# ============================================================
daemon:
  # 连接 Recorder 内部 WS
  recorder_ws: "ws://127.0.0.1:8766"

  # Recorder HTTP API
  recorder_api: "http://127.0.0.1:8767"

  # FastAPI 服务（健康检查、调试接口）
  api:
    host: "127.0.0.1"
    port: 8780

# ============================================================
# 数据库配置
# ============================================================
database:
  path: "~/.yuubot/yuubot.db"     # SQLite 数据库路径

# ============================================================
# Agent 配置
# ============================================================
agent:
  persona: |
    你是一个有用的QQ机器人助手。
    你可以通过 ybot CLI 工具来完成各种任务。
    回复时请简洁友好。

  # 使用的 skills 列表
  skills:
    - im
    - web
    - mem

  # yuuagents 配置路径（用于 setup()）
  yuuagents_config: "~/.yagents/config.yaml"

  # Skills 文档搜索路径
  skill_paths:
    - "~/.yagents/skills"

# ============================================================
# LLM 配置
# ============================================================
llm:
  provider: "openai"
  model: "gpt-4o"
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"     # 支持环境变量引用

# ============================================================
# API Keys
# ============================================================
api_keys:
  tavily: "${TAVILY_API_KEY}"      # Web search 用

# ============================================================
# 定时任务（主动模式）
# ============================================================
cron_jobs: []
  # - task: "检查待办事项并提醒"
  #   cron: "0 9 * * *"            # 每天9点
  #   ctx_id: 1                    # 发送到指定 ctx

# ============================================================
# 记忆系统配置
# ============================================================
memory:
  forget_days: 90                  # 记忆保留天数（默认90天）
  max_length: 500                  # 单条记忆最大字符数

# ============================================================
# Web skill 配置
# ============================================================
web:
  browser_profile: "~/.yuubot/browser_profile"  # Playwright 持久化目录
  headless: true                   # 是否无头模式
  download_dir: "~/.yuubot/downloads"           # 默认下载目录

# ============================================================
# 响应模式默认配置
# ============================================================
response:
  # 群聊默认响应模式
  group_default: "at"              # at | free
  # 私聊白名单（除 Master 外允许私聊的 QQ 号）
  dm_whitelist: []
```

## 配置加载逻辑

```python
# config.py 伪代码
import yaml
from pathlib import Path

def load_config(path: str = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    
    # 环境变量替换：${VAR_NAME} → os.environ["VAR_NAME"]
    raw = resolve_env_vars(raw)
    
    # 路径展开：~ → home dir
    raw = expand_paths(raw)
    
    # 校验必填字段
    validate(raw)
    
    return Config(**raw)
```

## 环境变量

敏感信息（API keys 等）支持通过环境变量注入：

```yaml
api_key: "${OPENAI_API_KEY}"
```

也可以使用 `.env` 文件，yuubot 启动时自动加载。

## 配置文件搜索顺序

1. `--config` 参数指定的路径
2. 当前目录 `./config.yaml`
3. `~/.yuubot/config.yaml`
