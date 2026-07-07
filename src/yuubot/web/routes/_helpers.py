from collections.abc import Callable
from pathlib import Path

from fastapi import Request

from ...app import Yuubot
from ..client_ip import client_ip_from_scope, is_loopback


def react_dist_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "web" / "dist"


async def route_exists(app: Yuubot, route_id: str) -> bool:
    return any(record.id == route_id for record in await app.list_routes())


def make_client_is_loopback(trusted: frozenset[str]) -> Callable[[Request], bool]:
    def client_is_loopback(request: Request) -> bool:
        return is_loopback(client_ip_from_scope(request.scope, trusted))

    return client_is_loopback
