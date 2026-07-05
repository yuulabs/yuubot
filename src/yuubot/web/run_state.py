import os
from pathlib import Path

import msgspec


class ServerRunState(msgspec.Struct, frozen=True):
    host: str
    port: int
    pid: int


def run_state_path(data_dir: Path) -> Path:
    return data_dir / "run" / "server.json"


def write(data_dir: Path, host: str, port: int) -> None:
    path = run_state_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = ServerRunState(host=host, port=port, pid=os.getpid())
    path.write_text(msgspec.json.encode(state).decode("utf-8"), encoding="utf-8")


def read(data_dir: Path) -> ServerRunState | None:
    path = run_state_path(data_dir)
    if not path.is_file():
        return None
    return msgspec.json.decode(path.read_text(encoding="utf-8"), type=ServerRunState)


def clear(data_dir: Path) -> None:
    path = run_state_path(data_dir)
    if path.is_file():
        path.unlink()
