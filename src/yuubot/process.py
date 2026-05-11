"""Process-level infrastructure primitives."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from starlette.types import ASGIApp

from yuubot.bootstrap.config import BootstrapConfig, DatabaseConfig, TraceConfig
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
    name: str = "trace"

    async def start(self) -> None:
        return

    async def stop(self) -> None:
        return


async def open_store(config: DatabaseConfig) -> Store:
    store = await Store.open(config.path)
    await store.migrate()
    return store


async def open_resources(
    config: BootstrapConfig,
    create_store: Callable[[DatabaseConfig], Awaitable[Store]] | None = None,
) -> Resources:
    if create_store is not None:
        store = await create_store(config.database)
    else:
        store = await open_store(config.database)
    return await Resources.from_store(
        store, secret_codec=SecretCodec(config.secrets.master_key)
    )
