# 代码问题集合

> 本文档记录 yuubot 代码库中已识别的反模式、抽象泄露和性能问题，供维护者参考。
> 审阅日期：2026-03-12。所有问题均基于 `refactor.md` 的架构标准。

## 目录

1. [静默失败](#1-静默失败)
2. [Dict 驱动开发](#2-dict-驱动开发)
3. [全局可变状态](#3-全局可变状态)
4. [N+1 查询问题](#4-n1-查询问题)
5. [抽象泄露](#5-抽象泄露)
6. [脆弱的类型检测](#6-脆弱的类型检测)
7. [循环导入的症状性修复](#7-循环导入的症状性修复)
8. [不一致的错误处理](#8-不一致的错误处理)
9. [其他代码异味](#9-其他代码异味)
10. [优先级建议](#10-优先级建议)

---

## 1. 静默失败

**违反原则**：`refactor.md` 要求 "Fail Fast"，永远不吞掉错误或返回静默失败状态。

静默失败是本代码库最普遍的问题。异常被捕获后仅记录日志，执行继续，导致 Bug 被隐藏直到引发级联故障。

### 1.1 `db.py:187-189` — libsimple 加载失败静默降级

FTS5 中文分词静默降级，搜索质量下降但调用方无任何感知。
**修复方向**：区分"可选功能降级"（记录 INFO，暴露状态标志）和"必须成功的操作"（抛出异常）。

### 1.2 `agent_runner.py:290-291` — compressor 构建失败返回 None

`_make_compressor()` 失败时静默返回 None，对话上下文可能无限增长，直到 token 超限才暴露。
**修复方向**：缺失配置时抛出 `ConfigurationError`，与同文件 `_make_summary_llm()` 保持一致。

### 1.3 `agent_runner.py:703-705` — memory hints 构建失败返回空字符串

`_build_memory_hints()` 捕获所有异常返回 `""`，记忆功能静默失效。

### 1.4 `session.py:150-151` — 异步任务失败仅记录警告

`_sync_current_agent()` 是 fire-and-forget 任务，失败时无人知晓，agent 状态可能与 DB 不一致。

### 1.5 `compressor.py:44-46` — compress() 失败返回 None

调用方无法区分"压缩成功"和"压缩失败"，可能用未压缩的超长上下文继续执行。

### 1.6 `formatter.py:73-74` — at 名称解析失败静默回退

`_resolve_at_name()` 解析失败时返回原始 QQ 号，LLM 看到的是数字而非用户名，影响对话质量。

### 1.7 `prompt.py:175-176` — addon 文档加载失败静默跳过

`_load_addon_docs()` 捕获 `FileNotFoundError` 仅记录警告，agent 可能在缺少工具文档的情况下运行。

---

## 2. Dict 驱动开发

**违反原则**：`refactor.md` 禁止使用泛型 `dict` 作为万能容器传递数据，要求显式领域模型。

### 2.1 `models.py` — MessageEvent 使用无类型 dict

```python
class MessageEvent(msgspec.Struct):
    sender: dict  # 无类型验证，允许任意字段
    message: list[dict]  # OneBot CQ 段是无类型 dict
```

**问题**：运行时才发现字段缺失或类型错误。
**修复方向**：定义 `Sender` 和 `CQSegment` 的 msgspec.Struct，启用严格验证。

### 2.2 `agent_runner.py` — 环境变量无类型验证

```python
_agent_subprocess_env: dict[str, dict]  # 内层 dict 无结构约束
```

**修复方向**：定义 `SubprocessEnv` 类型，明确允许的环境变量。

### 2.3 `addons/__init__.py:27` — ContentBlock 无结构验证

```python
ContentBlock = dict[str, Any]  # 完全无约束
```

**修复方向**：定义 `TextBlock`, `ImageBlock` 等 tagged union。

### 2.4 `im.py:39-55` — _normalize_segment 接受任意 dict

接受任意 dict 并尝试重构为 OneBot 格式，无前置验证。
**修复方向**：输入应为 typed segment，输出为 OneBot dict。

---

## 3. 全局可变状态

**违反原则**：`refactor.md` 要求函数式风格，避免副作用。全局可变状态引入并发风险，难以测试。

### 3.1 `db.py` — 模块级标志

```python
_simple_loaded = False
_fts_rebuilt = False
```

**问题**：多线程环境下竞态条件，状态不可预测。
**修复方向**：封装为 `DBState` 单例，用锁保护。

### 3.2 `agent_runner.py:25-36` — 懒加载的全局工具注册表

```python
_ADDON_TOOLS: dict[str, Tool] = {}

def _get_addon_tools() -> dict[str, Tool]:
    if not _ADDON_TOOLS:  # 无锁，并发不安全
        from yuubot.addons.tools import ...
        _ADDON_TOOLS.update(...)
    return _ADDON_TOOLS
```

**问题**：并发调用可能重复加载，或读到半初始化状态。
**修复方向**：用 `@functools.lru_cache` 或显式单例模式。

### 3.3 `addons/__init__.py:58-59, 195` — 全局注册表

```python
_REGISTRY: dict[str, type[Addon]] = {}
_INSTANCES: dict[str, Addon] = {}
_current_context: ContextVar[AddonContext | None] = ContextVar(...)
```

**问题**：`_REGISTRY` 和 `_INSTANCES` 无锁保护，`_current_context` 在非 async 环境下行为未定义。
**修复方向**：注册表改为只读（模块加载时冻结），实例管理用依赖注入。

### 3.4 `formatter.py:21` — 无界增长的别名缓存

```python
_alias_cache: dict[int, str] = {}  # 无 TTL，无大小限制
```

**问题**：长期运行后内存泄漏。
**修复方向**：用 `functools.lru_cache` 或 TTL 缓存（如 `cachetools.TTLCache`）。

---

## 4. N+1 查询问题

**性能影响**：规模扩大时性能线性或指数退化。

### 4.1 `dispatcher.py:317` — 每条群消息查询 GroupSetting

```python
# 每条消息都执行一次数据库查询
setting = await GroupSetting.filter(group_id=gid).first()
```

**问题**：高频群聊中每秒可能数十次查询。
**修复方向**：在 `Dispatcher` 初始化时加载所有 GroupSetting，用 dict 缓存，监听配置变更事件刷新。

### 4.2 `agent_runner.py:607-626` — 获取所有群列表后缓存

```python
groups = await self._api.get_group_list()  # 获取所有群
self._group_cache = {g["group_id"]: g for g in groups}
```

**问题**：bot 加入 1000+ 群时，每次解析群名都要先获取完整列表。
**修复方向**：按需查询单个群信息，用 LRU 缓存。

### 4.3 `formatter.py:46, 58` — 两次独立查询用户别名

```python
# 第一次查询
user = await User.filter(user_id=user_id).first()
# 第二次查询
alias = await UserAlias.filter(user_id=user_id).first()
```

**修复方向**：用 `prefetch_related` 或单次 JOIN 查询。

### 4.4 `store.py:114` — Python 迭代计数标签

```python
entries = await ImageEntry.all()  # 获取所有条目
tag_counts = {}
for entry in entries:
    for tag in entry.tags:
        tag_counts[tag] = tag_counts.get(tag, 0) + 1
```

**修复方向**：用 SQL 聚合：
```sql
SELECT json_each.value as tag, COUNT(*)
FROM image_entry, json_each(image_entry.tags)
GROUP BY tag
```

### 4.5 `store.py:86-89` — 获取 3x limit 后 Python 过滤

```python
entries = await query.limit(limit * 3)  # 过度获取
filtered = [e for e in entries if all(t in e.tags for t in tags)][:limit]
```

**修复方向**：用 SQLite JSON 操作符在 WHERE 子句中过滤。

---

## 5. 抽象泄露

**违反原则**：`refactor.md` 要求"最小知识原则"，组件不暴露内部实现。

### 5.1 `agent_runner.py:239` — 私有方法被外部调用

```python
# agent_runner.py
def _replace_command_prefix(self, ...):  # 私有方法
    ...

# dispatcher.py
runner._replace_command_prefix(...)  # 外部调用私有方法
```

**问题**：破坏封装，重构 `AgentRunner` 时可能破坏 `Dispatcher`。
**修复方向**：改为公开方法或移至共享工具模块。

### 5.2 `dispatcher.py` — _CtxWorker 紧耦合

`_CtxWorker` 是内部类但直接访问 `Dispatcher` 的多个私有属性，难以独立测试。
**修复方向**：提取为独立类，通过构造函数注入依赖。

### 5.3 `context.py` — get_or_create 并发不安全

```python
async def get_or_create(self, ctx_type: str, target_id: int) -> int:
    # 无锁，并发调用可能创建重复 ctx_id
    if (ctx_type, target_id) not in self._reverse:
        ctx_id = await self._allocate_new_id(ctx_type, target_id)
        self._forward[ctx_id] = (ctx_type, target_id)
        self._reverse[(ctx_type, target_id)] = ctx_id
    return self._reverse[(ctx_type, target_id)]
```

**修复方向**：用数据库 UNIQUE 约束 + INSERT OR IGNORE，或用 asyncio.Lock 保护。

---

## 6. 脆弱的类型检测

**违反原则**：`refactor.md` 要求"严格类型"，零容忍类型警告。

### 6.1 `formatter.py:108, 125` — hasattr 鸭子类型

```python
if hasattr(seg, 'text'):  # 应用 isinstance(seg, TextSegment)
    ...
if hasattr(seg, 'qq'):  # 应用 isinstance(seg, AtSegment)
    ...
```

**问题**：任何有 `text` 属性的对象都会被当作 TextSegment，类型安全丢失。
**修复方向**：用 `isinstance` + tagged union。

### 6.2 `compressor.py` — tuple 长度检测消息格式

```python
if isinstance(msg, tuple) and len(msg) == 2:
    role, content = msg
    # 无验证 role 是否为合法字符串，content 是否为预期类型
```

**修复方向**：定义 `Message = tuple[Literal["user", "assistant", "system"], str | list]` 类型。

---

## 7. 循环导入的症状性修复

**违反原则**：`refactor.md` 禁止用延迟导入掩盖循环导入，要求结构化解决。

### 7.1 `agent_runner.py:31-35` — 懒加载 addon tools

```python
def _get_addon_tools() -> dict[str, Tool]:
    if not _ADDON_TOOLS:
        from yuubot.addons.tools import ...  # 函数内导入避免循环
```

**根因**：`agent_runner` 需要 `addons.tools`，`addons` 需要 `agent_runner` 提供的某些类型。
**修复方向**：提取共享类型到 `core/types.py`，应用依赖倒置。

### 7.2 `addons/__init__.py:340-346` — 模块加载时导入所有 addon

```python
# 任一 addon 导入失败，整个框架崩溃
from . import im, mem, web, img, schedule, hhsh
```

**修复方向**：改为注册表模式，addon 自行注册，失败时仅该 addon 不可用。

---

## 8. 不一致的错误处理

**影响**：调用方无法统一处理错误，代码难以维护。

### 8.1 配置缺失的不同处理方式

```python
# agent_runner.py:253-256
def _make_summary_llm(self):
    if "summary_llm" not in self.config.yuuagents:
        raise ValueError("summary_llm not configured")  # 抛出异常

# agent_runner.py:290-291
def _make_compressor(self):
    if "summary_llm" not in self.config.yuuagents:
        return None  # 返回 None
```

**修复方向**：统一为抛出 `ConfigurationError`。

### 8.2 操作失败的不同返回方式

```python
# im.py:84
return "错误：无法发送消息"  # 返回错误文本

# img.py:65-68
def delete(self, image_id: int) -> bool:
    # 返回布尔值
    return deleted_count > 0
```

**修复方向**：统一为抛出领域异常（如 `MessageSendError`, `ImageNotFoundError`）。

---

## 9. 其他代码异味

### 9.1 `addons/__init__.py:310-335` — 手动解析 YAML frontmatter

手动字符串操作解析 `---` 包裹的 YAML，边界情况（如文档中包含 `---`）会崩溃。
**修复方向**：用 `python-frontmatter` 库。

### 9.2 `db.py` — 硬编码路径

```python
lib_path = Path(__file__).parent.parent / "vendor" / "libsimple"
```

**修复方向**：从环境变量 `YUUBOT_LIBSIMPLE_PATH` 读取，回退到默认路径。

### 9.3 `session.py:142-157` — fire-and-forget 异步任务

```python
asyncio.create_task(_sync_current_agent(...))  # 不 await，失败无人知晓
```

**修复方向**：用 `TaskGroup` 管理后台任务，或显式存储 task 引用并在关闭时 await。

### 9.4 `dispatcher.py:316-327` — 错误上下文信息差

```python
except Exception as e:
    logger.error(f"Failed to query group setting: {e}")
    # 记录的是 Tortoise 内部信息（context ID, connection ID），而非实际错误
```

**修复方向**：记录 `group_id`, `exception type`, `traceback`。

### 9.5 `models.py` — 无数据验证

```python
class MessageEvent(msgspec.Struct):
    user_id: int  # 允许负数
    group_id: int | None  # 无约束
```

**修复方向**：用 msgspec 的 `Meta` 或 attrs 的 validators 添加约束。

### 9.6 `formatter.py:188-189` — reply 消息缺失时返回占位符

```python
if not msg:
    return "[消息已撤回或不存在]"  # 静默回退
```

**修复方向**：抛出 `MessageNotFoundError`，由调用方决定如何处理。

---

## 10. 优先级建议

### P0（立即修复，影响稳定性）

1. **消除 N+1 查询** — `dispatcher.py:317` 的 GroupSetting 查询（高频操作）
2. **修复并发不安全的全局状态** — `agent_runner.py` 的 `_ADDON_TOOLS`，`addons/__init__.py` 的注册表
3. **统一错误处理** — 配置缺失、操作失败应统一抛出异常

### P1（影响可维护性）

4. **消除 Dict 驱动开发** — `models.py` 的 `MessageEvent.sender` 和 `message` 字段
5. **修复抽象泄露** — `agent_runner._replace_command_prefix` 被外部调用
6. **结构化解决循环导入** — 提取共享类型到 `core/types.py`

### P2（技术债，逐步偿还）

7. **消除静默失败** — 所有 `except: log + continue` 改为 `except: raise`
8. **严格类型检测** — `hasattr` 改为 `isinstance`
9. **优化缓存策略** — `formatter.py` 的别名缓存加 TTL

### P3（优化）

10. **其他 N+1 查询** — `formatter.py`, `store.py` 的查询优化
11. **手动 YAML 解析** — 改用 `python-frontmatter`
12. **硬编码路径** — 改为环境变量配置

---

## 附录：问题文件排名

按问题严重程度和数量排序：

1. **agent_runner.py** — 8+ 问题（懒加载、静默失败、N+1、抽象泄露）
2. **dispatcher.py** — 5+ 问题（N+1、静默失败、错误上下文差）
3. **formatter.py** — 5+ 问题（全局缓存、N+1、脆弱类型检测）
4. **addons/__init__.py** — 6+ 问题（全局状态、循环导入、脆弱解析）
5. **db.py** — 4+ 问题（全局状态、静默失败、硬编码路径）
6. **models.py** — 3+ 问题（Dict 驱动、无验证）
7. **session.py** — 3+ 问题（fire-and-forget 任务、静默失败）
8. **compressor.py** — 2+ 问题（静默失败、脆弱类型检测）
9. **store.py** — 2+ 问题（N+1 查询）
10. **im.py**, **img.py**, **prompt.py** — 各 1-2 问题

---

**维护建议**：

- 新增功能前，先检查本文档对应模块是否有已知问题，优先修复后再添加功能
- 修复问题时，更新本文档标记为"已修复"并注明 commit hash
- 每季度重新审阅一次，识别新引入的反模式
