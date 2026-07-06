# Design: External Inbound and Conversation Wakeup

**实现顺序：3**（依赖 [01-runtime-events.md](01-runtime-events.md)、
[02-admin-boundary.md](02-admin-boundary.md)）

## Scenario

外部系统需要把消息送进指定 Actor 的 conversation，并触发 `run_loop`。分两条 HTTP 边界：

- **App webhook**（`public_url_base`）：第三方平台回调；Integration adapter 验签。
- **Actor inbound**（`admin_url_base`）：向指定 actor 投递 user 消息；同机 loopback 默认
  放行，远程经 reverse SSH 打回本机 loopback。

入站路径解析 HTTP 后调用 `runtime.wakeup.deliver`（定义见
[01-runtime-events.md](01-runtime-events.md)）。与 Tasks 无模块耦合；共享 `WakeupDelivery`。

公网路由与部署细节见 [deployment-design.md](../deployment/deployment-design.md)。

## Concepts

```text
AppWebhook           = app-level webhook；Integration adapter 验签；公网边界
ActorInbound         = admin 面 actor 入站；loopback 默认可用；远程经 reverse SSH
InboundEnvelope      = 公网边界校验后的入站消息数据
IntegrationInboundAdapter = 平台 HTTP → InboundEnvelope
```

`ActorMessage`、`WakeupTarget`、`WakeupPayload`、`WakeupDelivery` 见 01；本文只描述 HTTP
facade 与 adapter 边界。

## Data Shapes

```py
class InboundEnvelope(msgspec.Struct, frozen=True):
  text: str
  conversation_id: str | None = None
  route: str | None = None
  source: dict = {}

class ActorInboundBody(msgspec.Struct, frozen=True):
  text: str
  conversation_id: str | None = None
  source: dict = {}
```

## HTTP contract

错误信封见 [02-admin-boundary.md](02-admin-boundary.md#http-error-envelope)。

### `POST /webhooks/app/{integration_type}`（public）

正式公网 contract。`POST /api/inbound/{integration_type}` 仅为历史调试路径，**不属于**
v1 公网或管理面正式 contract。

**Request**

```http
POST /webhooks/app/{integration_type}
Content-Type: application/json   # 或 adapter 声明的 content-type
# 平台签名头由 adapter 定义（如 Authorization、X-Hub-Signature-256）
```

Body 由 adapter 解析；校验通过后产出 `InboundEnvelope`。典型 JSON（平台相关）：

```json
{
  "route": "qq.group.id:232411",
  "text": "ping",
  "conversation_id": "route-c1",
  "source": { "message_id": "m1" }
}
```

**Success `200`**

```json
{
  "integration_type": "qq",
  "delivered": true,
  "actor_id": "amy",
  "conversation_id": "route-c1"
}
```

`delivered: false` 表示验签通过但 route 未命中 Gateway；仍 `200`，不投递 mailbox。

**Errors**

| Status | code | 场景 |
| --- | --- | --- |
| 400 | `bad_request` | body 无法解析、缺少 route/text |
| 401 | `unauthorized` | adapter 验签失败 |
| 404 | `not_found` | 未知 `integration_type` |
| 503 | `provider_unavailable` | Integration 未 enable |
| 500 | `internal_error` | 未预期错误 |

### `POST /api/actors/{actor_id}/inbound`（admin）

**Request**

```http
POST /api/actors/{actor_id}/inbound
Content-Type: application/json
# loopback 免 AdminAuth；否则须 AdminAuth（02）
```

```json
{
  "text": "job done",
  "conversation_id": "c1",
  "source": { "caller": "ci" }
}
```

**Success `200`**

```json
{
  "actor_id": "amy",
  "conversation_id": "c1",
  "delivered": true
}
```

**Errors**

| Status | code | 场景 |
| --- | --- | --- |
| 400 | `bad_request` | 缺少 `text`、JSON 非法 |
| 401 | `unauthorized` | 非 loopback 且未通过 AdminAuth |
| 404 | `not_found` | 未知 `actor_id` |
| 500 | `internal_error` | mailbox 不可用 |

远程可信主机典型路径（reverse SSH）：

```text
remote$ ssh -R 18765:127.0.0.1:8765 yuubot-host -N
remote$ curl -sS http://127.0.0.1:18765/api/actors/{actor_id}/inbound \
  -H 'Content-Type: application/json' \
  -d '{"text":"job done","conversation_id":"..."}'
```

## Central Flow

### Wakeup delivery（Core）

```py
await runtime.wakeup.deliver(
  WakeupTarget(
    kind="app_webhook",  # 或 actor_inbound
    actor_id=target.actor_id,
    conversation_id=envelope.conversation_id,
  ),
  WakeupPayload(text=envelope.text, source=envelope.source),
)
```

| Kind | Authority | Target resolution | History effect |
| --- | --- | --- | --- |
| `app_webhook` | Integration adapter | `envelope.route` → Gateway | user input（01 role 表） |
| `actor_inbound` | loopback 或 AdminAuth | path `actor_id` + body | user input |

`task_delivery` 由 [04-tasks.md](04-tasks.md) 经同一 `runtime.wakeup.deliver` 投递。

```py
# app webhook (public_url_base)
envelope = await adapter.validate_webhook(request, secrets=secret_resolver)
target = gateway.resolve(envelope.route)
if target is None:
  return WebhookResponse(delivered=False, ...)
await runtime.wakeup.deliver(
  WakeupTarget(kind="app_webhook", actor_id=target.actor_id, conversation_id=envelope.conversation_id),
  WakeupPayload(text=envelope.text, source=envelope.source),
)

# actor inbound (admin_url_base)
assert is_loopback(client) or request.state.auth is not None
body = parse(ActorInboundBody)
await runtime.wakeup.deliver(
  WakeupTarget(kind="actor_inbound", actor_id=actor_id, conversation_id=body.conversation_id),
  WakeupPayload(text=body.text, source=body.source),
)
```

```py
class IntegrationInboundAdapter:
  async def validate_webhook(
    self,
    request: PublicRequest,
    *,
    secrets: SecretResolver,
  ) -> InboundEnvelope:
    pass
```

### Actor wake handling

```py
async def handle_mailbox_message(actor, message: ActorMessage) -> None:
  conversation = await actor.conversation_for_message(message)
  inbound_kind = message.source.get("inbound_kind")

  if inbound_kind in {"task_delivery", "conversation_callback"}:
    # 见 04-tasks.md
    ...
    return

  await conversation.run_loop(user_input_from_actor_message(message))
```

`conversation_id=None` 的 `actor_inbound` 是普通 user input，由 Actor 默认 inbound loop 按 TTL 复用当前 actor conversation 或新建 conversation；它不是 conversation callback。`conversation_callback` 是 developer continuation，必须由投递方绑定明确 owner conversation。

完整 `ActorMessage` 与 role 映射见 [01-runtime-events.md](01-runtime-events.md#actormessage共享-mailbox-契约)。

## Context Access

```text
Core needs:
  runtime.wakeup, Gateway route table, Integration registry, SecretResolver
  admin_url_base / public_url_base

Source:
  wakeup, gateway, integrations  <- Runtime
  SecretResolver, URL bases        <- DeploymentConfig

Access path:
  Public facade -> adapter validate -> runtime.wakeup.deliver
  Admin facade -> loopback or AdminAuth -> runtime.wakeup.deliver

Missing context: none
Accepted debt:
  v1 远程 actor inbound 以 SSH 信任边界为主；请求级 HMAC 后续补充。
```

## Invariants

1. 公网 inbound 仅 `POST /webhooks/app/{integration_type}`；不经 AdminAuth。
2. Actor inbound 只在 `admin_url_base`；loopback 默认放行，否则 AdminAuth。
3. App webhook 认证属于 Integration adapter。
4. 每次 wakeup 恰好一次 `runtime.wakeup.deliver` → 一次 mailbox enqueue 尝试。
5. Actor mailbox 串行化是外部入站恢复 Conversation 的唯一路径。
6. `app_webhook` / `actor_inbound` 作为 user input；不得追加 developer role。

## Related

- 共享投递与 ActorMessage：[01-runtime-events.md](01-runtime-events.md)
- Admin 边界与错误信封：[02-admin-boundary.md](02-admin-boundary.md)
- Task 终态投递：[04-tasks.md](04-tasks.md)
