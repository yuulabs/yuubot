from __future__ import annotations

from pathlib import Path

import pytest

from support.api import base_url, boot_app, http_json, running_server
from yuubot.upgrade import apply_update, check_update
from yuubot.upgrade.apply import build_apply_script, schedule_apply
from yuubot.upgrade.git import check_git_update
from yuubot.upgrade.install import detect_install


def _write_install_assets(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "deps.yaml").write_text("steps:\n  - id: python\n    cwd: .\n    run: true\n", encoding="utf-8")
    installer = scripts / "install-deps.sh"
    installer.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    installer.chmod(0o755)


def test_detect_install_requires_git(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_install_assets(root)
    supported, kind, message = detect_install(root)
    assert supported is False
    assert kind == "package"
    assert "git checkout" in message


def test_detect_install_accepts_git_checkout(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    _write_install_assets(root)
    supported, kind, message = detect_install(root)
    assert supported is True
    assert kind == "git_source"
    assert message == ""


@pytest.mark.asyncio
async def test_check_git_update_reports_available(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    async def fake_run_git(_root: Path, *args: str) -> tuple[int, str, str]:
        if args[:2] == ("fetch", "origin"):
            return 0, "", ""
        if args[:2] == ("rev-parse", "HEAD"):
            return 0, "aaa", ""
        if args[:2] == ("rev-parse", "@{u}"):
            return 0, "origin/main", ""
        if args[:2] == ("rev-parse", "origin/main"):
            return 0, "bbb", ""
        if args[0] == "rev-list":
            return 0, "2", ""
        raise AssertionError(args)

    monkeypatch.setattr("yuubot.upgrade.git._run_git", fake_run_git)
    current, remote, available, message = await check_git_update(root)
    assert current == "aaa"
    assert remote == "bbb"
    assert available is True
    assert message == ""


def test_build_apply_script_uses_install_deps(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_install_assets(root)
    script = build_apply_script(
        root,
        tmp_path / "config.yaml",
        tmp_path / "data",
        8765,
        tmp_path / "update.log",
        True,
    )
    assert "deploy-server.sh" in script
    assert "--upgrade-only" in script
    assert "YUUBOT_NONINTERACTIVE=1" in script
    assert f"--config {tmp_path / 'config.yaml'}" in script
    assert f"--data-dir {tmp_path / 'data'}" in script
    assert "--skip-web-build" in script


def test_deploy_caddy_public_vhost_allows_mcp_oauth_callback() -> None:
    content = Path("scripts/deploy-server.sh").read_text(encoding="utf-8")
    oauth_index = content.index("@mcp_oauth_callback path_regexp ^/api/mcp-oauth/[^/]+/callback$")
    api_404_index = content.index("respond /api/* 404")
    upgrade_index = content.index("    migrate_caddy_public_oauth_callback")
    restart_index = content.index('log_step "Stopping yuubot.service before migrations"')
    assert oauth_index < api_404_index
    assert upgrade_index < restart_index
    assert "reverse_proxy @mcp_oauth_callback 127.0.0.1:$YUUBOT_PUBLIC_PORT" in content


def test_deploy_uses_builtin_admin_auth_not_caddy_basic_auth() -> None:
    content = Path("scripts/deploy-server.sh").read_text(encoding="utf-8")
    caddy_template = content[
        content.index("write_caddy_site() {") : content.index("install_app_dependencies() {")
    ]
    assert "mode: builtin" in content
    assert 'auth["mode"] = "builtin"' in content
    assert "username: $bootstrap_admin_username" in content
    assert 'builtin["username"] = admin_username' in content
    assert 'read -r -p "Yuubot admin username [admin]: " username' in content
    assert "caddy hash-password" not in content
    assert "header_up X-Forwarded-User {http.auth.user.id}" not in caddy_template
    assert "basic_auth {" not in caddy_template
    assert "write_caddy_site \"$domain\" \"$public_domain\"" in content


def test_apply_update_rejects_unsupported_install(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(ValueError, match="git checkout"):
        apply_update(
            tmp_path / "config.yaml",
            tmp_path / "data",
            8765,
            root=root,
        )


@pytest.mark.asyncio
async def test_check_update_git_failure_keeps_install_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    _write_install_assets(root)

    async def fake_check_git_update(_root: Path) -> tuple[str | None, str | None, bool, str]:
        return None, None, False, "git fetch origin failed"

    monkeypatch.setattr("yuubot.upgrade.check_git_update", fake_check_git_update)
    status = await check_update(root)
    assert status.supported is True
    assert status.install_kind == "git_source"
    assert status.update_available is False
    assert status.message == "git fetch origin failed"


@pytest.mark.asyncio
async def test_check_update_supported_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    _write_install_assets(root)

    async def fake_check_git_update(_root: Path) -> tuple[str | None, str | None, bool, str]:
        return "aaa", "bbb", True, ""

    monkeypatch.setattr("yuubot.upgrade.check_git_update", fake_check_git_update)
    status = await check_update(root)
    assert status.supported is True
    assert status.install_kind == "git_source"
    assert status.update_available is True
    assert status.current_commit == "aaa"
    assert status.remote_commit == "bbb"


@pytest.mark.asyncio
async def test_update_status_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await boot_app(tmp_path / "data")
    app.config_path = tmp_path / "config.yaml"

    async def fake_check_update(_root: Path | None = None):
        from yuubot.upgrade.types import UpdateStatus

        return UpdateStatus(
            True,
            "git_source",
            "0.1.0",
            "abc",
            "def",
            True,
            "update available",
        )

    monkeypatch.setattr("yuubot.web.routes.update.check_update", fake_check_update)
    async with running_server(app) as server:
        payload = await http_json("GET", f"{base_url(server)}/api/admin/update/status")
    assert payload["supported"] is True
    assert payload["update_available"] is True


@pytest.mark.asyncio
async def test_update_apply_route_schedules_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = await boot_app(tmp_path / "data")
    app.config_path = tmp_path / "config.yaml"
    shutdown_calls: list[bool] = []

    def fake_schedule_apply(**kwargs):
        shutdown = kwargs.get("on_shutdown")
        if shutdown is not None:
            shutdown_calls.append(True)
        from yuubot.upgrade.types import UpdateApplyResult

        return UpdateApplyResult("scheduled", str(tmp_path / "update.log"))

    monkeypatch.setattr("yuubot.web.routes.update.apply_update", lambda *_, **kwargs: fake_schedule_apply(**kwargs))
    async with running_server(app) as server:
        payload = await http_json("POST", f"{base_url(server)}/api/admin/update/apply")
    assert payload["status"] == "scheduled"
    assert shutdown_calls == [True]


@pytest.mark.asyncio
async def test_schedule_apply_writes_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    _write_install_assets(root)
    launched: list[list[str]] = []

    class FakeProcess:
        pid = 12345

    def fake_popen(args: list[str], **kwargs: object) -> FakeProcess:
        launched.append(args)
        return FakeProcess()

    monkeypatch.setattr("yuubot.upgrade.apply.subprocess.Popen", fake_popen)
    result = schedule_apply(
        root=root,
        config_path=tmp_path / "config.yaml",
        data_dir=tmp_path / "data",
        port=8765,
        skip_web_build=False,
    )
    assert result.status == "scheduled"
    assert launched
    script_path = Path(launched[0][1])
    assert script_path.is_file()
    content = script_path.read_text(encoding="utf-8")
    assert "deploy-server.sh" in content
    assert "--upgrade-only" in content
