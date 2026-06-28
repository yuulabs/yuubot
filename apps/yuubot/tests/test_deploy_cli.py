"""Tests for server deployment maintenance commands."""

from __future__ import annotations

import subprocess

import click.testing

import yuubot.cli as cli_module
from yuubot.cli import cli


def test_deploy_shutdown_stops_systemd_units(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli_module, "_is_root", lambda: False)
    monkeypatch.setattr(cli_module, "_run_deploy_command", fake_run)

    result = click.testing.CliRunner().invoke(cli, ["deploy", "shutdown"])

    assert result.exit_code == 0
    assert calls == [
        [
            "sudo",
            "systemctl",
            "stop",
            "yuubot-admin.service",
            "yuubot-daemon.service",
        ]
    ]


def test_deploy_uninstall_preserves_data_by_default(monkeypatch) -> None:
    calls = _capture_deploy_commands(monkeypatch)
    monkeypatch.setenv("YUUBOT_CONFIG_DIR", "/tmp/yuubot-config")
    monkeypatch.setenv("YUU_DATA_DIR", "/tmp/yuubot-data")
    monkeypatch.setenv("YUUBOT_CADDY_SITE_FILE", "/tmp/yuubot.caddy")

    result = click.testing.CliRunner().invoke(cli, ["deploy", "uninstall"])

    assert result.exit_code == 0
    assert ["sudo", "rm", "-rf", "/tmp/yuubot-config"] in calls
    assert ["sudo", "rm", "-f", "/tmp/yuubot.caddy"] in calls
    assert ["sudo", "rm", "-rf", "/tmp/yuubot-data"] not in calls
    assert "preserved data directory: /tmp/yuubot-data" in result.output


def test_deploy_uninstall_remove_data_removes_data_dir(monkeypatch) -> None:
    calls = _capture_deploy_commands(monkeypatch)
    monkeypatch.setenv("YUU_DATA_DIR", "/tmp/yuubot-data")

    result = click.testing.CliRunner().invoke(
        cli,
        ["deploy", "uninstall", "--remove-data"],
    )

    assert result.exit_code == 0
    assert ["sudo", "rm", "-rf", "/tmp/yuubot-data"] in calls
    assert "removed data directory: /tmp/yuubot-data" in result.output


def _capture_deploy_commands(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake_run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(cli_module, "_is_root", lambda: False)
    monkeypatch.setattr(cli_module, "_run_deploy_command", fake_run)
    return calls
