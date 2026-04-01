from __future__ import annotations

import yaml

from yuubot.config import load_config, write_yagents_config


def test_load_config_prefers_agent_llm_refs_over_legacy_yuuagents_agents(
    tmp_path,
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agent_llm_refs": {"main": "test/test-model"},
                "yuuagents": {
                    "agents": {
                        "main": {
                            "provider": "openrouter",
                            "model": "anthropic/claude-sonnet-4.1",
                        }
                    }
                },
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    cfg = load_config(str(config_path))

    assert cfg.agent_llm_ref("main") == "test/test-model"
    assert cfg.yuuagents["agents"]["main"]["provider"] == "test"
    assert cfg.yuuagents["agents"]["main"]["model"] == "test-model"


def test_build_yuuagents_config_uses_agent_llm_refs(yuubot_config) -> None:
    payload = yuubot_config.build_yuuagents_config()

    assert payload["agents"]["main"]["provider"] == "test"
    assert payload["agents"]["main"]["model"] == "test-model"
    assert payload["agents"]["general"]["provider"] == "test"
    assert payload["agents"]["general"]["model"] == "test-model"
    assert payload["agents"]["ops"]["provider"] == "test"
    assert payload["agents"]["ops"]["model"] == "test-model"


def test_write_yagents_config_emits_generated_payload(tmp_path, yuubot_config) -> None:
    target = tmp_path / "config.yaml"

    written = write_yagents_config(yuubot_config, path=target)
    assert written == target

    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert payload["agents"]["main"]["provider"] == "test"
    assert payload["agents"]["main"]["model"] == "test-model"
    assert payload["agents"]["general"]["provider"] == "test"
    assert payload["agents"]["general"]["model"] == "test-model"
