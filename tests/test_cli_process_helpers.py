from __future__ import annotations

from types import SimpleNamespace

import httpx

from yuubot.cli import _daemon_api_alive, _screen_quit, _screen_session_ids


def test_screen_session_ids_match_exact_screen_name(monkeypatch) -> None:
    stdout = """
There are screens on:
    1452.yuubot\t(Detached)
    2201.yuubot-helper\t(Detached)
    3344.recorder\t(Detached)
    9988.yuubot\t(Detached)
4 Sockets in /run/screen/S-user.
"""

    monkeypatch.setattr(
        "yuubot.cli.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout=stdout),
    )

    assert _screen_session_ids("yuubot") == ["1452.yuubot", "9988.yuubot"]


def test_screen_quit_terminates_each_matching_session(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    stdout = """
There are screens on:
    1452.yuubot\t(Detached)
    9988.yuubot\t(Detached)
2 Sockets in /run/screen/S-user.
"""

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["screen", "-ls"]:
            return SimpleNamespace(stdout=stdout)
        calls.append(tuple(cmd))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr("yuubot.cli.subprocess.run", fake_run)

    _screen_quit("yuubot")

    assert calls == [
        ("screen", "-S", "1452.yuubot", "-X", "quit"),
        ("screen", "-S", "9988.yuubot", "-X", "quit"),
    ]


def test_daemon_api_alive_rejects_non_2xx_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "yuubot.cli.httpx.get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=503),
    )

    assert _daemon_api_alive("http://127.0.0.1:8780") is False


def test_daemon_api_alive_handles_request_errors(monkeypatch) -> None:
    monkeypatch.setattr(
        "yuubot.cli.httpx.get",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.RequestError("boom")),
    )

    assert _daemon_api_alive("http://127.0.0.1:8780") is False
