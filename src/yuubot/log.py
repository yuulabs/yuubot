"""Centralized loguru logging setup for yuubot.

Call `setup(log_dir, name=...)` once at process startup.
All modules use `from loguru import logger` directly.
Third-party stdlib logging (uvicorn, tortoise-orm, websockets) is
intercepted and routed through loguru automatically.

Log files live in log_dir (default ~/.yuubot/logs/):
  daemon.log / recorder.log  — current log per process (DEBUG+, full detail)
  daemon.*.log.gz            — rotated daemon archives
  recorder.*.log.gz          — rotated recorder archives

To find system behavior around a known task_id or ctx_id:
  grep "ctx=5" ~/.yuubot/logs/daemon.log
  grep "task_id=abc123" ~/.yuubot/logs/daemon.log
"""

import logging
import sys
from pathlib import Path

from loguru import logger


class _Interceptor(logging.Handler):
    """Redirect stdlib logging (uvicorn, tortoise, websockets, etc.) into loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup(log_dir: str | Path, level: str = "INFO", name: str = "yuubot") -> None:
    """Configure loguru sinks. Call once at process startup.

    Console (stderr): INFO+ only, compact colored format.
    File: DEBUG+, full timestamp + module:line, size-based rotation.
    """
    log_dir = Path(log_dir).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # Console: key events only (INFO+)
    logger.add(
        sys.stderr,
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> <level>{level.name[0]}</level> <cyan>{module}</cyan> | {message}",
        colorize=True,
    )

    # File: everything (DEBUG+), precise timestamps for timeline reconstruction
    logger.add(
        log_dir / f"{name}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} {level.name[0]} {module}:{line} | {message}",
        rotation="20 MB",
        retention=5,
        compression="gz",
        encoding="utf-8",
        enqueue=True,  # non-blocking, safe for async code
    )

    # Intercept stdlib logging from third-party libraries
    logging.basicConfig(handlers=[_Interceptor()], level=0, force=True)
