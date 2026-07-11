> **已过时**：这是历史服务设计，仅供追溯，不得作为当前实现依据。当前权威设计见
> [`design/system-design.md`](../../system-design.md)。

# Design: Share

**实现顺序：5**（依赖 [01-runtime-events.md](01-runtime-events.md)、
[02-admin-boundary.md](02-admin-boundary.md)）

## Scenario

管理员在 browse UI 选中 actor workspace 里的一个文件夹，点击「分享」，获得公网只读链接。任意互联网用户打开链接浏览静态 HTML/CSS/JS。Bot 只负责写入 workspace，不创建分享。

## Concepts

```text
ShareGrant   = durable 记录 + published/{share_id}/ 磁盘快照
ShareRegistry = Runtime.shares；publish / resolve / revoke
Owner        = 管理员（admin UI / HTTP）
Boundary     = workspace 目录 -> copy -> published/{share_id}/
```

磁盘与 URL 定义见 [deployment-design.md](../deployment/deployment-design.md)。

## ShareGrant

```py
class ShareGrant(msgspec.Struct, frozen=True):
  id: str
  actor_id: str
  source_path: str
  created_at: str
  expires_at: str | None
  revoked: bool = False
  refreshed_at: str | None = None
```

持久化：`ApplicationState.share_grants`。

## Core

```py
class ShareRegistry:
  async def publish(
    self, *, actor_id: str, source_path: str, expires_at: str | None,
  ) -> ShareGrant: ...

  async def refresh(self, share_id: str) -> ShareGrant: ...  # v1 可选实现

  async def revoke(self, share_id: str) -> ShareGrant: ...

  def resolve_file(self, share_id: str, rel_path: str) -> Path: ...
```

### publish（copy-on-share）

```text
1. 解析 workspace 下 source_path；须为目录；containment 校验（拒绝 ..）
2. share_id = new_opaque_id()
3. dst = data_dir/published/{share_id}
4. 原子发布（见下）
5. persist ShareGrant；runtime.emit("share.created", ...)
```

**Copy 原子性与失败回滚**

| 步骤 | 行为 |
| --- | --- |
| 拷贝 | 先写入 `published/{share_id}.tmp/`，完成后 `rename` 为 `published/{share_id}/`（同文件系统原子） |
| 中途失败 | 删除 `.tmp`；**不**写入 ShareGrant |
| 目标已存在 | 不应发生（新 opaque id）；若冲突则 abort 并 `500` |
| 并发 publish | 每次新 `share_id`；同一 `source_path` 可有多份独立快照 |

**符号链接**

- 拷贝时**不跟随**指向 published 根外的 symlink；或记录为断开链接的占位文件（v1 推荐：
  **跳过**逃逸 symlink 并记日志）。
- `resolve_file` 打开文件前 `realpath` containment 检查，拒绝逃出 `published/{share_id}/`。

### refresh（扩展点）

`POST /api/shares/{share_id}/refresh`：对未 revoke 的 grant，重复 publish 拷贝语义到**同一**
`share_id` 目录（先 `.tmp` 再 swap）。语义：

- 成功：更新 `refreshed_at`；公网 URL 不变。
- 失败：保留旧快照与旧 grant；返回 `500`，不部分替换。

v1 可实现为 `501` / 未实现；contract 已定义以便后续对齐。

### revoke 与过期清理

```text
revoke(share_id):
  grant.revoked = True
  persist
  schedule async_delete(published/{share_id}/)   # 不阻塞 HTTP

过期:
  resolve_file / public GET 检查 expires_at；过期视为 404
  后台 sweeper 删除过期 grant 与 published 目录（owner：进程 startup 注册的定时任务）
```

### resolve_file（公网 GET）

```text
load grant → revoked / expired → 404
normalize rel_path（空 path → index 查找顺序）
realpath containment → Path
```

**MIME / index fallback**

| 请求 path | 行为 |
| --- | --- |
| `""` 或 `/` | 依次尝试 `index.html`, `index.htm`；无则 404 |
| 无扩展名且为目录 | 同上 index 规则 |
| 文件 | 按扩展名映射 `Content-Type`（`.html` `text/html`；`.js` `application/javascript`；未知 `application/octet-stream`） |
| 目录 listing | v1 **不提供**；目录无 index → 404 |

## HTTP

错误信封见 [02-admin-boundary.md](02-admin-boundary.md)。

### Admin（`admin_url_base` + AdminAuth）

**`POST /api/shares`**

Request:

```json
{ "actor_id": "amy", "source_path": "reports/q3", "expires_at": "2026-12-31T00:00:00Z" }
```

Success `201`:

```json
{
  "id": "sh_xxx",
  "actor_id": "amy",
  "source_path": "reports/q3",
  "created_at": "...",
  "expires_at": "...",
  "revoked": false,
  "url": "https://public.example.com/s/sh_xxx/"
}
```

`url` 用 `deployment-design` 的 `share_url` 公式。

| Status | code | 场景 |
| --- | --- | --- |
| 400 | `bad_request` | `source_path` 非目录、非法路径 |
| 404 | `not_found` | 未知 `actor_id` |
| 500 | `internal_error` | 拷贝失败 |

**`GET /api/shares`** → `{ "items": [ ShareGrant, ... ] }`

**`GET /api/shares/{share_id}`** → 单条 ShareGrant + `url`

**`DELETE /api/shares/{share_id}`** → `200` `{ "id", "revoked": true }`；published 异步清理

**`POST /api/shares/{share_id}/refresh`**（可选）→ `200` 更新后的 ShareGrant

### Public（`public_url_base`）

```http
GET /s/{share_id}/{path}
```

| 场景 | 响应 |
| --- | --- |
| 成功 | `200` + 文件 body + `Content-Type` |
| revoked / expired / 未知 share | `404 not_found` |
| path 逃逸 | `404 not_found` |

## Context access

```text
Core needs: actor workspace, data_dir/published, ShareGrant store, ShareRegistry
Source:
  HTTP body     <- Facade after AdminAuth
  runtime.shares <- Runtime
```

## Invariants

1. Actor / `yb.*` **无**分享 API。
2. 公网只读 `published/{share_id}/`，永不直读 workspace。
3. `share_id` 为不可猜测随机 id。
4. publish 失败不得留下半成品 grant 或可见目录。
5. revoke 后公网立即 404（逻辑删除优先于物理删除）。

## Related

- 前置：[01-runtime-events.md](01-runtime-events.md)、[02-admin-boundary.md](02-admin-boundary.md)
- 索引：[README.md](README.md)
