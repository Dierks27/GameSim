"""Self-tuning with Optuna, walk-forward, no leakage.

Every game in every trial is simulated with a model built only from data before its week (the backtest).
Trials score the TRAIN seasons; the best trial is then scored once on the held-out VALIDATION season and
compared with the current config on the same games and seeds. config.yaml is never modified here:
the winner is written to config.tuned.yaml and `gridsim tune --apply` promotes it.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
import time
from pathlib import Path

import yaml

from .backtest import run_backtest
from .config import MODEL_REV, Config, load_config


def _get(raw: dict, key: str):
    node = raw
    for p in key.split("."):
        if isinstance(node, list):
            node = node[int(p)]
        else:
            node = node[p]
    return node


def _set(raw: dict, key: str, value) -> None:
    parts = key.split(".")
    node = raw
    for p in parts[:-1]:
        node = node[int(p)] if isinstance(node, list) else node[p]
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def apply_params(cfg: Config, params: dict) -> Config:
    import copy
    raw = copy.deepcopy(cfg.raw)
    for k, v in params.items():
        _set(raw, k, v)
        if k == "data.garbage_time.wp_low":
            _set(raw, "data.garbage_time.wp_high", round(1 - v, 4))
    return Config(raw, cfg.path)


def objective_value(m: dict, w: dict) -> float:
    return (float(w["log_loss"]) * m["log_loss"] + float(w["crps_margin"]) * m["crps_margin"] / 10.0
            + float(w["crps_total"]) * m["crps_total"] / 10.0)


def score_config(cfg: Config, seasons: list[int], weeks: list[int], sims: int, seed: int, every: int,
                 workers: int | None, verbose: bool = False, pool=None) -> tuple[dict, list]:
    """Pooled backtest metrics over several seasons (every k-th game kept, deterministic)."""
    import polars as pl

    from .metrics import summarize_games
    frames, comps = [], []
    for s in seasons:
        keep = (lambda g: (hash_game(g["game_id"]) % every) == 0) if every > 1 else None
        g, ex = run_backtest(cfg, s, weeks, sims=sims, seed=seed, workers=workers, games_filter=keep, verbose=verbose,
                             pool=pool)
        frames.append(g)
        comps.append(ex["components"])
    gall = pl.concat(frames, how="diagonal_relaxed")
    return summarize_games(gall), [gall, pl.concat(comps, how="diagonal_relaxed")]


def hash_game(gid: str) -> int:
    import zlib
    return zlib.crc32(gid.encode())


def suggest(trial, space: dict) -> dict:
    out = {}
    for key, spec in space.items():
        lo, hi = spec["low"], spec["high"]
        if spec.get("type") == "int":
            out[key] = trial.suggest_int(key, int(lo), int(hi), log=bool(spec.get("log")))
        else:
            out[key] = trial.suggest_float(key, float(lo), float(hi), log=bool(spec.get("log")))
    return out


def is_sampling_key(key: str) -> bool:
    from .config import SAMPLING_KEYS, SIM_BUILD_KEYS
    sec, _, rest = key.partition(".")
    keys = SAMPLING_KEYS.get(sec, "missing")
    if keys == "missing":
        return False
    if keys is None:
        return not (sec == "sim" and rest.split(".")[0] in SIM_BUILD_KEYS)
    return rest.split(".")[0] in keys


def _be_nice() -> None:
    """Run tuning (and its worker processes, which inherit this) at low priority: anything you do on the PC goes
    first."""
    import os
    import sys
    try:
        if sys.platform == "win32":
            import ctypes
            BELOW_NORMAL = 0x00004000
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(10)
    except Exception:
        pass


def run_tune(cfg: Config, train: list[int], validate: int, trials: int, sims: int | None = None,
             workers: int | None = None, verbose: bool = True, sampling_only: bool = False) -> Path:
    import optuna

    tc = cfg.tune
    sims = sims or int(tc["sims_per_game"])
    weeks = list(range(int(tc["weeks"][0]), int(tc["weeks"][1]) + 1))
    seed = int(tc["seed"])
    every = int(tc.get("train_every_kth_game", 1))
    weights = tc["objective_weights"]
    space = tc["space"]
    if sampling_only:   # only sampling-time knobs: every trial reuses the cached built models (much faster)
        space = {k: v for k, v in space.items() if is_sampling_key(k)}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = cfg.resolve_path("tuning") / stamp
    out.mkdir(parents=True, exist_ok=True)
    for s in train + [validate]:
        if s < 2021:
            raise ValueError("do not tune on seasons before 2021 (17-game schedule); got %d" % s)

    t0 = time.time()

    from .backtest import PinnedPool
    _be_nice()
    # leave cores and memory free so the PC stays usable while tuning runs for hours (config `resources`)
    from .resources import tune_worker_count
    seasons = list(dict.fromkeys(list(train) + [validate]))
    workers = workers or tune_worker_count(cfg, len(seasons), verbose=verbose)
    pool = PinnedPool(workers, seasons=seasons)
    base_train, _ = score_config(cfg, train, weeks, sims, seed, every, workers, pool=pool)
    base_obj = objective_value(base_train, weights)
    if verbose:
        print(f"baseline train objective {base_obj:.5f} ({time.time() - t0:.0f}s)", flush=True)

    history = []

    def objective(trial):
        params = suggest(trial, space)
        c = apply_params(cfg, params)
        m, _ = score_config(c, train, weeks, sims, seed, every, workers, pool=pool)
        val = objective_value(m, weights)
        rec = {"trial": trial.number, "objective": val, **params,
               **{k: m[k] for k in ("log_loss", "brier", "crps_margin", "crps_total", "mae_margin", "mae_total")}}
        history.append(rec)
        with open(out / "trials.csv", "w", newline="", encoding="utf-8") as fh:
            wri = csv.DictWriter(fh, fieldnames=list(rec.keys()))
            wri.writeheader()
            wri.writerows(history)
        if verbose:
            print(f"trial {trial.number}: {val:.5f} (best {min(h['objective'] for h in history):.5f}) "
                  f"{json.dumps({k: round(v, 4) for k, v in params.items()})}", flush=True)
        return val

    study = optuna.create_study(direction="minimize", sampler=optuna.samplers.TPESampler(seed=seed))
    # always evaluate the current config first so the tuner can only improve on it
    study.enqueue_trial({k: _get(cfg.raw, k) for k in space})
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study.optimize(objective, n_trials=trials)
    best = study.best_params
    tuned = apply_params(cfg, best)

    # held-out validation: same games and seeds for current vs tuned
    vweeks = list(range(int(tc["validate_weeks"][0]), int(tc["validate_weeks"][1]) + 1))
    cur_val, cur_frames = score_config(cfg, [validate], vweeks, sims, seed, 1, workers, pool=pool)
    new_val, new_frames = score_config(tuned, [validate], vweeks, sims, seed, 1, workers, pool=pool)
    pool.close()
    best_train = min(h["objective"] for h in history)
    cur_v, new_v = objective_value(cur_val, weights), objective_value(new_val, weights)
    train_gain = base_obj - best_train
    val_gain = cur_v - new_v
    overfit = train_gain > 0 and (val_gain <= 0 or val_gain < 0.25 * train_gain)
    # Two simulations of the same season differ by luck (about 0.01 in this objective at 300 sims), so "better on
    # the held-out season" only counts when the gain is larger than its own margin of error, game by game.
    gain_se = paired_gain_se(cur_frames[0], new_frames[0], weights)
    recommended = val_gain > 0 and (gain_se is None or val_gain > gain_se)

    from .diagnose import diagnose, write_report
    write_report(diagnose(*new_frames), out / "diagnose_validation", f"Diagnose — tuned config, {validate} validation")
    with open(out / "config.tuned.yaml", "w", encoding="utf-8") as fh:
        yaml.safe_dump(tuned.raw, fh, sort_keys=False)
    if recommended:
        shutil.copy(out / "config.tuned.yaml", cfg.resolve_path("config.tuned.yaml"))
    summary = {
        "train_seasons": train, "validate_season": validate, "trials": trials, "sims_per_game": sims,
        "best_params": best, "current_params": {k: _get(cfg.raw, k) for k in space},
        "train": {"current": base_obj, "best": best_train, "gain": train_gain},
        "validation": {"current": cur_v, "tuned": new_v, "gain": val_gain, "gain_se": gain_se,
                       "current_metrics": {k: v for k, v in cur_val.items() if k != "calibration"},
                       "tuned_metrics": {k: v for k, v in new_val.items() if k != "calibration"}},
        "likely_overfit": overfit, "recommended": recommended, "model_rev": MODEL_REV, "config_hash_current": cfg.hash,
        "config_hash_tuned": tuned.hash, "runtime_seconds": round(time.time() - t0, 1),
    }
    with open(out / "summary.json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
    if verbose:
        print(before_after_table(summary))
    return out


def paired_gain_se(cur, new, weights: dict) -> float | None:
    """Standard error of the validation gain (current minus tuned objective), from the per-game differences on the
    same games. None if the two runs cannot be paired."""
    import numpy as np
    import polars as pl
    try:
        def per_game(g):
            g = g.sort("game_id")
            p = np.clip((g["p_home"] + 0.5 * g["p_tie"]).to_numpy(), 1e-6, 1 - 1e-6)
            y = g["home_win"].to_numpy()
            ll = -(y * np.log(p) + (1 - y) * np.log(1 - p))
            return g["game_id"].to_list(), (float(weights["log_loss"]) * ll
                                            + float(weights["crps_margin"]) * g["crps_margin"].to_numpy() / 10.0
                                            + float(weights["crps_total"]) * g["crps_total"].to_numpy() / 10.0)
        ids_c, c = per_game(cur)
        ids_n, n = per_game(new)
        if ids_c != ids_n or len(c) < 20:
            return None
        d = c - n
        return float(np.std(d, ddof=1) / np.sqrt(len(d)))
    except (KeyError, pl.exceptions.PolarsError, ValueError):
        return None


def before_after_table(s: dict) -> str:
    L = [f"Train {s['train_seasons']} objective: current {s['train']['current']:.5f} -> best {s['train']['best']:.5f}",
         f"Validation {s['validate_season']} objective: current {s['validation']['current']:.5f} -> tuned {s['validation']['tuned']:.5f}",
         f"{'metric (validation)':<24}{'current':>10}{'tuned':>10}"]
    cm, tm = s["validation"]["current_metrics"], s["validation"]["tuned_metrics"]
    for k in ("log_loss", "brier", "crps_margin", "crps_total", "mae_margin", "mae_total", "coverage_80_margin", "sd_pred_margin"):
        L.append(f"{k:<24}{cm[k]:>10.4f}{tm[k]:>10.4f}")
    L.append(f"{'parameter':<36}{'current':>12}{'tuned':>12}")
    for k, v in s["best_params"].items():
        L.append(f"{k:<36}{s['current_params'][k]!s:>12.12}{v:>12.4g}")
    if s["likely_overfit"]:
        L.append("WARNING: likely overfit — train improved but validation barely moved or got worse.")
    se = s["validation"].get("gain_se")
    if se is not None:
        L.append(f"Validation gain {s['validation']['gain']:+.5f}, margin of error ±{se:.5f} "
                 "(two simulations of the same season differ by about this much on luck alone).")
    if s["recommended"]:
        L.append("Recommended: yes — config.tuned.yaml written; `gridsim tune --apply` to promote.")
    elif s["validation"]["gain"] > 0:
        L.append("Recommended: no — the tuned settings did slightly better on the held-out season, but by less than "
                 "the margin of error, so that is not evidence; keeping current config.")
    else:
        L.append("Recommended: no — validation did not improve; keeping current config.")
    return "\n".join(L)


def _summaries(cfg: Config) -> list[dict]:
    """Every tuning run's summary, newest first."""
    out = []
    for p in sorted(cfg.resolve_path("tuning").glob("*/summary.json"), reverse=True):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)), abs(float(b)))
    except (TypeError, ValueError):
        return a == b


def _shipped() -> dict:
    from .config import DEFAULT_CONFIG_PATH
    return load_config(DEFAULT_CONFIG_PATH, use_local=False).raw


def _has(raw: dict, key: str) -> bool:
    try:
        _get(raw, key)
        return True
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def _in_use(cfg: Config, params: dict, base: dict | None = None) -> bool:
    """Are all of these tuned values the ones the config uses right now? A setting that ships as a whole number
    (k_outcome: 60) also counts in its whole-number form: the Settings page used to save a tuned 63.67 back as 63
    whenever anything on it was saved, and such files exist."""
    try:
        if not params:
            return False
        base = base if base is not None else _shipped()
        for k, v in params.items():
            now = _get(cfg.raw, k)
            shipped = _get(base, k) if _has(base, k) else None
            whole = isinstance(shipped, int) and not isinstance(shipped, bool) and isinstance(v, float)
            if not (_same(now, v) or (whole and _same(now, int(v)))):
                return False
        return True
    except (KeyError, IndexError, TypeError, ValueError, OverflowError):
        return False


def tuned_status(cfg: Config) -> str:
    """Where the newest tuning run stands: "none" (nothing to use: no run, or it was not recommended), "fresh"
    (recommended, found with this version of the model, not in use yet), "applied" (you are using its values), or
    "stale" (recommended, but found with an older version of the model: its settings compensate for things that
    have since been fixed, so it is not offered — tune again).

    Only the newest run counts: an older run's config.tuned.yaml is never offered on the strength of a newer run."""
    runs = _summaries(cfg)
    if not runs:
        # a tuned file without its run record cannot be tied to a model version
        return "stale" if cfg.resolve_path("config.tuned.yaml").exists() else "none"
    held = runs[0]
    if not held.get("recommended") or not held.get("best_params"):
        return "none"
    if int(held.get("model_rev") or 1) != MODEL_REV:
        return "stale"
    return "applied" if _in_use(cfg, held["best_params"]) else "fresh"


def stale_tuned_in_use(cfg: Config) -> list[str]:
    """Settings in use right now that came from a tuning found with an OLDER version of the model (promoted before
    an update). Empty when there is nothing of the kind. Compared by value, so it does not matter which button or
    command put them there."""
    base = None
    for s in _summaries(cfg):
        params = s.get("best_params") or {}
        if int(s.get("model_rev") or 1) == MODEL_REV or not params:
            continue
        base = base if base is not None else _shipped()
        if not _in_use(cfg, params, base):
            continue
        # a value equal to the shipped one is not "from the tuning"; a setting this version no longer ships is
        keys = [k for k in params if not _has(base, k) or not _same(_get(base, k), _get(cfg.raw, k))]
        if keys:
            return keys
    return []


def _drop(raw: dict, key: str) -> None:
    parts = key.split(".")
    node = raw
    for p in parts[:-1]:
        node = node.get(p) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def _backup_local(cfg: Config, stamp: str) -> Path:
    from .config import local_config_path
    backup = cfg.resolve_path("tuning") / f"config.local.backup.{stamp}.yaml"
    backup.parent.mkdir(parents=True, exist_ok=True)
    if local_config_path().exists():
        shutil.copy(local_config_path(), backup)
    else:
        backup.write_text("# (no config.local.yaml before this change: shipped defaults)\n", encoding="utf-8")
    return backup


def drop_stale_tuned(cfg: Config) -> Path:
    """Put the values of an old-model tuning (see stale_tuned_in_use) back to the shipped ones. Every other setting
    of yours stays. Returns the backup of the previous settings."""
    from .config import DEFAULT_CONFIG_PATH, deep_diff, save_local
    from .userdata import auto_backup
    keys = stale_tuned_in_use(cfg)
    if not keys:
        raise RuntimeError("no settings from an older tuning are in use")
    auto_backup(cfg, "before-standard-settings")
    backup = _backup_local(cfg, dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    base = load_config(DEFAULT_CONFIG_PATH, use_local=False)
    back = {k: _get(base.raw, k) for k in keys if _has(base.raw, k)}
    if "data.garbage_time.wp_low" in back:
        back["data.garbage_time.wp_high"] = _get(base.raw, "data.garbage_time.wp_high")
    import copy
    raw = copy.deepcopy(cfg.raw)
    for k, v in back.items():
        _set(raw, k, copy.deepcopy(v))
    for k in keys:
        if k not in back:                 # a setting this version no longer has: take it out of your settings
            _drop(raw, k)
    save_local(deep_diff(raw, base.raw))
    return backup


def apply_tuned(cfg: Config) -> Path:
    """Promote the newest tuning run: its tuned values are written into config.local.yaml (your settings layer) on
    top of the settings you have NOW, so nothing else you changed since the tune started is undone, config.yaml
    stays as shipped, and updates never undo a promoted tuning. Returns the backup of the previous settings."""
    from .config import DEFAULT_CONFIG_PATH, deep_diff, save_local
    from .userdata import auto_backup
    status = tuned_status(cfg)
    if status == "stale":
        raise RuntimeError("this tuning result was found with an older version of GridSim's model, so it is not "
                           "used — run the tune again (the new run tests the current model)")
    if status == "none":
        raise FileNotFoundError("no recommended tuning result — run `gridsim tune` first")
    held = _summaries(cfg)[0]
    gone = [k for k in held["best_params"] if not _has(cfg.raw, k)]
    if gone:
        raise RuntimeError("this tuning result sets values this version of GridSim does not have ("
                           + ", ".join(gone) + ") — run the tune again")
    auto_backup(cfg, "before-tuned-settings")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = _backup_local(cfg, stamp)
    base = load_config(DEFAULT_CONFIG_PATH, use_local=False)
    save_local(deep_diff(apply_params(cfg, held["best_params"]).raw, base.raw))
    new = load_config()
    hist = cfg.resolve_path("tuning") / "history.csv"
    row = {"promoted_at": stamp, "config_hash": new.hash, "previous_hash": cfg.hash,
           "validate_season": held.get("validate_season"),
           "val_objective": (held.get("validation") or {}).get("tuned"),
           "val_log_loss": ((held.get("validation") or {}).get("tuned_metrics") or {}).get("log_loss"),
           "val_crps_margin": ((held.get("validation") or {}).get("tuned_metrics") or {}).get("crps_margin"),
           "val_crps_total": ((held.get("validation") or {}).get("tuned_metrics") or {}).get("crps_total")}
    new_file = not hist.exists()
    with open(hist, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if new_file:
            w.writeheader()
        w.writerow(row)
    return backup
