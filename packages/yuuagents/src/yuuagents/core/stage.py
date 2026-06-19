from __future__ import annotations

from collections.abc import Mapping

import yuullm
from attrs import define, field

from yuuagents.core.eventbus import EventBus
from yuuagents.llm.session import ProviderPoolSessionFactory
from yuuagents.core.mailbox import MailBox
from yuuagents.core.runtime import Runtime
from yuuagents.tool.primitives import ToolRegistry
from yuuagents.types.values import LlmOptions


@define
class Stage:
    """Resource container: holds all runtime resources, no behavior."""

    mailbox: MailBox
    eventbus: EventBus
    runtime: Runtime
    llm_session_factories: dict[str, ProviderPoolSessionFactory] = field(factory=dict)
    llm_options: dict[str, LlmOptions] = field(factory=dict)

    @classmethod
    def from_config(
        cls,
        *,
        mailbox: MailBox | None = None,
        eventbus: EventBus | None = None,
        provider_pool: yuullm.ProviderPool | None = None,
        llm_provider: str = "default",
        llm_session_factories: Mapping[str, ProviderPoolSessionFactory] | None = None,
        llm_options: Mapping[str, LlmOptions] | None = None,
    ) -> Stage:
        mailbox = mailbox or MailBox()
        eventbus = eventbus or EventBus()
        runtime = Runtime(
            registry=ToolRegistry(),
            eventbus=eventbus,
        )
        factories = _stage_llm_session_factories(
            provider_pool=provider_pool,
            llm_provider=llm_provider,
            llm_session_factories=llm_session_factories,
        )
        return cls(
            mailbox=mailbox,
            eventbus=eventbus,
            runtime=runtime,
            llm_session_factories=dict(factories),
            llm_options=dict(llm_options or {}),
        )


def _stage_llm_session_factories(
    *,
    provider_pool: yuullm.ProviderPool | None,
    llm_provider: str,
    llm_session_factories: Mapping[str, ProviderPoolSessionFactory] | None,
) -> dict[str, ProviderPoolSessionFactory]:
    if provider_pool is not None and llm_session_factories is not None:
        raise ValueError("pass either provider_pool or llm_session_factories, not both")
    if llm_session_factories is not None:
        return dict(llm_session_factories)
    if provider_pool is None:
        return {}
    return {llm_provider: ProviderPoolSessionFactory(provider_pool)}
