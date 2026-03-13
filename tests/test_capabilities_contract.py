import pytest
import os

from yuubot.capabilities import CapabilityContext, execute, load_capability_doc
from yuubot.capabilities.contract import ActionFilter, load_all_contracts


def test_load_capability_doc_renders_from_contract():
    doc = load_capability_doc("web")

    assert "# web Capability" in doc
    assert "### search" in doc
    assert "web search" in doc


def test_load_capability_doc_filters_actions():
    doc = load_capability_doc(
        "mem",
        action_filter=ActionFilter(mode="include", actions=frozenset({"save", "recall"})),
    )

    assert "### save" in doc
    assert "### recall" in doc
    assert "### delete" not in doc
    assert "### restore" not in doc


def test_load_capability_doc_supports_exclude_actions():
    doc = load_capability_doc(
        "mem",
        action_filter=ActionFilter(mode="exclude", actions=frozenset({"delete", "restore"})),
    )

    assert "### save" in doc
    assert "### recall" in doc
    assert "### delete" not in doc
    assert "### restore" not in doc


async def test_execute_rejects_disallowed_action():
    with pytest.raises(ValueError, match="not available to this agent"):
        await execute(
            "mem delete 1",
            context=CapabilityContext(
                agent_name="main",
                action_filters={
                    "mem": ActionFilter(mode="include", actions=frozenset({"save", "recall"}))
                },
            ),
        )


def test_load_all_contracts_caches_until_mtime_changes(tmp_path, monkeypatch):
    contract_dir = tmp_path / "demo"
    contract_dir.mkdir()
    contract_path = contract_dir / "contract.yaml"
    contract_path.write_text(
        "name: demo\nsummary: first\nactions:\n  - name: ping\n    summary: ping\n    usage: demo ping\n    payload_rule: none\n    return_shape: text\n",
        encoding="utf-8",
    )

    from yuubot.capabilities import contract as contract_mod

    monkeypatch.setattr(contract_mod, "_CACHE_KEY", None)
    monkeypatch.setattr(contract_mod, "_CACHE_VALUE", {})
    monkeypatch.setattr(contract_mod, "_iter_contract_paths", lambda: [contract_path])

    contracts = load_all_contracts()
    assert contracts["demo"].summary == "first"

    contract_path.write_text(
        "name: demo\nsummary: second\nactions:\n  - name: ping\n    summary: ping\n    usage: demo ping\n    payload_rule: none\n    return_shape: text\n",
        encoding="utf-8",
    )
    stat = contract_path.stat()
    os.utime(contract_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    cached = load_all_contracts()
    assert cached["demo"].summary == "second"
