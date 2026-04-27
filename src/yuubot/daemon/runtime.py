"""RFC2 yuuagents runtime factories for yuubot."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import time
import uuid

import attrs
import yuullm
import yuuagents as ya
from yuuagents.python_runtime import resolve_agent_runtime

from yuubot.characters import CHARACTER_REGISTRY
from yuubot.config import Config
from yuubot.core import env
from yuubot.core.types import InboundMessage
from yuubot.daemon.file_tools import EditFileTool, ReadChatFileTool, ReadFileTool
from yuubot.daemon.observability import YuubotBillingSink, YuubotRuntimeObserver
from yuubot.prompt import Character, render_system_prompt


@attrs.define(frozen=True)
class KernelTokenBinding:
    token: str
    agent_id: str
    bot_kind: str
    ctx_id: int
    group_id: int
    user_id: int
    conversation_id: str
    character_name: str
    task_id: str
    expires_at: float


_TOKEN_BINDINGS: dict[str, KernelTokenBinding] = {}
_TOKEN_TTL_S = 60 * 60 * 24


def issue_kernel_token(
    *,
    bot_kind: str,
    ctx_id: int,
    group_id: int,
    user_id: int,
    conversation_id: str,
    character_name: str,
    task_id: str,
) -> str:
    """Issue an opaque token binding kernel calls to a yuubot session."""

    token = f"rfc2.{ctx_id}.{character_name}.{task_id}.{uuid.uuid4().hex}"
    _TOKEN_BINDINGS[token] = KernelTokenBinding(
        token=token,
        agent_id="",
        bot_kind=bot_kind,
        ctx_id=ctx_id,
        group_id=group_id,
        user_id=user_id,
        conversation_id=conversation_id,
        character_name=character_name,
        task_id=task_id,
        expires_at=time.time() + _TOKEN_TTL_S,
    )
    _prune_kernel_tokens()
    return token


def resolve_kernel_token(token: str) -> KernelTokenBinding | None:
    binding = _TOKEN_BINDINGS.get(token)
    if binding is None:
        return None
    if binding.expires_at < time.time():
        _TOKEN_BINDINGS.pop(token, None)
        return None
    return binding


def revoke_kernel_token(token: str) -> None:
    _TOKEN_BINDINGS.pop(token, None)


def bind_kernel_agent_id(
    *,
    ctx_id: int,
    conversation_id: str,
    character_name: str,
    task_id: str,
    agent_id: str,
) -> None:
    for token, binding in list(_TOKEN_BINDINGS.items()):
        if (
            binding.ctx_id == ctx_id
            and binding.conversation_id == conversation_id
            and binding.character_name == character_name
            and binding.task_id == task_id
        ):
            _TOKEN_BINDINGS[token] = attrs.evolve(binding, agent_id=agent_id)


def _prune_kernel_tokens() -> None:
    now = time.time()
    for token, binding in list(_TOKEN_BINDINGS.items()):
        if binding.expires_at < now:
            _TOKEN_BINDINGS.pop(token, None)


def python_backend_for_bot_kind(bot_kind: str) -> str:
    if bot_kind == "master":
        return "kernel"
    return "restricted"


@attrs.define
class YuubotRuntimeFactory:
    config: Config
    runtime_observer: YuubotRuntimeObserver = attrs.field(factory=YuubotRuntimeObserver)
    billing_sink: YuubotBillingSink = attrs.field(factory=YuubotBillingSink)

    def create_engine(self) -> ya.Engine:
        return ya.Engine(
            tools=[ReadFileTool(), EditFileTool(), ReadChatFileTool()],
            python=ya.PythonRuntime(config=self._base_python_config()),
            observers=[
                ya.YuuTraceObserver(default_model="unknown", tags=("yuubot", "rfc2")),
                self.runtime_observer,
            ],
            billing=self.billing_sink,
        )

    def build_definition(
        self,
        character: Character,
        llm: yuullm.YLLMClient,
        *,
        bot_kind: str = "",
        supports_vision: bool = False,
    ) -> ya.AgentDefinition:
        delegates = self._delegate_descriptions(character)
        python_backend = python_backend_for_bot_kind(bot_kind) if bot_kind else ""
        resolved_runtime = resolve_agent_runtime(
            ya.PythonRuntime(config=self._base_python_config()),
            None,
            import_modules=character.spec.resolved_imports(),
            expand_functions=character.spec.expand_functions,
        )
        system_prompt = render_system_prompt(
            character,
            delegate_descriptions=delegates,
            python_backend=python_backend,
            python_runtime=resolved_runtime.python,
        )
        if not supports_vision:
            system_prompt += (
                "\n\n消息中 <img src=\"...\"> 标签所指向的图片，"
                "你无法直接查看。请用 execute_python 调用可用的 "
                "`yb.describe_image(path)` 或 `vision.describe_image(path)` "
                "获取图片的文字描述后再作回应。"
            )
        return ya.AgentDefinition(
            name=character.name,
            llm=llm,
            system_prompt=system_prompt,
            tools=character.spec.tools,
            import_modules=character.spec.resolved_imports(),
            expand_functions=(),  # docs are now in system prompt via ExpandFunctionsSection
        )

    def build_runtime(
        self,
        character: Character,
        message: InboundMessage,
        *,
        conversation_id: str,
        task_id: str,
        bot_kind: str,
        supports_vision: bool = False,
    ) -> ya.AgentRuntime:
        workspace_root = self._workspace_root(message.ctx_id)
        workspace_root.mkdir(parents=True, exist_ok=True)
        python_backend = python_backend_for_bot_kind(bot_kind)
        token = issue_kernel_token(
            bot_kind=bot_kind,
            ctx_id=message.ctx_id,
            group_id=message.group_id,
            user_id=message.sender.user_id,
            conversation_id=conversation_id,
            character_name=character.name,
            task_id=task_id,
        )
        state = {
            "bot_kind": bot_kind,
            "supports_vision": supports_vision,
            "ctx_id": message.ctx_id,
            "chat_type": message.chat_type,
            "group_id": message.group_id,
            "user_id": message.sender.user_id,
            "conversation_id": conversation_id,
            "agent_name": character.name,
            "character_name": character.name,
            "task_id": task_id,
            "bot_id": self.config.bot.qq,
            "workspace_root": str(workspace_root),
            "recorder_base_url": self.config.daemon.recorder_api,
            "daemon_base_url": f"http://{self.config.daemon.api.host}:{self.config.daemon.api.port}",
            "delegate_depth": 0,
            "token": token,
            "python_backend": python_backend,
        }
        return ya.AgentRuntime(
            python=ya.PythonRuntimeOverride(
                cwd=str(workspace_root),
                inherit_envs=self._python_inherit_envs(),
                env_allowlist=self._python_env_allowlist(),
                extra_envs={
                    "YUUBOT_RECORDER_URL": self.config.daemon.recorder_api,
                    "YUUBOT_DAEMON_URL": state["daemon_base_url"],
                    "YUUBOT_AGENT_TOKEN": token,
                    **self._python_extra_envs(),
                },
                sys_path=self._python_sys_path(),
                startup_code=character.spec.startup_code,
                state=state,
            )
        )

    def bind_agent_metadata(
        self,
        agent_id: str,
        *,
        message: InboundMessage,
        conversation_id: str,
        character_name: str = "",
        task_id: str,
    ) -> None:
        self.runtime_observer.bind_agent(
            agent_id,
            {
                "ctx_id": message.ctx_id,
                "conversation_id": conversation_id,
                "task_id": task_id,
                "user_id": message.sender.user_id,
            },
        )
        bind_kernel_agent_id(
            ctx_id=message.ctx_id,
            conversation_id=conversation_id,
            character_name=character_name,
            task_id=task_id,
            agent_id=agent_id,
        )

    def _delegate_descriptions(self, character: Character) -> list[tuple[str, str]]:
        policy = character.spec.delegate_policy
        if policy is None:
            return []
        result: list[tuple[str, str]] = []
        for name in policy.allowed_agents:
            candidate = CHARACTER_REGISTRY.get(name)
            if candidate is not None:
                result.append((name, candidate.description))
        return result

    def _base_python_config(self) -> ya.PythonKernelConfig:
        return ya.PythonKernelConfig(
            sys_path=self._python_sys_path(),
            inherit_envs=self._python_inherit_envs(),
            env_allowlist=self._python_env_allowlist(),
            extra_envs=self._python_extra_envs(),
        )

    def _python_config_raw(self) -> dict[str, Any]:
        raw = self.config.yuuagents.get("python", {})
        return raw if isinstance(raw, dict) else {}

    def _python_sys_path(self) -> tuple[str, ...]:
        raw = self._python_config_raw()
        configured = raw.get("sys_path", ())
        if isinstance(configured, str):
            configured_paths = (configured,)
        else:
            configured_paths = tuple(str(item) for item in configured or ())
        yuubot_src = str(Path(__file__).resolve().parents[2])
        return tuple(dict.fromkeys((yuubot_src, *configured_paths)))

    def _python_inherit_envs(self) -> bool:
        raw = self._python_config_raw()
        return bool(raw.get("inherit_envs", True))

    def _python_env_allowlist(self) -> tuple[str, ...] | None:
        raw = self._python_config_raw()
        value = raw.get("env_allowlist")
        if value is None:
            return None
        return tuple(str(item) for item in value)

    def _python_extra_envs(self) -> dict[str, str]:
        raw = self._python_config_raw()
        extra = raw.get("extra_envs", {})
        if not isinstance(extra, dict):
            return {}
        return {str(key): str(value) for key, value in extra.items()}

    def _workspace_root(self, ctx_id: int) -> Path:
        raw = (
            self.config.yuuagents.get("workspace_root")
            or os.environ.get(env.WORKSPACE_ROOT)
        )
        base = Path(str(raw)).expanduser() if raw else Path.home() / ".yuubot" / "workspaces"
        return base / f"ctx-{ctx_id}"
