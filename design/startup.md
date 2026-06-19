# yuubot 全栈启动指南

> 用于本地开发、前端联调和全量测试。

## 环境要求

| 组件 | 版本 | 验证命令 |
|------|------|----------|
| Python | ≥ 3.14 | `python3 --version` |
| Node.js | ≥ 22 | `node --version` |
| pnpm | ≥ 9 | `pnpm --version` |
| uv | ≥ 0.5 | `uv --version` |

## 1. 生成 Master Key

`secrets.master_key` 必须是 32 字节的 base64 编码：

```bash
python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"
```

测试用固定 key（**仅限本地 loopback**）：

```
eXV1Ym90LXRlc3QtbWFzdGVyLWtleS0zMi1ieXRlcyE=
```

## 2. 准备 config.yaml

```yaml
# 保存为 config.yaml (放项目根目录，已在 .gitignore)
admin:
  host: 127.0.0.1
  port: 8781

server:
  daemon_host: 127.0.0.1
  daemon_port: 8780
  daemon_secret: test-daemon-secret

database:
  path: ""  # 空 = 使用 <data_dir>/yuubot/yuubot.db

secrets:
  master_key: eXV1Ym90LXRlc3QtbWFzdGVyLWtleS0zMi1ieXRlcyE=

trace:
  enabled: false

paths:
  data_dir: ~/.yuubot

yuuagents:
  strict: false
  tool_backends:
    background: {}
```

## 3. 构建前端

```bash
cd web
pnpm install
pnpm build         # tsc -b && vite build
```

产物输出到 `web/dist/`。admin 进程启动时会自动挂载该目录。

**开发模式**（前端热更新 + API 代理）：

```bash
cd web && pnpm dev   # → http://127.0.0.1:5173
```

Vite dev server 会把 `/api` 和 `/healthz` 代理到 `127.0.0.1:8781`。

## 4. 启动后端

### 4.1 仅 Admin + 前端（不需要 daemon）

```bash
uv run ybot --config config.yaml admin
# http://127.0.0.1:8781 → 前端页面
# /api/resources/* → 502 (daemon 未启动)
```

### 4.2 完整启动（daemon + admin）

**方式 A：两个终端分别启动**

```bash
# 终端 1
uv run ybot --config config.yaml daemon

# 终端 2
uv run ybot --config config.yaml admin
```

**方式 B：一键启动（子进程）**

```bash
uv run ybot --config config.yaml dev
# 自动启动 daemon (8780) + admin (8781)
# 任一进程退出则全部终止
```

### 4.3 启动验证

```bash
# 健康检查
curl http://127.0.0.1:8781/healthz | python3 -m json.tool

# 预期输出：
# {
#   "status": "ok",
#   "admin": "127.0.0.1:8781",
#   "daemon": "http://127.0.0.1:8780",
#   "ingress_rules": 0,
#   "integrations": 0,
#   "plugins": 0
# }

# 前端页面
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8781/
# 预期: 200

# SPA fallback（直接访问子路由）
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8781/admin/conversations
# 预期: 200
```

## 5. API 测试速查

### 创建 LLM Backend

```bash
curl -s -X POST http://127.0.0.1:8781/api/resources/llm-backends \
  -H "Content-Type: application/json" \
  -d '{
    "name": "openai-main",
    "yuuagents_provider": "openai",
    "model_capabilities": {"chat": true, "tool_calling": true},
    "models": {"names": ["gpt-4o"]},
    "pricing": {"entries": []},
    "budget": {"daily_usd": 10},
    "provider_options": {"base_url": "https://api.openai.com/v1"},
    "default_model": "gpt-4o"
  }' | python3 -m json.tool
```

### 创建 Character

```bash
curl -s -X POST http://127.0.0.1:8781/api/resources/characters \
  -H "Content-Type: application/json" \
  -d '{
    "name": "helper",
    "description": "A helpful assistant",
    "system_prompt": "You are a helpful assistant.",
    "facade_module": "",
    "default_hints": {"language": "zh-CN", "tone": "friendly"}
  }' | python3 -m json.tool
```

### 创建 Capability Set

```bash
curl -s -X POST http://127.0.0.1:8781/api/resources/capability-sets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "default",
    "description": "Default capability set"
  }' | python3 -m json.tool
```

### 创建 Actor（需要先有 Character + LLM Backend + Capability Set）

```bash
# 获取各资源 id
CHAR_ID=$(curl -s http://127.0.0.1:8781/api/resources/characters | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")
BK_ID=$(curl -s http://127.0.0.1:8781/api/resources/llm-backends | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")
CAP_ID=$(curl -s http://127.0.0.1:8781/api/resources/capability-sets | python3 -c "import sys,json; print(json.load(sys.stdin)['data'][0]['id'])")

curl -s -X POST http://127.0.0.1:8781/api/resources/actors \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"test-actor\",
    \"type\": \"simple_loop\",
    \"default_model\": \"gpt-4o\",
    \"default_character_id\": \"$CHAR_ID\",
    \"capability_set_id\": \"$CAP_ID\",
    \"default_llm_backend_id\": \"$BK_ID\",
    \"default_budget\": {\"max_steps\": 20},
    \"enabled\": true
  }" | python3 -m json.tool
```

## 6. 服务端口速查

| 端口 | 服务 | 说明 |
|------|------|------|
| 8780 | Daemon | 资源 CRUD API（admin 代理到此处） |
| 8781 | Admin | 前端页面 + API 代理 + 插件管理 |
| 8782 | Trace UI | yuutrace 监控面板（可选） |
| 5173 | Vite Dev | 前端开发热更新（`pnpm dev`） |

## 7. 停止服务

```bash
# 方式 A：Ctrl+C 对应终端

# 方式 B：查找并终止
pkill -f "yuubot.cli"
```

## 8. 重置数据

```bash
rm -rf ~/.yuubot/yuubot/yuubot.db
```

`database.path` 为空时数据库位于 `<data_dir>/yuubot/yuubot.db`。

## 9. 常见问题

**`secrets.master_key must be 32 bytes base64`**

→ 检查 master_key 是否为 32 字节 base64。用第 1 步的命令重新生成。

**`daemon_unavailable` (502)**

→ daemon 进程未启动。检查 `ybot daemon` 是否在运行。

**前端页面白屏 / 404**

→ 确认 `web/dist/` 存在。运行 `cd web && pnpm build`。

**`Object missing required field 'model_capabilities'`**

→ LLM Backend 创建 payload 缺少必填字段。参考上方的创建示例。
