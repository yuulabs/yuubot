# 02. Bootstrap Config

Bootstrap Config 只负责让系统启动，不承载用户的主要业务配置。

## 原则

- 文件和 env 用于 **启动必需配置**。
- Runtime Resources 存 DB，不应长期依赖 YAML。
- 修改 Bootstrap Config 通常需要重启。
- Admin UI 可以展示 Bootstrap Config，但默认不允许在线修改；UI 应标注 `restart required`。

## 推荐文件

短期兼容当前结构：

```text
.env
llm.yaml
config.yaml
docker_config.yaml
```

长期目标：

```text
.env
config.yaml
```

但不要求 v2 第一阶段删除 `llm.yaml` 或 `docker_config.yaml`。它们可以作为 seed source 或迁移输入。

## 启动加载顺序

短期：

```text
.env
  -> llm.yaml
  -> config.yaml
  -> docker_config.yaml
  -> DB runtime overrides / runtime resources
```

注意：DB 中的大对象不应叫 “config override”，而应叫 Runtime Resource，例如 `llm_providers`、`actors`、`channels`。

## Bootstrap Config 示例

```yaml
admin:
  host: 127.0.0.1
  port: 8781
  secret: ${YUU_ADMIN_SECRET}

server:
  daemon_host: 127.0.0.1
  daemon_port: 8780

database:
  path: ${YUU_DATA_DIR}/yuubot.db

secrets:
  # 用于加密 DB 中的 provider api keys / oauth tokens。
  # 必须稳定；丢失后 DB 中 secret 无法解密。
  master_key: ${YUU_SECRET_KEY}

trace:
  enabled: true
  collector_host: 127.0.0.1
  collector_port: 4318
  ui_host: 127.0.0.1
  ui_port: 8782

paths:
  data_dir: ${YUU_DATA_DIR}
  workspace_dir: ${YUU_WORKSPACE_DIR}
  logs_dir: ${YUU_LOGS_DIR}

seed:
  builtin_providers: true
  builtin_characters: true
  builtin_actors: true
  builtin_web_channel: true
```

## 应保留在 Bootstrap Config 的内容

- DB 路径和数据目录。
- daemon/admin 监听 host/port。
- trace collector/ui host/port。
- admin secret。
- secret encryption master key。
- 容器和路径相关配置。
- 首次 seed 开关。
- 低层网络绑定和内部服务 URL。

## 不应保留为长期 YAML 配置的内容

这些应该进入 DB Runtime Resources：

- LLM providers。
- Search providers。
- GitHub / Linear / W&B / SwanLab credentials。
- Character 定义和覆盖。
- Actor 定义、模型绑定、资源策略。
- Channel 实例配置。
- Route rules。
- Context pinning。

## Admin UI 展示规则

Admin UI 可以展示所有 Bootstrap Config，但要明确：

```text
trace.ui_port = 8782
Source: config.yaml
Hot reload: no
Restart required: yes
```

对于 secret，只显示是否设置，不显示明文。
