from __future__ import annotations

from yuubot import napcat


def test_build_launch_env_strips_proxy_vars_for_qq_direct(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.test:7890")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.test:7890")
    monkeypatch.setenv("ALL_PROXY", "socks5://proxy.test:1080")
    monkeypatch.setenv("NO_PROXY", "example.com")

    env = napcat._build_launch_env(qq_direct=True)

    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env
    assert "ALL_PROXY" not in env
    assert env["NO_PROXY"] == "example.com,localhost,127.0.0.1,::1"
    assert env["no_proxy"] == env["NO_PROXY"]


def test_build_launch_env_preserves_proxy_vars_when_disabled(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.test:7890")

    env = napcat._build_launch_env(qq_direct=False)

    assert env["HTTP_PROXY"] == "http://proxy.test:7890"
