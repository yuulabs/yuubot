# 02. Bootstrap Config

Bootstrap Config 只负责让系统启动，不承载用户的主要业务配置。v2 不把旧 YAML 拆分方式作为兼容目标；旧文件最多作为一次性导入输入，不进入长期启动路径。

## 原则

- 文件和 env 只用于 **启动必需配置**。
- Runtime Resources 存 DB，不依赖 YAML 作为运行时事实来源。
- 修改 Bootstrap Config 通常需要重启。
- Admin UI 可以展示 Bootstrap Config，但默认不允许在线修改；UI 应标注 `restart required`。
- 旧 `llm.yaml` / `docker_config.yaml` 不应被新代码持续读取；如确实需要保留数据，提供显式 import 命令导入 DB 后停止使用。

## 推荐文件

v2 目标只保留：

```text
.env
config.yaml
```

`config.yaml` 放启动系统所需的低层配置；Provider、Character、Actor、Channel、Route 和三方服务凭证都进入 DB Runtime Resources。

## 启动加载顺序

```text
.env
  -> config.yaml
  -> DB runtime resources
```

注意：DB 中的大对象不叫 “config override”，而叫 Runtime Resource，例如 `llm_providers`、`integration_providers`、`actors`、`channels`。启动路径不应实现“先读旧 YAML，再用 DB 覆盖”的兼容层。

## 一次性导入

如果当前环境里仍有少量旧配置，可以提供显式导入工具：

```text
ybot import-legacy --from llm.yaml --from docker_config.yaml
```

导入规则：

- import 是人工触发的一次性操作，不在每次启动时自动运行。
- import 只写 DB Runtime Resources，不创建长期兼容读取路径。
- import 完成后 Admin UI 应展示导入结果和需要用户确认的字段。
- 无法可靠映射的旧字段应明确报错或跳过并记录 warning，不做隐式 fallback。

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
  # 32 bytes base64，用于加密 DB 中 config 内的 Secret 字段。
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

```

## 应保留在 Bootstrap Config 的内容

- DB 路径和数据目录。
- daemon/admin 监听 host/port。
- trace collector/ui host/port。
- admin secret。
- secret encryption master key。
- yuuagents daemon infrastructure config（`StageConfig.providers`、strict mode 等）。
- 容器和路径相关配置。
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
- 三方服务的 special-case flags 或 integration-specific patch 配置。
- Actor 的 `AgentDefinition` 字段（model override、budget、capabilities、prompt provider config）。

## Admin UI 展示规则

Admin UI 可以展示所有 Bootstrap Config，但要明确：

```text
trace.ui_port = 8782
Source: config.yaml
Hot reload: no
Restart required: yes
```

对于 secret，只显示是否设置，不显示明文。

## yuuagents Infrastructure Config

`yuuagents` 是 daemon 基建，配置修改后通过重启 daemon 生效，不走 Runtime Resource 热更新。Admin UI 可以提供一个显式 restart daemon 按钮，但不应该尝试在线 patch 正在运行的 `Stage` / executor。

示例：

```yaml
yuuagents:
  strict: false
  providers:
    background: {}
    schedule:
      db_path: ${YUU_DATA_DIR}/schedule.sqlite3
    ipykernel:
      cwd: ${YUU_WORKSPACE_DIR}
      inherit_envs: false
```

Actor Runtime 启动 Actor 时将它与 Actor 的 DB 资源机械拼装。实现上应手动构造 yuuagents `Stage`，以便注入 yuuagents `EventBus` observer；不要为了 observability 新增 yuubot 全局 EventBus。

```text
BootstrapConfig.yuuagents.providers -> yuuagents.StageConfig.providers
Actor.llm_provider/model/options     -> yuuagents.StageConfig.llm + AgentDefinition.llm
Character.system_prompt              -> AgentDefinition.prompts.system
Actor.agent_capabilities             -> AgentDefinition.capabilities
Actor.agent_prompt_providers         -> AgentDefinition.prompts.providers
```

这条路径禁止引入第二套 yuubot agent DSL；需要支持的新 executor 应优先作为 yuuagents provider 配置项出现。
