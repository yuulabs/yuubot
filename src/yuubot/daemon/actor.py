"""YuubotActor + Stage/Definition factories — replaces mate/ layer + daemon/runtime.py.

Stage factories assemble yuuagents v0.2.0 providers from yuubot Config.
Definition factories translate old Character → new AgentDefinition.
YuubotActor(Actor) handles HumanMessage dispatch, agent lifecycle, context rollover, and IM reply.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from pathlib import Path
from typing import Any

import attrs
import yuullm
import yuuagents as ya
from loguru import logger

from yuubot.config import Config
from yuubot.core import env
from yuubot.prompt import Character, render_system_prompt
from yuubot.characters import get_character
from yuubot.daemon.hooks import ContextHook, ScheduleHook
from yuubot.daemon.gateway import OutboundMessage
from yuubot.daemon.providers import ReadChatFileProvider, RestrictedPythonProvider
from yuubot.model_resolution import build_llm_client, parse_effort_suffix

# ═══════════════════════════════════════════════════════════════════════════════
# HumanMessage — yuubot-specific MailMessage carrying session context
# ═══════════════════════════════════════════════════════════════════════════════


@attrs.define
class HumanMessage(ya.MailMessage):
    """IM-agnostic inbound message carrying session context for the Actor."""

    ctx_id: int = 0
    chat_type: str = ""       # channel-local type, e.g. "private" | "group" | "web"
    sender_id: int = 0
    character_name: str = ""  # target character (yuu/shiori/general/...)
    reply_target: str = ""    # IM adapter routing target
    workspace_root: str = ""  # session working directory
    group_id: int = 0
    supports_vision: bool = False
    bot_kind: str = ""        # "master" | "group"
    task_id: str = ""
    conversation_id: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# Kernel token management (module-level — imported by local_api.py)
# ═══════════════════════════════════════════════════════════════════════════════

_TOKEN_TTL = 3600.0  # 1 hour


@attrs.define
class KernelTokenBinding:
    """Bearer token bound to a specific kernel session / agent context."""

    token: str
    agent_id: str = ""
    bot_kind: str = ""
    ctx_id: int = 0
    group_id: int = 0
    user_id: int = 0
    conversation_id: str = ""
    character_name: str = ""
    task_id: str = ""
    expires_at: float = 0.0


_token_store: dict[str, KernelTokenBinding] = {}
_token_lock = asyncio.Lock()


_ROLLOVER_SUMMARY_PROMPT = """请基于截至目前的完整上下文，写一份用于移交给下一轮同一 agent 的中文摘要。

要求：
- 保留用户目标、约束、已确认事实、关键决策、工具执行结果、待办事项和风险。
- 不要编造上下文中没有的信息。
- 不要调用工具，只输出摘要文本。
- 用紧凑但具体的条目表达，方便后续 agent 直接继续工作。
"""


async def issue_kernel_token(
    bot_kind: str,
    ctx_id: int,
    *,
    user_id: int = 0,
    group_id: int = 0,
    conversation_id: str = "",
    character_name: str = "",
    task_id: str = "",
    agent_id: str = "",
) -> KernelTokenBinding:
    """Create and store a new kernel bearer token."""
    token = secrets.token_hex(32)
    binding = KernelTokenBinding(
        token=token, agent_id=agent_id, bot_kind=bot_kind,
        ctx_id=ctx_id, user_id=user_id, group_id=group_id,
        conversation_id=conversation_id, character_name=character_name,
        task_id=task_id, expires_at=time.time() + _TOKEN_TTL,
    )
    async with _token_lock:
        _token_store[token] = binding
    return binding


def resolve_kernel_token(token: str) -> KernelTokenBinding | None:
    """Look up a kernel token. Returns None if missing or expired."""
    binding = _token_store.get(token)
    if binding is None:
        return None
    if time.time() > binding.expires_at:
        _token_store.pop(token, None)
        return None
    return binding


async def revoke_kernel_token(token: str) -> None:
    async with _token_lock:
        _token_store.pop(token, None)


async def revoke_kernel_tokens_for_ctx(ctx_id: int) -> None:
    async with _token_lock:
        stale = [t for t, b in _token_store.items() if b.ctx_id == ctx_id]
        for token in stale:
            _token_store.pop(token, None)


def bind_kernel_agent_id(token: str, agent_id: str) -> None:
    binding = _token_store.get(token)
    if binding is not None:
        binding.agent_id = agent_id


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def python_backend_for_bot_kind(bot_kind: str) -> str:
    return "kernel" if bot_kind == "master" else "restricted"


def _workspace_root(config: Config, ctx_id: int) -> str:
    root = str(config.yuuagents.get("workspace_root", "") or env.get(env.WORKSPACE_ROOT, ""))
    base = Path(root).expanduser() if root else Path.home() / ".yuubot" / "workspaces"
    return str(base / f"ctx-{ctx_id}")


def _extract_llm_config(config: Config) -> dict[str, Any]:
    """Extract default LLM ref for Stage construction."""
    refs = config.agent_llm_refs
    if not refs:
        raise ValueError("No agent_llm_refs configured — cannot build Stage LLM")
    ref = refs.get("yuu") or refs.get("shiori") or next(iter(refs.values()))
    provider_name, _, model = ref.partition("/")
    return {"provider": provider_name, "model": model}


def _build_llm_options(config: Config, character_name: str) -> dict[str, Any]:
    """Build llm_options for an AgentDefinition's LlmConfig."""
    ref = config.agent_llm_ref(character_name)
    provider_name, _, model = ref.partition("/")
    model, effort = parse_effort_suffix(model)
    agents = config.yuuagents.get("agents", {})
    agent_cfg = agents.get(character_name, {}) if isinstance(agents, dict) else {}
    max_tokens = None
    if isinstance(agent_cfg, dict):
        mt = agent_cfg.get("max_tokens")
        if isinstance(mt, int):
            max_tokens = mt
    result: dict[str, Any] = {
        "provider": provider_name,
        "model": model,
        "max_tokens": max_tokens,
    }
    if effort:
        result["reasoning_effort"] = effort
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Stage factories
# ═══════════════════════════════════════════════════════════════════════════════


def build_master_stage(config: Config) -> ya.Stage:
    """Stage with ipykernel, bash, fileop, schedule, background, and read_chat_file."""
    return _build_stage(config, "master")


def build_group_stage(config: Config) -> ya.Stage:
    """Stage with ipykernel + read_chat_file only (security boundary)."""
    return _build_stage(config, "group")


def _build_stage(config: Config, bot_kind: str) -> ya.Stage:
    llm_cfg = _extract_llm_config(config)
    python_config = _python_kernel_config(config, ctx_id=0)

    provider_configs: dict[str, Any] = {
        "ipykernel": (
            RestrictedPythonProvider.create(python_config)
            if bot_kind == "group"
            else {"config": python_config}
        ),
    }

    if bot_kind == "master":
        provider_configs.update({
            "bash": {},
            "fileop": {},
            "schedule": {"db_path": config.database.path or ":memory:"},
            "background": {},
        })

    llm = build_llm_client(llm_cfg["provider"], llm_cfg["model"], config)
    stage = ya.Stage.from_config({"providers": provider_configs, "llm": llm})
    stage.providers["read_chat_file"] = ReadChatFileProvider()
    return stage


_YUUBOT_SESSION_BOOTSTRAP = """
class _YuubotSessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

SESSION_STATE = _YuubotSessionState(SESSION_STATE)
def get_session_state():
    return SESSION_STATE

import builtins as _yuubot_builtins
_yuubot_builtins.get_session_state = get_session_state
"""


def _python_kernel_config(config: Config, ctx_id: int) -> ya.PythonKernelConfig:
    raw = config.yuuagents.get("python", {})
    data = dict(raw) if isinstance(raw, dict) else {}
    extra_envs = {str(k): str(v) for k, v in dict(data.get("extra_envs") or {}).items()}
    extra_envs.setdefault("YUUBOT_DB_PATH", config.database.path)
    extra_envs.setdefault("YUUBOT_DB_SIMPLE_EXT", config.database.simple_ext)
    extra_envs.setdefault("YUUBOT_RECORDER_URL", config.daemon.recorder_api)
    extra_envs.setdefault("YUUBOT_DAEMON_URL", f"http://{config.daemon.api.host}:{config.daemon.api.port}")
    startup_code = "\n".join(
        part for part in (_YUUBOT_SESSION_BOOTSTRAP, str(data.get("startup_code", ""))) if part.strip()
    )
    cwd = str(Path(str(data.get("cwd") or _workspace_root(config, ctx_id))).expanduser())
    Path(cwd).mkdir(parents=True, exist_ok=True)
    return ya.PythonKernelConfig(
        python=data.get("python"),
        cwd=cwd,
        inherit_envs=bool(data.get("inherit_envs", True)),
        env_allowlist=tuple(data["env_allowlist"]) if data.get("env_allowlist") is not None else None,
        extra_envs=extra_envs,
        sys_path=tuple(str(item) for item in data.get("sys_path", ())),
        startup_code=startup_code,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AgentDefinition factories
# ═══════════════════════════════════════════════════════════════════════════════


def build_master_definition(character: Character, llm_options: dict[str, Any]) -> ya.AgentDefinition:
    """Build AgentDefinition with full master capabilities."""
    return build_definition(character, llm_options, bot_kind="master")


def build_group_definition(character: Character, llm_options: dict[str, Any]) -> ya.AgentDefinition:
    """Build AgentDefinition with ipykernel + read_chat_file only."""
    return build_definition(character, llm_options, bot_kind="group")


def build_definition(
    character: Character,
    llm_options: dict[str, Any],
    bot_kind: str,
    *,
    session_state: dict[str, Any] | None = None,
    python_config: ya.PythonKernelConfig | None = None,
) -> ya.AgentDefinition:
    """Translate a yuubot Character into a yuuagents AgentDefinition.

    Capabilities derived from character.spec.tools:
    - "execute_python" → ipykernel (with imports, expand_functions)
    - "read_file"/"edit_file" → fileop (master only)
    - "read_chat_file" → always included
    - bash, schedule, background included for all master agents
    """
    tools = set(character.spec.tools)
    capabilities: dict[str, dict[str, Any]] = {}
    session_state = dict(session_state or {})
    python_config = python_config or ya.PythonKernelConfig()

    if "execute_python" in tools:
        imports = character.spec.resolved_imports()
        capabilities["ipykernel"] = {
            "imports": [
                {"module": imp.module, "alias": imp.alias} if imp.alias else imp.module
                for imp in imports
            ],
            "expand_functions": list(character.spec.expand_functions),
            "state": session_state,
            "config": python_config,
        }

    if bot_kind == "master":
        capabilities.update({"bash": {}, "schedule": {}, "background": {}})
        if "read_file" in tools or "edit_file" in tools:
            capabilities["fileop"] = {}

    if "read_chat_file" in tools:
        capabilities["read_chat_file"] = {"ctx_id": int(session_state.get("ctx_id", 0))}

    budget = ya.BudgetConfig(
        max_steps=character.spec.max_turns or 0,
        max_tokens=character.spec.max_context_tokens or 0,
    )

    stream_options = {
        str(key): value
        for key, value in llm_options.items()
        if key not in {"provider", "model", "max_tokens"} and value is not None
    }
    llm = ya.LlmConfig(
        provider=str(llm_options.get("provider", "")),
        model=str(llm_options.get("model", "")),
        max_tokens=(
            int(llm_options["max_tokens"])
            if isinstance(llm_options.get("max_tokens"), int)
            else None
        ),
        stream_options=stream_options,
    )

    python_backend = python_backend_for_bot_kind(bot_kind) if "execute_python" in tools else ""
    python_runtime = None
    if "execute_python" in tools:
        from yuuagents.python_runtime import PythonRuntime, _resolve_python

        python_runtime = _resolve_python(
            PythonRuntime(
                config=python_config,
                imports=character.spec.resolved_imports(),
                state=ya.JsonSessionState(session_state),
                expand_functions=character.spec.expand_functions,
            ),
            default_doc_mode="summary",
        )
    system_prompt = render_system_prompt(
        character,
        python_backend=python_backend,
        python_runtime=python_runtime,
    )

    prompt_providers: dict[str, dict[str, Any]] = {}
    for name in capabilities:
        prompt_providers[name] = {"level": "type-only" if name == "ipykernel" else "detail"}

    return ya.AgentDefinition(
        name=character.name,
        llm=llm,
        budget=budget,
        capabilities=capabilities,
        prompts=ya.PromptDefinition(system=system_prompt, providers=prompt_providers),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# YuubotActor — IM-aware Actor with context tracking and rollover
# ═══════════════════════════════════════════════════════════════════════════════


class YuubotActor(ya.Actor):
    """Actor that handles HumanMessage, manages agent-ctx mapping, and sends IM replies."""

    def __init__(
        self,
        stage: ya.Stage,
        *,
        bot_kind: str,
        config: Config,
        im_sender: Any = None,
    ) -> None:
        super().__init__(stage)
        self.bot_kind: str = bot_kind
        self.config: Config = config
        self.im_sender = im_sender  # async callable(ctx_id, OutboundMessage) → None
        self._agent_ctx: dict[str, dict[str, Any]] = {}          # agent_id → context info
        self._agent_for_key: dict[tuple[int, str], str] = {}     # (ctx_id, char_name) → agent_id
        self._ctx_hook = ContextHook()
        self._sched_hook = ScheduleHook(db_path=config.database.path or ":memory:")
        stage.runtime.hooks.extend([self._ctx_hook, self._sched_hook])

    # ── Agent lifecycle ───────────────────────────────────────────────────

    def create_agent(self, definition: ya.AgentDefinition) -> ya.Agent:
        """Create an Agent — ToolHook context is pre-registered on Runtime via Stage."""
        agent = super().create_agent(definition)
        return agent

    # ── Message dispatch ───────────────────────────────────────────────────

    async def handle_message(self, message: ya.MailMessage) -> ya.Agent | None:
        """Dispatch HumanMessage and ScheduleTriggerMessage; fall back to parent for rest."""
        if isinstance(message, HumanMessage):
            return await self._handle_human_message(message)
        if isinstance(message, ya.ScheduleTriggerMessage):
            return await self._handle_schedule_trigger(message)
        return await super().handle_message(message)

    async def _handle_human_message(self, msg: HumanMessage) -> ya.Agent | None:
        key = (msg.ctx_id, msg.character_name)
        agent_id = self._agent_for_key.get(key)
        agent = self.agents.get(agent_id) if agent_id else None

        if agent is None:
            agent = await self._create_agent_for_message(msg)
            if agent is None:
                return None
            self._agent_for_key[key] = agent.agent_id

        if msg.content is not None:
            agent.append_message(msg.content)

        await self._run_agent_serialized(agent)

        # Send response via IM
        response_text = _extract_last_text(agent)
        if response_text and self.im_sender:
            try:
                await self.im_sender(
                    msg.ctx_id,
                    OutboundMessage(text=response_text, reply_to=msg.conversation_id or msg.reply_target),
                )
            except Exception:
                logger.exception("Failed to send IM reply for agent {}", agent.agent_name)

        if not self._agent_ctx.get(agent.agent_id, {}).get("preserve_python_session", True):
            await self.expire_agent(agent)

        return agent

    async def _create_agent_for_message(self, msg: HumanMessage) -> ya.Agent | None:
        """Create a new agent from character definition for the inbound message."""
        try:
            character = get_character(msg.character_name)
        except KeyError:
            return None
        if not character.supports_bot_kind(msg.bot_kind):
            return None

        llm_options = _build_llm_options(self.config, msg.character_name)
        token = await issue_kernel_token(
            msg.bot_kind,
            msg.ctx_id,
            user_id=msg.sender_id,
            group_id=msg.group_id,
            conversation_id=msg.conversation_id,
            character_name=msg.character_name,
            task_id=msg.task_id,
        )
        ctx_info = {
            "ctx_id": msg.ctx_id, "chat_type": msg.chat_type,
            "character_name": msg.character_name, "bot_kind": msg.bot_kind,
            "group_id": msg.group_id, "sender_id": msg.sender_id,
            "workspace_root": msg.workspace_root, "conversation_id": msg.conversation_id,
            "task_id": msg.task_id, "reply_target": msg.reply_target,
            "token": token.token,
            "supports_vision": msg.supports_vision,
            "preserve_python_session": character.spec.preserve_python_session,
        }
        definition = build_definition(
            character,
            llm_options,
            msg.bot_kind,
            session_state=self._session_state(ctx_info),
            python_config=_python_kernel_config(self.config, msg.ctx_id),
        )
        agent = self.create_agent(definition)
        bind_kernel_agent_id(token.token, agent.agent_id)
        ctx_info["agent_id"] = agent.agent_id
        self._agent_ctx[agent.agent_id] = ctx_info
        if self._ctx_hook is not None:
            self._ctx_hook.bind_agent(agent.agent_id, ctx_info)
        if self._sched_hook is not None:
            self._sched_hook.bind_agent(agent.agent_id, ctx_info)
        return agent

    async def _handle_schedule_trigger(self, msg: ya.ScheduleTriggerMessage) -> ya.Agent | None:
        """Handle a schedule trigger: look up ctx from job_id, route to right agent."""
        if self._sched_hook is None:
            return await super().handle_message(msg)

        ctx = await self._sched_hook.lookup_ctx(msg.job_id)
        if ctx is None:
            # Fall back to parent: route by agent_name only (no ctx context)
            return await super().handle_message(msg)

        ctx_id = int(ctx.get("ctx_id", 0))
        char_name = str(ctx.get("character_name") or msg.agent_name or "")
        key = (ctx_id, char_name)
        agent_id = self._agent_for_key.get(key)
        agent = self.agents.get(agent_id) if agent_id else None

        if agent is None:
            # Create a new agent via a synthetic HumanMessage
            from yuubot.daemon.actor import HumanMessage as _HM
            synthetic = _HM(
                ctx_id=ctx_id,
                character_name=char_name,
                bot_kind=str(ctx.get("bot_kind", self.bot_kind)),
                chat_type=str(ctx.get("chat_type", "private")),
                reply_target=str(ctx.get("reply_target", "")),
                workspace_root=str(ctx.get("workspace_root", _workspace_root(self.config, ctx_id))),
                content=msg.content,
            )
            agent = await self._create_agent_for_message(synthetic)
            if agent is None:
                return None
            self._agent_for_key[key] = agent.agent_id
        elif msg.content is not None:
            agent.append_message(msg.content)

        await self._run_agent_serialized(agent)

        # Send any response via IM
        response_text = _extract_last_text(agent)
        if response_text and self.im_sender:
            try:
                await self.im_sender(
                    ctx_id,
                    OutboundMessage(
                        text=response_text,
                        reply_to=str(ctx.get("conversation_id", "") or ctx.get("reply_target", "")),
                    ),
                )
            except Exception:
                logger.exception("Failed to send IM reply for schedule trigger agent {}", agent.agent_name)

        if not self._agent_ctx.get(agent.agent_id, {}).get("preserve_python_session", True):
            await self.expire_agent(agent)

        return agent

    # ── Agent loop with context rollover ───────────────────────────────────

    async def run_agent_loop(self, agent: ya.Agent) -> None:
        """Override to add context-rollover (summarize + replace_history) when budget exceeded."""
        while not agent.done():
            if agent.budget.is_exceeded():
                await self._handle_budget_exceeded(agent)
                if agent.budget.is_exceeded():
                    break
            await agent.call_llm()
            await agent.call_tools()

    def _session_state(self, ctx: dict[str, Any]) -> dict[str, Any]:
        daemon_base = f"http://{self.config.daemon.api.host}:{self.config.daemon.api.port}"
        return {
            "bot_kind": ctx.get("bot_kind", self.bot_kind),
            "ctx_id": int(ctx.get("ctx_id", 0)),
            "chat_type": str(ctx.get("chat_type", "")),
            "group_id": int(ctx.get("group_id", 0) or 0),
            "user_id": int(ctx.get("sender_id", 0) or 0),
            "conversation_id": str(ctx.get("conversation_id", "")),
            "agent_name": str(ctx.get("character_name", "")),
            "character_name": str(ctx.get("character_name", "")),
            "agent_id": str(ctx.get("agent_id", "")),
            "task_id": str(ctx.get("task_id", "")),
            "bot_id": self.config.bot.qq,
            "bot_name": "yuubot",
            "workspace_root": str(ctx.get("workspace_root", "")),
            "database_path": self.config.database.path,
            "database_simple_ext": self.config.database.simple_ext,
            "recorder_base_url": self.config.daemon.recorder_api,
            "napcat_http_base_url": self.config.recorder.napcat_http,
            "daemon_base_url": daemon_base,
            "daemon_self_url": self.config.daemon.self_url or daemon_base,
            "delegate_depth": 0,
            "token": str(ctx.get("token", "")),
            "python_backend": python_backend_for_bot_kind(str(ctx.get("bot_kind", self.bot_kind))),
            "supports_vision": bool(ctx.get("supports_vision", False)),
        }

    async def _handle_budget_exceeded(self, agent: ya.Agent) -> None:
        """Summarize history, replace, and reset step counter."""
        ctx = self._agent_ctx.get(agent.agent_id, {})
        pending_messages = _pending_messages(agent)
        try:
            summary = await self._summarize_history(agent, ctx)
        except Exception:
            return

        system_msg = agent.history[0] if agent.history and agent.history[0].role == "system" else None
        history_without_system = agent.history[1:] if system_msg else agent.history
        recent = history_without_system[-8:]

        new_history: list[yuullm.Message] = []
        if system_msg:
            new_history.append(system_msg)
        if summary:
            new_history.append(yuullm.user(f"[上下文摘要]\n{summary}"))
        new_history.extend(recent)
        new_history.extend(pending_messages)

        agent.replace_history(new_history)
        agent.budget.reset_steps()

        await self.stage.eventbus.emit("actor.rollover", {
            "agent_id": agent.agent_id, "agent_name": agent.agent_name,
            "ctx_id": ctx.get("ctx_id"),
        })

    async def _summarize_history(self, agent: ya.Agent, ctx: dict[str, Any]) -> str | None:
        """Ask a temporary clone of the current agent to prepare a handoff summary."""
        history = _history_with_pending(agent)
        if len(history) < 4:
            return None
        temp_agent_id = f"{agent.agent_id}_rollover_summary"
        temp_agent_name = f"{agent.agent_name}:rollover"
        try:
            await self.stage.eventbus.emit("actor.rollover_summary_started", {
                "agent_id": temp_agent_id,
                "agent_name": temp_agent_name,
                "source_agent_id": agent.agent_id,
                "source_agent_name": agent.agent_name,
                "ctx_id": ctx.get("ctx_id"),
                "conversation_id": ctx.get("conversation_id", ""),
                "task_id": ctx.get("task_id", ""),
            })
            temp_agent = ya.Agent(
                agent_id=temp_agent_id,
                agent_name=temp_agent_name,
                history=history,
                budget=ya.Budget(limits=dict(agent.budget.limits)),
                tool_specs=agent.tool_specs,
                runtime=self.stage.runtime,
                eventbus=self.stage.eventbus,
                llm=_rollover_llm(agent, temp_agent_id, temp_agent_name, ctx),
                llm_options=agent.llm_options,
            )
            temp_agent.append_message(yuullm.user(_ROLLOVER_SUMMARY_PROMPT))
            await temp_agent.call_llm()
            summary = _extract_last_text(temp_agent)
            await self.stage.eventbus.emit("actor.rollover_summary_finished", {
                "agent_id": temp_agent_id,
                "agent_name": temp_agent_name,
                "source_agent_id": agent.agent_id,
                "source_agent_name": agent.agent_name,
                "ctx_id": ctx.get("ctx_id"),
                "conversation_id": ctx.get("conversation_id", ""),
                "task_id": ctx.get("task_id", ""),
                "has_summary": bool(summary),
            })
            return summary
        except Exception:
            await self.stage.eventbus.emit("actor.rollover_summary_failed", {
                "agent_id": temp_agent_id,
                "agent_name": temp_agent_name,
                "source_agent_id": agent.agent_id,
                "source_agent_name": agent.agent_name,
                "ctx_id": ctx.get("ctx_id"),
                "conversation_id": ctx.get("conversation_id", ""),
                "task_id": ctx.get("task_id", ""),
            })
            return None

    # ── Cleanup ────────────────────────────────────────────────────────────

    async def expire_agent(self, agent: ya.Agent) -> None:
        ctx = self._agent_ctx.pop(agent.agent_id, {})
        ctx_id = ctx.get("ctx_id")
        char_name = ctx.get("character_name")
        if ctx_id is not None and char_name:
            self._agent_for_key.pop((ctx_id, char_name), None)
        if self._ctx_hook is not None:
            self._ctx_hook.unbind_agent(agent.agent_id)
        if self._sched_hook is not None:
            self._sched_hook.unbind_agent(agent.agent_id)
        await super().expire_agent(agent)

    async def close(self) -> None:
        for agent in list(self.agents.values()):
            try:
                await self.expire_agent(agent)
            except Exception:
                pass
        await super().close()
        for provider in self.stage.providers.values():
            close = getattr(provider, "aclose", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_last_text(agent: ya.Agent) -> str | None:
    """Extract text from the last assistant message in agent history."""
    for msg in reversed(agent.history):
        if msg.role == "assistant":
            chunks = [
                str(item.get("text", ""))
                for item in msg.content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "".join(chunks).strip()
            if text:
                return text
    return None


def _pending_messages(agent: ya.Agent) -> list[yuullm.Message]:
    pending = getattr(agent, "_pending_messages", ())
    return list(pending)


def _history_with_pending(agent: ya.Agent) -> list[yuullm.Message]:
    history = list(agent.history)
    history.extend(_pending_messages(agent))
    return history


def _rollover_llm(
    agent: ya.Agent,
    temp_agent_id: str,
    temp_agent_name: str,
    ctx: dict[str, Any],
) -> Any:
    llm = agent.llm
    try:
        from yuubot.daemon.llm_trace import LLMTraceContext, TracedLLMClient
    except Exception:
        return llm
    if not isinstance(llm, TracedLLMClient):
        return llm
    return TracedLLMClient(
        client=llm.client,
        trace=LLMTraceContext(
            ctx_id=_optional_int(ctx.get("ctx_id")),
            runtime_id=temp_agent_id,
            task_id=_rollover_task_id(ctx, temp_agent_id),
            agent_name=temp_agent_name,
        ),
    )


def _rollover_task_id(ctx: dict[str, Any], temp_agent_id: str) -> str:
    task_id = str(ctx.get("task_id", "") or "")
    return f"{task_id}:rollover" if task_id else temp_agent_id


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
