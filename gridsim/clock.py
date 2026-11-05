"""Clock model: empirical live-ball durations and between-snap runoff.

Snap-to-snap deltas (game_seconds_remaining differences) are split into:
  duration  live-ball time of the play. Taken from snaps after which the clock was stopped
            anyway (incompletions; out of bounds inside the stop windows; timeout called
            right after; change of possession) -> the delta *is* the play duration.
  runoff    time from the end of a running-clock play to the next snap:
            delta - mean duration(kind), split by tempo (normal / hurry / milk) and by
            out-of-bounds-restart (outside the stop windows the clock restarts on the
            ready-for-play signal).
Deltas spanning a quarter end or the two-minute warning are dropped (the engine handles both).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .pools import K_COMP, K_INC, K_PEN, K_RUN, K_SACK, K_SCR

T_NORMAL, T_HURRY, T_MILK = 0, 1, 2
DUR_FALLBACK = {K_INC: 5.0, K_COMP: 6.0, K_RUN: 5.0, K_SACK: 6.0, K_SCR: 6.0, K_PEN: 4.0}


def tempo_of(qtr: int, gsec: float, hsec: float, diff: int, cc: dict) -> int:
    """gsec: seconds left in game (or in the OT period); hsec: seconds left in the half."""
    if qtr == 2 and hsec <= cc["hurry_q2_seconds"]:
        return T_HURRY
    if qtr >= 4:
        if diff < 0 and (gsec <= cc["hurry_q4_seconds"] or (diff <= -9 and gsec <= cc["hurry_q4_two_score_seconds"])):
            return T_HURRY
        if diff == 0 and gsec <= 120:
            return T_HURRY
        if diff > 0 and (gsec <= cc["milk_q4_seconds"] or (diff >= 9 and gsec <= cc["milk_q4_two_score_seconds"])):
            return T_MILK
    return T_NORMAL


def tempo_expr(cc: dict) -> pl.Expr:
    q, g, h, d = pl.col("qtr"), pl.col("game_seconds_remaining"), pl.col("half_seconds_remaining"), pl.col("score_differential")
    hurry = ((q == 2) & (h <= cc["hurry_q2_seconds"])) | (
        (q >= 4) & (((d < 0) & ((g <= cc["hurry_q4_seconds"]) | ((d <= -9) & (g <= cc["hurry_q4_two_score_seconds"]))))
                    | ((d == 0) & (g <= 120))))
    milk = (q >= 4) & (d > 0) & ((g <= cc["milk_q4_seconds"]) | ((d >= 9) & (g <= cc["milk_q4_two_score_seconds"])))
    return pl.when(hurry).then(T_HURRY).when(milk).then(T_MILK).otherwise(T_NORMAL).cast(pl.Int8)


def stop_window_expr() -> pl.Expr:
    """Out-of-bounds stops the clock: last 2:00 of the first half, last 5:00 of the second half / OT."""
    q, g, h = pl.col("qtr"), pl.col("game_seconds_remaining"), pl.col("half_seconds_remaining")
    return ((q == 2) & (h <= 120)) | ((q >= 4) & (g <= 300))


def in_stop_window(qtr: int, gsec: float, hsec: float) -> bool:
    return (qtr == 2 and hsec <= 120) or (qtr >= 4 and gsec <= 300)


@dataclass
class ClockModel:
    dur: dict[int, list[float]]
    dur_mean: dict[int, float]
    runoff: dict[int, list[float]]      # tempo -> runoff samples (in-bounds running clock)
    oob_runoff: list[float]             # out of bounds outside stop windows
    pen_runoff: list[float]             # after post-snap penalties outside stop windows (clock was running)
    presnap_elapsed: list[float]        # runoff after a pre-snap foul while the clock was running
    punt_dur: list[float]
    fg_dur: list[float]
    team_pace: dict                     # offense -> multiplicative factor on normal-tempo runoff

    def summary(self) -> dict:
        return {
            "dur_mean": {k: round(v, 2) for k, v in self.dur_mean.items()},
            "runoff_mean": {t: round(float(np.mean(v)), 1) for t, v in self.runoff.items()},
            "oob_runoff_mean": round(float(np.mean(self.oob_runoff)), 1),
        }


def _lst(x: pl.Series, fallback: list[float]) -> list[float]:
    v = [float(a) for a in x.drop_nulls().to_list()]
    return v if len(v) >= 30 else fallback


def build_clock(plays: pl.DataFrame, cfg) -> ClockModel:
    """plays: prepared plays (not garbage filtered), scrimmage + special teams."""
    from .pools import _kind_expr

    cc = cfg.clock
    df = plays.filter(pl.col("elapsed").is_not_null() & ~pl.col("crosses_2mw") & (pl.col("elapsed") >= 0))
    df = df.with_columns(tempo=tempo_expr(cc), window=stop_window_expr().fill_null(False))
    sc = df.filter(pl.col("call").is_not_null() & pl.col("down").is_not_null()).with_columns(kind=_kind_expr())
    stopped_after = (
        (pl.col("kind") == K_INC)
        | ((pl.col("out_of_bounds") == 1) & pl.col("window"))
        | ((pl.col("n_to") > 0) & pl.col("window"))
        | pl.col("turnover")
    )
    dur, dur_mean = {}, {}
    st = sc.filter(stopped_after & (pl.col("elapsed") <= 20) & ~pl.col("td_off") & ~pl.col("td_def"))
    for kind in (K_INC, K_COMP, K_RUN, K_SACK, K_SCR, K_PEN):
        v = _lst(st.filter(pl.col("kind") == kind)["elapsed"], [DUR_FALLBACK[kind]])
        dur[kind] = v
        dur_mean[kind] = float(np.mean(v))

    # map (not join): joins do not preserve row order, and these lists are sampled by index
    run = sc.with_columns(dm=pl.col("kind").cast(pl.Int64).replace_strict({int(k): float(v) for k, v in dur_mean.items()},
                                                                          default=6.0, return_dtype=pl.Float64))
    run = run.with_columns(ro=(pl.col("elapsed") - pl.col("dm")).clip(0, 45))
    clean = (~pl.col("turnover") & ~pl.col("td_off") & ~pl.col("td_def") & (pl.col("safety").fill_null(0) == 0)
             & (pl.col("n_to") == 0) & (pl.col("posteam") == pl.col("n_posteam"))
             & pl.col("n_play_type").is_in(["pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike", "no_play"]))
    running = run.filter(clean & pl.col("kind").is_in([K_COMP, K_RUN, K_SACK, K_SCR]) & (pl.col("out_of_bounds").fill_null(0) == 0))
    runoff = {}
    fb = {T_NORMAL: [33.0], T_HURRY: [15.0], T_MILK: [38.0]}
    for t in (T_NORMAL, T_HURRY, T_MILK):
        runoff[t] = _lst(running.filter((pl.col("tempo") == t))["ro"], fb[t])
    oob = run.filter(clean & (pl.col("out_of_bounds") == 1) & ~pl.col("window") & (pl.col("kind") != K_INC))
    oob_runoff = _lst(oob["ro"], [28.0])
    # penalties restart the clock on the ready-for-play signal only if it was running before the foul
    pen = run.filter(clean & (pl.col("kind") == K_PEN) & ~pl.col("window") & pl.col("prev_running"))
    pen_runoff = _lst(pen["ro"], [20.0])
    pre = df.filter(pl.col("presnap") & ~pl.col("window") & pl.col("prev_running") & (pl.col("n_to") == 0))
    presnap_elapsed = _lst(pre["elapsed"].clip(0, 45), [20.0])
    punt_dur = _lst(df.filter(pl.col("play_type") == "punt")["elapsed"].clip(0, 25), [8.0])
    fg_dur = _lst(df.filter(pl.col("play_type") == "field_goal")["elapsed"].clip(0, 15), [5.0])
    # Team pace: offense's mean runoff in neutral situations (normal tempo, score within 7)
    # relative to league, shrunk: factor = (n*mean_T + k*mean_L) / (n + k) / mean_L
    k_pace = float(cc.get("k_pace", 150))
    neutral = running.filter((pl.col("tempo") == T_NORMAL) & (pl.col("score_differential").abs() <= 7))
    mean_l = float((neutral["ro"] * neutral["w"]).sum() / neutral["w"].sum()) if neutral.height else 31.0
    tp = neutral.group_by("posteam").agg((pl.col("ro") * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
    team_pace = {r["posteam"]: round(((r["s"] + k_pace * mean_l) / (r["n"] + k_pace)) / mean_l, 10) for r in tp.iter_rows(named=True)}
    return ClockModel(dur, dur_mean, runoff, oob_runoff, pen_runoff, presnap_elapsed, punt_dur, fg_dur, team_pace)
