"""Runtime services shared by agent runs."""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yuuagents.context import DockerExecutor
    from yuuagents.daemon.docker import DockerManager

import yuutools as yt
from loguru import logger
from yuuagents import tools as agent_tools

from yuubot.capabilities.contract import ActionFilter
from yuubot.characters import CHARACTER_REGISTRY, get_character
from yuubot.config import Config
from yuubot.core import env
from yuubot.daemon.llm_factory import make_llm
from yuubot.prompt import RuntimeInfo, build_prompt_spec, build_system_prompt


def _set_or_pop(values: dict[str, str], key: str, value: str) -> None:
    if value:
        values[key] = value
    else:
        values.pop(key, None)


@lru_cache(maxsize=1)
def get_capability_tools() -> dict[str, Any]:
    """Load capability tools once."""
    from yuubot.capabilities.tools import call_cap_cli, read_cap_doc
    from yuubot.sandbox.tool import sandbox_python

    return {
        "call_cap_cli": call_cap_cli,
        "read_cap_doc": read_cap_doc,
        "sandbox_python": sandbox_python,
    }


DOCKER_TOOLS = {"execute_bash", "read_file", "edit_file", "delete_file"}


class AgentRuntime:
    """Owns prompt/env/tool construction and runtime initialization."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.initialized = False
        self.docker: DockerManager | None = None

    @property
    def docker_executor(self) -> DockerExecutor | None:
        """Return docker as DockerExecutor protocol for AgentContext injection."""
        return self.docker

    def build_tool_manager(self, tool_names: list[str]) -> Any:
        cap_tools = get_capability_tools()
        tool_manager = yt.ToolManager()

        builtin_names = [name for name in tool_names if name not in cap_tools]
        cap_names = [name for name in tool_names if name in cap_tools]

        for tool in agent_tools.get(builtin_names):
            tool_manager.register(tool)
        for name in cap_names:
            tool_manager.register(cap_tools[name])
        return tool_manager

    def build_capability_context(
        self,
        *,
        ctx_id: int | str = "",
        user_id: int | str = "",
        user_role: str = "",
        agent_name: str = "",
        task_id: str = "",
        bot_name: str = "",
        allowed_caps: frozenset[str] | None = None,
        action_filters: dict[str, ActionFilter] | None = None,
        docker_host_mount: str = "",
        docker_home_host_dir: str = "",
        docker_home_dir: str = "",
    ) -> Any:
        from yuubot.capabilities import CapabilityContext

        return CapabilityContext(
            config=self.config,
            ctx_id=int(ctx_id) if ctx_id else None,
            user_id=int(user_id) if user_id else None,
            user_role=user_role,
            agent_name=agent_name,
            task_id=task_id,
            bot_name=bot_name,
            allowed_caps=allowed_caps,
            action_filters=action_filters,
            docker_host_mount=docker_host_mount,
            docker_home_host_dir=docker_home_host_dir,
            docker_home_dir=docker_home_dir,
        )

    @staticmethod
    def new_task_id() -> str:
        return uuid.uuid4().hex

    def build_agent_env(
        self,
        *,
        task_id: str,
        ctx_id: int | str = "",
        user_id: int | str = "",
        user_role: str = "",
        agent_name: str = "",
        docker_mount: str = "",
        docker_home: str = "",
        docker_home_dir: str = "",
    ) -> dict[str, str]:
        import os

        values = {key: str(value) for key, value in os.environ.items()}
        values[env.TASK_ID] = task_id
        values[env.IN_BOT] = "1"
        _set_or_pop(values, env.BOT_CTX, str(ctx_id) if ctx_id else "")
        _set_or_pop(values, env.USER_ID, str(user_id) if user_id else "")
        _set_or_pop(values, env.USER_ROLE, user_role)
        _set_or_pop(values, env.AGENT_NAME, agent_name)
        _set_or_pop(values, env.DOCKER_HOST_MOUNT, docker_mount)
        _set_or_pop(values, env.DOCKER_HOME_HOST_DIR, docker_home)
        _set_or_pop(values, env.DOCKER_HOME_DIR, docker_home_dir)
        return values

    async def ensure_init(self) -> None:
        if self.initialized:
            return
        try:
            import json

            import msgspec
            from yuuagents.config import Config as YuuagentsConfig
            from yuuagents.init import setup

            from yuubot import config as yuubot_config

            base_data = json.loads(msgspec.json.encode(YuuagentsConfig()))
            merged_data = yuubot_config._deep_merge(base_data, self.config.yuuagents)
            cfg = msgspec.convert(merged_data, YuuagentsConfig)
            await setup(cfg)
            self.initialized = True

            if not self.any_agent_needs_docker():
                logger.info("No agent uses docker tools, skipping Docker initialization")
                return

            try:
                from yuuagents.daemon.docker import DockerManager

                self.docker = DockerManager(image=cfg.docker.image)
                await self.docker.start()
                logger.info("Docker initialized for AgentRunner")
            except Exception:
                logger.opt(exception=True).warning(
                    "Docker not available, execute_bash will not work",
                )
                self.docker = None
        except ImportError:
            logger.warning("yuuagents not installed, agent features disabled")
        except Exception:
            logger.exception("Failed to initialize yuuagents")

    async def stop(self) -> None:
        if self.docker is not None:
            await self.docker.stop()
            self.docker = None

    async def resolve_docker(self, task_id: str) -> tuple[str, str]:
        if self.docker is not None:
            container_id = await self.docker.resolve(task_id=task_id)
            return self.docker.workdir, container_id
        from pathlib import Path

        return str(Path.home()), ""

    async def docker_home_info(self, container_id: str) -> tuple[str, str, str]:
        if self.docker is None or not container_id:
            return "", "", ""

        docker_mount = "/mnt/host"
        container_home = self.docker.container_home
        host_home_dir = await self.docker.host_home_dir(container_id)
        return docker_mount, host_home_dir, container_home

    def get_runtime(self, agent_name: str) -> RuntimeInfo:
        char = CHARACTER_REGISTRY.get(agent_name)
        provider_name = char.provider if char and char.provider else ""
        model = char.model if char and char.model else ""

        if not provider_name or not model:
            agents = self.config.yuuagents.get("agents", {})
            agent_cfg = agents.get(agent_name, {})
            if not provider_name:
                provider_name = agent_cfg.get("provider", "")
            if not model:
                model = agent_cfg.get("model", "")

        providers = self.config.yuuagents.get("providers", {})
        provider_cfg = providers.get(provider_name, {})
        models_cfg = provider_cfg.get("models", {})
        model_cfg = models_cfg.get(model, {})
        return RuntimeInfo(
            provider=provider_name,
            model=model,
            supports_vision=bool(model_cfg.get("vision", False)),
        )

    def has_vision(self, agent_name: str) -> bool:
        return self.get_runtime(agent_name).supports_vision

    def build_prompt(self, agent_name: str) -> tuple[Any, Any]:
        char = get_character(agent_name)
        runtime = self.get_runtime(agent_name)
        prompt_spec = build_prompt_spec(char, runtime, self.config.skill_paths)
        system_prompt = build_system_prompt(prompt_spec)
        return prompt_spec, system_prompt

    @staticmethod
    def needs_docker(tools: list[str]) -> bool:
        return bool(DOCKER_TOOLS & set(tools))

    def any_agent_needs_docker(self) -> bool:
        for char in CHARACTER_REGISTRY.values():
            if DOCKER_TOOLS & set(char.spec.tools):
                return True
        return False

    def make_llm(self, agent_name: str = "main") -> Any:
        return make_llm(agent_name, self.config)
