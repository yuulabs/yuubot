"""ASGI server lifecycle: bind socket, publish run state, serve, clean shutdown."""

import asyncio
import socket
from pathlib import Path

import uvicorn
from attrs import define, field

from ..app import DEFAULT_HOST, DEFAULT_PORT, Yuubot
from ..app.deployment import DeploymentConfig, ServerConfig, load_deployment_config, origin_for
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
    _socket: socket.socket
    development: bool = False
    _sessions: SessionStore = field(factory=SessionStore)
    _server: uvicorn.Server = field(init=False)

    @_server.default
    def _make_server(self) -> uvicorn.Server:
        return uvicorn.Server(
            uvicorn.Config(
                create_asgi_app(
                    self.app,
                    deployment=self.deployment,
                    on_shutdown=self.shutdown,
                    sessions=self._sessions,
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
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        deployment: DeploymentConfig | None = None,
        development: bool = False,
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
                server=ServerConfig(host=host, port=server_port),
                admin_url_base=origin,
                public_url_base=origin,
            )
        else:
            resolved = DeploymentConfig(
                server=ServerConfig(host=host, port=server_port),
                admin_url_base=deployment.admin_url_base,
                public_url_base=deployment.public_url_base,
                trusted_proxies=deployment.trusted_proxies,
                admin_auth=deployment.admin_auth,
            )
        return cls(
            app=app,
            host=host,
            server_port=server_port,
            deployment=resolved,
            socket=bound,
            development=development,
        )

    async def serve(self) -> None:
        self.app.runtime.development = self.development
        log_path = configure_logging(self.app.runtime.logs_dir, development=self.development)
        print(f"Logs: {log_path}", flush=True)
        await self.app.startup()
        write_run_state(self.app.runtime.data_dir, self.host, self.server_port)
        try:
            await self._server.serve(sockets=[self._socket])
        finally:
            clear_run_state(self.app.runtime.data_dir)
            await self.app.shutdown()

    def serve_forever(self) -> None:
        asyncio.run(self.serve())

    def shutdown(self) -> None:
        self._server.should_exit = True


def make_server(
    app: Yuubot,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    deployment: DeploymentConfig | None = None,
    development: bool = False,
) -> UvicornServer:
    return UvicornServer.create(app, host=host, port=port, deployment=deployment, development=development)


async def serve_async(
    config: str | Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    app_loader: AppLoader = Yuubot.from_config_file,
) -> None:
    config_path = Path(config)
    app = await app_loader(config_path)
    deployment = load_deployment_config(str(config_path), host=host, port=port)
    await make_server(app, host=host, port=port, deployment=deployment).serve()


def serve(
    config: str | Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    app_loader: AppLoader = Yuubot.from_config_file,
) -> None:
    asyncio.run(serve_async(config, host=host, port=port, app_loader=app_loader))
