import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILENAME = "yuubot.log"


def configure_logging(
    logs_dir: Path,
    development: bool,
    max_bytes: int = 50 * 1024 * 1024,
    backup_count: int = 5,
) -> Path:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / LOG_FILENAME
    level = logging.DEBUG if development else logging.INFO

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)
    return log_path


def rotated_log_paths(logs_dir: Path) -> list[Path]:
    if not logs_dir.is_dir():
        return []
    paths = [logs_dir / f"{LOG_FILENAME}.{index}" for index in range(1, 100)]
    return [path for path in paths if path.is_file()]
