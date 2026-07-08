import asyncio
import contextlib
import datetime as dt
import errno
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

import aiosqlite
import msgspec
import yaml

from ..app import load_process_config
from ..db import Database, auto_legacy_db, inspect_legacy, migrate_legacy, migration_files, pending_versions
from ..db.migrate import current_version
from ..upgrade import apply_update, check_update, project_root
from ..web.server import UvicornServer, make_server
from ..web.run_state import ServerRunState
from ..web.run_state import read as read_run_state
from ..web.types import AppLoader
from .io import admin_post, bootstrap_snapshot, emit, error_payload, not_running_payload

SYSTEMD_UNITS = ("yuubot.service", "yuubot-daemon.service", "yuubot-admin.service")
SYSTEMD_UNIT_PATHS = tuple(Path("/etc/systemd/system") / unit for unit in SYSTEMD_UNITS)


async def chat(app_loader: AppLoader, config: Path, actor: str, message: str, conversation_id: str | None) -> None:
    app = await app_loader(config)
    try:
        async for event in app.chat_stream(actor, message, conversation_id):
            print(msgspec.json.encode(event).decode(), flush=True)
    finally:
        await app.shutdown()


async def deploy(app_loader: AppLoader, config: Path, dry_run: bool, json_output: bool) -> int:
    try:
        app = await app_loader(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4
    paths = {
        "data_dir": app.runtime.data_dir,
        "workspace_dir": app.runtime.workspace_dir,
        "logs_dir": app.runtime.logs_dir,
        "db_dir": app.runtime.db_dir,
    }
    payload: dict[str, object] = {
        "ok": True,
        "config": str(config),
        "dry_run": dry_run,
        "paths": {key: str(value) for key, value in paths.items()},
    }
    emit(payload, json_output=json_output)
    await app.shutdown()
    return 0


def uninstall(config: Path, remove_data_files: bool, json_output: bool) -> int:
    try:
        data_dir = config_data_dir(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4

    operations: list[dict[str, object]] = []
    for args in (
        ("systemctl", "disable", "--now", "yuubot.service"),
        ("systemctl", "disable", "--now", "yuubot-daemon.service", "yuubot-admin.service"),
        ("rm", "-f", *(str(path) for path in SYSTEMD_UNIT_PATHS)),
        ("systemctl", "daemon-reload"),
        ("rm", "-f", str(caddy_site_file())),
        ("systemctl", "reload", "caddy"),
    ):
        operations.append(run_system_command(args))

    data_removed = False
    if remove_data_files:
        try:
            remove_data_dir(data_dir)
        except Exception as exc:
            emit(error_payload(exc), json_output=json_output)
            return 4
        data_removed = True

    payload: dict[str, object] = {
        "ok": True,
        "config": str(config),
        "data_dir": str(data_dir),
        "data_removed": data_removed,
        "service_units": [str(path) for path in SYSTEMD_UNIT_PATHS],
        "caddy_site_file": str(caddy_site_file()),
        "operations": operations,
    }
    emit(payload, json_output=json_output)
    return 0


def export_data_dir(config: Path, output_path: Path | None, json_output: bool) -> int:
    try:
        data_dir = config_data_dir(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4
    if read_run_state(data_dir) is not None:
        emit({"ok": False, "error": {"code": "service_running", "message": "stop yuubot before exporting data_dir"}}, json_output=json_output)
        return 5
    if not data_dir.is_dir():
        emit(
            {
                "ok": False,
                "error": {"code": "data_dir_missing", "message": f"data_dir does not exist: {data_dir}"},
            },
            json_output=json_output,
        )
        return 4

    archive = resolve_export_path(output_path)
    if archive.is_relative_to(data_dir.resolve()):
        emit(
            {
                "ok": False,
                "error": {"code": "invalid_export_path", "message": "export path must be outside data_dir"},
            },
            json_output=json_output,
        )
        return 4
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp_archive = archive.with_name(f".{archive.name}.tmp")
    if tmp_archive.exists():
        tmp_archive.unlink()
    try:
        with tarfile.open(tmp_archive, "w:gz") as tar:
            tar.add(data_dir, arcname=data_dir.name)
        tmp_archive.replace(archive)
    finally:
        if tmp_archive.exists():
            tmp_archive.unlink()

    payload: dict[str, object] = {
        "ok": True,
        "config": str(config),
        "data_dir": str(data_dir),
        "path": str(archive),
        "size_bytes": archive.stat().st_size,
    }
    emit(payload, json_output=json_output)
    return 0


async def check(app_loader: AppLoader, config: Path, json_output: bool) -> int:
    try:
        app = await app_loader(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4
    payload: dict[str, object] = {
        "ok": True,
        "config": str(config),
        "data_dir": str(app.runtime.data_dir),
        "database": str(app.runtime.state.path),
        "workspace_dir": str(app.runtime.workspace_dir),
        "schema_version": await app.runtime.state.schema_version(),
        "providers": len(app.provider_records),
        "actors": len(app.actor_records),
        "integrations": len(app.integration_records),
    }
    emit(payload, json_output=json_output)
    await app.shutdown()
    return 0


async def dev(
    app_loader: AppLoader,
    config: Path,
    host: str,
    port: int,
    web_host: str,
    web_port: int,
) -> int:
    pnpm = shutil.which("pnpm")
    if pnpm is None:
        print("pnpm is required to run the web dev server. Install pnpm, then run `cd web && pnpm install`.", file=sys.stderr)
        return 4
    try:
        web_dir = resolve_web_dir(config)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    server: UvicornServer | None = None
    backend_task: asyncio.Task[None] | None = None
    frontend: asyncio.subprocess.Process | None = None
    frontend_wait: asyncio.Task[int] | None = None
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    registered_signals: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown_event.set)
            registered_signals.append(sig)

    try:
        try:
            app = await app_loader(config)
            server = make_server(app, host, port, development=True)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                print(port_busy_hint(host, port, config), file=sys.stderr)
            print(f"failed to start backend: {exc}", file=sys.stderr)
            return 4

        backend_origin = f"http://{host}:{server.server_port}"
        web_origin = f"http://{web_host}:{web_port}"
        env = os.environ.copy()
        env["YUUBOT_API_ORIGIN"] = backend_origin

        print("Starting yuubot dev:", flush=True)
        print(f"  backend  {backend_origin}", flush=True)
        print(f"  frontend {web_origin}", flush=True)
        print("Press Ctrl-C to stop.", flush=True)

        backend_task = asyncio.create_task(server.serve())
        await wait_for_backend_start(server, backend_task)
        frontend = await asyncio.create_subprocess_exec(
            pnpm,
            "exec",
            "vite",
            "--host",
            web_host,
            "--port",
            str(web_port),
            cwd=web_dir,
            env=env,
            start_new_session=True,
        )
        frontend_wait = asyncio.create_task(frontend.wait())
        shutdown_wait = asyncio.create_task(shutdown_event.wait())
        done, pending = await asyncio.wait(
            {backend_task, frontend_wait, shutdown_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*pending, return_exceptions=True)

        if backend_task in done:
            try:
                await backend_task
            except Exception as exc:
                print(f"backend exited: {exc}", file=sys.stderr)
                return 1
            return 0
        if frontend is not None and frontend.returncode is not None:
            return frontend.returncode
        return 0 if shutdown_event.is_set() else 1
    finally:
        for sig in registered_signals:
            with contextlib.suppress(NotImplementedError):
                loop.remove_signal_handler(sig)
        if server is not None:
            server.shutdown()
        if frontend is not None:
            await terminate_process(frontend, True)
        if frontend_wait is not None and not frontend_wait.done():
            frontend_wait.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await frontend_wait
        if backend_task is not None:
            await wait_for_backend_stop(backend_task)


def port_busy_hint(host: str, port: int, config: Path) -> str:
    state = run_state_for_config(config)
    if state is not None and state.host == host and state.port == port:
        try:
            os.kill(state.pid, 0)
        except ProcessLookupError:
            return f"port {port} is in use (stale run state pid {state.pid})"
        return f"port {port} is in use by pid {state.pid}; stop it with: kill {state.pid}"
    return f"port {port} is in use"


def resolve_web_dir(config: Path) -> Path:
    candidates = [config.resolve().parent / "web", Path.cwd() / "web"]
    for candidate in candidates:
        if (candidate / "package.json").is_file():
            return candidate
    raise FileNotFoundError("could not find web/package.json next to the config file or current working directory")


async def wait_for_backend_start(server: UvicornServer, backend_task: asyncio.Task[None]) -> None:
    for _ in range(200):
        if backend_task.done():
            await backend_task
            return
        if server._server.started:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("backend did not start within 10 seconds")


async def terminate_process(process: asyncio.subprocess.Process, process_group: bool = False) -> None:
    if process.returncode is not None:
        return
    if process_group and process.pid:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    else:
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:
        if process_group and process.pid:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        await process.wait()


async def wait_for_backend_stop(backend_task: asyncio.Task[None]) -> None:
    if not backend_task.done():
        try:
            await asyncio.wait_for(backend_task, timeout=5)
        except TimeoutError:
            backend_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await backend_task


def config_data_dir(config: Path) -> Path:
    return Path(load_process_config(config).data_dir)


def resolve_export_path(output_path: Path | None) -> Path:
    filename = f"yuubot-data-{dt.datetime.now(dt.UTC).strftime('%Y%m%d-%H%M%S')}.tar.gz"
    if output_path is None:
        return (Path.cwd() / filename).resolve()
    expanded = Path(os.path.expanduser(os.path.expandvars(str(output_path))))
    if expanded.exists() and expanded.is_dir():
        return (expanded / filename).resolve()
    if str(output_path).endswith(os.sep):
        return (expanded / filename).resolve()
    if expanded.name in {"", "."}:
        return (expanded / filename).resolve()
    return expanded.resolve()


def caddy_site_file() -> Path:
    raw_site = os.environ.get("YUUBOT_CADDY_SITE_FILE")
    if raw_site:
        return Path(raw_site)
    conf_dir = Path(os.environ.get("YUUBOT_CADDY_CONF_DIR", "/etc/caddy/conf.d"))
    return conf_dir / "yuubot.caddy"


def run_system_command(args: tuple[str, ...]) -> dict[str, object]:
    command = sudo_args(args)
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return {
            "command": list(args),
            "ok": False,
            "returncode": 127,
            "stderr": str(exc),
        }
    return {
        "command": command,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stderr": result.stderr.strip(),
    }


def sudo_args(args: tuple[str, ...]) -> list[str]:
    if os.geteuid() == 0:
        return list(args)
    if args[0] == "systemctl" or any(path.startswith("/etc/") for path in args[1:]):
        return ["sudo", *args]
    return list(args)


def remove_data_dir(data_dir: Path) -> None:
    resolved = data_dir.resolve()
    if resolved in {Path("/"), Path.home().resolve()}:
        raise ValueError(f"refusing to remove unsafe data_dir: {resolved}")
    if not resolved.exists():
        return
    if not resolved.is_dir():
        raise ValueError(f"data_dir is not a directory: {resolved}")
    shutil.rmtree(resolved)


def old_config_data(config: Path | None) -> dict[str, object]:
    if config is None:
        return {}
    with config.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        return {}
    paths = data.get("paths") if isinstance(data.get("paths"), dict) else {}
    database = data.get("database") if isinstance(data.get("database"), dict) else {}
    secrets = data.get("secrets") if isinstance(data.get("secrets"), dict) else {}
    return {
        "data_dir": str(paths.get("data_dir") or "") if isinstance(paths, dict) else "",
        "database_path": str(database.get("path") or "") if isinstance(database, dict) else "",
        "master_key": str(secrets.get("master_key") or "") if isinstance(secrets, dict) else "",
    }


def legacy_db_from_old_config(info: dict[str, object]) -> Path | None:
    database_path = str(info.get("database_path") or "")
    if database_path and database_path != ":memory:":
        return Path(database_path).expanduser()
    data_dir = str(info.get("data_dir") or "")
    if data_dir:
        return Path(data_dir).expanduser() / "yuubot" / "yuubot.db"
    return None


async def migrate_command(
    app_loader: AppLoader,
    config: Path,
    legacy_db: Path | None,
    old_config: Path | None,
    force_import: bool,
    dry_run: bool,
    json_output: bool,
) -> int:
    try:
        data_dir = config_data_dir(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4
    payload: dict[str, object]

    if dry_run:
        db_path = data_dir / "db" / "yuubot.db"
        if db_path.exists():
            db = await Database.open(data_dir / "db", migrate_on_open=False)
            try:
                schema_version = await current_version(db)
                pending = await pending_versions(db)
                legacy = await migrate_legacy(
                    db,
                    data_dir,
                    legacy_db,
                    old_config,
                    True,
                    force_import,
                )
            finally:
                await db.close()
        else:
            schema_version = 0
            pending = [version for version, _path in migration_files()]
            old_config_info = old_config_data(old_config)
            legacy = await inspect_legacy(
                None,
                data_dir,
                legacy_db or legacy_db_from_old_config(old_config_info) or auto_legacy_db(data_dir),
                old_config_info,
            )
        payload = {
            "ok": True,
            "config": str(config),
            "dry_run": True,
            "schema_version": schema_version,
            "database": str(db_path),
            "pending_migrations": pending,
            "legacy": legacy,
        }
        emit(payload, json_output=json_output)
        return 0

    if legacy_db is not None or old_config is not None or (data_dir / "yuubot" / "yuubot.db").exists():
        try:
            db = await Database.open(data_dir / "db")
            try:
                legacy = await migrate_legacy(
                    db,
                    data_dir,
                    legacy_db,
                    old_config,
                    False,
                    force_import,
                )
                payload = {
                    "ok": True,
                    "config": str(config),
                    "dry_run": False,
                    "schema_version": await current_version(db),
                    "database": str(db.path),
                    "pending_migrations": await pending_versions(db),
                    "legacy": legacy,
                }
            finally:
                await db.close()
        except Exception as exc:
            emit(error_payload(exc), json_output=json_output)
            return 4
        emit(payload, json_output=json_output)
        return 0

    try:
        app = await app_loader(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4
    payload = {
        "ok": True,
        "config": str(config),
        "dry_run": False,
        "schema_version": await app.runtime.state.schema_version(),
        "database": str(app.runtime.state.path),
        "pending_migrations": await pending_versions(app.runtime.db),
    }
    emit(payload, json_output=json_output)
    await app.shutdown()
    return 0


def status(config: Path, json_output: bool) -> int:
    run_state = run_state_for_config(config)
    if run_state is None:
        emit(not_running_payload(), json_output=json_output)
        return 3
    try:
        snapshot = bootstrap_snapshot(run_state.host, run_state.port)
    except (OSError, urllib.error.URLError, msgspec.DecodeError, TypeError) as exc:
        emit(error_payload(exc), json_output=json_output)
        return 3
    payload: dict[str, object] = {
        "ok": True,
        "config": str(config),
        "server": {"host": run_state.host, "port": run_state.port, "pid": run_state.pid},
        "bootstrap": msgspec.to_builtins(snapshot),
    }
    emit(payload, json_output=json_output)
    return 0


def interrupt(config: Path, conversation_id: str | None, interrupt_all: bool, json_output: bool) -> int:
    run_state = run_state_for_config(config)
    if run_state is None:
        emit(not_running_payload(), json_output=json_output)
        return 3
    body: dict[str, object] = {"all": True} if interrupt_all else {"conversation_id": conversation_id}
    try:
        payload = admin_post(run_state.host, run_state.port, "/api/admin/interrupt", body)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        emit(error_payload(exc), json_output=json_output)
        return 3
    emit({"ok": True, **payload}, json_output=json_output)
    return 0


def stop(config: Path, json_output: bool) -> int:
    run_state = run_state_for_config(config)
    if run_state is None:
        emit(not_running_payload(), json_output=json_output)
        return 3
    try:
        payload = admin_post(run_state.host, run_state.port, "/api/admin/shutdown", {})
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        emit(error_payload(exc), json_output=json_output)
        return 3
    emit({"ok": True, **payload}, json_output=json_output)
    return 0


async def db_info(config: Path, json_output: bool) -> int:
    try:
        data_dir = config_data_dir(config)
    except Exception as exc:
        emit(error_payload(exc), json_output=json_output)
        return 4
    if read_run_state(data_dir) is not None:
        emit({"ok": False, "error": {"code": "database_locked", "message": "yuubot service is running"}}, json_output=json_output)
        return 5
    path = data_dir / "db" / "yuubot.db"
    schema_version = 0
    if path.exists():
        db = await Database.open(data_dir / "db", migrate_on_open=False)
        try:
            schema_version = await current_version(db)
        finally:
            await db.close()
    tables: dict[str, int] = await table_counts(path) if path.exists() else {}
    payload: dict[str, object] = {
        "ok": True,
        "config": str(config),
        "data_dir": str(data_dir),
        "db_dir": str(data_dir / "db"),
        "logs_dir": str(data_dir / "logs"),
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "schema_version": schema_version,
        "tables": tables,
    }
    if not json_output:
        print_db_info(payload)
        return 0
    emit(payload, json_output=json_output)
    return 0


def print_db_info(payload: dict[str, object]) -> None:
    print("ok")
    for key in ("config", "data_dir", "db_dir", "logs_dir", "path", "exists", "size_bytes", "schema_version"):
        print(f"{key}: {payload[key]}")
    tables = payload["tables"]
    if isinstance(tables, dict) and tables:
        print("tables:")
        for name, count in tables.items():
            print(f"  {name}: {count}")
    else:
        print("tables: none")


def run_state_for_config(config: Path) -> ServerRunState | None:
    try:
        data_dir = config_data_dir(config)
    except Exception:
        return None
    return read_run_state(data_dir)


async def table_counts(path: Path) -> dict[str, int]:
    async with aiosqlite.connect(path) as db:
        cursor = await db.execute("select name from sqlite_master where type = 'table' and name not like 'sqlite_%' order by name")
        rows = await cursor.fetchall()
        counts: dict[str, int] = {}
        for (name,) in rows:
            count_cursor = await db.execute(f'select count(*) from "{name}"')
            row = await count_cursor.fetchone()
            assert row is not None
            counts[str(name)] = int(row[0])
        return counts


def version() -> str:
    try:
        return importlib.metadata.version("yuubot")
    except importlib.metadata.PackageNotFoundError:
        return "0.1.0"


async def upgrade_check(json_output: bool) -> int:
    status = await check_update(project_root())
    emit(msgspec.to_builtins(status), json_output=json_output)
    return 0


async def upgrade_apply(
    app_loader: AppLoader,
    config: Path,
    port: int,
    json_output: bool,
    skip_web_build: bool,
) -> int:
    initial_run_state = run_state_for_config(config)
    root = project_root()
    try:
        apply_update(
            config,
            config_data_dir(config),
            port,
            skip_web_build,
            root=root,
        )
    except ValueError as exc:
        emit(error_payload(exc), json_output=json_output)
        return 1

    # The scheduled script restarts the service; wait for a fresh run state.
    for _ in range(3000):
        run_state = run_state_for_config(config)
        if run_state is not None and (initial_run_state is None or run_state.pid != initial_run_state.pid):
            emit(
                {
                    "ok": True,
                    "config": str(config),
                    "server": {"host": run_state.host, "port": run_state.port, "pid": run_state.pid},
                    "status": "scheduled",
                },
                json_output=json_output,
            )
            return 0
        await asyncio.sleep(0.1)

    emit({"ok": False, "error": "update was scheduled but the server did not start"}, json_output=json_output)
    return 1
