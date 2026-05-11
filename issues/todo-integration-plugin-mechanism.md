# TODO: Integration Plugin 机制

## 背景

当前 `IntegrationFactoryRegistry` 的工厂注册是硬编码在 `default_integration_factories()` 里的，第三方无法在不修改核心代码的情况下注册新 integration（如 LINE、Telegram 等通信渠道）。

## 目标

让第三方开发者只需实现两个 Protocol（`IntegrationFactory` + `IntegrationInstance`），通过 pip install 即可接入，无需修改核心代码。

## 设计要点

### 1. 插件发现机制（混合模式）

- **Entry Points**：第三方包在 `pyproject.toml` 声明 `[project.entry-points."yuubot.integrations"]`，安装后自动发现
- **Config 注册**：`config.yaml` 中支持 `integrations.plugins` 列表，用于开发调试或本地路径
- 优先级：built-in → entry points → config override

### 2. IntegrationMetadata 标准化

在 `IntegrationFactory` 上增加 `metadata` 属性，包含：
- `plugin_id`, `name`, `description`, `version`, `author`
- `config_schema_type: type[msgspec.Struct]` — 供 Admin UI 自动生成配置表单
- `requires_capabilities` — 声明依赖其他 integration 提供的能力
- `conflicts_with` — 声明互斥的 plugin

### 3. IntegrationContext 替代多参数签名

将 `factory.create(record, repository, *, gateway)` 改为 `factory.create(record, context: IntegrationContext)`，context 封装：
- `repository`
- `gateway`
- `capability_invoker` — 允许 integration 调用其他 integration 的 Capability

### 4. 生命周期钩子（可选扩展）

`IntegrationInstance` 增加可选钩子：`on_enable`, `on_disable`, `on_config_changed`, `health_check`

## 影响范围

- `core/integrations/contracts.py` — Protocol 扩展
- `core/integrations/registry.py` — 发现逻辑
- `core/integrations/core.py` — create 调用签名
- `bootstrap/config.py` — config 中的 plugins 字段解析
- Admin UI — 基于 config_schema 自动生成表单（后续）
