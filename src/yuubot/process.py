"""Process-level infrastructure primitives."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yuutrace
from starlette.types import ASGIApp

from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, TraceConfig
from yuubot.bootstrap.layout import DataLayout
from yuubot.resources.root import Resources
from yuubot.resources.secrets import SecretCodec
from yuubot.resources.store.resource import Store


class ASGIServer(Protocol):
    """Blocking ASGI server process boundary."""

    name: str

    async def serve(self, app: ASGIApp, *, host: str, port: int) -> None: ...


@dataclass
class UvicornServer:
    """Production HTTP/WebSocket serving is delegated to uvicorn."""

    name: str = "uvicorn"
    log_level: str = "info"

    async def serve(self, app: ASGIApp, *, host: str, port: int) -> None:
        import uvicorn

        server_config = uvicorn.Config(
            app,
            host=host,
            port=port,
            lifespan="on",
            log_level=self.log_level,
        )
        await uvicorn.Server(server_config).serve()


class Service(Protocol):
    """A long-lived process component owned by the daemon."""

    name: str

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


@dataclass
class ServiceHost:
    """Start services in order and stop them in reverse order."""

    services: tuple[Service, ...]
    _started: list[Service] = field(default_factory=list, init=False, repr=False)

    @classmethod
    def from_iterable(cls, services: Iterable[Service]) -> "ServiceHost":
        return cls(tuple(services))

    @property
    def started(self) -> bool:
        return bool(self._started)

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("service host is already started")
        try:
            for service in self.services:
                await service.start()
                self._started.append(service)
        except Exception:
            await self.stop()
            raise

    async def stop(self) -> None:
        while self._started:
            service = self._started.pop()
            await service.stop()


@dataclass
class TraceService:
    """Trace collector/UI wiring owned by bootstrap config."""

    config: TraceConfig
    db_path: str
    name: str = "trace"
    _collector_thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _ui_thread: threading.Thread | None = field(default=None, init=False, repr=False)

    async def start(self) -> None:
        if not self.config.enabled:
            yuutrace.disable()
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        endpoint = f"http://{self.config.collector_host}:{self.config.collector_port}/v1/traces"
        yuutrace.init(endpoint=endpoint, service_name="yuubot")

        from yuutrace.cli.server import run_server
        from yuutrace.cli.ui import run_ui

        self._collector_thread = threading.Thread(
            target=run_server,
            kwargs={
                "db_path": self.db_path,
                "host": self.config.collector_host,
                "port": self.config.collector_port,
            },
            daemon=True,
        )
        self._collector_thread.start()
        self._ui_thread = threading.Thread(
            target=run_ui,
            kwargs={
                "db_path": self.db_path,
                "host": self.config.ui_host,
                "port": self.config.ui_port,
            },
            daemon=True,
        )
        self._ui_thread.start()

    async def stop(self) -> None:
        from yuutrace.cli.server import shutdown_server
        from yuutrace.cli.ui import shutdown_ui

        shutdown_server()
        shutdown_ui()

        for t in (self._collector_thread, self._ui_thread):
            if t is not None and t.is_alive():
                t.join(timeout=5.0)

    @property
    def status(self) -> str:
        if not self.config.enabled:
            return "disabled"
        if self._collector_thread is not None and self._collector_thread.is_alive():
            return "running"
        return "starting"


async def open_store(config: DatabaseConfig, *, layout: DataLayout) -> Store:
    path = config.path or str(layout.db_path)
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    store = await Store.open(path)
    await store.migrate()
    return store


async def open_resources(
    config: BootstrapConfig,
    create_store: Callable[[DatabaseConfig], Awaitable[Store]] | None = None,
) -> Resources:
    layout = DataLayout.from_path(config.paths.data_dir)
    if create_store is not None:
        store = await create_store(config.database)
    else:
        store = await open_store(config.database, layout=layout)
    return await Resources.from_store(
        store, secret_codec=SecretCodec(config.secrets.master_key)
    )
