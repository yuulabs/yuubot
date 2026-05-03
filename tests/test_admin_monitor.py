from __future__ import annotations

from pathlib import Path

from yuubot.admin.app import _admin_settings


def test_admin_monitor_defaults_to_traces_ui_port(yuubot_config) -> None:
    settings = _admin_settings(yuubot_config)

    assert settings["monitor_url"] == ""
    assert settings["monitor_port"] == 8782


def test_admin_monitor_honors_yuutrace_ui_config(yuubot_config) -> None:
    yuubot_config.yuuagents["yuutrace"]["ui_port"] = "9090"
    yuubot_config.yuuagents["yuutrace"]["ui_url"] = "https://trace.example.test"

    settings = _admin_settings(yuubot_config)

    assert settings["monitor_url"] == "https://trace.example.test"
    assert settings["monitor_port"] == 9090


def test_admin_monitor_static_uses_settings_endpoint() -> None:
    html = Path("src/yuubot/admin/static/index.html").read_text(encoding="utf-8")

    assert "/api/admin/settings" in html
    assert ":4318" not in html
