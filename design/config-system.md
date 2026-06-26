# yuubot 配置系统：应然模型

状态：设计框架。不包含任何具体 provider / tool / integration 实例。
每个概念的 per-instance 推导将逐步补充到附录。

---

## 0. 设计方法论

### 0.1 概念定义规则

每个概念用三元组定义：

```
概念 = (组成部分, 来源, 封装位置)
```

- **组成部分**：该概念由哪些子概念或值构成，每个字段是什么类型。
- **来源**：每个组成部分的值从哪里来——用户填写、系统播种、推导生成、或外部引用。
- **封装位置**：该概念在代码中由哪个 struct / 模块拥有，存储在哪（DB / config.yaml / 内存读模型）。

### 0.2 判断标准

对每个概念和字段，用三个问题检验其存在的合理性：

1. **它拥有谁的生命周期？** 谁创建它、谁修改它、谁销毁它。
2. **它的边界在哪？** 哪些是它的内部状态，哪些是对外部概念的引用。
3. **它能被谁组合？** 它作为什么组件参与更高层概念。

### 0.3 存储模型与读模型分离

- **存储模型**：持久化形态。只存引用（FK）+ 自身配置。不含快照。
- **读模型**：turn 时刻从存储模型解析出的只读复合视图。只在一次 turn 的生命周期内存在。
- 二者不得混在同一个 struct 里。当前 ActorRecord / ConversationRecord 的核心混乱就是把两者混在了一起。

### 0.4 死字段判据

一个声明的字段若满足以下全部条件，应删除：

- 在声明层存在（struct field + ORM column）。
- 在消费层无读取点（全代码库 grep 不到 `.field` 的运行时使用，排除 CRUD 的读写镜像）。
- 仅在预设/测试中被赋值，但不产生运行时行为。

### 0.5 推导优先于声明

用户不应手动填写完整运行时配置。系统应从一小组用户字段 + 硬编码的"还可以"的默认值推导出完整配置。推导规则是 per-concept 各自定义的。

详见 §4 Tool 推导方法论。

---

## 1. 进程级配置（重启生效，部署者拥有）

### 1.1 ProcessNode = (role, bind_addr, shared_secret)

- **组成部分**：`role ∈ {admin, daemon}`；`bind_addr: (host, port)`；`shared_secret: str`。
- **来源**：部署者在 `config.yaml` 中填写。
- **封装**：`BootstrapConfig.admin` / `BootstrapConfig.server`。
- **不变量**：两个进程间的信任关系由 shared_secret 建立。非 loopback 绑定时 secret 必填。

### 1.2 Site = (master_key, data_root, trace_sink)

- **组成部分**：`master_key: str`（32-byte base64）；`data_root: Path`（磁盘唯一根）；`trace_sink: (enabled, host, port)`。
- **来源**：部署者在 `config.yaml` 中填写。
- **封装**：`BootstrapConfig.secrets` / `BootstrapConfig.paths` / `BootstrapConfig.trace`。
- **不变量**：
  - `master_key` 解密 DB 中所有 Secret 字段。丢失 = 存储的 Secret 不可恢复。
  - `data_root` 是所有磁盘位置的唯一根。子路径由 `DataLayout` 派生，不允许其他代码拼接路径。
  - `trace_sink` 是 OTEL collector 地址。trace DB 路径由 `DataLayout` 派生。

这两个概念当前已经清洁，不在本次重构范围内。

---

## 2. 资源级配置：核心概念（DB 拥有，Admin API 管理，热改）

以下概念从最底层开始，逐层组合。

### 2.1 Provider = (api_type)

- **组成部分**：`api_type ∈ {openai-chat-completion, openai-compatible, anthropic-messages}`，枚举。三者语义：
  - `openai-chat-completion`：仅实现 OpenAI Chat Completions 子集（`POST /v1/chat/completions`）。绝大多数第三方"OpenAI 兼容"厂商属于此——它们只复刻了 chat completions 这一个接口形态。
  - `openai-compatible`：**完整兼容** OpenAI 的 API 面，包括 Responses API（`POST /v1/responses`）以及 chat completions。只有端点真正暴露 `/v1/responses` 等完整 OpenAI 接口面时才归此类。openai.com 自身即此类的范本。
  - `anthropic-messages`：Anthropic Messages API。
- **来源**：yuullm 包内静态定义。yuubot 不创建、不修改，只引用。
- **封装**：yuullm 包内。
- **职责**：一个 LLM 线协议适配器。它不是"OpenAI"或"DeepSeek"（那些是厂商），只是"用哪套 wire protocol 对话"。

#### 2.1.1 定义

```python
# packages/yuullm/src/yuullm/providers/presets.py（新增）

class ProviderPreset(msgspec.Struct, frozen=True):
    """厂商预设。由 yuullm 静态声明，不由用户创建。"""
    identity: str            # 厂商预设键，e.g. "openai" / "anthropic" / "deepseek" / "openrouter"
    api_type: str            # 线协议，三个枚举之一
    display_name: str        # UI 展示名
    default_base_url: str    # 厂商官方 base_url（可被 backend 覆盖）

PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    # openai.com 自身暴露完整 OpenAI API 面（含 /v1/responses）→ openai-compatible
    "openai":     ProviderPreset("openai",     "openai-compatible",      "OpenAI",         "https://api.openai.com/v1"),
    "anthropic":  ProviderPreset("anthropic",  "anthropic-messages",     "Anthropic",      "https://api.anthropic.com"),
    # 第三方厂商通常只复刻 chat completions 子集，未实现 /responses → openai-chat-completion
    "deepseek":   ProviderPreset("deepseek",   "openai-chat-completion", "DeepSeek",       "https://api.deepseek.com"),
    "openrouter":ProviderPreset("openrouter",  "openai-chat-completion", "OpenRouter",     "https://openrouter.ai/api/v1"),
    # … 增删厂商在此处；归属哪一 api_type 取决于该端点是否真正实现 /responses 等完整 OpenAI 接口面
}

def resolve_provider(identity: str) -> ProviderPreset:
    if identity not in PROVIDER_PRESETS:
        raise ConfigurationError(f"unknown provider_identity {identity!r}")
    return PROVIDER_PRESETS[identity]
```

#### 2.1.2 字段替换与删除

| 当前字段 | 位置 | 处理 | 原因 |
|---|---|---|---|
| `LLMBackendRecord.yuuagents_provider: str` | `records.py:51` | **删除** | v1 遗留，与 `provider_name` 重复定义同一件事 |
| `LLMProviderOptions.provider_name: str` | `validation.py:37` | **删除** | v1 遗留，重复定义 |
| `_resolve_yuuagents_provider()` | `_constants.py:41-50` | **删除** | base_url 启发式 + 集合判定，被 `resolve_provider()` 取代 |
| `_YUUAGENTS_KNOWN_FACTORIES` | `_constants.py:38` | **删除** | 上述函数的实现细节，随之移除 |
| `LLMProviderOptions.base_url: str` | `validation.py:36` | **保留** | 厂商官方域名是预设的默认值，但用户可覆盖（自建网关、代理），这是 backend 级覆盖 |

#### 2.1.3 生效路径

```
LLMBackendRecord.provider_identity (用户填)
  └─ resolve_provider(provider_identity) → ProviderPreset
       ├─ api_type       → yuuagents LlmConfig.provider（取代 yuuagents_provider 经启发式映射）
       └─ base_url       → LLMProviderOptions.base_url 默认值（用户未填时）
```

调用点：
- `_definition.py:28`：`provider=_resolve_yuuagents_provider(...)` → `provider=resolve_provider(binding.llm.backend.provider_identity).api_type`
- `_stage.py:42`：`_resolve_yuuagents_provider(...)` → 同上

### 2.2 Secret = 加密凭证

- **组成部分**：`EncryptedSecret(@rest: {"$enc":"v1","ct":"<b64>"})`；`.reveal(master_key) → str`。
- **来源**：AES-GCM 加密，master_key 来自 Site。
- **封装**：`core/secrets.py` / `resources/secrets.py`。

#### 2.2.1 现状

```python
# api_key 类型不一致：
# - IntegrationRecord.config (dict，typed_config 解出后含 EncryptedSecret) — 已加密
# - LLMProviderOptions.api_key: str = ""   (validation.py:38)              — 明文
```

`LLMProviderOptions.api_key` 是明文 `str`，绕过了 `core/secrets.py` 的对称加密管道，与集成 side 不一致，且在 DB 中以明文落盘。

#### 2.2.2 应然

```python
# core/validation.py
class LLMProviderOptions(msgspec.Struct, forbid_unknown_fields=False):
    base_url: str = ""                      # 默认取 ProviderPreset.default_base_url
    api_key: EncryptedSecret = msgspec.field(default_factory=EncryptedSecret.empty)
    timeout: float = 60.0
    max_retries: int = 2
    # 删 provider_name（见 §2.1.2）
```

`EncryptedSecret` 已经支持 msgspec 编解码（`secret_decode_hook` 注册在 `resources/codec.py`），所以 `LLMProviderOptions.api_key` 改用 `EncryptedSecret` 类型后，Admin API 收到的明文会在 `repository.create_llm_backend` 边界被加密落盘，assembly 时刻经 `dec_hook` 还原为可 `reveal(master_key)` 的 `EncryptedSecret`。

#### 2.2.3 生效路径

- Admin API 创建 backend：明文 → `EncryptedSecret.from_plain(plain, master_key)` → DB `{"$enc":"v1","ct":...}`
- `_llm_session.py` 取 key：`api_options.api_key.reveal(master_key)` → 传给 yuullm

不需要新增字段或新增表；只改 `LLMProviderOptions.api_key` 的类型。

### 2.3 ModelConfig = (pricing, capabilities)

- **组成部分**：
  - `pricing: Pricing` — {input_per_million, cached_input_per_million, output_per_million}
  - `capabilities: ModelCapabilities` — {vision, tool_calling, reasoning, ...}
- **来源**：用户在 Admin UI 中手工维护。Provider API（`GET /v1/models`）不返回定价或能力信息。
- **封装**：`LLMBackendRecord.model_configs: dict[str, ModelConfig]`，key = 模型名。
- **职责**：用户对一个模型的配置。不在 dict 中 = 尚未配置，不能在运行时使用。
- **不变量**：不区分"价格为零"和"有价格"——用户可能在使用免费模型。零价格合法。

#### 2.3.1 定义

```python
# resources/records.py（重构后）

class ModelCapabilities(msgspec.Struct):
    chat: bool = True
    vision: bool = False
    tool_calling: bool = False
    reasoning: bool = False
    embedding: bool = False
    structured_output: bool = False

class Pricing(msgspec.Struct, frozen=True):
    """单一模型的定价。嵌入 ModelConfig，不再是独立表。"""
    input_per_million: float = 0.0
    cached_input_per_million: float = 0.0
    output_per_million: float = 0.0

class ModelConfig(msgspec.Struct, frozen=True):
    """用户对一个模型的配置。key 是模型名，存在 LLMBackendRecord.model_configs 中。"""
    pricing: Pricing = msgspec.field(default_factory=Pricing)
    capabilities: ModelCapabilities = msgspec.field(default_factory=ModelCapabilities)
```

#### 2.3.2 模型实时列表与配置分离

Provider `GET /v1/models`（或 Anthropic models API）返回的实时列表仅含模型 ID（偶尔含 display_name），**不返回定价或能力信息**：

| Provider | `id` | `display_name` | 其他能力/定价 |
|---|---|---|---|
| OpenAI (`/v1/models`) | ✅ | ❌ | ❌ |
| Anthropic (`/v1/models`) | ✅ | ✅ 偶尔 | ❌ |

两个信息源有不同的更新频率和责任人：

```
Provider API（实时、自动、厂商拥有）          用户维护（手工、异步、部署者拥有）
┌──────────────────────────┐              ┌──────────────────────────────┐
│ ProviderModel            │              │ ModelConfig                  │
│   id: str                │   对照       │   pricing: Pricing           │
│   display_name?: str     │ ──────►     │   capabilities: Caps         │
└──────────────────────────┘              └──────────────────────────────┘
                                                  ▲
                                                  │ 用户通过 Admin UI 编辑
                                          LLMBackend.model_configs dict
```

运行时 assembly 仅查 `model_configs`：

```
model X 能否使用？
  config = backend.model_configs.get(X)
  if config is None:
      raise "model X not configured — edit in Admin UI"
  pricing = config.pricing
  capabilities = config.capabilities
```

#### 2.3.3 Admin UI 交互流程

1. 用户进入 LLM Backend 详情页，看到已配置的模型行列表（来自 `model_configs`）。
2. 用户点击"刷新模型列表"→ 后端调该 provider 的 `list_models()` 拿到实时模型 ID 列表。
3. 差集：实时列表 − `model_configs.keys()` = 新模型（未配置）。
4. 新模型以行形式追加到列表末尾，仅显示模型名，其他字段空白，带有"请编辑"提示。
5. 用户点击行填写 pricing、确认 capabilities。
6. 保存后写入 `model_configs`，该模型即可在 Actor 等处引用。

#### 2.3.4 字段替换与删除

| 当前字段 | 位置 | 处理 | 原因 |
|---|---|---|---|
| `ModelCatalog` (`names: tuple[str, ...]`) | `records.py:27` | **删除** | 模型名字列表来自 provider API，不再持久化存储 |
| `PricingTable` (`entries: tuple[PricingEntry, ...]`) | `records.py:38` | **删除** | 独立定价表 → 下沉进 `ModelConfig.pricing` |
| `PricingEntry` (`model: str, ...`) | `records.py:31` | **改名 → `Pricing`，删去 `.model` 字段** | `.model` 在原结构里只是表的外键；嵌入 `ModelConfig` 后冗余 |
| `LLMBackendRecord.model_capabilities: ModelCapabilities` | `records.py:52` | **删除，下沉到 `ModelConfig.capabilities`** | 粒度错——backend 级 capabilities 对多模型 backend 无意义 |
| `LLMBackendRecord.models: ModelCatalog` | `records.py:53` | **改为 `model_configs: dict[str, ModelConfig]`** | 不再是平铺列表，而是 key=模型名的配置字典；不在 dict 中 = 未配置 |
| `LLMBackendRecord.pricing: PricingTable` | `records.py:54` | **删除** | 下沉进 `ModelConfig.pricing`，backend 不再拥有平铺定价表 |

#### 2.3.5 生效路径

- 播种阶段：不再做 builtin catalogue 播种。DB 中已有的 `models` + `pricing` 数据迁移为 `model_configs` dict。
- `_check_pricing_for_budget`（`_stage.py:122-141`）：`config = binding.llm.backend.model_configs.get(model)`，若 `config is None` 则 raise（模型未配置，无法预算检查）。
- costing（`core/costing.py`）：模型定价查询走 `backend.model_configs[model].pricing`，不再平铺遍历。
- Admin UI 刷新：调 provider `list_models()` 获得实时列表，对比 `backend.model_configs.keys()` 得到差集（新模型），UI 展示为"待配置行"。

### 2.4 LLMBackend = (provider_identity, api_key, model_configs, budget, recommended_model)

- **组成部分**：
  - `provider_identity: str` — → 概念 2.1 解析到 api_type
  - `api_key: EncryptedSecret` — → 概念 2.2
  - `model_configs: dict[str, ModelConfig]` — → 概念 2.3，key = 模型名
  - `budget: BudgetPolicy` — {daily_usd, monthly_usd}，backend 级配额
- **来源**：provider_identity & api_key & budget 来自用户（创建时）；model_configs 由用户在 Admin UI 中维护（§2.3.3）。
- **封装**：`resources/records.py` LLMBackendRecord；DB `llm_backends` 表。
- **职责**：一个"商户账户"——连到某个 LLM 厂商所需的全部不可变凭证 + 该账户下可用的模型清单 + 该账户的配额。
- **用法**：`backend.bind(model) → BoundLLM`；`llm.stream(generation_params) → Usage`。

#### 2.4.1 重构后 struct

```python
# resources/records.py（重构后）

class LLMBackendRecord(msgspec.Struct):
    """一个 LLM 厂商账户：凭证 + 模型配置字典 + 配额。"""
    name: str
    provider_identity: str                              # → §2.1 resolve_provider()
    api_key: EncryptedSecret                            # → §2.2（取代明文 str）
    model_configs: dict[str, ModelConfig] = msgspec.field(default_factory=dict)  # → §2.3（取代 ModelCatalog + PricingTable）
    budget: BudgetPolicy = msgspec.field(default_factory=BudgetPolicy)
    provider_options: LLMProviderOptions = msgspec.field(default_factory=LLMProviderOptions)
    # 删: yuuagents_provider（→ provider_identity，§2.1.2）
    # 删: model_capabilities（→ ModelConfig.capabilities，§2.3.4）
    # 删: pricing（→ ModelConfig.pricing，§2.3.4）
    # 删: default_stream_options（→ 后端默认 GenerationParams，§2.5.2）
    # 删: default_model，由actor运行时确定
    id: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

#### 2.4.2 字段替换与删除汇总

| 当前字段 | 处理 | 取向 |
|---|---|---|
| `yuuagents_provider` | 删 | → `provider_identity`（§2.1.2） |
| `model_capabilities` | 删 | → `ModelConfig.capabilities`（§2.3.4） |
| `models: ModelCatalog` | 改为 `model_configs` | → `dict[str, ModelConfig]`（§2.3.4） |
| `pricing: PricingTable` | 删 | → `ModelConfig.pricing`（§2.3.4） |
| `default_stream_options: StreamOptions` | 删 | → backend 默认 `GenerationParams`，单独字段、不再复用 StreamOptions（§2.5.2） |
| `default_model` | 删 | model对于性能影响很大， per actor配置 |
| `provider_options.api_key: str` | 类型升级 | → `EncryptedSecret`（§2.2.2） |
| `provider_options.provider_name` | 删 | → `provider_identity`（§2.1.2） |

### 2.5 GenerationParams = (max_tokens, temperature, top_p, stop)

- **组成部分**：4 个采样参数，全部 optional。
- **来源**：backend 推荐一份默认（`default_generation_params`），actor 可以覆盖，turn 时刻合并解析一份。
- **封装**：无独立 struct（当前散在 StreamOptions / YuuAgentLLMOptions / Stage.llm_options / BoundLLM.stream_options 四处）。
- **职责**：一次 LLM 调用的采样参数。不是配置——是 per-call 的。
- **不变量**：单一解析点。`generation_params = backend.default ⨄ actor.override`，turn 时刻解析一次，传给 `llm.stream()`。

#### 2.5.1 现状（4 处分裂 + 2 个死字段）

| 承载点 | 位置 | 状态 |
|---|---|---|
| `StreamOptions` | `validation.py:23-30` | 声明 5 字段（含 `.model`） |
| `YuuAgentLLMOptions.stream_options` | `records.py:113` | 嵌一份 StreamOptions，actor 覆盖用 |
| `LLMBackendRecord.default_stream_options` | `records.py:61` | 嵌一份，backend 默认 |
| `BoundLLM.stream_options` | `llm.py:13` | 合并产物，**死字段**——`_definition.py:31` 实际读的是 `binding.llm_options.stream_options`，根本没碰 `binding.llm.stream_options` |
| `StreamOptions.model` | `validation.py:26` | **死字段**——`_stage.py:92` 用 `opts.pop("model", None)` 显式删除，因为 YuuSession 把 `model` 当 session selector 而非 stream kwarg |

#### 2.5.2 应然

```python
# core/validation.py（重构后）

class GenerationParams(msgspec.Struct, forbid_unknown_fields=False):
    """一次 LLM 调用的采样参数。不含 model（model 是 session selector，不是采样参数）。"""
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
```

```python
# core/llm.py（重构后）

class BoundLLM(msgspec.Struct):
    """turn 时刻的解析产物。携带解析完成后的 generation_params，供 LlmConfig 直接传给 yuuagents。"""
    backend: LLMBackendRecord
    model: str
    generation_params: GenerationParams
    # 删 stream_options：dict[str, object] — 死字段（见 §2.5.1）
```

```python
# resources/records.py（字段重命名 + 类型升级）

class LLMBackendRecord(msgspec.Struct):
    ...
    default_generation_params: GenerationParams = msgspec.field(default_factory=GenerationParams)
    # 删 default_stream_options: StreamOptions — 由 default_generation_params 取代

# ActorRecord 持有 generation_override（§2.8.1），不再是 YuuAgentLLMOptions 包装：
#   generation_override: GenerationParams = msgspec.field(default_factory=GenerationParams)
# YuuAgentLLMOptions 整体删除——其 max_tokens 即 GenerationParams.max_tokens（采样上限），
# 其 stream_options 即 GenerationParams；无独立存在价值，actor 直接持 GenerationParams。
# ConversationRecord 不持 generation params（§2.9.3 已删逐会话覆盖）。
```

#### 2.5.3 单一解析点

```python
# core/bindings.py（取代当前 _bound_llm）

def _bound_llm(resolved: ResolvedConversation) -> BoundLLM:
    """turn 时刻的唯一解析点：backend 默认 ⨄ actor.generation_override。
    resolved 携带 live 的 actor 与 backend（§2.9.5）；merge 一次，下游只读。"""
    backend = resolved.llm_backend
    actor = resolved.actor
    params = _merge_generation_params(
        backend.default_generation_params,
        actor.generation_override,
    )
    return BoundLLM(
        backend=backend,
        model=actor.model,
        generation_params=params,
    )

def _merge_generation_params(base: GenerationParams, override: GenerationParams) -> GenerationParams:
    """override 中非 None 的字段覆盖 base。None 视为'未设置'，不覆盖。"""
    return GenerationParams(
        max_tokens=override.max_tokens if override.max_tokens is not None else base.max_tokens,
        temperature=override.temperature if override.temperature is not None else base.temperature,
        top_p=override.top_p if override.top_p is not None else base.top_p,
        stop=override.stop if override.stop is not None else base.stop,
    )
```

#### 2.5.4 字段替换与删除

| 当前字段 | 位置 | 处理 | 原因 |
|---|---|---|---|
| `StreamOptions.model` | `validation.py:26` | **删除** | 死字段，`_stage.py:92` 显式 pop |
| `StreamOptions`（整体） | `validation.py:23` | **改名 → `GenerationParams`**，删 `.model` 字段 | 名字误导（"stream options" 暗示含 model/会话级参数），实际只是 4 个采样参数 |
| `YuuAgentLLMOptions`（整体） | `records.py:109-113` | **删除整个 struct** | 其 `max_tokens` = `GenerationParams.max_tokens`，`stream_options` = `GenerationParams`；无独立存在价值。actor 改直接持 `generation_override: GenerationParams`（§2.8.2）；conversation 不持（§2.9.3） |
| `LLMBackendRecord.default_stream_options` | `records.py:61` | **改名 → `default_generation_params`**，类型 `GenerationParams` | 名称对齐职责 |
| `BoundLLM.stream_options: dict[str, object]` | `llm.py:13` | **删 dict，改为 `generation_params: GenerationParams`** | 死字段，且从 dict 改为强类型 |
| `_stage_llm_options()` + `opts.pop("model", None)` | `_stage.py:84-93` | **删除整个函数** | 它的存在仅是为给死字段 `StreamOptions.model` 创可删的条件；单一解析点取代后，`_stage` 直接读 `binding.llm.generation_params` |
| `validate_stream_options()` | `validation.py:43` | **改名 → `validate_generation_params()`**，返回 `GenerationParams` 而非 dict | 配合 struct 升级 |

#### 2.5.5 消费点更新

| 文件:行 | 当前 | 重构后 |
|---|---|---|
| `_definition.py:31` | `stream_options=msgspec.to_builtins(binding.llm_options.stream_options)` | `generation_params=binding.llm.generation_params`（由 `_bound_llm` 已解析，§2.5.3） |
| `_runtime.py:619` | `summary_session.stream(**agent.llm.options)` | `summary_session.stream(**asdict(agent.llm.generation_params))` |

> 不变量 6（§4）由本节落地：`generation_params = backend.default ⨄ actor.override`，turn 时刻在 `_bound_llm` 解析一次，下游只读不再合并。

### 2.6 Character（删除——降格为 `Actor.persona_prompt`）

#### 2.6.1 判据

Character 在运行时仅被消费一次：`_prompt.py` 的 character 渲染函数读 `binding.character.system_prompt` 作为 system prompt 首段正文，于 agent definition build 时拼装。其余字段全部死或仅为 admin UI 展示：

- `Character.system_prompt` → 实即 persona 文本，唯一行为消费点。
- `Character.facade_module` → 死字段。assembly 用 `_constants.FACADE_IMPORTS` 硬编码常量，全代码库无运行时读取 `binding.character.facade_module`。
- `Character.default_hints` → 死字段，零消费。
- `Character.name` / `.description` → 仅 admin UI 列表展示，无行为消费。
- `simple_loop.py:99` 的 `is_table("characters")` reload 钩子 → 随表删除一并清除。

Character 表不拥有独立生命周期：种子点仅播种 `"general"` / `"shiori"` 两条，每条本质是一个 system_prompt 字符串。一个 actor 想换 persona 就改自己的 `persona_prompt`，不存在 N:M 复用关系。

#### 2.6.2 处理

**删除整个 Character 概念**：`CharacterRecord`、`CharacterORM`、`characters` 表、`builtin_presets._seed_character`、`PresetPair.character`、admin API 的 character 路由、`simple_loop` 的 characters reload 钩子、§2.9 的 character override 字段。

唯一存活的字段 `persona_prompt` 下沉为 `Actor pessoa_prompt: str`（§2.8）。persona 文本直接由 actor 持有，不经任何中间资源表。

system prompt 首段是 prompt 组装契约本身——但其命名随数据源变更：`SECTION_HEADERS` 第一项从 `"Character"` 改为 `"Persona"`（段名指其内容，不再指已删的概念），渲染函数 `_render_character` → `_render_persona`，读 `binding.actor.persona_prompt`。

内置 persona 文案（`_SHIORI_SYSTEM_PROMPT`、`"You are a helpful assistant."`）不进 DB，转为代码侧常量 `BUILTIN_PERSONA_PROMPTS: dict[str, str]`，仅作 admin UI 创建 actor 时的模板下拉——这是 UI 预设，不是配置模型的一部分。

#### 2.6.3 消费点更新

| 文件:位置 | 当前 | 重构后 |
|---|---|---|
| `_prompt.py` `SECTION_HEADERS` | `("Character", "System Instructions", ...)` | `("Persona", "System Instructions", ...)` |
| `_prompt.py` `_render_character` | 读 `binding.character.system_prompt` | 改名 `_render_persona`；读 `binding.actor.persona_prompt` |
| `bindings.py` | `character=conversation.character` | 删（ResolvedConversation 持 `actor`；persona 读 `actor.persona_prompt`） |
| `simple_loop.py`、`conversations.py` | `character_name=agent_binding.character.name`（trace span 属性） | 删该 trace 属性（与 `actor.name` 重复概念） |
| `simple_loop.py:99` | `is_table("characters")` reload 钩子 | 删（表已不存在） |
| `builtin_presets.py` | 播种 Character 表 | 删 `_seed_character` / `PresetPair.character`；persona 文案转 `BUILTIN_PERSONA_PROMPTS` 常量（UI 预设） |
| Admin API（`handlers.py`、`validators.py`、`_schemas.py`） | character CRUD + 按 character 过滤 conversations | 删 character 路由；validators 按 actor 过滤 |

> §4 不变量 7：system prompt 首段 `# Persona` 组装 contract 保留，数据源 = `Actor.persona_prompt`（非资源表）。

### 2.7 CapabilitySet = (workspace_path, tools, loop_policy, integration_ids)

- **组成部分**：
  - `workspace_path: str`
  - `tools: tuple[ToolSelection]` — 用户配置的 tool 列表，每个带 tool_name + user_fields（→ §3 编译为 ToolBinding）
  - `loop_policy: LoopPolicy` — → §2.7.2
  - `integration_ids: tuple[str, ...]` — 选中的 `IntegrationRecord.id` 列表。前端粒度是 integration 实例整体：要么该实例全部可用，要么完全不可用。
- **来源**：workspace_path、tools、integration_ids 来自用户；loop_policy 来自用户或预设。
- **封装**：`resources/records.py` CapabilitySetRecord；DB `capability_sets` 表。
- **职责**：一份可被多个 Actor 引用的、关于"这个 actor 能做什么"的声明。同时通过 integration_ids 声明哪些 integration 实例对 actor 可见。
- **组合关系**：Actor 引用一个 CapabilitySet，loop 参数随引用走。
- **不变量**：
  - 所有对 LLM 可见的 tool 必须出现在 `tools` tuple 中——不存在"隐式注入的 tool"。
  - tool 自己的 description 是 tool surface 的说明来源；system prompt 不再为每个 tool 追加重复说明。
  - `integration_ids` 是 integration 可视面的声明边界：不在列表中的 integration 实例，actor 既看不到它的 SDK，也不能调用它的能力。
  - 前端不暴露 method/function 级筛选。`CapabilitySpec.id` 仍存在，但它是 integration 内部的运行时调用单位，不是用户配置粒度。
  - facade 的可见能力由 `integration_ids` 推导：选中且启用的 integration 实例贡献它声明并实际提供的全部 capabilities。
- **删节**（7 个死字段 + 整个 ResourcePolicy + 3 个 RuntimePolicy 死字段）：
  - `bootstrap_path`、`enabled_global_skill_refs`、`workspace_skill_root`、`preexpanded_skill_refs`、`prompt_fragments`、`permission_limits`、`integration_visible_state`
  - `ResourcePolicy`（整体）：`budget_usd_daily`、`concurrency_limit`、`bridge_nodes`、`workspace_access`
  - `RuntimePolicy` 死字段：`memory_enabled`、`memory_curator_enabled`、`strict_usage_sink`

#### 2.7.1 Integration 可见性与 SDK prompt

CapabilitySet 只存 integration 实例选择，不存 capability/method 列表：

```python
class CapabilitySetRecord(msgspec.Struct):
    name: str
    description: str = ""
    workspace_path: str = ""
    tools: tuple[ToolSelection, ...] = ()
    integration_ids: tuple[str, ...] = ()     # FK to IntegrationRecord.id
    loop_policy: LoopPolicy = msgspec.field(default_factory=LoopPolicy)
    id: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

turn 时刻推导出读模型：

```python
class IntegrationCapabilityRef(msgspec.Struct, frozen=True):
    integration_id: str
    capability_id: str

class VisibleIntegrationSurface(msgspec.Struct, frozen=True):
    integration_id: str
    integration_name: str
    sdk: IntegrationSdkSpec
    capabilities: tuple[CapabilitySpec, ...]
    capability_refs: tuple[IntegrationCapabilityRef, ...]
```

推导规则：

```
selected integration_ids
  ∩ existing enabled IntegrationRecord
  ∩ running IntegrationInstance
  → VisibleIntegrationSurface[]
```

每个选中的 integration 实例贡献它声明并实际提供的全部 capabilities。内部调用使用 `(integration_id, capability_id)` 二元组，避免同类 integration 多实例时只靠裸 capability id 路由不清晰。`CapabilitySpec.id` 保持为 integration 内部的稳定函数标识，但不作为前端筛选项。

Integration SDK prompt 由 integration 自己声明，按 integration 粒度渲染：

```python
class IntegrationSdkSpec(msgspec.Struct, frozen=True):
    import_paths: tuple[str, ...] = ()
    prompt_summary: str = ""       # 短说明 + 1-3 个代表性示例
    doc_modules: tuple[str, ...] = ()
```

归属：

- `IntegrationFactory` 声明 `sdk_spec()` 或等价属性。
- `CapabilitySet` 只声明选中哪些 `integration_ids`。
- facade/codegen 根据 `VisibleIntegrationSurface` 暴露对应 SDK。
- system prompt 的 `# Integration SDKs` 段按 integration 渲染 `prompt_summary`，不逐个展开 function input/output schema。

Admin UI：

- CapabilitySet 编辑页的 integration 区域使用平铺 checkbox 列表，不使用树形 method/function 选择器。
- 每一行对应一个 `IntegrationRecord` 实例，checkbox value = `integration.id`。
- 行内展示：`display_name`（或 `record.name`）、实例别名/描述、enabled/running 状态、SDK import paths 摘要。
- disabled 或未运行的 integration 可以显示为不可选或带状态标记；保存时只写入选中的 `integration_ids`。
- 不展示 capability/method 子节点，不提供半选态；选择语义始终是"该 integration 实例整体可见"。

prompt 原则：

- tool 说明来自 tool definition / description；system prompt 不为 tool 重复说明。
- integration 说明来自 SDK prompt；system prompt 只给整体用法、入口模块和少量示例。
- 小函数数量可能很多，详细函数和参数说明放在 SDK 模块 docstring / generated facade docstring 中，由 agent 在需要时通过 Python introspection 查看。
- 如果没有选中 integration，`# Integration SDKs` 渲染为 `No integration SDKs configured.`

#### 2.7.2 LoopPolicy = (rollover_enabled, idle_timeout_s, summarize_steps_span)

- **组成部分**：
  - `rollover_enabled: bool` — history 超过 token 阈值时是否压缩为摘要后继续
  - `idle_timeout_s: float` — agent 多久没有新消息就释放内存中的状态
  - `summarize_steps_span: int` — 摘要时取最近多少步的对话
- **来源**：用户或预设。
- **职责**：loop 收敛策略。
- **归属理由**：这 3 个参数描述"给定这套工具和这个工作区，loop 怎么收敛"。它们是 capability set 的行为属性，跟"哪个 actor 实例"无关。

### 2.8 Actor = (identity, refs, persona_prompt, model, generation_override, per_run_budget)

#### 2.8.1 存储模型（DB 存 FK + own config）

```python
# resources/records.py（重构后）

class ActorRecord(msgspec.Struct):
    """存储模型：只存 FK + 自身配置。不嵌入 Character / CapabilitySet / LLMBackend。"""
    name: str
    persona_prompt: str = ""                          # → §2.6：原 Character.system_prompt 下沉
    type: str = "simple_loop"                          # actor 实现类型
    enabled: bool = True
    capability_set_id: str = ""                        # FK to CapabilitySet
    llm_backend_id: str = ""                           # FK to LLMBackend
    model: str = ""                                    # actor 选定 model. 不可为空。
    generation_override: GenerationParams = msgspec.field(default_factory=GenerationParams)   # → §2.5
    per_run_budget: RunBudget = msgspec.field(default_factory=RunBudget)                      # {max_steps, max_tokens, max_usd}
    config: dict[str, object] = msgspec.field(default_factory=dict)                           # actor type 特定配置
    id: str = ""
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def typed_config(self, schema: type[ConfigT]) -> ConfigT:
        return msgspec.convert(self.config, type=schema, strict=False)
```

```python
# resources/records.py（新增读模型）

class ResolvedActor(msgspec.Struct, frozen=True):
    """turn 时刻读模型：FK 重水合后的只读复合视图。只在一次 turn 的生命周期内存在。
    下游（assembly）只读此模型，不读取 ActorRecord 的 FK。persona 来自带 persona_prompt 的 actor 本身。"""
    actor: ActorRecord
    capability_set: CapabilitySetRecord
    llm_backend: LLMBackendRecord
```

#### 2.8.2 字段替换与删除

| 当前字段 | 位置 | 处理 | 原因 |
|---|---|---|---|
| `ActorRecord.default_character: CharacterRecord` | `records.py:202` | **改为内嵌字段 `persona_prompt: str`** | Character 表删除（§2.6）；唯一存活的 `system_prompt` 内容下沉为 actor 字段 |
| `ActorRecord.capability_set: CapabilitySetRecord` | `records.py:203` | **改为 `capability_set_id: str`** | 嵌入完整 struct = 存储层混入读模型，语义模糊 |
| `ActorRecord.default_llm_backend: LLMBackendRecord` | `records.py:204` | **改为 `llm_backend_id: str`** | 同上 |
| `ActorRecord.default_model: str` | `records.py:205` | **改名 → `model: str`** | "default" 在 actor 语境里冗余——actor 本身就是常驻身份，model 是它的选定值 |
| `ActorRecord.default_llm_options: YuuAgentLLMOptions` | `records.py:206-208` | **改名 → `generation_override: GenerationParams`** | 去掉套了一层的 YuuAgentLLMOptions，直接持 GenerationParams（§2.5.2） |
| `ActorRecord.default_budget: YuuAgentBudget` | `records.py:209` | **改名 → `per_run_budget: RunBudget`**，类型名对齐 §2.8.1 | 同上 |

#### 2.8.3 重水合边界

```python
# core/bindings.py（重构后）

async def resolve_actor(
    repository: ResourceRepository,
    actor_id: str,
) -> ResolvedActor:
    """turn 时刻唯一的 FK → struct 重水合点。返回只读 ResolvedActor。
    persona_prompt 直接来自 actor 本身（§2.6），无需 character 水合。"""
    record = await repository.get(ActorORM, actor_id)
    if record is None or not record.enabled:
        raise KeyError(f"active actor {actor_id} does not exist")
    capability_set = await repository.get(CapabilitySetORM, record.capability_set_id)
    llm_backend = await repository.get(LLMBackendORM, record.llm_backend_id)
    return ResolvedActor(actor=record, capability_set=capability_set,
                         llm_backend=llm_backend)
```

> 不变量 1（§4）由本节落地：存储层只存引用（FK）+ 自身配置；不变量 2：`ResolvedActor` 只在 turn 时刻存在，是只读读模型。persona 随 actor 走——Character 不存在独立的失效路径。

### 2.9 Conversation = (actor_ref, history_snapshot, metadata)

#### 2.9.1 概念

第一性原理：Conversation 是「绑定到某 Actor 的一次会话实例」。它本身**不拥有任何 LLM 配置**——persona / model / generation / budget / capability_set / llm_backend 全部由 Actor 拥有（§2.8）。Conversation 只拥有：

- 对 Actor 的 live 引用（`actor_id`）
- 一次性冻结的 history snapshot（system prompt + tool_specs，首次 send 时封冻）
- 会话级行为字段：title、reply_address、metadata、timestamps

当前 `ConversationRecord` 嵌入完整 character / capability_set / llm_backend struct，并携带 `model` / `llm_options` / `budget` 等逐会话覆盖字段。这些都是"保留现有覆盖语义"的兼容层——它们让"一个会话使用与所属 actor 不同的 backend/persona/model"成为可能。第一性原理下不存在这种需求：要不同配置就用不同 actor（actor 是轻量 FK 行），Conversation 不应是 Actor 配置的覆盖层。**全部删除。**

#### 2.9.2 存储模型（DB 存 FK + own metadata + 冻结 snapshot）

```python
# resources/records.py（重构后）

class ConversationRecord(msgspec.Struct):
    """存储模型：actor_ref（live）+ 冻结 history + 会话级 metadata。
    不嵌入任何 Actor 配置——persona/capability/llm_backend/model/generation/budget 全来自 Actor。"""
    conversation_id: str
    actor_id: str                                                            # live 引用 Actor
    history_snapshot: tuple[ConversationHistoryItemRecord, ...] = ()         # 一次性冻结于首条 send
    title: str = ""
    reply_address: str = ""
    metadata: dict[str, object] = msgspec.field(default_factory=dict) # 含type字段，以便于反序列化
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

```python
# resources/records.py（新增读模型）

class ResolvedConversation(msgspec.Struct, frozen=True):
    """turn 时刻读模型：actor live-follow + 该会话冻结的 history + actor refs 水合。
    下游直接读 ResolvedActor 的 actor-owned 字段（model / persona_prompt / per_run_budget）；
    generation_params 需经 `_bound_llm` 在 turn 时刻合并解析（§2.5.3），不在此直接暴露。"""
    conversation: ConversationRecord
    actor: ActorRecord                                                        # live-follow
    capability_set: CapabilitySetRecord                                       # 来自 actor.capability_set_id
    llm_backend: LLMBackendRecord                                             # 来自 actor.llm_backend_id
    history: tuple[ConversationHistoryItemRecord, ...]                        # 冻结 snapshot 副本

    @property
    def model(self) -> str: return self.actor.model
    @property
    def persona_prompt(self) -> str: return self.actor.persona_prompt
    @property
    def per_run_budget(self) -> RunBudget: return self.actor.per_run_budget
```

> `ResolvedActor` 与 `ResolvedConversation` 的分工：`ResolvedActor` = turn-time actor 配置读模型（含 actor refs + actor-owned config 的解析产物）；`ResolvedConversation` = ResolvedActor + 该会话冻结的 history + conversation 自身 metadata。下游（agent loop / costing / prompt assembly）从 `ResolvedConversation` 取一切——要么是 conversation 自身字段，要么经它读到 actor 配置。

#### 2.9.3 字段替换与删除

| 当前字段 | 位置 | 处理 | 原因 |
|---|---|---|---|
| `ConversationRecord.character: CharacterRecord` | `records.py:237` | **删** | Character 表删除（§2.6）；persona 来自 `actor.persona_prompt`，会话不持有 |
| `ConversationRecord.capability_set: CapabilitySetRecord` | `records.py:238` | **删** | follow `actor.capability_set_id`，会话不覆盖 |
| `ConversationRecord.llm_backend: LLMBackendRecord` | `records.py:239` | **删** | follow `actor.llm_backend_id`，会话不覆盖 |
| `ConversationRecord.model: str` | `records.py:240` | **删** | 来自 `actor.model`，会话不覆盖 |
| `ConversationRecord.llm_options: YuuAgentLLMOptions` | `records.py:241` | **删** | generation params 来自 `actor.generation_override`，会话不覆盖 |
| `ConversationRecord.budget: YuuAgentBudget` | `records.py:242` | **删** | per-run budget 来自 `actor.per_run_budget`，会话不覆盖 |

> 说明：上表 6 个字段全部删除，而非"改名"。原 §2.9 草案的"改为 `*_override_id` / `*_override`"是把嵌入 struct 换成覆盖字段——但覆盖层本身在第一性原理下不需要，连根删除。

#### 2.9.4 不变量

- **绑定 = 引用 Actor**：Conversation 只存 `actor_id`。`actor` 是 live-follow——Actor 的 persona/model/capability/backend 编辑后，下一个 turn 生效。Conversation 不对这些做快照、不做覆盖。
- **History = snapshot**：首次 send 时 `[tool_specs, system_message]`（system_message 含当时 `actor.persona_prompt` 拼装结果）冻结进 `history_snapshot`。后续 turn 从该 snapshot 重放——所以 `actor.persona_prompt` 的 live-edit 只影响新会话或尚未首次 send 的会话；已冻结会话的 system prompt 不变。
- **无逐会话覆盖**：Conversation 不存在对 actor 配置的 override 机制。若需不同配置，使用不同 Actor。

#### 2.9.5 重水合边界

```python
# core/bindings.py（重构后）

async def resolve_conversation(
    repository: ResourceRepository,
    conversation_id: str,
) -> ResolvedConversation:
    """turn 时刻唯一的 conversation 解析点。
    actor live-follow；capability_set / llm_backend 从 actor 的 FK 水合；history 冻结读取。"""
    conv = await repository.get(ConversationORM, conversation_id)
    if conv is None:
        raise LookupError(f"conversation {conversation_id!r} does not exist")
    actor = await repository.get(ActorORM, conv.actor_id)                       # live
    capability_set = await repository.get(CapabilitySetORM, actor.capability_set_id)
    llm_backend = await repository.get(LLMBackendORM, actor.llm_backend_id)
    history = await repository.load_history(conv.conversation_id)              # frozen snapshot
    return ResolvedConversation(
        conversation=conv, actor=actor,
        capability_set=capability_set, llm_backend=llm_backend,
        history=history,
    )
```

> 不变量 3（§4）：history prefix once-frozen；Conversation 仅引用 Actor，不做覆盖。

---

## 3. ToolBinding 推导方法论

### 3.1 两个视角

ToolBinding 有存储视角和运行时视角：

- **存储视角**（用户/前端拥有，存在 DB 里）：
  ```
  ToolSelection = (tool_name, user_fields)
  ```
  `user_fields` 是系统为该 tool 定义的前端字段（一个小子集，可能为空 struct）。系统决定哪些字段需要用户填写、哪些从 context 推导。每个 tool 的 user_fields 结构由系统（yuubot）单独维护，**不是 tool 类声明的**。

- **运行时视角**（assembly 拥有，turn 时刻推导）：
  ```
  ToolBinding = (tool_name, config: tool_cls.config_type)
  ```
  `config` 是完整的类型化运行时配置。

### 3.2 推导函数

推导由系统（yuubot）主导。系统为每个 tool 声明一个推导函数：

```
config = derive_fn(user_fields, context)
```

- **输入 user_fields**：系统为该 tool 定义的前端字段（ToolSelection.user_fields）。其结构由系统（yuubot）声明（§3.5）。
- **输入 context**：assembly 时刻才有的运行时状态，包括：
  - `workspace_path: Path` — actor 的工作区根路径
  - `venv_python: str` — 工作区 .venv 的 python 路径
  - `facade` — 集成 facade 状态（哪些集成可用、startup_code、sys_path 等）
  - `identity` — {actor_id, agent_name, session_id, mailbox_id}
- **输出**：完整的 typed config（tool_cls.config_type 的实例）。
- **所有者**：推导函数是系统的职责，不是 tool 类的方法。推导函数位于 yuubot 层（ToolFactory 上或独立的注册表中）。

### 3.3 当前问题

当前推导逻辑散落在 `core/assembly/_tools.py` 的多个私有函数里（`_tool_definition_configs`、`_builtin_tool_configs`、`_python_agent_tool_config`、`_facade_imports` 等），不是 tool 自身声明的契约。

但改造的方向不是把"所有东西"都塞进 tool 类。**user_fields_type / defaults 不属于 tool 类**：tool 类只知道自己的 config_type，不知道宿主系统（yuubot）拥有什么 context。

- 同一个 `BashTool` 在 yuubot 中可推导 `workspace_root` 和 `venv_python` 从 context，但在另一个系统中可能需要用户填写。
- `ExecutePythonTool` 的完整 `PythonRuntime` 配置（startup_code、imports、sys_path、session_state）在 yuubot 中全部来自 facade + identity 等 context，用户看不到任何配置字段。

因此：**user_fields_type 和 derive 都是系统层的契约，不是 tool 层的契约。** tool 层只声明 config_type（运行时配置结构）和运行时行为（from_startup、definition、create_coro、cancel）。系统层为每个 tool 单独定义 user_fields 结构 + derive 函数。

### 3.4 两个层级的契约

#### 3.4.1 Tool 类（yuuagents / yuutools 包内）：纯运行时

Tool 类不知道 config 是怎么来的。它只负责在 `from_startup` 接收到完整 typed config 后执行。

```py
# packages/yuuagents/src/yuuagents/tool/primitives.py

class Tool(ABC, Generic[P, R]):
    config_type: ClassVar[type]          # 完整运行时配置结构（msgspec.Struct 子类）

    @classmethod
    def from_startup(cls, runtime, config) -> Tool:
        """接收已推导完成的 typed config，构建运行时实例。"""
        ...

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition[P, R]: ...

    @abstractmethod
    def create_coro(self, task: ToolCallTask, context: ToolContext) -> Coroutine: ...
```

**Tool 类上没有 derive。** 推导是系统的职责，不是 tool 的。

#### 3.4.2 ToolFactory（yuubot 层）：系统拥有推导函数

```py
# apps/yuubot/src/yuubot/core/tools/contracts.py

class ToolFactory(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def config_schema(self) -> type | dict[str, object]: ...

    def derive(self, user_fields, context: ToolDeriveContext) -> msgspec.Struct:
        """系统层的推导函数。将 user_fields + 系统 context 转为 typed config。
        
        user_fields 的类型由该 tool 对应的 user_fields_type 决定（§3.5），
        由编译系统调用前保证匹配。返回的 config 类型 = config_schema。
        """
        ...

    def tool_class(self) -> type[Tool]: ...
```

推导函数是 `ToolFactory` 的组成部分，因为：
- `ToolFactory` 已经存在于 yuubot 层——不跨包修改 yuuagents
- 工厂知道 `config_schema`（即输出类型），derive 的签名自然对齐
- 系统集成者创建工厂时就编写 derive，不需要修改 yuuagents 中的 Tool 子类
- 不同系统可以为同一个 yuuagents Tool 写不同的 derive（使用不同的 context + user_fields）

assembly 层通过**编译系统**（§3.6）将 ToolSelection 转为 ToolBinding。编译系统调用 `factory.derive()`，**不调用** tool 类的任何方法。

> 核心原则（修正后）：推导是系统层的职责，不是 tool 层的职责。tool 类（yuuagents）对自身 config 的构造方式完全无知。系统（yuubot）通过 ToolFactory.derive 主导整个推导过程。

### 3.5 系统层：per-tool user fields 定义

系统（yuubot）**为每个注册的 tool** 单独定义一个 user_fields 结构（msgspec.Struct subclass）。这个结构是前端渲染的依据，也是存储到 `ToolSelection.user_fields` 的类型约束。

```py
# yuubot 层维护的映射（每个 tool factory 附带一个 user_fields 类型）

from msgspec import Struct

class EmptyFrontendFields(Struct):
    """该 tool 无需用户填写任何字段——全量配置从 context 推导。"""

# ── 系统注册表 ──

TOOL_USER_FIELDS: dict[str, type[Struct]] = {
    "bash":             EmptyFrontendFields,      # workspace_root ← context.workspace_path（§6.1）
    "read":             EmptyFrontendFields,      # workspace_root ← context.workspace_path（§6.2）
    "edit":             EmptyFrontendFields,      # workspace_root ← context.workspace_path（§6.3）
    "write":            EmptyFrontendFields,      # workspace_root ← context.workspace_path（§6.4）
    "execute_python":   EmptyFrontendFields,      # PythonRuntime 全量从 context + facade 推导（§6.6）
    "restart_kernel":   EmptyFrontendFields,      # 空配置，无需任何字段（§6.5）
}
```

每个 `ToolFactory`（定义在 `core/tools/contracts.py`）可附带一个 `user_fields_type` 属性，由系统在注册时设置。这仅仅是**元数据**，供 Admin UI 动态渲染配置表单用——不是 derivation 的输入契约。`factory.derive()` 的 user_fields 类型在编译系统调用时由系统保证匹配。

> **为什么不在 tool 类上声明 user_fields_type？** 因为同一个 tool 在不同上下文环境中可能有不同的 user_fields。tool 类属于 yuuagents/yuutools 包，不知道宿主系统的 context 边界。系统层的 TOOL_USER_FIELDS 是宿主应用（yuubot）的决策——它知道自己的 context 能提供什么，从而决定哪些字段需要用户填写。

### 3.6 编译系统

编译系统是**唯一的** ToolSelection → ToolBinding 转换入口。它替代当前 `core/assembly/_tools.py` 中所有散落的私有推导函数。

编译系统通过 `ToolRegistry` 查找 ToolFactory，调用 `factory.derive()`。**不直接接触 tool 类。**

```py
# core/assembly/_compiler.py（新增）

@define
class ToolDeriveContext:
    """系统在 assembly 时刻收集的完整运行时状态。
    所有字段由编译系统准备，factory.derive 消费。"""
    workspace_path: str
    venv_python: str
    facade: ActorFacadeBinding | None
    actor_id: str
    agent_name: str
    session_id: str
    mailbox_id: str


@define
class ToolBinding:
    """运行时视角：编译完成的 tool 实例配置。"""
    tool_name: str
    config: msgspec.Struct                     # factory.config_schema 的实例


def compile_tool_bindings(
    selections: list[ToolSelection],
    context: ToolDeriveContext,
    registry: ToolRegistry,
) -> list[ToolBinding]:
    """编译系统：纯 1:1 编译器。每个 ToolSelection → 一个 ToolBinding。
    
    不做隐式注入。CapabilitySet.tools 中列出了哪些 tool，
    编译系统就编译哪些 tool。额外工具应通过 CapabilitySet 编辑
    流程显式添加（admin UI 层负责预填充），不在此处注入。
    """
    bindings: list[ToolBinding] = []
    for sel in selections:
        factory = registry.get(sel.tool_name)
        config = factory.derive(sel.user_fields, context)
        bindings.append(ToolBinding(tool_name=sel.tool_name, config=config))
    return bindings
```

#### 3.6.1 编译系统替换什么

编译系统是 `_tools.py` 的替代品，但不是「把注入逻辑搬个家」。原来由 assembly 隐式注入的工具（execute_python、restart_kernel 等）改为在 **CapabilitySet 编辑层**显式添加到 `tools` 中——admin UI 或系统逻辑在创建/编辑 CapabilitySet 时预填充。编译系统只编译 `tools` 中已列出的条目。

| 被替代函数 | 去向 |
|---|---|
| `_tool_definition_configs()` | 验证 → `compile_tool_bindings` 内联（registry.get + factory.derive） |
| `_agent_tool_configs()` | → `compile_tool_bindings`（纯 1:1 编译器；组合逻辑移到 CapabilitySet 编辑层） |
| `_builtin_tool_configs()` | 由 system 在 CapabilitySet 编辑时预填充 `tools`，不再是 assembly 职责 |
| `_python_agent_tool_config()` | `ExecutePythonToolFactory.derive`（但不由编译系统自动调用；`tools` 需显式包含 execute_python） |
| `_python_tool_runtime()` | → `ExecutePythonToolFactory.derive` 的具体实现 |
| `_facade_imports()` | → `ExecutePythonToolFactory.derive` 内部推导逻辑 |
| `_facade_expand_functions()` | → `ExecutePythonToolFactory.derive` 内部推导逻辑 |
| `_handwritten_external_modules()` | → `ExecutePythonToolFactory.derive` 内部推导逻辑 |
| `_python_session_state()` | → `ExecutePythonToolFactory.derive` 从 context.identity 推导 |

`_tools.py` 本身缩减为仅保留 `set_assembly_tool_registry` 跨模块注册函数（或移入 `_compiler.py`）。

#### 3.6.2 与 assembly 的集成

```py
# core/assembly/_definition.py（更新后）

def build_agent_definition(
    binding: ResolvedConversation,
    facade: ActorFacadeBinding | None,
    registry: ToolRegistry,
) -> AgentDefinition:
    context = ToolDeriveContext(
        workspace_path=binding.capability_set.workspace_path,
        venv_python=facade.venv_python if facade else "",
        facade=facade,
        actor_id=binding.actor.id,
        agent_name=binding.actor.name,
        session_id=binding.conversation.conversation_id,
        mailbox_id=binding.actor.id,
    )
    tool_bindings = compile_tool_bindings(
        list(binding.capability_set.tools),
        context,
        registry,
    )
    
    # 编译系统不做隐式注入。CapabilitySet.tools 中缺少某个 tool = 该 tool 不在 runtime 中。
    # 不变量 4 由 CapabilitySet 编辑层保障——admin UI 负责预填充必要工具。
    
    definition.tools = {
        tb.tool_name: msgspec.to_builtins(tb.config)
        for tb in tool_bindings
    }
    return definition
```

### 3.7 新增工具的设计流程

每个新工具由两个角色配合完成：

**Tool 作者（yuuagents/yuutools 层面）的职责：**

1. **定义 config_type**：完整运行时配置结构（msgspec.Struct 子类）。
2. **实现 Tool 子类**：from_startup、definition、create_coro、cancel 等运行时方法。
3. **不写 derive**。Tool 类对 config 的构造方式完全无知。

**系统集成者（yuubot 层面）的职责：**

4. **创建 ToolFactory 子类**：实现 name、description、config_schema、tool_class()。
5. **写 factory.derive(user_fields, context) → config_type**：推导规则。明确从 user_fields 的哪些字段和 context 的哪些字段推导出 config_type 的每个字段。
6. **定义 user_fields 结构**：根据系统能提供的 context，决定哪些 config 字段需要用户填写。若全部可推导，user_fields 为空 struct。
7. **注册**：factory 加入 ToolRegistry；user_fields_type 加入 TOOL_USER_FIELDS 映射（或作为 factory 属性）。
8. **Admin UI** 自动从 user_fields_type 的 JSON Schema 渲染配置表单。

> 核心原则（最终版）：推导完全由系统主导。Tool 类（yuuagents）对自身 config 的构建方式完全无知。系统（yuubot）通过 ToolFactory.derive 和编译系统完成所有推导工作。

---

## 4. 组合拓扑

```
Provider(api_type)          Secret
      │                       │
      ▼                       ▼
   LLMBackend ──bind(model, gen_params)──► BoundLLM ──stream──► Usage
      ▲                                                     │
      │ ref                                                 ▼
    Actor ──persona_prompt, model, gen_override, per_run_budget   costing ← ModelConfig.pricing
     │ ref                        └─ gen_params = backend.default ⨄ actor.gen_override (turn-time merge)
      ├──ref──► CapabilitySet ──{tools: [ToolSelection], integration_ids, loop_policy}
      │                       └── 编译系统: ToolSelection[ ] + context → compile_tool_bindings → [ToolBinding]
      │                           └── registry.get(name).derive(user_fields, context) → config
      │                           └── (推导完全由系统主导; Tool 类不参与)
      │                       └── integration_ids → VisibleIntegrationSurface[] → facade SDK + Integration SDK prompt
     │  (persona 不再独立成表，§2.6；persona_prompt 内嵌为 actor 字段)
     │
     └──actor_ref(live)
               │
               ▼
         Conversation ──{actor_ref(live), history_snapshot(frozen), metadata}
                  └── 不持任何 actor 配置；全部 follow actor（§2.9）
```

### 不变量汇总

1. 存储层只存引用（FK）+ 自身配置，不存快照。
2. 已解析的复合视图（ResolvedActor / ResolvedConversation）只在 turn 时刻存在，是只读读模型。
3. Conversation 的 history prefix once-frozen，结构绑定 live-follow-actor。
4. 所有对 LLM 可见的 tool 必须出现在 CapabilitySet.tools 中——不存在隐式注入的 tool；tool 的说明来自 tool definition/description，不在 system prompt 重复维护。
5. 推导完全由系统主导。Tool 类（yuuagents）不参与推导，只消费 from_startup 接收到的 typed config。derive 是 ToolFactory（yuubot 层）的职责。
6. GenerationParams 在 turn 时刻有且仅有一个解析点。
7. system prompt 首段 `# Persona` 是 prompt 组装契约；数据源 = `Actor.persona_prompt`（非资源表），不存在 Character 资源概念。
8. 前端 integration 授权粒度是 `IntegrationRecord.id` 整体；method/function 级 capability id 只作为 integration 内部调用单位和 facade 生成输入。
9. system prompt 的 integration 内容按 SDK 粒度渲染短说明和示例，不逐个展开所有 function schema，避免 prompt 随函数数量线性膨胀。

---

## 5. 死字段清单（汇总）

本表与各概念的「字段替换与删除」小节一一对应。详细原因 + 替换目标见各 §。

| 概念 | 删除字段 | 替换/去向 | 判据 / 详情 |
|---|---|---|---|
| `StreamOptions` | `.model` | 删（model 是 session selector，非采样参数） | `_stage.py:92` 显式 pop，死字段（§2.5.4） |
| `StreamOptions`（整体） | 改名 → `GenerationParams` | `core/validation.py` | 名字暗示含会话级参数；实际 4 采样字段（§2.5.4） |
| `YuuAgentLLMOptions`（整体） | 删整个 struct | `records.py:109-113` | 其 `max_tokens` = `GenerationParams.max_tokens`，`stream_options` = `GenerationParams`；无独立价值，actor 改直接持 `generation_override: GenerationParams`（§2.5.4, §2.8.2） |
| `LLMBackendRecord.default_stream_options` | 改名 → `.default_generation_params` | `records.py:61` | 名称对齐职责（§2.5.4） |
| `BoundLLM.stream_options: dict` | 改名 → `.generation_params: GenerationParams` | `llm.py:13` | 死字段（`_definition.py:31` 读 `llm_options` 不读它）；并 dict → 强类型（§2.5.4） |
| `_stage_llm_options()` + `opts.pop("model")` | 删整个函数 | `_stage.py:84-93` | 单一解析点取代（§2.5.3） |
| `validate_stream_options()` | 改名 → `validate_generation_params()` | `validation.py:43` | 返回 `GenerationParams` 而非 dict（§2.5.4） |
| `LLMProviderOptions.provider_name` | 删 | `validation.py:37` | 与 `yuuagents_provider` 重复定义同件事（§2.1.2） |
| `_resolve_yuuagents_provider()` / `_YUUAGENTS_KNOWN_FACTORIES` | 删 | `_constants.py:38,41-50` | base_url 启发式 + 集合判定，由 `resolve_provider()` 取代（§2.1.2） |
| `LLMProviderOptions.api_key: str` | 类型升级 → `EncryptedSecret` | `validation.py:38` | 明文落盘，与集成 side 不一致（§2.2.3） |
| `ModelCatalog`（整体） | 删 | `records.py:27` | 模型名字列表来自 provider API，不再持久化存储（§2.3.4） |
| `PricingTable`（整体） | 删 | `records.py:38` | 独立表 → 下沉进 `ModelConfig.pricing`（§2.3.4） |
| `PricingEntry` | 改名 → `Pricing`，删 `.model` 字段 | `records.py:31` | `.model` 是表外键，嵌入 ModelConfig 后冗余（§2.3.4） |
| `LLMBackendRecord.model_capabilities` | 删 | `records.py:52` | 下沉到 `ModelConfig.capabilities`（§2.3.4） |
| `LLMBackendRecord.models: ModelCatalog` | 改为 `model_configs: dict[str, ModelConfig]` | `records.py:53` | key=模型名的配置字典；不在 dict 中 = 未配置（§2.3.4） |
| `LLMBackendRecord.pricing: PricingTable` | 删 | `records.py:54` | 下沉进 `ModelConfig.pricing`（§2.3.4） |
| `LLMBackendRecord.yuuagents_provider` | 删 | `records.py:51` | → `provider_identity`（§2.1.2） |
| `LLMBackendRecord.default_model` | 改名 → `.recommended_model` | `records.py:60` | 消歧 actor 选定 vs backend 推荐（§2.4.2） |
| `ActorRecord.default_character: CharacterRecord` | 改 → 内嵌 `.persona_prompt: str` | `records.py:202` | Character 表删除，唯一存活字段 system_prompt 下沉（§2.6, §2.8.2） |
| `ActorRecord.capability_set: CapabilitySetRecord` | 改 → `.capability_set_id: str` | `records.py:203` | 嵌入 struct = 存储层混入读模型（§2.8.2） |
| `ActorRecord.default_llm_backend: LLMBackendRecord` | 改 → `.llm_backend_id: str` | `records.py:204` | 同上（§2.8.2） |
| `ActorRecord.default_model: str` | 改名 → `.model: str` | `records.py:205` | "default" 在 actor 语境里冗余（§2.8.2） |
| `ActorRecord.default_llm_options: YuuAgentLLMOptions` | 改名 → `.generation_override: GenerationParams` | `records.py:206-208` | 去套层（§2.8.2）；YuuAgentLLMOptions 整体已删 |
| `ActorRecord.default_budget: YuuAgentBudget` | 改名 → `.per_run_budget: RunBudget` | `records.py:209` | 类型名对齐（§2.8.2） |
| `ConversationRecord.character: CharacterRecord` | 删 | `records.py:237` | Character 表删除；persona 来自 `actor.persona_prompt`，会话不持有（§2.9.3） |
| `ConversationRecord.capability_set: CapabilitySetRecord` | 删 | `records.py:238` | follow `actor.capability_set_id`，会话不覆盖（§2.9.3） |
| `ConversationRecord.llm_backend: LLMBackendRecord` | 删 | `records.py:239` | follow `actor.llm_backend_id`，会话不覆盖（§2.9.3） |
| `ConversationRecord.model: str` | 删 | `records.py:240` | 来自 `actor.model`，会话不覆盖（§2.9.3） |
| `ConversationRecord.llm_options: YuuAgentLLMOptions` | 删 | `records.py:241` | generation params 来自 `actor.generation_override`（merge 于 turn 时刻），会话不覆盖（§2.9.3） |
| `ConversationRecord.budget: YuuAgentBudget` | 删 | `records.py:242` | per-run budget 来自 `actor.per_run_budget`，会话不覆盖（§2.9.3） |
| `CharacterRecord`（整表 + `CharacterORM` + admin 路由） | 删整表 | `records.py:159-172` | 唯一行为字段 system_prompt 下沉成 actor.persona_prompt；其余字段死或仅 UI 标签（§2.6.1） |
| `CharacterRecord.facade_module` | 删 | `records.py:163` | 死字段；assembly 用 `_constants.FACADE_IMPORTS` 硬编码常量，从未读此字段（§2.6.1） |
| `CharacterRecord.default_hints` | 删 | `records.py:164` | 无消费 |
| `CharacterRecord.name` / `.description` | 删 | `records.py:161-162` | 仅 UI 标签，无行为消费（§2.6.1） |
| `_prompt.py` `SECTION_HEADERS[0]` `"Character"` | 改名 → `"Persona"` | `_prompt.py:39-45` | 段名指其内容；不再指已删的 Character 概念（§2.6.2） |
| `_prompt.py` `_render_character` | 改名 → `_render_persona`，数据源改 `actor.persona_prompt` | `_prompt.py:114-115` | 同上（§2.6.3） |
| `character_name` trace span 属性 | 删 | `simple_loop.py:71,231`、`conversations.py:911` | 与 `actor.name` 重复概念（§2.6.3） |
| `simple_loop.py:99` `is_table("characters")` 钩子 | 删 | `simple_loop.py:99` | 表已不存在，无失效路径（§2.6.3） |
| `builtin_presets._seed_character` / `PresetPair.character` | 删；文案转 `BUILTIN_PERSONA_PROMPTS` 常量字典 | `builtin_presets.py:104,179,183-207` | 不再是 DB 资源，仅 admin UI 模板（§2.6.2） |
| `ToolConfig` | `.spec` (ToolSpecConfig) | 删 | 全链不读 |
| `RuntimePolicy` | `memory_enabled` / `memory_curator_enabled` / `strict_usage_sink` | 删 | 仅预设设值，无消费 |
| `ResourcePolicy` | 整体（`budget_usd_daily` / `concurrency_limit` / `bridge_nodes` / `workspace_access`） | 删 | 仅预设设值，无 enforcement |
| `CapabilitySetRecord` | `bootstrap_path` / `enabled_global_skill_refs` / `workspace_skill_root` / `preexpanded_skill_refs` / `prompt_fragments` / `permission_limits` / `integration_visible_state` | 删 | 声明+存储零消费 |
| `PromptTemplateRecord` | 整表 | 删 | 运行时无消费 |

---

## 6. 附录：per-instance 推导（逐步补充）

每个工具的推导逻辑。分为 tool 层（config_type）和系统层（derive + user_fields_type）。

格式：

```
### Tool: <name>

**config_type**（tool 层）: <struct 定义与字段>

**derive(user_fields, context) → config_type**（系统层 ToolFactory 方法）:
  - <config_type 字段> ← <来源：user_fields.x / context.y / 硬编码默认值>

**系统定义的 user_fields_type**: <struct 或 EmptyFrontendFields>
```

---

### 6.1 bash

**config_type**（tool 层）: `BashToolConfig`

| 字段 | 类型 | 默认值 |
|---|---|---|
| `workspace_root` | `str` | `""` |
| `timeout_s` | `float` | `30.0` |
| `max_timeout_s` | `float` | `120.0` |
| `max_stderr_chars` | `int` | `4000` |

**derive(user_fields, context) → BashToolConfig**:

```
BashToolConfig(
    workspace_root=context.workspace_path,
    # timeout_s, max_timeout_s, max_stderr_chars 取 struct 默认值
)
```

**系统定义的 user_fields_type**: `EmptyFrontendFields`

**推导理由**: workspace_root 完全来自 context；timeout 参数和安全限制（max_stderr_chars）有合理的硬编码默认值，不需要用户按 tool selection 调整。

---

### 6.2 read

**config_type**（tool 层）: `FileToolConfig`

| 字段 | 类型 | 默认值 |
|---|---|---|
| `workspace_root` | `str` | `""` |
| `max_read_bytes` | `int` | `2_000_000` |

**derive(user_fields, context) → FileToolConfig**:

```
FileToolConfig(
    workspace_root=context.workspace_path,
    # max_read_bytes 取 struct 默认值
)
```

**系统定义的 user_fields_type**: `EmptyFrontendFields`

**推导理由**: workspace_root 来自 context；max_read_bytes（2MB）是合理上限，无需用户配置。

---

### 6.3 edit

**config_type**（tool 层）: `FileToolConfig`（与 read 共享同一 struct，`max_read_bytes` 对 edit 无行为影响）

| 字段 | 类型 | 默认值 |
|---|---|---|
| `workspace_root` | `str` | `""` |
| `max_read_bytes` | `int` | `2_000_000`（edit 不消费此字段） |

**derive(user_fields, context) → FileToolConfig**:

```
FileToolConfig(
    workspace_root=context.workspace_path,
)
```

**系统定义的 user_fields_type**: `EmptyFrontendFields`

**推导理由**: 只需要 workspace_root；共享 struct 中 unused 字段不暴露给用户。

---

### 6.4 write

**config_type**（tool 层）: `FileToolConfig`（与 read/edit 共享 struct）

| 字段 | 类型 | 默认值 |
|---|---|---|
| `workspace_root` | `str` | `""` |
| `max_read_bytes` | `int` | `2_000_000`（write 不消费此字段） |

**derive(user_fields, context) → FileToolConfig**:

```
FileToolConfig(
    workspace_root=context.workspace_path,
)
```

**系统定义的 user_fields_type**: `EmptyFrontendFields`

---

### 6.5 restart_kernel

**config_type**（tool 层）: `RestartKernelConfig`（空 struct，无字段）

**derive(user_fields, context) → RestartKernelConfig**:

```
RestartKernelConfig()
```

该 tool 在运行时通过 `runtime.registry.resolve("execute_python")` 按名查找目标实例，不依赖任何配置字段。

**系统定义的 user_fields_type**: `EmptyFrontendFields`

---

### 6.6 execute_python

**config_type**（tool 层）: `PythonRuntime`

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `config` | `PythonKernelConfig` | 默认构造 | 内嵌 kernel 启动参数（见下） |
| `imports` | `tuple[PythonImport, ...]` | `()` | kernel 启动时预导入的模块 |
| `state` | `dict[str, object]` | `{}` | 暴露为 kernel 中的 `SESSION_STATE` |
| `expand_functions` | `tuple[str, ...] \| None` | `None` | 函数文档展开 glob 模式 |

`PythonKernelConfig` 子字段：

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `python` | `str \| None` | `None` | kernel 解释器路径 |
| `cwd` | `str \| None` | `None` | kernel 工作目录 |
| `inherit_envs` | `bool` | `True` | 是否继承父进程 env |
| `env_allowlist` | `tuple[str, ...] \| None` | `None` | 环境变量白名单 |
| `extra_envs` | `dict[str, str]` | `{}` | 额外环境变量 |
| `sys_path` | `tuple[str, ...]` | `()` | 额外 `sys.path` 条目 |
| `startup_code` | `str` | `""` | kernel 启动时执行的 Python 代码 |

**derive(user_fields, context) → PythonRuntime**:

```
PythonRuntime(
    config=PythonKernelConfig(
        python=context.venv_python,           # facade 的 .venv python
        cwd=context.workspace_path,            # actor 工作区根
        sys_path=tuple(context.facade.sys_path),
        startup_code=_build_startup_code(context.facade.startup_code),
    ),
    imports=_build_imports(context.facade),
    state=_build_state(context.identity),
    # expand_functions=None — 默认不展开函数详情，agent 通过模块 doc 自行发现
)
```

**各字段推导规则**:

| 字段 | 来源 | 说明 |
|---|---|---|
| `config.python` | `context.venv_python` | 工作区 `.venv/bin/python` |
| `config.cwd` | `context.workspace_path` | actor 工作区根 |
| `config.sys_path` | `facade.sys_path` | 集成注册时收集的 sys.path |
| `config.startup_code` | `facade.startup_code` + 硬编码数据别名预导入（`matplotlib.use("Agg")`, `pd`, `np`, `plt`） | 集成 startup_code 和数据分析库预导入拼接 |
| `config.inherit_envs` | `True`（硬编码默认） | 全量继承父进程 env |
| `config.env_allowlist` | `None`（硬编码默认） | 无白名单限制 |
| `config.extra_envs` | `{}`（硬编码默认） | 无额外 env |
| `imports` | `yb`, `yb.actor`, `yb.delegate`, `yb.schedule`, `yb.tasks`（固定）+ 按 `facade.visible_integrations` 派生的 `yext.*` 模块 | 系统 facade + 集成 facade。`facade.visible_integrations` 已是 `CapabilitySet.integration_ids` ∩ enabled/running integration 实例后的可见 integration SDK 列表 |
| `state` | `context.identity`: `{actor_id, agent_name, session_id, mailbox_id}` | 四字段恒来自 identity |
| `expand_functions` | `None` | 不展开函数详情 |

其中 `_build_startup_code` 拼接：

```python
_PRELOADED_DATA_ALIASES = (
    "import matplotlib\n"
    'matplotlib.use("Agg")\n'
    "import pandas as pd\n"
    "import numpy as np\n"
    "import matplotlib.pyplot as plt\n"
)

def _build_startup_code(facade_startup_code: str) -> str:
    code = facade_startup_code
    if code and not code.endswith("\n"):
        code += "\n"
    code += _PRELOADED_DATA_ALIASES
    return code
```

`_build_imports` 构建：

```python
_FACADE_IMPORTS = (
    PythonImport(module="yb"),
    PythonImport(module="yb.actor"),
    PythonImport(module="yb.delegate"),
    PythonImport(module="yb.schedule"),
    PythonImport(module="yb.tasks"),
)

def _build_imports(facade: ActorFacadeBinding) -> tuple[PythonImport, ...]:
    """系统 facade（yb） + 按可见 integration SDK 派生的集成 facade（yext.*）。

    ``facade.visible_integrations`` 已经是按 integration 实例选择后的可见 SDK 列表：
        CapabilitySet.integration_ids
        ∩ enabled IntegrationRecord
        ∩ running IntegrationInstance

    不在交集内的 integration 不会出现在 facade.visible_integrations 中，
    因此也不会被导入到 kernel——actor 看不到它不应看到的 SDK。
    """
    yext_modules = set()
    for integration in facade.visible_integrations:
        yext_modules.update(integration.sdk.import_paths)
    return _FACADE_IMPORTS + tuple(
        PythonImport(module=m) for m in sorted(yext_modules)
    )
```

**系统定义的 user_fields_type**: `EmptyFrontendFields`

**推导理由**: `PythonRuntime` 的全部字段均可从 context + facade 推导。imports 由 identity（固定 yb 系）和可见 integration SDK（yext.* 系）确定；state 由 identity 确定；expand_functions 默认不展开（agent 通过模块 `__doc__` 自行发现函数）；kernel config 由 venv/workspace/facade 确定。用户没有任何需要填写的字段。

**设计理念**: Python 侧不做过多限制（kernel 环境隔离由 ipykernel 保证）。信息组织通过 integration SDK 实现：system prompt 只展示每个 integration 的短 SDK 说明和代表性示例；每个 facade module 的 `__doc__` 写好详细用法文档，agent 在需要时通过 Python introspection 自行发现深层函数。
