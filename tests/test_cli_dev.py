from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import msgspec
from yuubot.bootstrap.config import BootstrapConfig
from yuubot.cli import _build_web, _run_dev
import yuubot.cli as cli_module


def test_dev_returns_nonzero_when_child_exits_before_health(
    yuubot_config: BootstrapConfig,
    monkeypatch,
) -> None:
    _patch_dev_dependencies(yuubot_config, monkeypatch)
    processes = [FakeProcess([0]), FakeProcess([None])]
    spawned = processes.copy()

    code = _run_dev(
        "config.yaml",
        popen=lambda _argv: processes.pop(0),
        health_probe=lambda _url: False,
        startup_timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert code == 1
    assert spawned[1].terminated


def test_dev_waits_for_both_health_checks_before_returning_child_exit(
    yuubot_config: BootstrapConfig,
    monkeypatch,
) -> None:
    _patch_dev_dependencies(yuubot_config, monkeypatch)
    processes = [FakeProcess([None, 7]), FakeProcess([None, None])]
    spawned = processes.copy()

    code = _run_dev(
        "config.yaml",
        popen=lambda _argv: processes.pop(0),
        health_probe=lambda _url: True,
        startup_timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert code == 7
    assert spawned[1].terminated


def test_dev_gracefully_stops_children_on_keyboard_interrupt(
    yuubot_config: BootstrapConfig,
    monkeypatch,
) -> None:
    _patch_dev_dependencies(yuubot_config, monkeypatch)
    monkeypatch.setattr(cli_module.os, "killpg", _missing_process_group)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.time, "sleep", interrupt)
    processes = [FakeProcess([None]), FakeProcess([None])]
    spawned = processes.copy()

    code = _run_dev(
        "config.yaml",
        popen=lambda _argv: processes.pop(0),
        health_probe=lambda _url: True,
        startup_timeout_s=1.0,
        shutdown_timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert code == 130
    assert all(process.terminated for process in spawned)
    assert all(process.wait_calls == 1 for process in spawned)


def test_dev_kills_children_that_ignore_graceful_shutdown(
    yuubot_config: BootstrapConfig,
    monkeypatch,
) -> None:
    _patch_dev_dependencies(yuubot_config, monkeypatch)
    monkeypatch.setattr(cli_module.os, "killpg", _missing_process_group)

    def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.time, "sleep", interrupt)
    processes = [
        FakeProcess([None], wait_timeouts_before_exit=1),
        FakeProcess([None]),
    ]
    spawned = processes.copy()

    code = _run_dev(
        "config.yaml",
        popen=lambda _argv: processes.pop(0),
        health_probe=lambda _url: True,
        startup_timeout_s=1.0,
        shutdown_timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert code == 130
    assert spawned[0].terminated
    assert spawned[0].killed
    assert spawned[0].wait_calls == 2
    assert spawned[1].terminated
    assert not spawned[1].killed


def test_build_web_uses_cache_when_dist_is_newer(
    yuubot_config: BootstrapConfig,
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config_with_web_dist(yuubot_config, tmp_path)
    web_root = Path(config.admin.web_dist_dir).parent
    package_json = _write_file(web_root / "package.json", "{}")
    source_file = _write_file(web_root / "src" / "App.tsx", "export function App() {}")
    dist_index = _write_file(web_root / "dist" / "index.html", "<main></main>")
    _set_mtime(package_json, 100.0)
    _set_mtime(source_file, 100.0)
    _set_mtime(dist_index, 200.0)
    calls: list[list[str]] = []

    def fake_run(
        argv: list[str],
        *,
        cwd: str,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        _ = cwd, capture_output, text
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    _build_web(config)

    assert calls == []


def test_build_web_rebuilds_when_source_is_newer(
    yuubot_config: BootstrapConfig,
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config_with_web_dist(yuubot_config, tmp_path)
    web_root = Path(config.admin.web_dist_dir).parent
    package_json = _write_file(web_root / "package.json", "{}")
    source_file = _write_file(web_root / "src" / "App.tsx", "export function App() {}")
    dist_index = _write_file(web_root / "dist" / "index.html", "<main></main>")
    _set_mtime(package_json, 100.0)
    _set_mtime(dist_index, 100.0)
    _set_mtime(source_file, 200.0)
    calls: list[tuple[list[str], str]] = []

    def fake_run(
        argv: list[str],
        *,
        cwd: str,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output, text
        calls.append((argv, cwd))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    _build_web(config)

    assert calls == [(["npm", "run", "build"], str(web_root))]


def _missing_process_group(_pid: int, _sig: int) -> None:
    raise ProcessLookupError


def _patch_dev_dependencies(yuubot_config: BootstrapConfig, monkeypatch) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_bootstrap_config",
        lambda _path: yuubot_config,
    )
    monkeypatch.setattr(cli_module, "_build_web", lambda _config: None)


def _config_with_web_dist(config: BootstrapConfig, tmp_path: Path) -> BootstrapConfig:
    return msgspec.structs.replace(
        config,
        admin=msgspec.structs.replace(
            config.admin,
            web_dist_dir=str(tmp_path / "web" / "dist"),
        ),
    )


def _write_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _set_mtime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


@dataclass
class FakeProcess:
    poll_results: list[int | None]
    wait_timeouts_before_exit: int = 0
    terminated: bool = False
    killed: bool = False
    wait_calls: int = 0
    pid: int = 999_999
    _returncode: int | None = field(default=None, init=False)
    _index: int = field(default=0, init=False)

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if self._index >= len(self.poll_results):
            return self.poll_results[-1]
        result = self.poll_results[self._index]
        self._index += 1
        return result

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        timeout_s = 0.0 if timeout is None else timeout
        self.wait_calls += 1
        if self.wait_timeouts_before_exit > 0:
            self.wait_timeouts_before_exit -= 1
            raise subprocess.TimeoutExpired("fake", timeout_s)
        if self.killed:
            self._returncode = -9
        elif self.terminated:
            self._returncode = -15
        else:
            self._returncode = 0
        return self._returncode
