"""Situation buckets, shared by the data side (vectorized polars) and the engine (scalar).

Buckets (spec):
  down      1, 2, 3, 4
  distance  1-2, 3-6, 7-10, 11+                      -> 0..3
  zone      own 1-20, own 21-50, opp 49-21, red zone 20-6, goal-to-go 5-1   -> 0..4
            (yardline_100: 80-99, 50-79, 21-49, 6-20, 1-5)
  score     down 9+, down 1-8, tied, up 1-8, up 9+   -> 0..4
  time      normal, last 2:00 of a half, Q4 last 5:00 (outside last 2:00)   -> 0..2

Outcome-pool back-off chains are defined here too (see DECISIONS.md, "Back-off chain").
"""
from __future__ import annotations

import polars as pl


def dist_b(togo: int) -> int:
    if togo <= 2:
        return 0
    if togo <= 6:
        return 1
    if togo <= 10:
        return 2
    return 3


def zone_b(yl: int) -> int:
    if yl >= 80:
        return 0
    if yl >= 50:
        return 1
    if yl >= 21:
        return 2
    if yl >= 6:
        return 3
    return 4


def score_b(diff: int) -> int:
    if diff <= -9:
        return 0
    if diff < 0:
        return 1
    if diff == 0:
        return 2
    if diff <= 8:
        return 3
    return 4


def time_b(qtr: int, half_sec: float, game_sec: float) -> int:
    """game_sec for OT is passed as seconds left in the OT period (treated like late Q4)."""
    if qtr != 1 and qtr != 3 and half_sec <= 120:
        return 1
    if qtr >= 4 and game_sec <= 300:
        return 2
    return 0


# ---- vectorized versions ---------------------------------------------------------------

def dist_expr(col: str = "ydstogo") -> pl.Expr:
    c = pl.col(col)
    return pl.when(c <= 2).then(0).when(c <= 6).then(1).when(c <= 10).then(2).otherwise(3).cast(pl.Int8)


def zone_expr(col: str = "yardline_100") -> pl.Expr:
    c = pl.col(col)
    return pl.when(c >= 80).then(0).when(c >= 50).then(1).when(c >= 21).then(2).when(c >= 6).then(3).otherwise(4).cast(pl.Int8)


def score_expr(col: str = "score_differential") -> pl.Expr:
    c = pl.col(col)
    return pl.when(c <= -9).then(0).when(c < 0).then(1).when(c == 0).then(2).when(c <= 8).then(3).otherwise(4).cast(pl.Int8)


def time_expr() -> pl.Expr:
    q, h, g = pl.col("qtr"), pl.col("half_seconds_remaining"), pl.col("game_seconds_remaining")
    late_half = ((q == 2) | (q >= 4)) & (h <= 120)
    return pl.when(late_half).then(1).when((q >= 4) & (g <= 300)).then(2).otherwise(0).cast(pl.Int8)


def add_bucket_columns(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        dist_b=dist_expr(), zone_b=zone_expr(), score_b=score_expr(), time_b=time_expr(),
    ).with_columns(
        downgrp3=pl.when(pl.col("down") >= 3).then(3).otherwise(pl.col("down")).cast(pl.Int8),
        downgrp2=pl.when(pl.col("down") >= 3).then(1).otherwise(0).cast(pl.Int8),
        distgrp3=pl.when(pl.col("dist_b") >= 2).then(2).otherwise(pl.col("dist_b")).cast(pl.Int8),
        rz=(pl.col("yardline_100") <= 20).cast(pl.Int8),
        fz=pl.when(pl.col("yardline_100") <= 5).then(2).when(pl.col("yardline_100") <= 20).then(1).otherwise(0).cast(pl.Int8),
    )


# ---- outcome pool back-off chains ------------------------------------------------------
# Each level is a tuple of key columns. Engine computes the same keys with `outcome_keys`.
LEAGUE_LEVELS = [
    ("down", "dist_b", "zone_b", "time_b"),
    ("down", "dist_b", "zone_b"),
    ("downgrp2", "dist_b", "zone_b"),
    ("dist_b", "zone_b"),
    ("zone_b",),
]
# Team pools keep the spec's red-zone / goal-to-go zones at every level and never fall back to all-field rows:
# a midfield or 20-yard-line row applied at the 3 turns any 3+ yard gain into a TD (see DECISIONS.md,
# "Team pools keep the goal-to-go split"). A thin team cell is shrunk toward the zone-specific league pool
# by n/(n + k_outcome) instead.
TEAM_LEVELS = [
    ("downgrp3", "distgrp3", "fz"),
    ("downgrp2", "fz"),
    ("fz",),
]


def fz_b(yl: int) -> int:
    """Field zone for team pools: 0 outside the red zone, 1 red zone 20-6, 2 goal-to-go 5-1."""
    return 2 if yl <= 5 else (1 if yl <= 20 else 0)


def situation_values(down: int, togo: int, yl: int, tb: int) -> dict[str, int]:
    db = dist_b(togo)
    return {
        "down": down, "dist_b": db, "zone_b": zone_b(yl), "time_b": tb,
        "downgrp2": 1 if down >= 3 else 0, "downgrp3": 3 if down >= 3 else down,
        "distgrp3": 2 if db >= 2 else db, "rz": 1 if yl <= 20 else 0, "fz": fz_b(yl),
    }
