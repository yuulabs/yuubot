from pathlib import Path

from ...app import Yuubot


def react_dist_dir() -> Path:
    return Path(__file__).resolve().parents[4] / "web" / "dist"


async def route_exists(app: Yuubot, route_id: str) -> bool:
    return any(record.id == route_id for record in await app.list_routes())
