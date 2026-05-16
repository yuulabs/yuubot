# TODO: Integration Runtime Storage

## 背景

`IntegrationFactory.create(record, repository, *, gateway)` 现在给 integration 的资源只有：record（自身配置）、`ResourceRepository`（平台资源 CRUD）、`Gateway`（消息出入口）。这覆盖了「读自己 config」「投递消息」，但**没覆盖 integration 自己的持久化业务数据**。

具体的真实需求：

- **Telegram** 要存聊天记录 / 媒体元数据（量大，schema 由 integration 自己定）。
- **Linear、GitHub** webhook 要记 `last_event_cursor` / `delivery_id` 去重（小，但必须跨 daemon 重启保留）。
- **任何 long-poll 类 integration**（Telegram getUpdates 也是）要持久化 `update_offset` 之类的游标——内存里的 `self.offset` 在重启后丢失，会导致重复处理或漏处理。

如果让每个 integration 各自决定怎么落盘（开 SQLite、写 JSON、共用平台 DB……），会有几个具体问题：

1. **Blast radius 不可控**：integration 直接拿 `repository` 写自己的表 / 跑自己的 migration，一次失误能让 daemon 起不来。
2. **生命周期清理困难**：删除 integration 时，平台不知道它在哪些地方落了什么，没法可靠地清。
3. **入门门槛高**：只为存一个 int 游标，先要选 SQLite/JSON、写 atomic write、想 lock，每个作者重新发明一遍。

## 目标

让每个 integration 有一个**显式、独占、易清理**的存储位置。`factory.create` 收窄出口，不再让 integration 直接接触平台 `ResourceRepository`。

## 设计：只给目录

### 核心决策

**只分配一个私有目录，不提供平台级 KV 服务。**

理由：
- Integration 自己开个 SQLite 就有 atomic write，零样板。
- 清理 = `rmtree(data_dir)`，比 DB 行删除还干净。
- 对于外部子进程插件（见 `todo-integration-external-plugin.md`），进程隔离意味着插件根本看不到平台 DB，KV 路由地址反而增加协议复杂度。
- 存一个 int 游标写本地文件比 HTTP round-trip 更快更简单。

### `IntegrationStorage` 注入

`factory.create` 签名调整为：

```python
async def create(
    self,
    record: IntegrationRecord,
    *,
    gateway: Gateway,
    storage: IntegrationStorage,
) -> IntegrationInstance: ...
```

`IntegrationStorage` 是一个绑定到当前 `integration_id` 的小对象：

```python
class IntegrationStorage(Protocol):
    @property
    def data_dir(self) -> Path: ...   # <data_root>/integrations/<integration_id>/
```

`ResourceRepository` 从 `factory.create` 签名中移除——平台资源对 integration 不可见。

### 目录约定

- 路径形如 `<data_root>/integrations/<integration_id>/`，平台保证它存在、整个目录归该 integration 独占。
- Integration 自己决定里面的结构：自己的 `tg.db`（SQLite）、自己的 migration、自己的索引、放文件、放 lmdb 都可以。平台不查 schema，不做 join，不做备份分片。
- 备份 = 备份这一整个目录 + 平台 DB。导出/迁移以目录为单位。

### 生命周期挂钩

- **enable / create**：平台保证 `data_dir` 存在；不预创建任何文件。
- **disable / close**：平台不删 data_dir——integration 可能只是被临时停用，重新 enable 时数据要在。
- **delete integration**：平台先 `disable()`，再 `rmtree(data_dir)`。一次性完成。
- **rename integration**：禁止。`integration_id` 是稳定主键，rename 等价于 delete + create。

## 与外部插件的关系

对于外部子进程插件（`todo-integration-external-plugin.md`），`data_dir` 通过环境变量 `YUUBOT_DATA_DIR` 传递给插件进程。插件在自己的进程里自由使用这个目录，效果完全一致。

## 影响范围

- `core/integrations/contracts.py` — `IntegrationFactory.create` 签名，新增 `IntegrationStorage` Protocol
- `core/integrations/core.py` — 在 `_enable_locked` 里构造并注入 storage；reconcile / disable / delete 路径协调清理
- `core/integrations/echo.py` — 跟随签名调整（不实际使用 storage，但要接收）
- Bootstrap config — 复用 `paths.data_dir` 配置项，`<data_dir>/integrations/` 作为 integration 私有根
- `design/arch-v2/03-runtime-resources.md` — Integration 段落补「资源契约」小节
- 测试 — 删除 integration 时 data_dir 的清理一致性

## 非目标

- 跨 integration 的存储共享 / 全局二级索引。
- 平台层面的全局备份策略（先以「整个 data_dir 树 + DB」为单元）。
- Integration 沙箱化文件访问（暂不限制 integration 写到 data_dir 之外，靠约定；外部插件靠进程隔离）。
