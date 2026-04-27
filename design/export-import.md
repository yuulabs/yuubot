# yuubot 导出 / 导入归档协议（runtime unification phase 0）

本文档定义 runtime architecture unification 的正式迁移协议，用于在 `bare_machine` 与 `container` 两种标准部署之间迁移数据。

## 1. 目标

导出 / 导入必须满足：

1. 是正式产品能力，而不是临时运维脚本。
2. 支持按类别选择导出内容。
3. 能覆盖当前旧部署到标准化新部署的迁移路径。
4. 不依赖 host/runtime 路径投影。

## 2. 归档格式

- **容器格式**：`zip`
- **主清单文件**：`manifest.json`
- **manifest version**：`1`

归档是面向“产品数据”的协议，不等同于某个 sqlite 文件或某个目录树的直接打包。

## 3. 类别

正式支持三类数据：

1. `core`
2. `messages`
3. `traces`

CLI 必须支持任意合法组合，例如：

- `core`
- `messages`
- `traces`
- `core+messages`
- `core+traces`
- `core+messages+traces`

## 4. 类别边界

### 4.1 `core`

`core` 表示 bot 的核心产品状态，至少包括：

1. 主 bot 数据库中与角色、设置、记忆、图片库、topic 等产品状态相关的数据
2. 与这些状态直接关联的本地资产
3. 恢复标准化部署所需的最小元信息

它不自动包含完整消息归档，也不自动包含 traces。

### 4.2 `messages`

`messages` 表示消息归档及其依赖媒体，至少包括：

1. 消息记录
2. forward / 引用所需的消息相关数据
3. 被消息记录引用的媒体 payload

它的边界是“恢复消息浏览/检索语义所需的数据”。

### 4.3 `traces`

`traces` 表示 agent / trace 侧证据数据，至少包括：

1. `~/.yagents/traces.db` 的等价内容
2. 恢复 trace 浏览与排障所需的相关元信息

它与 `core`、`messages` 分离，避免标准迁移时把运行调试产物和业务数据混在一起。

## 5. manifest 结构

`manifest.json` 至少包含：

```json
{
  "manifest_version": 1,
  "created_at": "2026-04-19T00:00:00Z",
  "source": {
    "product": "yuubot",
    "yuubot_version": "x.y.z",
    "yuuagents_version": "a.b.c",
    "deployment_mode": "bare_machine"
  },
  "categories": ["core", "messages"],
  "entries": {
    "core": {
      "metadata_path": "core/metadata.json",
      "payload_paths": ["core/db.sqlite3", "core/assets/..."]
    },
    "messages": {
      "metadata_path": "messages/metadata.json",
      "payload_paths": ["messages/db.sqlite3", "messages/media/..."]
    }
  }
}
```

规则：

1. `categories` 只声明本次实际导出的类别。
2. 未选中的类别不得伪装成空目录存在于 manifest 中。
3. manifest 必须足够描述导入阶段的校验与重分发行为。

## 6. 初始化导入行为

标准化新部署必须支持初始化导入：

1. `ybot import <archive>` 可在已安装环境中显式执行。
2. container 安装/启动流程必须支持 `--import-path <archive>` 或等价入口。
3. 初始化导入发生在服务正式对外提供功能前。
4. 导入完成后，数据必须被重分发到标准化目标位置，而不是保留旧部署路径假设。

## 7. 路径与重分发原则

导入时必须满足：

1. 归档里的路径只作为归档内部布局使用。
2. 目标部署中的真实路径由当前 deployment manifest / config 决定。
3. 不允许通过 `/mnt/host`、host-home 映射、container-home 回投等旧逻辑恢复数据。

换句话说：导入负责“把产品数据放到当前部署应在的位置”，而不是“把旧路径原样复制回去”。

## 8. 旧部署兼容说明

在 runtime unification 切换期间，需要提供旧部署兼容导出路径。

最低要求：

1. **必须支持旧部署导出 `core+messages`**
2. 这个兼容导出能力是正式迁移桥，而不是一次性人工脚本
3. 兼容桥的职责是把旧部署数据解释为标准 manifest categories，而不是延续旧 runtime 语义

`traces` 可随后补齐，但 `core+messages` 是迁移前置条件。

## 9. 验收要求

协议完成后，至少要能证明：

1. 选 `core` 时不会偷偷带出 `messages` 或 `traces`
2. 选 `messages` 时会包含消息引用的媒体 payload
3. 选 `traces` 时不会污染核心迁移数据
4. 旧部署 `core+messages` 导出可导入到新的 bare-machine 或 container 部署
5. 导入后的 bot 看到的是当前部署环境中的真实路径，而不是历史 host/runtime 映射路径
