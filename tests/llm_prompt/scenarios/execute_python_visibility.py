"""Scenario: the ``execute_python`` tool is visible in the LLM prompt."""

from __future__ import annotations

import httpx
import msgspec

from tests.helpers import (
    insert_echo_actor_resources,
    make_test_daemon_infrastructure,
)
from tests.llm_prompt.scenario import PromptScenario, ScenarioContext, ScenarioStep
from tests.llm_prompt.scenario import (
    AssertToolExists,
    AssertToolDescriptionContains,
    AssertSystemPromptContains,
)
from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, PathsConfig
from yuubot.runtime.daemon import YuubotDaemon, build_daemon

SOURCE_PATH = "channels/prompt-verify"
ACTOR_ID = "prompt-verify-actor"
INTEGRATION_ID = "prompt-verify"
SYSTEM_PROMPT = "You are a verification assistant that uses echo tool."


class ExecutePythonToolVisibility(PromptScenario):
    """The execute_python tool and its capability documentation are present
    in the LLM's prompt by default — no discovery step needed."""

    @property
    def name(self) -> str:
        return "execute_python tool default visibility"

    @property
    def description(self) -> str:
        return (
            "Verifies that the execute_python tool spec exists and its "
            "description documents the available capability imports "
            "(e.g. yext.echo.echo), and that the system prompt includes "
            "IM-mode guidance."
        )

    async def setup(self, ctx: ScenarioContext) -> None:
        assert ctx.config is not None, "yuubot_config fixture required"
        assert ctx.tmp_path is not None, "tmp_path fixture required"

        daemon = await _build_daemon(ctx.config, ctx.tmp_path)
        ctx.daemon = daemon  # runner will stop it
        await daemon.start()

        await insert_echo_actor_resources(
            daemon.resources.repository,
            actor_id=ACTOR_ID,
            integration_id=INTEGRATION_ID,
            source_path=SOURCE_PATH,
            system_prompt=SYSTEM_PROMPT,
        )
        await daemon.resources.event_bus.drain()
        await daemon.actors.start_actor(ACTOR_ID)

        async with _client(daemon) as client:
            await client.post(
                "/integration/echo",
                json={
                    "integration_id": INTEGRATION_ID,
                    "message_id": "msg-verify-1",
                    "sender_id": "user-verify",
                    "sender_name": "Verifier",
                    "kind": "private",
                    "text": "verify prompt",
                    "content": [{"type": "text", "text": "verify prompt"}],
                },
            )

    def steps(self) -> list[ScenarioStep]:
        return [
            ScenarioStep(
                assertion=AssertToolExists("execute_python"),
            ),
            ScenarioStep(
                assertion=AssertToolDescriptionContains(
                    "execute_python",
                    "yext.echo.echo",
                ),
            ),
            ScenarioStep(
                assertion=AssertToolDescriptionContains(
                    "execute_python",
                    "Returns the payload unchanged.",
                ),
            ),
            ScenarioStep(
                assertion=AssertSystemPromptContains(SYSTEM_PROMPT),
            ),
            ScenarioStep(
                assertion=AssertSystemPromptContains("tim.Channel"),
            ),
        ]


async def _build_daemon(
    base_config: BootstrapConfig,
    tmp_path: object,
) -> YuubotDaemon:
    from pathlib import Path
    assert isinstance(tmp_path, Path)
    return await build_daemon(
        msgspec.structs.replace(
            base_config,
            database=DatabaseConfig(path=":memory:"),
            paths=PathsConfig(
                data_dir=str(tmp_path / "data"),
            ),
        ),
        components=make_test_daemon_infrastructure(),
    )


def _client(daemon: YuubotDaemon) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=daemon.asgi_app()),
        base_url="http://testserver",
    )
