# 08. Bridge 节点与远程资源

Bridge 是基础设施层，不是 Channel。它允许远程节点主动连回 yuubot，提供计算资源或网络位置能力。

## 使用场景

- GPU 节点：Actor 通过 Bridge 执行命令、跑训练、传文件。
- 家宽 PC：需要特定网络位置的外部服务在家宽环境运行，通过 Bridge 暴露给 yuubot。
- 移动设备或临时机器：节点主动连回，不需要公网 IP。

## 安全原则

用户对安全不应承担过多细节，系统默认必须安全：

1. Client 付出更多资源，所以 Client 必须严格验证 Server。
2. private key 不离开所属机器。
3. registration token 一次性、短期有效、DB 中 hash 存储。
4. heartbeat 使用 node token 或 HMAC，不复用 registration token。
5. Tunnel key 和 Command key 分离。
6. Bridge client 使用专用低权限用户，不默认 root。

## Key 模型

Bridge 有两个方向，使用两套 key。

### Tunnel Key

```text
owner: client
private key: client
public key: server authorized_keys
purpose: client 连接 server 并建立 SSH reverse tunnel
```

### Command Key

```text
owner: server
private key: server
public key: client authorized_keys
purpose: server 通过 tunnel 登录 client 执行命令
```

这样“client 生成 key”和“server 生成 key”不冲突，因为它们不是同一把 key。

## 注册流程

1. Admin UI 生成 registration token。
2. 用户在远程节点运行 `yuubot-bridge-client register`。
3. Client 通过 HTTPS 连接 Server。
4. Client 验证 Server：
   - 生产：系统 CA + Let's Encrypt 等公开证书。
   - 自签名：配置 server certificate fingerprint，必须 pin。
5. Client 生成 Tunnel keypair。
6. Client POST `/bridge/register`：

```json
{
  "registration_token": "one_time_token",
  "node_name": "gpu-node-1",
  "tunnel_ssh_pubkey": "ssh-ed25519 AAAA...",
  "capabilities": {
    "gpu": {"count": 2, "model": "RTX 4090", "vram_gb": 48},
    "cpu": {"cores": 32},
    "services": ["ssh", "jupyter", "im_login"]
  }
}
```

7. Server 验证 token。
8. Server 生成 Command keypair，保存 private key，返回 public key 给 client。
9. Server 分配 reverse tunnel port。
10. Server 返回：

```json
{
  "node_id": "uuid",
  "node_api_token": "secret-for-heartbeat",
  "ssh_user": "yuubot-bridge",
  "reverse_tunnel": {
    "remote_host": "127.0.0.1",
    "remote_port": 2201,
    "local_host": "127.0.0.1",
    "local_port": 22
  },
  "server_ssh_host_key_fingerprint": "SHA256:...",
  "command_ssh_pubkey": "ssh-ed25519 AAAA..."
}
```

11. Client 将 `command_ssh_pubkey` 加入本机 `authorized_keys`。
12. Client 建立 reverse tunnel：

```bash
ssh -N \
  -R 127.0.0.1:2201:127.0.0.1:22 \
  -i ~/.yuubot/bridge/tunnel_key \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o StrictHostKeyChecking=yes \
  yuubot-bridge@server
```

13. Server 通过 `ssh -p 2201 127.0.0.1` 访问 Client。

## authorized_keys 限制

Server 上 tunnel key 条目应尽量限制：

```text
no-pty,no-agent-forwarding,no-X11-forwarding,permitlisten="127.0.0.1:2201"
```

Client 上 command key 对应用户应是低权限用户，是否允许 docker/sudo 由用户显式配置。

## Heartbeat

```text
POST /bridge/heartbeat
Authorization: Bearer node_api_token
```

或使用 HMAC 签名。

Server 规则：

- 每 30s heartbeat。
- 超过 90s 标记 offline。
- tunnel 断开时标记 degraded/offline。

## Agent API

只对 master / explicit trusted Actor 开放。

```python
nodes = yb.bridge_list_nodes()
result = yb.bridge_exec("gpu-node-1", "nvidia-smi")
yb.bridge_upload("gpu-node-1", local_path="/tmp/a", remote_path="/data/a")
yb.bridge_download("gpu-node-1", remote_path="/data/b", local_path="/tmp/b")
```

## Channel 使用 Bridge

Channel 可以依赖 Bridge 的网络位置能力，例如某个可选 IM adapter 需要访问 home-pc 上的本地服务：

```yaml
channels:
  im-home:
    bridge_node: home-pc
    local_service_port: 3000
```

但 Bridge 本身不参与消息路由，也不要求 v2 core 内置该 IM adapter。消息流仍然是：

```text
External service on home-pc
  -> Bridge tunnel/proxy
  -> Optional ChannelAdapter
  -> Gateway
  -> Actor
```
