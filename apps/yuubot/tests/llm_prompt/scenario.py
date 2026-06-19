"""Core types for LLM prompt visibility scenarios.

Defines the vocabulary for describing what the LLM should see
and how it should behave at each step of an interaction.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import yuullm


# ---------------------------------------------------------------------------
# Prompt snapshot — what the LLM sees at one point
# ---------------------------------------------------------------------------


@dataclass
class PromptSnapshot:
    """Everything the LLM sees in a single invocation.

    Built from a ``PromptCapture`` entry at a given call index.
    """

    system_text: str
    """Concatenated text from all system-role messages."""

    user_text: str
    """Concatenated text from all user-role messages."""

    all_messages: list[yuullm.Message]
    """Every message in the history (system, user, assistant, tool)."""

    tool_specs: list[dict[str, Any]]
    """OpenAI-format tool definitions (each is ``{"type": "function", "function": …}``)."""

    @classmethod
    def from_capture_data(
        cls,
        messages: list[yuullm.Message],
        tools: list[dict[str, Any]],
    ) -> PromptSnapshot:
        return cls(
            system_text=_collect_text(messages, "system"),
            user_text=_collect_text(messages, "user"),
            all_messages=messages,
            tool_specs=tools,
        )


def _collect_text(messages: list[yuullm.Message], role: str) -> str:
    parts: list[str] = []
    for msg in messages:
        if msg.role != role:
            continue
        content = msg.content if isinstance(msg.content, list) else []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Assertion
# ---------------------------------------------------------------------------


@dataclass
class AssertionResult:
    """Outcome of a single assertion."""
    passed: bool
    reason: str = ""


# Convenience constructors
def assertion_passed() -> AssertionResult:
    return AssertionResult(passed=True)


def assertion_failed(reason: str) -> AssertionResult:
    return AssertionResult(passed=False, reason=reason)


# Type alias for assertion callables
Assertion = Callable[[PromptSnapshot], AssertionResult]


# ---------------------------------------------------------------------------
# Tool call action — describes how to script the LLM
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A tool call the scripted LLM should make at this step.

    The framework converts this to a ``yuullm.ToolCall`` when building
    the response script for ``PromptCapture``.
    """
    name: str
    arguments: dict[str, Any]


# ---------------------------------------------------------------------------
# Scenario step
# ---------------------------------------------------------------------------


@dataclass
class ScenarioStep:
    """One step in a prompt visibility scenario.

    Every step has an *assertion* that verifies the current prompt snapshot.
    If the step also has an *action*, the framework simulates the LLM making
    that tool call, waits for the daemon to process the result, and then
    advances the snapshot for the next step.

    If an *action* is set and a *side_effect* is provided, the framework
    calls ``side_effect(ctx)`` **after** the tool has been fully processed
    (the daemon appended the result and made the next LLM call).  This lets
    scenarios verify side-effects such as integration outbound queues,
    database state, or trace exports.
    """

    assertion: Assertion
    """Check the current prompt snapshot.

    If this step is **after** an action, the snapshot already includes the
    tool result from that action.
    """

    action: ToolCall | None = None
    """If set, the scripted LLM returns this tool call instead of a plain
    text response.  The daemon will execute the tool, append the result to
    history, and call the LLM again, producing a new snapshot."""


# ---------------------------------------------------------------------------
# Scenario base class
# ---------------------------------------------------------------------------


class PromptScenario(abc.ABC):
    """A complete scenario describing an LLM prompt visibility interaction.

    Subclasses override :meth:`setup` to configure the daemon and
    :meth:`steps` to define the assert / action chain.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable scenario name (used as test ID)."""

    @property
    def description(self) -> str:
        """Optional longer description."""
        return ""

    @abc.abstractmethod
    async def setup(self, ctx: ScenarioContext) -> None:
        """Configure the daemon, integrations, actors, and PromptCapture.

        Implementations should:

        * Create a ``PromptCapture`` and register it via
          ``register_test_llm_provider``.
        * Build and start the daemon.
        * Insert resource records and start actors.

        The framework calls :meth:`setup` **after** mounting the response
        script onto the capture, but **before** triggering the message
        that kicks off the LLM interaction.
        """

    @abc.abstractmethod
    def steps(self) -> Sequence[ScenarioStep]:
        """Return the ordered list of scenario steps."""


# ---------------------------------------------------------------------------
# Scenario context — shared state between the runner and the scenario
# ---------------------------------------------------------------------------


@dataclass
class ScenarioContext:
    """Runtime context passed to :meth:`PromptScenario.setup`.

    The framework populates *capture* before calling setup.
    The test function passes fixtures such as *config*, *tmp_path*,
    and *monkeypatch* through the runner.

    Scenarios set *daemon* so the runner can stop it after assertions.
    """

    capture: PromptCaptureHandle | None = None
    """Handle to query captured LLM calls."""

    daemon: Any = None
    """Daemon instance; set by scenario setup. Runner stops it after use."""

    config: Any = None
    """BootstrapConfig from test fixture, if available."""

    tmp_path: Any = None
    """tmp_path from test fixture, if available."""

    monkeypatch: Any = None
    """monkeypatch from test fixture, if available."""


class PromptCaptureHandle:
    """Narrow interface that scenarios use to interact with PromptCapture.

    This avoids leaking the full ``PromptCapture`` internals to scenarios.
    """

    def __init__(self, capture: Any) -> None:
        self._capture = capture

    @property
    def calls(self) -> list[list[yuullm.Message]]:
        """All captured LLM calls (list of message lists)."""
        return self._capture.calls

    @property
    def tools(self) -> list[list[dict[str, Any]]]:
        """Tool specs recorded for each LLM call."""
        return self._capture.tools


# ---------------------------------------------------------------------------
# Built-in assertion builders
# ---------------------------------------------------------------------------


def AssertToolExists(name: str) -> Assertion:
    """Assert that a tool with *name* exists in the tool specs."""
    def check(snapshot: PromptSnapshot) -> AssertionResult:
        for spec in snapshot.tool_specs:
            func = spec.get("function", {})
            if isinstance(func, dict) and func.get("name") == name:
                return assertion_passed()
        return assertion_failed(f"Tool {name!r} not found in tool specs")
    return check


def AssertToolDescriptionContains(name: str, text: str) -> Assertion:
    """Assert that tool *name*'s description contains *text*."""
    def check(snapshot: PromptSnapshot) -> AssertionResult:
        for spec in snapshot.tool_specs:
            func = spec.get("function", {})
            if isinstance(func, dict) and func.get("name") == name:
                desc = func.get("description", "")
                if text in desc:
                    return assertion_passed()
                return assertion_failed(
                    f"Tool {name!r} description does not contain {text!r}.\n"
                    f"Actual description:\n{desc}"
                )
        return assertion_failed(f"Tool {name!r} not found")
    return check


def AssertSystemPromptContains(text: str) -> Assertion:
    """Assert that the system prompt text contains *text*."""
    def check(snapshot: PromptSnapshot) -> AssertionResult:
        if text in snapshot.system_text:
            return assertion_passed()
        return assertion_failed(
            f"System prompt does not contain {text!r}.\n"
            f"System prompt:\n{snapshot.system_text}"
        )
    return check


def AssertHistoryContains(text: str) -> Assertion:
    """Assert that any message in the history contains *text*."""
    def check(snapshot: PromptSnapshot) -> AssertionResult:
        combined = "\n".join(
            _all_message_text(msg) for msg in snapshot.all_messages
        )
        if text in combined:
            return assertion_passed()
        return assertion_failed(
            f"No message in history contains {text!r}.\n"
            f"Full history:\n{combined}"
        )
    return check


def AssertUserMessageContains(text: str) -> Assertion:
    """Assert that the user message text contains *text*."""
    def check(snapshot: PromptSnapshot) -> AssertionResult:
        if text in snapshot.user_text:
            return assertion_passed()
        return assertion_failed(
            f"User message does not contain {text!r}.\n"
            f"User message:\n{snapshot.user_text}"
        )
    return check


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _all_message_text(msg: yuullm.Message) -> str:
    """Extract all text content from a single message."""
    content = msg.content if isinstance(msg.content, list) else []
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_result":
                result = block.get("content", "")
                if isinstance(result, list):
                    for item in result:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                elif isinstance(result, str):
                    parts.append(result)
    return "\n".join(parts)
