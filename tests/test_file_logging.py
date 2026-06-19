"""Tests for configure_file_logging in yuubot.runtime.process."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from yuubot.runtime.process import configure_file_logging


@pytest.fixture(autouse=True)
def _reset_root_logger() -> Iterator[None]:
    """Reset the root logger after each test to avoid handler state leaking."""
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(logging.NOTSET)


def test_configure_file_logging_writes_to_file(tmp_path: Path) -> None:
    """Log messages should appear in the expected log file with correct format."""
    logs_dir = tmp_path / "logs"
    configure_file_logging(logs_dir=logs_dir, process_name="test-proc")

    logger = logging.getLogger("test.module")
    logger.info("test log message")

    # Flush all handlers to ensure the message is written to disk
    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = logs_dir / "test-proc.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "test log message" in content
    assert "test.module" in content


def test_configure_file_logging_is_idempotent(tmp_path: Path) -> None:
    """Calling configure_file_logging twice must not duplicate handlers."""
    logs_dir = tmp_path / "logs"
    configure_file_logging(logs_dir=logs_dir, process_name="proc")
    handler_count_1 = len(logging.getLogger().handlers)

    configure_file_logging(logs_dir=logs_dir, process_name="proc")
    handler_count_2 = len(logging.getLogger().handlers)

    assert handler_count_1 == handler_count_2


def test_configure_file_logging_creates_directory(tmp_path: Path) -> None:
    """configure_file_logging must create nested log directories and the log file."""
    logs_dir = tmp_path / "nested" / "logs"
    assert not logs_dir.exists()

    configure_file_logging(logs_dir=logs_dir, process_name="proc")

    assert logs_dir.exists()
    assert (logs_dir / "proc.log").exists()
