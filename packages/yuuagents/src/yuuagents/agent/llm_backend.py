"""AgentLLMBackend — wraps YuuSession + factory + options for agent LLM access."""

from __future__ import annotations

import yuullm
from attrs import define, field

from yuuagents.llm.session import ProviderPoolSessionFactory
from yuuagents.types.values import LlmOptions


@define
class AgentLLMBackend:
    """Packages the LLM resources needed by an Agent.

    Replaced the old pattern of holding llm_session, llm_session_factory,
    llm_options, model_selector as separate Agent fields.
    """

    session: yuullm.YuuSession  # holds history
    factory: ProviderPoolSessionFactory  # for replace_history
    options: LlmOptions = field(factory=dict)
    model: str = ""  # model identifier for events/cost
