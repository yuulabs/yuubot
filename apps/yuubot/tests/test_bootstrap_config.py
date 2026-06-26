"""Bootstrap config boundary tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from yuubot.bootstrap.config import BootstrapConfigError, load_bootstrap_config
from yuubot.core.secrets import master_key_for_tests


def _write_config(
    path: Path,
    *,
    extra: str = "",
    database_path: str | None = None,
) -> None:
    db_path = (
        database_path
        if database_path is not None
        else str(path.parent / "data" / "yuubot" / "yuubot.db")
    )
    path.write_text(
        f"""
admin:
  host: 127.0.0.1
  port: 8781
  secret: ""

server:
  daemon_host: 127.0.0.1
  daemon_port: 8780
  daemon_secret: test-daemon-secret

database:
  path: {db_path!r}

secrets:
  master_key: {master_key_for_tests()!r}

trace:
  enabled: true
  collector_host: 127.0.0.1
  collector_port: 4318

paths:
  data_dir: {str(path.parent / "data")!r}
{extra}
""",
        encoding="utf-8",
    )


def test_load_bootstrap_config_requires_explicit_file(tmp_path: Path) -> None:
    with pytest.raises(BootstrapConfigError, match="config file does not exist"):
        load_bootstrap_config(tmp_path / "missing.yaml")


def test_load_bootstrap_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, extra="legacy_runtime_key: true\n")

    with pytest.raises(BootstrapConfigError, match="unknown field"):
        load_bootstrap_config(config_path)


def test_load_bootstrap_config_rejects_unknown_nested_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, extra="  # keep final newline\n")
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace(
            "  collector_port: 4318",
            "  collector_port: 4318\n  ui_port: 8782",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BootstrapConfigError, match="unknown field"):
        load_bootstrap_config(config_path)


def test_load_bootstrap_config_rejects_missing_required_field(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("  daemon_port: 8780\n", ""), encoding="utf-8")

    with pytest.raises(BootstrapConfigError, match="Object missing required field"):
        load_bootstrap_config(config_path)


def test_load_bootstrap_config_rejects_empty_database_path(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, database_path="")

    with pytest.raises(BootstrapConfigError, match="database.path must be set"):
        load_bootstrap_config(config_path)
