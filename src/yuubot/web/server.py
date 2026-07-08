"""ASGI server lifecycle: bind socket, publish run state, serve, clean shutdown."""

import asyncio
import contextlib
import socket
from pathlib import Path

import uvicorn
from attrs import define, field

from ..app import DEFAULT_HOST, DEFAULT_PORT, Yuubot
from ..app.deployment import DeploymentConfig, ServerConfig, load_listener_deployments, origin_for
from ..runtime.logging_config import configure_logging
from .api import create_asgi_app
from .auth import SessionStore
from .run_state import clear as clear_run_state
from .run_state import write as write_run_state
from .types import AppLoader


@define
class UvicornServer:
    app: Yuubot
    host: str
    server_port: int
    deployment: DeploymentConfig
    _socket: socket.socket = field(alias="socket")
    development: bool = False
    _sessions: SessionStore = field(factory=SessionStore, alias="sessions")
    _manage_lifecycle: bool = True
    _write_run_state: bool = True
    _server: uvicorn.Server = field(init=False)

    def __attrs_post_init__(self) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                create_asgi_app(
                    self.app,
                    self.deployment,
                    self.shutdown,
                    self._sessions,
                ),
                host=self.host,
                port=self.server_port,
                log_level="critical",
                lifespan="off",
                access_log=False,
            )
        )

    @classmethod
    def create(
        cls,
        app: Yuubot,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        deployment: DeploymentConfig | None = None,
        development: bool = False,
        sessions: SessionStore | None = None,
    ) -> "UvicornServer":
        bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bound.bind((host, port))
        bound.listen()
        server_port = int(bound.getsockname()[1])
        app.server_host = host
        app.server_port = server_port
        if deployment is None:
            origin = origin_for(host, server_port)
            resolved = DeploymentConfig(
                ServerConfig(host, server_port),
                "local_dev",
                admin_url_base=origin,
                public_url_base=origin,
            )
        else:
            admin_url_base = deployment.admin_url_base or origin_for(host, server_port)
            public_url_base = deployment.public_url_base or admin_url_base
            resolved = DeploymentConfig(
                ServerConfig(host, server_port),
                deployment.surface,
                deployment.public_server,
                deployment.local_admin_server,
                deployment.trusted_admin_server,
                admin_url_base,
                public_url_base,
                deployment.trusted_proxies,
                deployment.admin_auth,
            )
        return cls(
            app=app,
            host=host,
            server_port=server_port,
            deployment=resolved,
            socket=bound,
            development=development,
            sessions=sessions or SessionStore(),
        )

    async def serve(self) -> None:
        try:
            if self._manage_lifecycle:
                self.app.runtime.development = self.development
                log_path = configure_logging(
                    self.app.runtime.logs_dir,
                    self.development,
                    self.app.runtime.resources_config.logs.max_bytes,
                    self.app.runtime.resources_config.logs.backup_count,
                )
                print(f"Logs: {log_path}", flush=True)
                await self._sessions.start_background_cleanup()
                await self.app.startup()
            if self._write_run_state:
                write_run_state(self.app.runtime.data_dir, self.host, self.server_port)
            await self._server.serve(sockets=[self._socket])
        finally:
            if self._write_run_state:
                clear_run_state(self.app.runtime.data_dir)
            if self._manage_lifecycle:
                try:
                    await self.app.shutdown()
                finally:
                    await self._sessions.stop_background_cleanup()

    def serve_forever(self) -> None:
        asyncio.run(self.serve())

    def shutdown(self) -> None:
        self._server.should_exit = True


@define
class MultiUvicornServer:
    app: Yuubot
    servers: tuple[UvicornServer, ...]
    development: bool = False

    @classmethod
    def create(
        cls,
        app: Yuubot,
        deployments: tuple[DeploymentConfig, ...],
        development: bool = False,
    ) -> "MultiUvicornServer":
        sessions = SessionStore()
        servers: list[UvicornServer] = []
        for deployment in deployments:
            server = UvicornServer.create(
                app,
                host=deployment.server.host,
                port=deployment.server.port,
                deployment=deployment,
                development=development,
                sessions=sessions,
            )
            server._manage_lifecycle = False
            server._write_run_state = deployment.surface in {"local_admin", "local_dev"}
            servers.append(server)
        return cls(app=app, servers=tuple(servers), development=development)

    async def serve(self) -> None:
        self.app.runtime.development = self.development
        log_path = configure_logging(
            self.app.runtime.logs_dir,
            self.development,
            self.app.runtime.resources_config.logs.max_bytes,
            self.app.runtime.resources_config.logs.backup_count,
        )
        print(f"Logs: {log_path}", flush=True)
        for server in self.servers:
            print(f"{server.deployment.surface}: http://{server.host}:{server.server_port}", flush=True)
        sessions = self.servers[0]._sessions if self.servers else None
        if sessions is not None:
            await sessions.start_background_cleanup()
        tasks: list[asyncio.Task[None]] = []
        try:
            await self.app.startup()
            tasks = [asyncio.create_task(server.serve()) for server in self.servers]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
            for server in self.servers:
                server.shutdown()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.app.shutdown()
            finally:
                if sessions is not None:
                    await sessions.stop_background_cleanup()

    def shutdown(self) -> None:
        for server in self.servers:
            server.shutdown()


def make_server(
    app: Yuubot,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    deployment: DeploymentConfig | None = None,
    development: bool = False,
) -> UvicornServer:
    return UvicornServer.create(app, host=host, port=port, deployment=deployment, development=development)


async def serve_async(
    config: str | Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    app_loader: AppLoader = Yuubot.from_config_file,
) -> None:
    config_path = Path(config)
    app = await app_loader(config_path)
    deployments = load_listener_deployments(str(config_path), host, port)
    await MultiUvicornServer.create(app, deployments=deployments).serve()


def serve(
    config: str | Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    app_loader: AppLoader = Yuubot.from_config_file,
) -> None:
    asyncio.run(serve_async(config, host, port, app_loader))
