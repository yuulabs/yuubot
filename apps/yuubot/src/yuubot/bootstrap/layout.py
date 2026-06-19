"""Canonical on-disk layout under the bootstrap data directory.

Single source of truth for every path the daemon and admin processes derive
from ``BootstrapConfig.paths.data_dir``. Callers must not concatenate
sub-paths themselves; they go through ``DataLayout`` instead.

Final shape:

    <data_dir>/
        yuubot/
            yuubot.db
            traces.db
            logs/
            runtime/facades/
            plugins/
        integrations/<integration_id>/
        workspace/actors/<safe_actor_id>/
        skills/
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DataLayout:
    """Resolve subpaths under a single data root.

    Parameters
    ----------
    data_dir:
        Root directory. ``~`` is expanded once at construction.
    """

    data_dir: Path

    @classmethod
    def from_path(cls, data_dir: str | Path) -> "DataLayout":
        return cls(data_dir=Path(data_dir).expanduser())

    @property
    def yuubot_dir(self) -> Path:
        return self.data_dir / "yuubot"

    @property
    def db_path(self) -> Path:
        return self.yuubot_dir / "yuubot.db"

    @property
    def traces_db_path(self) -> Path:
        return self.yuubot_dir / "traces.db"

    @property
    def logs_dir(self) -> Path:
        return self.yuubot_dir / "logs"

    @property
    def runtime_facades_dir(self) -> Path:
        return self.yuubot_dir / "runtime" / "facades"

    @property
    def plugins_dir(self) -> Path:
        return self.yuubot_dir / "plugins"

    @property
    def integrations_root(self) -> Path:
        return self.data_dir / "integrations"

    @property
    def workspace_root(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def skills_dir(self) -> Path:
        return self.data_dir / "skills"

    def integration_dir(self, integration_id: str) -> Path:
        return self.integrations_root / integration_id

    def ensure(self) -> None:
        """Create every directory needed before services start."""
        for path in (
            self.data_dir,
            self.yuubot_dir,
            self.logs_dir,
            self.runtime_facades_dir,
            self.plugins_dir,
            self.integrations_root,
            self.workspace_root,
            self.skills_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
