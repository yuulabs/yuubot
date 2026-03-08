# Recorder 进程设计

## 职责

Recorder 是 yuubot 的消息落盘与转发进程，是 NapCat 与 Daemon 之间的桥梁。

1. **接收** — 启动反向 WS 服务器，NapCat 连接过来推送 OneBot V11 事件
2. **落盘** — 解析消息事件，写入 SQLite（保证不丢消息）
3. **ctx 管理** — 为新的群聊/私聊分配 ctx_id
4. **转发** — 通过内部 WS 将事件转发给 Daemon
5. **API 代理** — 提供 HTTP API，代理 NapCat 的发送消息等接口

## 启动方式

```bash
ybot recorder start [--config config.yaml]
```

Recorder 应与 NapCat 绑定启停。提供启动脚本 `scripts/start_recorder.sh`。

## 模块设计

### server.py — 反向 WS 服务器

```python
# 伪代码
class NapCatWSServer:
    """接收 NapCat 反向 WS 连接"""
    
    async def start(self, host: str, port: int):
        """启动 WS 服务器，等待 NapCat 连接"""
        
    async def on_event(self, raw: dict):
        """收到 OneBot V11 事件"""
        event = parse_onebot_event(raw)
        
        # 1. 落盘
        await self.store.save(event)
        
        # 2. 转发给 daemon
        await self.relay.broadcast(event)
```

NapCat 配置反向 WS 地址指向此服务器（如 `ws://127.0.0.1:8765`）。

### store.py — 消息存储

负责将 OneBot 事件解析并写入 SQLite。

核心逻辑：
1. 收到 `message` 类型事件时：
   - 解析消息段（text/image/at/reply 等）
   - 查找或分配 ctx_id
   - 写入 `messages` 表
2. 收到其他事件（notice/request 等）时：
   - 写入 `events` 表（可选，用于审计）

### relay.py — 内部 WS 转发

```python
class RelayServer:
    """内部 WS 服务器，Daemon 连接过来接收事件"""
    
    async def start(self, host: str, port: int):
        """启动内部 WS 服务器"""
        
    async def broadcast(self, event: dict):
        """将事件广播给所有已连接的 daemon"""
        for client in self.clients:
            await client.send(event)
```

内部 WS 端口与 NapCat 反向 WS 端口不同（如 `ws://127.0.0.1:8766`）。

当 Daemon 未连接时，事件仍然正常落盘，只是不触发 agent。Daemon 重连后可从 SQLite 查询遗漏的消息。

### api.py — HTTP API 代理

Recorder 同时提供 HTTP API，用于：
1. **发送消息** — `POST /send_msg`，转发到 NapCat 的 HTTP API
2. **查询信息** — `GET /get_group_list`, `GET /get_friend_list` 等
3. **ctx 查询** — `GET /ctx/{ctx_id}` 返回 ctx 对应的群号/QQ号

这样 Skills（如 `ybot im send`）不需要直接连接 NapCat，统一通过 Recorder API。

```
Skills CLI  ──HTTP──▶  Recorder API  ──HTTP──▶  NapCat HTTP API
```

## 数据流

```
NapCat
  │ (反向WS, OneBot V11 JSON)
  ▼
NapCatWSServer (server.py)
  │
  ├──▶ Store (store.py) ──▶ SQLite
  │
  └──▶ RelayServer (relay.py) ──▶ Daemon (如果在线)
```

## 配置项

```yaml
recorder:
  # NapCat 反向 WS 服务器
  napcat_ws:
    host: "0.0.0.0"
    port: 8765
  
  # 内部 WS（转发给 daemon）
  relay_ws:
    host: "127.0.0.1"
    port: 8766
  
  # HTTP API
  api:
    host: "127.0.0.1"
    port: 8767
  
  # NapCat HTTP API 地址（用于代理发送）
  napcat_http: "http://127.0.0.1:3000"
```

## 可靠性考虑

- Recorder 崩溃重启后，NapCat 会自动重连反向 WS
- 落盘使用 SQLite WAL 模式，保证写入性能和并发读取
- 转发失败（daemon 不在线）不影响落盘
- Recorder 启动时从 SQLite 热加载 ctx_id 映射到内存
