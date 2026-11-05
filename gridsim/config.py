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


# Config keys read only at simulation time (not when building pools/tables). Models can be reused across configs
# that differ only in these, e.g. tuning trials that change sampling-time knobs.
SAMPLING_KEYS = {
    "sim": None,                     # whole section except the keys below
    "clock": ["runoff_scale"],
    "playcall": ["pass_logit_shift", "fg_min_make_prob"],
    "qb": ["k_qb"],
    "weather": None, "rest_travel": None, "two_point": None, "overtime": None, "availability": None,
    "players": None, "markets": None, "scanner": None, "notify": None, "tune": None, "backtest": None,
}
SIM_BUILD_KEYS = {"k_presnap"}       # sim.* keys that ARE used at build time


def build_hash(raw: dict[str, Any]) -> str:
    """Hash of the build-relevant part of a config (see SAMPLING_KEYS)."""
    r = copy.deepcopy(raw)
    for sec, keys in SAMPLING_KEYS.items():
        if sec not in r:
            continue
        if keys is None:
            if sec == "sim":
                r[sec] = {k: v for k, v in r[sec].items() if k in SIM_BUILD_KEYS}
            else:
                r.pop(sec)
        else:
            for k in keys:
                r[sec].pop(k, None)
    return config_hash(r)


def config_hash(raw: dict[str, Any]) -> str:
    blob = json.dumps(raw, sort_keys=True, default=str).encode()
    return hashlib.sha1(blob).hexdigest()[:10]


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    cfg = Config(raw, path)
    return cfg.copy_with(overrides) if overrides else cfg
