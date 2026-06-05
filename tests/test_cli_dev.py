from __future__ import annotations

from dataclasses import dataclass, field

from yuubot.bootstrap.config import BootstrapConfig
from yuubot.cli import _run_dev
import yuubot.cli as cli_module


def test_dev_returns_nonzero_when_child_exits_before_health(
    yuubot_config: BootstrapConfig,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_bootstrap_config",
        lambda _path: yuubot_config,
    )
    processes = [FakeProcess([0]), FakeProcess([None])]

    code = _run_dev(
        "config.yaml",
        popen=lambda _argv: processes.pop(0),
        health_probe=lambda _url: False,
        startup_timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert code == 1


def test_dev_waits_for_both_health_checks_before_returning_child_exit(
    yuubot_config: BootstrapConfig,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_bootstrap_config",
        lambda _path: yuubot_config,
    )
    processes = [FakeProcess([None, 7]), FakeProcess([None, None])]

    code = _run_dev(
        "config.yaml",
        popen=lambda _argv: processes.pop(0),
        health_probe=lambda _url: True,
        startup_timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert code == 7


@dataclass
class FakeProcess:
    poll_results: list[int | None]
    terminated: bool = False
    _index: int = field(default=0, init=False)

    def poll(self) -> int | None:
        if self._index >= len(self.poll_results):
            return self.poll_results[-1]
        result = self.poll_results[self._index]
        self._index += 1
        return result

    def terminate(self) -> None:
        self.terminated = True
