"""Assemble every sub-model for one (season, through_week) data window.

`build_model` is the only place data windows are decided:
  scrimmage outcomes + play calling : target season (through_week) + seasons_back prior seasons,
                                      recency-weighted, garbage time removed
  clock, punts                      : same window, garbage time kept
  FG / XP / 2PT                     : fg_seasons_back prior seasons (more data, decaying weights)
  kickoffs                          : only seasons in the target season's kickoff-rules era
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import polars as pl

from . import data as D
from .clock import ClockModel, build_clock
from .config import Config
from .kicking import KickingModel, build_kicking
from .playcall import PlayCallModel, build_playcall
from .pools import OutcomePools, build_outcome_pools, scrimmage_rows


@dataclass
class PresnapModel:
    p_off: dict[str, float]
    p_def: dict[str, float]
    league_off: float
    league_def: float
    off_rows: list[tuple]   # (yards, auto_first)
    def_rows: list[tuple]


@dataclass
class SimModel:
    cfg: Config
    season: int
    through_week: int | None
    pools: OutcomePools
    playcall: PlayCallModel
    kicking: KickingModel
    clock: ClockModel
    presnap: PresnapModel
    teams: list[str]
    meta: dict = field(default_factory=dict)


def kickoff_era_seasons(season: int, cfg: Config, available: list[int]) -> list[int]:
    kr = cfg.kickoff_rules
    eras = {int(k): v for k, v in kr["eras"].items()}
    era = eras.get(season, kr["default_era"])
    return [s for s in available if eras.get(s, kr["default_era"]) == era and s <= season]


def seasons_needed(season: int, cfg: Config) -> list[int]:
    back = max(int(cfg.data["seasons_back"]), int(cfg.kicking["fg_seasons_back"]), 2)
    return list(range(season - back, season + 1))


def load_prepared(season: int, cfg: Config, verbose: bool = False) -> pl.DataFrame:
    seasons = [s for s in seasons_needed(season, cfg) if s <= D.current_season()]
    raw = D.load_pbp(seasons, cfg, verbose=verbose)
    return D.prepare_plays(raw)


def _with_weights(df: pl.DataFrame, weights: dict[int, float]) -> pl.DataFrame:
    w = pl.lit(0.0)
    expr = None
    for s, wt in weights.items():
        expr = (pl.when(pl.col("season") == s).then(wt) if expr is None else expr.when(pl.col("season") == s).then(wt))
    w = expr.otherwise(0.0) if expr is not None else w
    return df.with_columns(w=w.cast(pl.Float64)).filter(pl.col("w") > 0)


def build_presnap(sc_plays: pl.DataFrame, cfg: Config) -> PresnapModel:
    k = float(cfg.sim.get("k_presnap", 400))
    base = sc_plays.filter(pl.col("down").is_not_null() & (pl.col("two_point_attempt").fill_null(0) == 0)
                           & (pl.col("call").is_not_null() | pl.col("presnap")))
    base = base.with_columns(
        po=(pl.col("presnap") & (pl.col("penalty_team") == pl.col("posteam"))).cast(pl.Float64),
        pd=(pl.col("presnap") & (pl.col("penalty_team") == pl.col("defteam"))).cast(pl.Float64))
    tot = base["w"].sum()
    lo = float((base["po"] * base["w"]).sum() / tot)
    ld = float((base["pd"] * base["w"]).sum() / tot)
    off = base.group_by("posteam").agg((pl.col("po") * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
    de = base.group_by("defteam").agg((pl.col("pd") * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
    p_off = {r["posteam"]: (r["s"] + k * lo) / (r["n"] + k) for r in off.iter_rows(named=True)}
    p_def = {r["defteam"]: (r["s"] + k * ld) / (r["n"] + k) for r in de.iter_rows(named=True)}
    pre = base.filter(pl.col("presnap"))
    rows_off = [(int(round(r[0])), bool(r[1])) for r in pre.filter(pl.col("po") == 1).select(["yards", "auto_first"]).iter_rows()]
    rows_def = [(int(round(r[0])), bool(r[1])) for r in pre.filter(pl.col("pd") == 1).select(["yards", "auto_first"]).iter_rows()]
    return PresnapModel(p_off, p_def, lo, ld, rows_off or [(-5, False)], rows_def or [(5, False)])


def build_model(cfg: Config, season: int, through_week: int | None = None, plays: pl.DataFrame | None = None,
                verbose: bool = False) -> SimModel:
    t0 = time.time()
    if plays is None:
        plays = load_prepared(season, cfg, verbose=verbose)
    available = sorted(plays["season"].unique().to_list())

    weights = D.season_weights(season, cfg)
    win_seasons = list(weights.keys())
    window = D.window_filter(plays, season, through_week, win_seasons)
    window = _with_weights(window, weights)
    tendency = window.filter(~D.garbage_time_mask(cfg))

    teams = sorted(set(window["posteam"].drop_nulls().unique().to_list()))
    sc = scrimmage_rows(tendency)
    pools = build_outcome_pools(sc, teams, float(cfg.home_field["tilt"]))
    pc_src = tendency if cfg.data["garbage_time"].get("apply_to_playcall", True) else window
    playcall = build_playcall(pc_src, cfg)
    presnap = build_presnap(tendency, cfg)
    clock = build_clock(window, cfg)

    fg_back = int(cfg.kicking["fg_seasons_back"])
    fg_weights = {season - i: 0.8 ** i for i in range(fg_back + 1)}
    fg_win = _with_weights(D.window_filter(plays, season, through_week, list(fg_weights)), fg_weights)

    era = kickoff_era_seasons(season, cfg, available)
    ko_win = D.window_filter(plays, season, through_week, era).with_columns(w=pl.lit(1.0))
    n_ko = ko_win.filter(pl.col("play_type") == "kickoff").height
    min_ko = int(cfg.kickoff_rules["min_era_kickoffs"])
    if n_ko < min_ko:  # thin era (early season): borrow the previous era
        prev = [s for s in available if s < min(era or [season])]
        if prev:
            era = era + [max(prev)]
            ko_win = D.window_filter(plays, season, through_week, era).with_columns(w=pl.lit(1.0))
    kicking = build_kicking(window, fg_win, ko_win, cfg, window, fg_win)

    meta = {
        "season": season, "through_week": through_week, "season_weights": weights,
        "window_seasons": win_seasons, "kickoff_seasons": sorted(era), "fg_seasons": sorted(fg_weights),
        "n_scrimmage_rows": pools.n_rows, "build_seconds": round(time.time() - t0, 2),
        "last_week_in_data": int(window.filter(pl.col("season") == season)["week"].max() or 0)
        if window.filter(pl.col("season") == season).height else None,
    }
    return SimModel(cfg, season, through_week, pools, playcall, kicking, clock, presnap, teams, meta)
