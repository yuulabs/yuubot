"""Test framework for LLM prompt visibility scenarios.

Provides ``PromptCapture`` — a scripted LLM provider that records all
invocations and can be configured to return tool calls via a response
script — and ``ScenarioRunner`` which drives :class:`PromptScenario`
execution.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import yuullm

from tests.helpers import register_test_llm_provider

from .scenario import (
    AssertionResult,
    PromptCaptureHandle,
    PromptScenario,
    PromptSnapshot,
    ScenarioContext,
    ToolCall,
)


# ---------------------------------------------------------------------------
# PromptCapture — scripted LLM provider
# ---------------------------------------------------------------------------


class PromptCapture:
    """Captures all LLM calls during a test and responds from a script.

    Usage::

        capture = PromptCapture()
        capture.set_response_script([action1, action2, None])
        register_test_llm_provider("openai", capture)
        # ... trigger daemon interaction ...
        await capture.wait_for_calls(3)
        snapshot = capture.snapshot(0)
    """

    def __init__(self) -> None:
        self.calls: list[list[yuullm.Message]] = []
        self.tools: list[list[dict[str, Any]]] = []
        self._response_script: list[ToolCall | None] = []
        self._script_index = 0

    # -- Public API used by the framework / scenarios -------------------

    def set_response_script(self, script: list[ToolCall | None]) -> None:
        """Set the script that controls what the LLM returns.

        Each entry corresponds to one LLM call:

        * ``None`` → return a plain ``"ok"`` text response (terminates loop).
        * ``ToolCall(...)`` → return a tool-call response (loop continues).
        """
        self._response_script = list(script)
        self._script_index = 0

    async def wait_for_calls(self, count: int, *, timeout: float = 10.0) -> None:
        """Wait until at least *count* LLM calls have been captured."""
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.calls) < count:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise AssertionError(
                    f"Timed out waiting for {count} LLM calls, "
                    f"got {len(self.calls)}"
                )
            await asyncio.sleep(0.01)

    def snapshot(self, call_index: int) -> PromptSnapshot:
        """Build a :class:`PromptSnapshot` from the *call_index*-th LLM call."""
        if call_index >= len(self.calls):
            raise IndexError(
                f"Call index {call_index} out of range "
                f"(have {len(self.calls)} calls)"
            )
        tools = self.tools[call_index] if call_index < len(self.tools) else []
        return PromptSnapshot.from_capture_data(
            messages=self.calls[call_index],
            tools=tools,
        )

    @property
    def handle(self) -> PromptCaptureHandle:
        """Return a narrow handle for scenarios to query."""
        return PromptCaptureHandle(self)

    # -- ScriptedLlmProvider protocol -----------------------------------

    @property
    def api_type(self) -> str:
        return "scripted"

    @property
    def provider(self) -> str:
        return "scripted"

    async def list_models(self) -> list[yuullm.ProviderModel]:
        return [yuullm.ProviderModel(id="gpt-4")]

    async def stream(
        self,
        history: yuullm.History,
        *,
        model: str,
        on_raw_chunk: yuullm.RawChunkHook | None = None,
        **kwargs: Any,
    ) -> yuullm.StreamResult:
        _ = model, on_raw_chunk, kwargs
        messages, tools = yuullm.split_history(history)
        self.calls.append(list(messages))
        self.tools.append(list(tools or ()))

        response = self._next_response()
        return _make_stream_result(response)

    # -- Internal -------------------------------------------------------

    def _next_response(self) -> ToolCall | None:
        if self._script_index < len(self._response_script):
            action = self._response_script[self._script_index]
            self._script_index += 1
            return action
        return None  # fallback: end the loop


def _make_stream_result(
    response: ToolCall | None,
) -> yuullm.StreamResult:
    async def stream_items() -> AsyncIterator[yuullm.StreamItem]:
        if response is None:
            yield yuullm.Response({"type": "text", "text": "ok"})
        else:
            yield yuullm.Response({
                "type": "text",
                "text": f"Calling {response.name}.",
            })
            yield yuullm.ToolCall(
                id=f"call_{uuid.uuid4().hex[:12]}",
                name=response.name,
                arguments=json.dumps(response.arguments),
            )

    return stream_items(), yuullm.Store(
        usage=yuullm.Usage(
            provider="fake",
            model="fake",
            input_tokens=1,
            output_tokens=1,
        ),
    )


# ---------------------------------------------------------------------------
# ScenarioRunner
# ---------------------------------------------------------------------------


@dataclass
class ScenarioResult:
    """Outcome of running a :class:`PromptScenario`."""
    name: str
    passed: bool
    failures: list[tuple[int, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.passed:
            return f"✓ {self.name}"
        lines = [f"✗ {self.name}"]
        for step_idx, reason in self.failures:
            lines.append(f"  Step {step_idx}: {reason}")
        return "\n".join(lines)


class ScenarioRunner:
    """Drives a :class:`PromptScenario` through setup, execution, and
    step-by-step assertion."""

    async def run(
        self,
        scenario: PromptScenario,
        **fixtures: Any,
    ) -> ScenarioResult:
        """Execute the scenario.

        Parameters
        ----------
        scenario:
            The scenario to run.
        **fixtures:
            Test fixtures forwarded to :class:`ScenarioContext`
            (e.g. ``config``, ``tmp_path``, ``monkeypatch``).
        """
        capture = PromptCapture()
        steps = list(scenario.steps())

        # 1. Build response script from steps with actions
        response_script: list[ToolCall | None] = [
            step.action for step in steps if step.action is not None
        ]
        response_script.append(None)  # terminal "ok"

        # 2. Mount response script
        capture.set_response_script(response_script)

        # 3. Register so the daemon infrastructure can find it
        register_test_llm_provider("openai", capture)

        # 4. Build context and let the scenario set itself up
        ctx = ScenarioContext(
            capture=capture.handle,
            **fixtures,
        )

        await scenario.setup(ctx)

        # 5. Wait for all LLM calls
        try:
            await capture.wait_for_calls(len(response_script))
        except AssertionError as exc:
            return ScenarioResult(
                name=scenario.name,
                passed=False,
                failures=[(-1, str(exc))],
            )

        # 6. Run step-by-step assertions
        call_index = 0
        failures: list[tuple[int, str]] = []

        for step_idx, step in enumerate(steps):
            snapshot = capture.snapshot(call_index)
            result: AssertionResult = step.assertion(snapshot)
            if not result.passed:
                failures.append((step_idx, result.reason))

            if step.action is not None:
                call_index += 1

        # 7. Cleanup: stop daemon if the scenario started one
        daemon = getattr(ctx, "daemon", None)
        if daemon is not None:
            try:
                await daemon.stop()
            except Exception:
                pass  # best-effort cleanup

        if failures:
            return ScenarioResult(
                name=scenario.name,
                passed=False,
                failures=failures,
            )
        return ScenarioResult(name=scenario.name, passed=True)
