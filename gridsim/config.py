"""Configuration loading and hashing.

All tunables live in config.yaml. Code reads them through `Config`, a thin dict wrapper
with attribute-style section access (cfg.sim['w_off']). `config_hash` identifies a run.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


class Config:
    def __init__(self, raw: dict[str, Any], path: Path | None = None):
        self.raw = raw
        self.path = path

    def __getattr__(self, name: str) -> Any:
        try:
            return self.raw[name]
        except KeyError as e:  # pragma: no cover - attribute errors are programming bugs
            raise AttributeError(name) from e

    def __getitem__(self, name: str) -> Any:
        return self.raw[name]

    def copy_with(self, overrides: dict[str, Any] | None = None) -> "Config":
        """Return a deep copy with dotted-key overrides applied, e.g. {'sim.w_off': 0.6}."""
        raw = copy.deepcopy(self.raw)
        for key, value in (overrides or {}).items():
            node = raw
            parts = key.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = value
        return Config(raw, self.path)

    @property
    def hash(self) -> str:
        return config_hash(self.raw)

    def resolve_path(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else REPO_ROOT / p


def config_hash(raw: dict[str, Any]) -> str:
    blob = json.dumps(raw, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:10]


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = Config(raw, path)
    return cfg.copy_with(overrides) if overrides else cfg
