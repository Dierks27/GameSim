"""Configuration loading and hashing.

All tunables live in config.yaml. Code reads them through `Config`, a thin dict wrapper
with attribute-style section access (cfg.sim['w_off']). `config_hash` identifies a run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"

# Everything that cannot be downloaded again (frozen predictions, the signal ledger, Kalshi price snapshots, lineup
# overrides, tuning/backtest history, your settings, .env) lives in ONE data folder outside the code folder, so a new
# ZIP, a fresh install or deleting the code folder never touches it. Default: <user folder>/GridSim Data
# (e.g. C:\Users\you\GridSim Data, not synced by OneDrive). The environment variable GRIDSIM_DATA moves it.
# Re-downloadable things (data/cache, out/cache sims) stay in the code folder.
USERDATA_NAMES = {"predictions", "signals", "positions", "overrides", "tuning", "backtests", "diagnose", "logs",
                  "config.local.yaml", "config.local.yaml.bak", "config.tuned.yaml", "regime_changes.yaml",
                  ".env", ".env.txt"}


def data_dir() -> Path:
    env = os.environ.get("GRIDSIM_DATA", "").strip().strip('"')
    return Path(env).expanduser() if env else Path.home() / "GridSim Data"


def local_config_path() -> Path:
    """Your own changes (Settings page, promoted tuning), merged over config.yaml. Lives in the data folder, so
    updating GridSim never wipes them."""
    return data_dir() / "config.local.yaml"


def __getattr__(name: str):            # LOCAL_CONFIG_PATH follows data_dir() (which can change in tests)
    if name == "LOCAL_CONFIG_PATH":
        return local_config_path()
    raise AttributeError(name)


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
        """Relative paths: your data (predictions, signals, overrides, tuning, ...) -> the data folder; everything
        else (data/cache, out/...) -> the code folder."""
        p = Path(p)
        if p.is_absolute():
            return p
        if p.parts and p.parts[0] in USERDATA_NAMES:
            return data_dir() / p
        return REPO_ROOT / p


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


# Sections that never change a number GridSim produces (where backups go, how many CPU cores / how much memory
# long jobs may use) are left out of the config hash, so
# changing them doesn't split the Scorecard into a new config version or invalidate cached sims.
HASH_EXCLUDE = {"backup", "resources", "watch"}


# Raised whenever the simulation code itself changes what it predicts (a fix to the model, not a setting). It is part
# of the config hash, so the Scorecard never pools predictions made by different versions of the model, sims cached by
# older code are not reused, and a tuning result found with an older model is not offered.
#   2 (Nov 2026): goal-line / red-zone team pools, matchup tilt measured without per-game noise, kickoff rules for
#                 seasons before 2023, players picked in a fixed order (same seed -> same sim).
MODEL_REV = 2


def config_hash(raw: dict[str, Any]) -> str:
    raw = {k: v for k, v in raw.items() if k not in HASH_EXCLUDE}
    blob = json.dumps(raw, sort_keys=True, default=str).encode()
    if MODEL_REV > 1:
        blob += f"|model_rev={MODEL_REV}".encode()
    return hashlib.sha1(blob).hexdigest()[:10]


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def deep_diff(new: dict, base: dict) -> dict:
    """The parts of `new` that differ from `base` (leaf values; lists compared whole)."""
    out = {}
    for k, v in new.items():
        b = base.get(k) if isinstance(base, dict) else None
        if isinstance(v, dict) and isinstance(b, dict):
            d = deep_diff(v, b)
            if d:
                out[k] = d
        elif v != b:
            out[k] = copy.deepcopy(v)
    return out


def load_local() -> dict:
    p = local_config_path()
    if p.exists():
        with open(p, "r", encoding="utf-8-sig") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def save_local(local: dict) -> Path:
    """Write config.local.yaml (backing up the previous one to config.local.yaml.bak)."""
    p = local_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        p.with_name(p.name + ".bak").write_bytes(p.read_bytes())
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("# Your GridSim settings: only the values you changed. Merged over config.yaml at load time.\n"
                 "# Delete this file (or use Settings -> Reset to defaults) to go back to the shipped defaults.\n")
        yaml.safe_dump(local, fh, sort_keys=True)
    return p


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None,
                use_local: bool | None = None) -> Config:
    """config.yaml, plus config.local.yaml merged over it when loading the default config."""
    explicit = path is not None
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if use_local is None:
        use_local = not explicit or Path(path).resolve() == DEFAULT_CONFIG_PATH.resolve()
    if use_local:
        raw = deep_merge(raw, load_local())
    cfg = Config(raw, path)
    return cfg.copy_with(overrides) if overrides else cfg
