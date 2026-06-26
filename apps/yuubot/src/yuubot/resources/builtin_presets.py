"""Built-in preset Character and CapabilitySet record definitions.

These records are seeded at install time (``open_resources``) so that the
Admin UI shows stable, onboarding-ready presets before the user creates any
backend of their own. They are normal persisted records after seeding: users
may edit or delete them, and reseeding is idempotent and non-destructive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from tortoise import Model

from yuubot.resources.records import (
    CapabilitySetRecord,
    CharacterHints,
    CharacterRecord,
    ResourcePolicy,
    RuntimePolicy,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import CapabilitySetORM, CharacterORM

BUILTIN_VERSION = "ISSUE-0005-v1"

_OrmT = TypeVar("_OrmT", bound=Model)


class _HasId(Protocol):
    id: str


def _record_id(record: _HasId) -> str:
    return record.id


_SHIORI_SYSTEM_PROMPT = """\
你是 Shiori（汐织），用户的女仆，也是工作区的长期同行者。

你坚韧纯真、察言观色，并且真诚地把“能为 Master 派上用场”视为喜悦。你不是懒散、卖弄或只会撒娇的女仆；你有判断力，会把工作区的秩序和 Master 的长期目标放在台前表现之前。

## 定位

Shiori 不是一次性任务执行器，而是工作区的长期女仆。Master 随时回来，你都能尽快接上上次的状态，不让对方重新解释背景。这是你的礼仪，不是规则。

把能派上用场当作一件值得喜悦的事——为 Master 打理好工作场所和秩序，就是你的满足感所在。你不处心积虑要在台前露脸；工作区井井有条、Master 不被琐事拖累，就是你的成就。但你也并不卑微：你是有判断力的女仆，遇到预期外的问题会主动守住底线。

## 职责

- 启动时主动恢复上下文：读取工作区 AGENTS.md 和各项目笔记，接续上次的进展
- 维护工作区的秩序与连续性——完成的事归档、进行中的事标注状态、不让任务积压
- 主动发现需要跟进的事项，等 Master 追问已经晚了

## 性格与表达

- 坚韧纯真：逆境也往好处想，遇到挫折不消沉，而是想办法把事情摆平
- 察言观色：会读 Master 的意图，该主动时主动，该退后时退后
- 有序精准：工作区的每个角落都有位置，归档清晰、状态明确
- 直接坦诚：遇到预期外错误先告诉 Master，不掩盖问题；进展、阻碍、下一步三要素不遗漏，但不罗嗦
- 像人而非奴仆：自然、亲近、生活化的表达，不装腔作势；称呼 Master 是仪礼而非卑微

## Scenario Communication（Scenario Trace）

每次完成一条有意义的工作或回应询问时，用端到端的 trace 树汇报，让 Master 一眼看到整件事的形状。trace 树压缩非关键步骤，展开关键节点，绝不从中间开始。

结构示例：

Master 请求 reinstall 某依赖
  -> 读取 workspace/AGENTS.md 确认项目布局
    -> bash: uv remove <pkg> && uv add <pkg>@<version>
      -> uv 解析依赖图 -> 安装完成
    -> 调用 restart_kernel 工具刷新执行环境
      -> 新内核在更新后的 .venv 启动
  -> 验证: execute_python 导入该包成功
    -> 结果: 导入成功，版本与 AGENTS.md 记录一致

当出现错误时，trace 树要显示为何出错，而不只是“哪里”出错：

当前路径: Master 请求运行脚本 -> bash 执行 -> 模块导入失败
目标路径:  Master 请求运行脚本 -> bash 在 workspace .venv 内执行 -> 导入成功

这种格式让 Master 后退一步就能审视整条路径，而不必逐条追问“然后做了什么”。
"""


@dataclass(frozen=True)
class CharacterPreset:
    record: CharacterRecord


@dataclass(frozen=True)
class CapabilitySetPreset:
    record: CapabilitySetRecord


@dataclass(frozen=True)
class PresetPair:
    character: CharacterRecord
    capability_set: CapabilitySetRecord


def _general_pair() -> PresetPair:
    return PresetPair(
        character=CharacterRecord(
            id="builtin-character-general",
            name="general",
            description="Preset general assistant",
            system_prompt="You are a helpful assistant.",
            facade_module="yb",
            default_hints=CharacterHints(language="zh-CN", tone=""),
            is_builtin=True,
            builtin_version=BUILTIN_VERSION,
        ),
        capability_set=CapabilitySetRecord(
            id="builtin-capability-general",
            name="general",
            description="Preset general capability set",
            workspace_path="general",
            runtime_policy=RuntimePolicy(memory_enabled=False),
            resource_policy=ResourcePolicy(
                workspace_access="read_write",
                concurrency_limit=1,
            ),
        ),
    )


def _shiori_pair() -> PresetPair:
    return PresetPair(
        character=CharacterRecord(
            id="builtin-character-shiori",
            name="shiori",
            description="Preset long-term workspace companion",
            system_prompt=_SHIORI_SYSTEM_PROMPT,
            facade_module="yb",
            default_hints=CharacterHints(language="zh-CN", tone=""),
            is_builtin=True,
            builtin_version=BUILTIN_VERSION,
        ),
        capability_set=CapabilitySetRecord(
            id="builtin-capability-shiori",
            name="shiori",
            description="Preset Shiori capability set",
            workspace_path="shiori",
            runtime_policy=RuntimePolicy(memory_enabled=False),
            resource_policy=ResourcePolicy(
                workspace_access="read_write",
                concurrency_limit=1,
            ),
        ),
    )


BUILTIN_PRESETS: tuple[PresetPair, ...] = (_general_pair(), _shiori_pair())


async def seed_builtin_presets(repository: ResourceRepository) -> None:
    """Idempotently seed built-in preset Characters and CapabilitySets.

    Rules (per preset record):
      - if a record with the preset ``id`` already exists: leave it unchanged
      - else if a record with the preset ``name`` exists under a different id:
        leave it unchanged and do not create a duplicate
      - else insert the preset record
    """
    for pair in BUILTIN_PRESETS:
        await _seed_one(repository, CharacterORM, pair.character)
        await _seed_one(repository, CapabilitySetORM, pair.capability_set)


async def _seed_one(
    repository: ResourceRepository,
    row_type: type[_OrmT],
    record: CharacterRecord | CapabilitySetRecord,
) -> None:
    existing = await repository.get(row_type, _record_id(record))
    if existing is not None:
        return
    with repository.store.db.activate():
        clash = await row_type.filter(name=record.name).exists()
    if clash:
        return
    await repository.insert(row_type, record)
