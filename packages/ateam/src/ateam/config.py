"""Per-Machine CLI configuration, kept at `$ATEAM_CONFIG` or `~/.config/ateam/config.json`."""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def config_path() -> Path:
    return Path(os.environ.get("ATEAM_CONFIG", Path.home() / ".config/ateam/config.json"))


def state_dir() -> Path:
    return Path(os.environ.get("ATEAM_STATE", Path.home() / ".local/state/ateam"))


@dataclass
class Config:
    homebase_url: str
    machine: str | None = None
    workspace_root: str | None = None
    # Development only: sent as X-ATeam-Node to a HomeBase started with --trust-header.
    dev_node: str | None = None

    @property
    def root(self) -> Path:
        if not self.workspace_root:
            raise SystemExit("this machine has no Workspace Root; run `ateam machine register`")
        return Path(self.workspace_root)


def load() -> Config:
    path = config_path()
    if not path.exists():
        raise SystemExit(f"no config at {path}; run `ateam machine register --homebase <url> ...`")
    return Config(**json.loads(path.read_text()))


def save(cfg: Config) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cfg), indent=2) + "\n")
