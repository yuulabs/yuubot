"""Behavior-oriented test helpers."""

from __future__ import annotations

import asyncio
import json
import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import yuullm
from starlette.types import ASGIApp
from yuuagents import ProviderPoolSessionFactory

from yuubot.core.actors import Actor
from yuubot.core.assembly._constants import _resolve_yuuagents_provider
from yuubot.core.bindings import ActorBinding, AgentBinding
from yuubot.core.gateway import Mailbox
from yuubot.core.integrations.impls.echo import (
    ECHO_CAPABILITY_ID,
    ECHO_INTEGRATION_NAME,
    ECHO_REPLY_CAPABILITY_ID,
)
from yuubot.process import ServiceHost
from yuubot.resources.events import ResourceChanged
from yuubot.resources.root import Resources
from yuubot.runtime.daemon import DaemonInfrastructure
from yuubot.resources.records import (
    ActorIngressRuleRecord,
    ActorRecord,
    BudgetPolicy,
    CapabilitySetRecord,
    CharacterHints,
    CharacterRecord,
    IntegrationRecord,
    LLMBackendRecord,
    ModelCapabilities,
    ModelCatalog,
    PricingTable,
    ResourcePolicy,
    RuntimePolicy,
    ToolConfig,
    YuuAgentBudget,
    YuuAgentLLMOptions,
)
from yuubot.resources.repository import ResourceRepository
from yuubot.resources.store.models import (
    ActorIngressRuleORM,
    ActorORM,
    CapabilitySetORM,
    CharacterORM,
    IntegrationORM,
    LLMBackendORM,
)


def build_im_send_argv(
    config_path: str,
    *,
    text: str,
    uid: int | None = None,
    gid: int | None = None,
) -> str:
    del config_path
    message = json.dumps([{"type": "text", "text": text}], ensure_ascii=False)
    parts = ["ybot", "im", "send"]
    if uid is not None:
        parts.extend(["--uid", str(uid)])
    if gid is not None:
        parts.extend(["--gid", str(gid)])
    command = " ".join(parts) + " -- " + shlex.quote(message)
    return json.dumps({"command": command}, ensure_ascii=False)


def sent_texts(sent: list[dict]) -> list[str]:
    """Extract text segments from captured recorder_api send_msg bodies."""
    texts: list[str] = []
    for body in sent:
        for seg in body.get("message", []):
            if seg.get("type") == "text":
                texts.append(seg.get("data", {}).get("text", ""))
    return texts


def llm_system_prompt(calls: list) -> str:
    """Extract concatenated system role text from the first LLM call."""
    if not calls:
        return ""
    for msg in calls[0].get("messages", []):
        if msg.get("role") == "system":
            content = msg.get("content", [])
            return "\n".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
    return ""


def llm_user_texts(calls: list) -> list[str]:
    """Extract all user-role text from the first LLM call."""
    if not calls:
        return []
    texts: list[str] = []
    for msg in calls[0].get("messages", []):
        if msg.get("role") == "user":
            content = msg.get("content", [])
            texts.append(
                "\n".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            )
    return texts


def history_text(history: list) -> str:
    return "\n".join(str(item) for item in history)


class ScriptedLlmProvider(Protocol):
    async def stream(
        self,
        history: yuullm.History,
        *,
        model: str,
        **options: Any,
    ) -> yuullm.StreamResult: ...


_TEST_LLM_FACTORIES: dict[str, ProviderPoolSessionFactory] = {}


def register_test_llm_provider(name: str, llm: ScriptedLlmProvider) -> None:
    _TEST_LLM_FACTORIES[name] = ScriptedProviderSessionFactory(provider=llm)


def make_test_daemon_infrastructure() -> DaemonInfrastructure:
    return DaemonInfrastructure(
        llm_session_factory_factory=test_llm_session_factory,
    )


def test_llm_session_factory(binding: AgentBinding) -> ProviderPoolSessionFactory | None:
    provider = _resolve_yuuagents_provider(binding.llm.backend.yuuagents_provider)
    return _TEST_LLM_FACTORIES.get(provider)


@dataclass
class ScriptedProviderSessionFactory:
    provider: ScriptedLlmProvider
    selector: str = ""

    def create_session(self, history: yuullm.History) -> yuullm.YuuSession:
        if not self.selector:
            raise ValueError("test LLM session requires a selector")
        return ScriptedProviderSession(
            provider=self.provider,
            selector=self.selector,
            messages=list(history),
        )

    def with_selector(self, selector: str) -> "ScriptedProviderSessionFactory":
        return ScriptedProviderSessionFactory(
            provider=self.provider,
            selector=selector,
        )


@dataclass
class ScriptedProviderSession:
    provider: ScriptedLlmProvider
    selector: str
    messages: yuullm.History

    @property
    def history(self) -> yuullm.History:
        return self.messages

    def append(self, msg: yuullm.Message) -> None:
        self.messages.append(msg)

    async def stream(
        self,
        **options: Any,
    ) -> yuullm.StreamResult:
        options = dict(options)
        options.pop("model", None)
        stream, store = await self.provider.stream(
            self.messages,
            model=self.selector,
            **options,
        )
        return self._commit_assistant_message(stream), store

    async def _commit_assistant_message(
        self,
        stream: AsyncIterator[yuullm.StreamItem],
    ) -> AsyncIterator[yuullm.StreamItem]:
        content: yuullm.MessageContent = []
        async for item in stream:
            yield item
            _accumulate_stream_item(content, item)
        if content:
            self.messages.append(yuullm.Message(role="assistant", content=content))


def _accumulate_stream_item(
    content: yuullm.MessageContent,
    item: yuullm.StreamItem,
) -> None:
    match item:
        case yuullm.Response(item=response):
            content.append(response)
        case yuullm.ToolCall() as tool_call:
            content.append(yuullm.tool_call_item(tool_call))
        case yuullm.ThinkingBlock() as thinking:
            content.append(thinking.to_message_item())
        case yuullm.AttemptRecovery():
            content.clear()
        case yuullm.Reasoning() | yuullm.Tick():
            pass


async def wait_worker(dispatcher, key: str, timeout: float = 5.0) -> None:
    worker = dispatcher._workers.get(key)
    if worker:
        await asyncio.wait_for(worker.queue.join(), timeout=timeout)


@dataclass
class EchoActorResources:
    integration: IntegrationRecord
    character: CharacterRecord
    llm_backend: LLMBackendRecord
    actor: ActorRecord
    ingress_rule: ActorIngressRuleRecord


async def insert_echo_actor_resources(
    repository: ResourceRepository,
    *,
    actor_id: str = "test-actor",
    integration_id: str = "echo-main",
    source_path: str = "channels/test",
    system_prompt: str = "You are a test actor.",
    actor_type: str = "simple_loop",
    max_steps: int = 4,
) -> EchoActorResources:
    """Insert a routable actor wired to an Echo integration."""

    character = await repository.insert(
        CharacterORM,
        make_character_record(actor_id, system_prompt=system_prompt),
    )
    llm_backend = await repository.insert(
        LLMBackendORM, make_llm_backend_record(actor_id)
    )
    integration = await repository.insert(
        IntegrationORM,
        make_echo_integration_record(integration_id, source_path),
    )
    capability_set = await repository.insert(
        CapabilitySetORM,
        make_capability_set_record(actor_id),
    )
    actor = await repository.insert(
        ActorORM,
        make_actor_record(
            actor_id,
            character=character,
            llm_backend=llm_backend,
            capability_set=capability_set,
            actor_type=actor_type,
            max_steps=max_steps,
        ),
    )
    ingress_rule = await repository.insert(
        ActorIngressRuleORM,
        make_actor_ingress_rule_record(
            integration_id=integration.id,
            source_path=source_path,
            actor_id=actor.id,
        ),
    )
    return EchoActorResources(
        integration=integration,
        character=character,
        llm_backend=llm_backend,
        actor=actor,
        ingress_rule=ingress_rule,
    )


def make_echo_integration_record(
    integration_id: str,
    source_path: str,
) -> IntegrationRecord:
    return IntegrationRecord(
        id=integration_id,
        name=ECHO_INTEGRATION_NAME,
        config={"source_path": source_path},
    )


def make_actor_ingress_rule_record(
    *,
    integration_id: str,
    source_path: str,
    actor_id: str,
) -> ActorIngressRuleRecord:
    return ActorIngressRuleRecord(
        id=f"{integration_id}:{source_path}:{actor_id}",
        actor_id=actor_id,
        source_id_pattern=integration_id,
        source_path_pattern=source_path,
    )


def make_character_record(
    actor_id: str,
    *,
    system_prompt: str = "You are a test actor.",
) -> CharacterRecord:
    character_id = f"{actor_id}-char"
    return CharacterRecord(
        id=character_id,
        name=character_id,
        description="",
        system_prompt=system_prompt,
        facade_module="yuubot.core.facade",
        default_hints=CharacterHints(),
    )


def make_llm_backend_record(
    actor_id: str,
    *,
    provider: str = "openai",
    model: str = "gpt-4",
) -> LLMBackendRecord:
    backend_id = f"{actor_id}-backend"
    return LLMBackendRecord(
        id=backend_id,
        name=backend_id,
        yuuagents_provider=provider,
        default_model=model,
        model_capabilities=ModelCapabilities(tool_calling=True),
        models=ModelCatalog(),
        pricing=PricingTable(),
        budget=BudgetPolicy(),
    )


def make_capability_set_record(
    actor_id: str,
    *,
    integration_capability_ids: tuple[str, ...] = (
        ECHO_CAPABILITY_ID,
        ECHO_REPLY_CAPABILITY_ID,
    ),
    agent_tools: tuple[ToolConfig, ...] = (),
    runtime_policy: RuntimePolicy | None = None,
    resource_policy: ResourcePolicy | None = None,
) -> CapabilitySetRecord:
    return CapabilitySetRecord(
        id=f"{actor_id}-capabilities",
        name=f"{actor_id}-capabilities",
        integration_capability_ids=integration_capability_ids,
        agent_tools=agent_tools,
        runtime_policy=runtime_policy or RuntimePolicy(),
        resource_policy=resource_policy
        or ResourcePolicy(workspace_access="read_write"),
    )


def make_actor_record(
    actor_id: str,
    *,
    character: CharacterRecord,
    llm_backend: LLMBackendRecord,
    capability_set: CapabilitySetRecord | None = None,
    actor_type: str = "simple_loop",
    max_steps: int = 4,
) -> ActorRecord:
    cap_set = capability_set or make_capability_set_record(actor_id)
    return ActorRecord(
        id=actor_id,
        name=actor_id,
        type=actor_type,
        default_character=character,
        capability_set=cap_set,
        default_llm_backend=llm_backend,
        default_model="",
        default_llm_options=YuuAgentLLMOptions(),
        default_budget=YuuAgentBudget(max_steps=max_steps),
    )


# ---------------------------------------------------------------------------
# Trace verification helpers
# ---------------------------------------------------------------------------


from yuutrace._typing import ConversationRecord, EventRecord, SpanRecord  # noqa: E402
from yuutrace.memory import MemoryTraceStore  # noqa: E402
from yuutrace.otel import (  # noqa: E402
    ATTR_COST_AMOUNT,
    ATTR_COST_CATEGORY,
    ATTR_LLM_MODEL,
    ATTR_LLM_PROVIDER,
    ATTR_LLM_USAGE_INPUT_TOKENS,
    ATTR_LLM_USAGE_OUTPUT_TOKENS,
    ATTR_TOOL_CALL_ID,
    ATTR_TOOL_INPUT,
    ATTR_TOOL_NAME,
    ATTR_TOOL_OUTPUT,
    EVENT_COST,
    EVENT_LLM_USAGE,
    EVENT_TOOL_USAGE,
)


def fetch_trace_conversation(store: MemoryTraceStore) -> ConversationRecord:
    """Fetch the single conversation trace from an in-memory trace store.

    Asserts exactly one conversation exists, then returns its full record
    (spans + events) for further inspection.
    """
    result = store.list_conversations()
    assert result["total"] == 1, (
        f"expected exactly 1 trace conversation, got {result['total']}"
    )
    conv_id = result["conversations"][0]["id"]
    conv = store.get_conversation(conv_id)
    assert conv is not None, f"conversation {conv_id!r} not found in trace store"
    return conv


def find_span_by_name(spans: list[SpanRecord], name: str) -> SpanRecord:
    """Return the first span whose ``name`` matches exactly.

    Raises ``AssertionError`` with the list of available span names if not found.
    """
    for span in spans:
        if span["name"] == name:
            return span
    available = sorted({s["name"] for s in spans})
    raise AssertionError(f"span {name!r} not found; available: {available}")


def find_spans_by_prefix(spans: list[SpanRecord], prefix: str) -> list[SpanRecord]:
    """Return all spans whose ``name`` starts with *prefix*."""
    return [s for s in spans if s["name"].startswith(prefix)]


def find_events(span: SpanRecord, event_name: str) -> list[EventRecord]:
    """Return all events on *span* whose ``name`` matches *event_name*."""
    return [ev for ev in span.get("events", []) if ev["name"] == event_name]


def assert_llm_usage(
    span: SpanRecord,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> EventRecord:
    """Assert a ``yuu.llm.usage`` event on *span* has the expected attributes.

    Only checks kwargs that are provided (``None`` means skip).
    Returns the first matching event for further inspection.
    """
    events = find_events(span, EVENT_LLM_USAGE)
    assert events, (
        f"no {EVENT_LLM_USAGE} event on span {span['name']!r}; "
        f"event names: {[e['name'] for e in span.get('events', [])]}"
    )
    ev = events[0]
    attrs = ev["attributes"]

    if input_tokens is not None:
        actual = attrs.get(ATTR_LLM_USAGE_INPUT_TOKENS)
        assert actual == input_tokens, (
            f"{ATTR_LLM_USAGE_INPUT_TOKENS}: expected {input_tokens}, got {actual!r}"
        )
    if output_tokens is not None:
        actual = attrs.get(ATTR_LLM_USAGE_OUTPUT_TOKENS)
        assert actual == output_tokens, (
            f"{ATTR_LLM_USAGE_OUTPUT_TOKENS}: expected {output_tokens}, got {actual!r}"
        )
    if provider is not None:
        actual = attrs.get(ATTR_LLM_PROVIDER)
        assert actual == provider, (
            f"{ATTR_LLM_PROVIDER}: expected {provider!r}, got {actual!r}"
        )
    if model is not None:
        actual = attrs.get(ATTR_LLM_MODEL)
        assert actual == model, f"{ATTR_LLM_MODEL}: expected {model!r}, got {actual!r}"
    return ev


def assert_tool_usage(
    span: SpanRecord,
    *,
    tool_name: str | None = None,
    tool_input: Any = None,
    tool_output: Any = None,
    call_id: str | None = None,
) -> list[EventRecord]:
    """Assert ``yuu.tool.usage`` events on *span* match expected attributes.

    Tool input/output are stored as JSON strings in the trace; this helper
    parses them before comparison so callers can pass either dicts or strings.

    Returns all matching events for further inspection.
    """
    events = find_events(span, EVENT_TOOL_USAGE)
    assert events, (
        f"no {EVENT_TOOL_USAGE} event on span {span['name']!r}; "
        f"event names: {[e['name'] for e in span.get('events', [])]}"
    )

    for ev in events:
        attrs = ev["attributes"]

        if tool_name is not None:
            actual = attrs.get(ATTR_TOOL_NAME)
            assert actual == tool_name, (
                f"{ATTR_TOOL_NAME}: expected {tool_name!r}, got {actual!r}"
            )

        if call_id is not None:
            actual = attrs.get(ATTR_TOOL_CALL_ID)
            assert actual == call_id, (
                f"{ATTR_TOOL_CALL_ID}: expected {call_id!r}, got {actual!r}"
            )

        if tool_input is not None:
            actual = _try_parse_json(attrs.get(ATTR_TOOL_INPUT))
            expected = _try_parse_json(tool_input)
            assert actual == expected, (
                f"{ATTR_TOOL_INPUT}: expected {expected!r}, got {actual!r}"
            )

        if tool_output is not None:
            actual = _try_parse_json(attrs.get(ATTR_TOOL_OUTPUT))
            expected = _try_parse_json(tool_output)
            assert actual == expected, (
                f"{ATTR_TOOL_OUTPUT}: expected {expected!r}, got {actual!r}"
            )

    return events


def assert_cost_event(
    span: SpanRecord,
    *,
    category: str | None = None,
    amount: float | None = None,
) -> EventRecord:
    """Assert a ``yuu.cost`` event on *span* has the expected values.

    Returns the first matching event.
    """
    events = find_events(span, EVENT_COST)
    assert events, f"no {EVENT_COST} event on span {span['name']!r}"
    ev = events[0]
    attrs = ev["attributes"]

    if category is not None:
        actual = attrs.get(ATTR_COST_CATEGORY)
        assert actual == category, (
            f"{ATTR_COST_CATEGORY}: expected {category!r}, got {actual!r}"
        )
    if amount is not None:
        actual = attrs.get(ATTR_COST_AMOUNT)
        assert actual == amount, (
            f"{ATTR_COST_AMOUNT}: expected {amount}, got {actual!r}"
        )
    return ev


def assert_span_timing(
    span: SpanRecord,
    *,
    min_duration_ns: int | None = None,
) -> None:
    """Assert *span* has valid timing: start > 0, end > 0, start ≤ end."""
    start = span["start_time_unix_nano"]
    end = span["end_time_unix_nano"]
    assert start > 0, f"span {span['name']!r}: start_time is {start}"
    assert end > 0, f"span {span['name']!r}: end_time is {end}"
    assert start <= end, f"span {span['name']!r}: start ({start}) > end ({end})"
    if min_duration_ns is not None:
        duration = end - start
        assert duration >= min_duration_ns, (
            f"span {span['name']!r}: duration {duration}ns < min {min_duration_ns}ns"
        )


# -- daemon resource HTTP harness -------------------------------------------


@dataclass
class _FakeActor:
    binding: ActorBinding
    started: bool = False

    @property
    def actor_id(self) -> str:
        return self.binding.actor.id

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def handle_resource_changed(self, event: ResourceChanged) -> None:
        _ = event

    async def handle_message(self, message) -> None:
        _ = message


@dataclass
class _FakeActorFactory:
    actor_type: str = "fake"
    actors: dict[str, _FakeActor] = field(default_factory=dict)

    async def create(self, binding: ActorBinding, mailbox: Mailbox) -> Actor:
        _ = mailbox
        actor = _FakeActor(binding)
        self.actors[binding.actor.id] = actor
        return actor


def build_resource_daemon_runtime(
    resources: Resources,
    workspace_root: Path,
    *,
    secret: str = "test-secret",
) -> tuple[ASGIApp, ServiceHost]:
    """Build a daemon ASGI app over *resources* for scenario-level HTTP tests.

    Returns the ASGI app and the ``ServiceHost`` that owns its lifecycle
    services (caller must ``start``/``stop`` it). Mirrors the production
    wiring used by the Admin UI without spawning real child processes.
    """
    from yuubot.bootstrap.config import ServerConfig, TraceConfig, YuuAgentsConfig
    from yuubot.core.actors import ActorFactoryRegistry, ActorManager
    from yuubot.core.actors.impls.python_session import ActorPythonSessionFactory
    from yuubot.core.actors.workspace import ActorWorkspaceResolver
    from yuubot.core.assembly import llm_session_factory_for_binding
    from yuubot.core.facade import FacadeWorkspace, IntegrationInvokeBridge
    from yuubot.core.gateway import Gateway
    from yuubot.core.integrations import IntegrationCore, IntegrationFactoryRegistry
    from yuubot.core.routing import RouteBindings
    from yuubot.process import ServiceHost, TraceService
    from yuubot.runtime.daemon import (
        ActorLifecycleService,
        IntegrationLifecycleService,
        RouteBindingService,
        _actor_lifecycle_handler,
        _integration_lifecycle_handler,
        build_daemon_asgi_app,
        build_refresh_dispatcher,
    )
    from yuubot.runtime.daemon.commands import build_default_resource_type_registry

    gateway = Gateway(routes=RouteBindings(rules=[]))
    actor_factories = ActorFactoryRegistry()
    actor_factories.register(_FakeActorFactory())
    actors = ActorManager(
        repository=resources.repository,
        factories=actor_factories,
        gateway=gateway,
        workspace_resolver=ActorWorkspaceResolver(workspace_root / "workspaces"),
    )
    integration_factories = IntegrationFactoryRegistry()
    integrations = IntegrationCore(
        repository=resources.repository,
        factories=integration_factories,
        gateway=gateway,
        integrations_root=workspace_root / "data" / "integrations",
    )
    routes = RouteBindingService(repository=resources.repository, gateway=gateway)
    services = ServiceHost.from_iterable(
        (
            IntegrationLifecycleService(integrations),
            routes,
            ActorLifecycleService(actors),
        )
    )
    refresh = build_refresh_dispatcher(
        routes=routes, actors=actors, integrations=integrations
    )
    type_registry = build_default_resource_type_registry(
        integration_lifecycle_handler=_integration_lifecycle_handler(integrations),
        actor_lifecycle_handler=_actor_lifecycle_handler(actors),
    )
    trace_service = TraceService(config=TraceConfig(enabled=False), db_path=":memory:")
    python_sessions = ActorPythonSessionFactory(
        integrations=integrations,
        workspace=FacadeWorkspace(workspace_root / "facades"),
        bridge=IntegrationInvokeBridge(integrations),
    )
    app = build_daemon_asgi_app(
        config=ServerConfig(daemon_secret=secret),
        resources=resources,
        services=services,
        actors=actors,
        integrations=integrations,
        gateway=gateway,
        refresh=refresh,
        trace_service=trace_service,
        type_registry=type_registry,
        yuuagents_config=YuuAgentsConfig(),
        python_sessions=python_sessions,
        llm_session_factory_factory=llm_session_factory_for_binding,
    )
    return app, services


def daemon_http_client(app: ASGIApp, *, secret: str = "test-secret") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        headers={"X-Daemon-Secret": secret},
    )


# -- internal ----------------------------------------------------------------


def _try_parse_json(value: Any) -> Any:
    """If *value* is a JSON string, parse and return the result.

    Otherwise return *value* as-is.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError, TypeError:
            return value
    return value
