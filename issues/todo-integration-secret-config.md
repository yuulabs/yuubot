# TODO: Integration Secret Config 协议

## 背景

`IntegrationFactory.config_schema` 已经能让前端通过 JSON Schema 渲染配置表单（见 `core/integrations/contracts.py` `integration_kind_info()`、`echo.py` `EchoIntegrationConfig`）。但 schema 协议目前不区分「普通字段」和「敏感字段」：bot_token、API key 等会以明文落进 `IntegrationORM.config` 的 JSON 列里，跟着 `sqlite3 .dump` / 备份 / 误打日志一起泄露。

我们的 threat model：admin 面板登入后视为可信，可以看明文。除此之外的任何路径（DB dump、log、trace、错误信息、export）都不应该出现明文。

## 目标

扩展 config schema 协议，让 factory 能在 Struct 里**显式标注哪些字段是敏感的**；平台围绕这个标注自动完成「DB 加密、UI 渲染、运行时 reveal、日志脱敏」四件事。**不引入单独的 secrets 表**——secret 只是「config 里的敏感字段」，不是独立资源。

## 设计要点

### 1. `Secret` wrapper 类型

平台提供一个 wrapper 类型（建议放在 `core/secrets.py`），factory 在 config struct 里直接用：

```python
from yuubot.core.secrets import Secret

class TelegramConfig(msgspec.Struct):
    bot_token: Secret
    chat_kind: str = "private"
```

约束：

- **不**继承 `str`。Integration 拿到明文必须显式调用 `.reveal()`。
- `__repr__` / `__str__` 一律返回 `"***"`，避免 f-string、loguru、pprint 误打明文。
- msgspec 在 schema 里映射成 `{"type": "string", "format": "secret"}`，前端按 format 渲染成带「显示/隐藏」按钮的输入框。

### 2. ORM ↔ record 边界透明加密

加密只发生在持久化层。`resources/orm.py`（或新建一个 codec 钩子）在两端各做一次：

- 写：record → ORM 时，遇到 `Secret` 字段，用 master_key AEAD 加密，存形如 `{"$enc": "v1", "ct": "..."}` 的 dict 进 `IntegrationORM.config` 的 JSON 列。
- 读：ORM → record 时，识别这个形状的 value，解密，包成 `Secret(plaintext)` 注入 record。

DB 永远只存密文。Integration 收到的 `record.config.bot_token` 永远是 `Secret` wrapper。

### 3. master_key 走 Bootstrap Config

Bootstrap config（`02-bootstrap-config.md`）新增字段：

```yaml
secrets:
  master_key: ${YUUBOT_SECRETS_KEY}  # 32 bytes base64
```

启动时校验：缺失则启动失败（不允许「降级到明文」回退路径）；轮换通过停机 + 重写 ciphertext 完成（v1 不做在线轮换）。

### 4. 日志/导出/错误的脱敏守则

Wrapper 已经兜住绝大多数路径，但仍需在 review checklist 里明确：

- `validate_integration_config` 等 raise 时不要把字段值拼进 message，只引用字段名。
- traces.db 中如果 dump 了 `record`，必须是 record 形态（已 wrap），不能是 ORM 原始字典（密文）。两者都不泄露明文，但避免误存密文产生混淆。
- 后续的 export / 备份脚本默认走 ORM 层（已加密），严禁直接序列化运行时 record。

### 5. Admin UI 行为

- 字段渲染成 password input + 「显示」按钮。点开就显，admin 可信。
- 编辑时，留空表示「不修改」；填了新值就覆盖。前端不回显原值，由用户主动点「显示」从后端拉。
- 后端提供 `GET /api/integrations/{id}/secrets/{field}/reveal` 之类的端点（仅 admin 可达），返回明文供 UI 显示按钮使用——避免「编辑表单时初始化就把明文打到前端」。

## 影响范围

- `core/secrets.py`（新增） — `Secret` 类型与 AEAD 加解密
- `core/integrations/contracts.py` — `integration_kind_info()` JSON Schema 生成识别 `Secret`，输出 `format: secret`
- `resources/orm.py` / `resources/codec.py` — ORM 层透明加密钩子
- `bootstrap/config.py` — `secrets.master_key` 字段
- `runtime/admin.py` 与 admin API（见 `11-api-design.md`） — reveal 端点、表单语义
- `design/arch-v2/03-runtime-resources.md` — 把现有「单独 secrets 表」段落替换为本方案
- `design/arch-v2/02-bootstrap-config.md` — 新增 `secrets.master_key`

## 非目标

- 单独的 `secrets` 资源表 / `SecretRef` 引用模型 / 跨 integration 共享 secret。
- OAuth credential 轮换（access_token + refresh_token 配对、过期时间）。等真正的 OAuth integration 出现再设计。
- Per-user / per-actor secret（GitHub PAT 类）。当前 threat model 不需要。
- 在线 master_key 轮换。
