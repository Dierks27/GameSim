"""Situational play-call model.

P(pass) on downs 1-3 (and on 4th-down go attempts):
  league cell (down, dist, zone, score, time) estimated with hierarchical additive smoothing
  toward its parent cell (drop score -> drop time -> drop zone -> down only -> global), then
  a per-team logit offset = shrunk pass-rate-over-expected:
      offset_T = n/(n + k_team_pass) * [logit(observed rate) - logit(mean expected rate)]

4th down:
  P(go | dist, yardline bin, score, time) league (same smoothing) + shrunk team offset.
  If not going: P(FG | yardline) league, evaluated at a kicker-range-adjusted yardline.
  Forced overrides (engine): must-go when trailing late, FG to tie/win late, etc.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import polars as pl

from . import buckets as B

FOURTH_YL_EDGES = [5, 10, 20, 30, 37, 45, 55, 65, 100]  # yardline_100 upper edges -> bins 0..8


def yl_bin4(yl: int) -> int:
    for i, e in enumerate(FOURTH_YL_EDGES):
        if yl <= e:
            return i
    return len(FOURTH_YL_EDGES) - 1


def _yl_bin_expr() -> pl.Expr:
    c = pl.col("yardline_100")
    e = pl.when(c <= FOURTH_YL_EDGES[0]).then(0)
    for i, edge in enumerate(FOURTH_YL_EDGES[1:], start=1):
        e = e.when(c <= edge).then(i)
    return e.otherwise(len(FOURTH_YL_EDGES) - 1).cast(pl.Int8)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 1e-5), 1 - 1e-5)
    return math.log(p / (1 - p))


def smoothed_table(df: pl.DataFrame, target: str, levels: list[tuple], m: float) -> dict[tuple, float]:
    """Hierarchical additive smoothing. levels: finest first, each a tuple of key columns.

    Returns a dict keyed by the finest-level tuple; missing cells are filled at lookup
    time via `lookup` using the coarser levels stored under keys prefixed with the level idx.
    """
    tot_w = df["w"].sum()
    p_global = float((df[target] * df["w"]).sum() / tot_w) if tot_w > 0 else 0.5
    table: dict[tuple, float] = {("G",): p_global}
    for li in range(len(levels) - 1, -1, -1):
        cols = levels[li]
        parent_cols = levels[li + 1] if li + 1 < len(levels) else None
        agg = df.group_by(list(cols)).agg((pl.col(target) * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
        for r in agg.iter_rows(named=True):
            key = tuple(int(r[c]) for c in cols)
            if parent_cols is None:
                parent = p_global
            else:
                pk = tuple(int(r[c]) for c in parent_cols)
                parent = table.get((li + 1,) + pk, p_global)
            table[(li,) + key] = (r["s"] + m * parent) / (r["n"] + m)
    return table


def lookup(table: dict, levels: list[tuple], values: dict[str, int]) -> float:
    for li, cols in enumerate(levels):
        v = table.get((li,) + tuple(values[c] for c in cols))
        if v is not None:
            return v
    return table[("G",)]


PASS_LEVELS = [
    ("down", "dist_b", "zone_b", "score_b", "time_b"),
    ("down", "dist_b", "zone_b", "time_b"),
    ("down", "dist_b", "zone_b"),
    ("down", "dist_b"),
    ("down",),
]
GO_LEVELS = [
    ("dist_b", "ylb4", "score_b", "time_b"),
    ("dist_b", "ylb4", "time_b"),
    ("dist_b", "ylb4"),
    ("ylb4",),
]


@dataclass
class PlayCallModel:
    pass_table: dict
    team_pass_offset: dict[str, float]
    go_table: dict
    team_go_offset: dict[str, float]
    fg_by_yl: list[float]          # P(FG | not going for it), index yardline_100 0..100
    memo: dict
    pass_shift: float = 0.0        # global logit calibration of P(pass) (config playcall.pass_logit_shift)

    def p_pass(self, team: str, down: int, togo: int, yl: int, diff: int, tb: int) -> float:
        key = (team, down, B.dist_b(togo), B.zone_b(yl), B.score_b(diff), tb)
        v = self.memo.get(key)
        if v is None:
            vals = {"down": key[1], "dist_b": key[2], "zone_b": key[3], "score_b": key[4], "time_b": tb}
            base = lookup(self.pass_table, PASS_LEVELS, vals)
            v = _sigmoid(_logit(base) + self.team_pass_offset.get(team, 0.0) + self.pass_shift)
            self.memo[key] = v
        return v

    def p_go(self, team: str, togo: int, yl: int, diff: int, tb: int) -> float:
        key = ("go", team, B.dist_b(togo), yl_bin4(yl), B.score_b(diff), tb)
        v = self.memo.get(key)
        if v is None:
            vals = {"dist_b": key[2], "ylb4": key[3], "score_b": key[4], "time_b": tb}
            base = lookup(self.go_table, GO_LEVELS, vals)
            v = _sigmoid(_logit(base) + self.team_go_offset.get(team, 0.0))
            self.memo[key] = v
        return v

    def p_fg(self, yl_eff: float) -> float:
        i = int(round(min(max(yl_eff, 1), 99)))
        return self.fg_by_yl[i]


def _team_offsets(df: pl.DataFrame, target: str, expected: str, k: float) -> dict[str, float]:
    agg = df.group_by("posteam").agg(
        (pl.col(target) * pl.col("w")).sum().alias("obs"), (pl.col(expected) * pl.col("w")).sum().alias("exp"),
        pl.col("w").sum().alias("n"))
    out = {}
    for r in agg.iter_rows(named=True):
        n = r["n"]
        if n <= 0:
            continue
        off = _logit(r["obs"] / n) - _logit(r["exp"] / n)
        out[r["posteam"]] = n / (n + k) * off
    return out


def build_playcall(plays: pl.DataFrame, cfg) -> PlayCallModel:
    """plays: prepared + weighted ('w') + garbage-time filtered snaps."""
    m = float(cfg.playcall["smoothing_m"])
    sc = plays.filter(
        pl.col("down").is_not_null() & (pl.col("two_point_attempt").fill_null(0) == 0) & ~pl.col("presnap")
        & pl.col("yardline_100").is_not_null() & pl.col("posteam").is_not_null()
    )
    sc = B.add_bucket_columns(sc)

    # ---- pass/run on downs 1-3 and 4th-down go plays
    pr = sc.filter(pl.col("call").is_not_null()).with_columns(is_pass=(pl.col("call") == "pass").cast(pl.Float64))
    pass_table = smoothed_table(pr, "is_pass", PASS_LEVELS, m)
    exp = [lookup(pass_table, PASS_LEVELS, r) for r in pr.select(["down", "dist_b", "zone_b", "score_b", "time_b"]).iter_rows(named=True)]
    pr = pr.with_columns(p_exp=pl.Series(exp))
    team_pass = _team_offsets(pr.filter(pl.col("down") <= 3), "is_pass", "p_exp", float(cfg.playcall["k_team_pass"]))

    # ---- 4th down decisions
    f4 = sc.filter((pl.col("down") == 4) & (pl.col("play_type").is_in(["pass", "run", "punt", "field_goal", "no_play"])))
    f4 = f4.with_columns(
        dec=pl.when(pl.col("play_type") == "punt").then(pl.lit("punt"))
        .when(pl.col("play_type") == "field_goal").then(pl.lit("fg"))
        .when(pl.col("call").is_not_null()).then(pl.lit("go")).otherwise(None),
        ylb4=_yl_bin_expr(),
    ).filter(pl.col("dec").is_not_null())
    f4 = f4.with_columns(is_go=(pl.col("dec") == "go").cast(pl.Float64))
    go_table = smoothed_table(f4, "is_go", GO_LEVELS, m)
    exp_go = [lookup(go_table, GO_LEVELS, r) for r in f4.select(["dist_b", "ylb4", "score_b", "time_b"]).iter_rows(named=True)]
    f4 = f4.with_columns(p_exp=pl.Series(exp_go))
    team_go = _team_offsets(f4, "is_go", "p_exp", float(cfg.playcall["k_team_4th"]))

    # FG vs punt by yardline (kicks only), smoothed with a +/-1 yard window (a wider window leaks the high
    # attempt rate at 35-37 across the range cliff and inflates 55+ yard attempts)
    kicks = f4.filter(pl.col("dec") != "go").with_columns(is_fg=(pl.col("dec") == "fg").cast(pl.Float64))
    by = kicks.group_by("yardline_100").agg((pl.col("is_fg") * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
    s_arr = [0.0] * 101
    n_arr = [0.0] * 101
    for r in by.iter_rows(named=True):
        y = int(r["yardline_100"])
        if 0 <= y <= 100:
            s_arr[y] += r["s"]
            n_arr[y] += r["n"]
    fg_by_yl = [0.0] * 101
    for y in range(101):
        lo, hi = max(0, y - 1), min(100, y + 1)
        s, n = sum(s_arr[lo:hi + 1]), sum(n_arr[lo:hi + 1])
        prior = 1.0 if y <= 30 else (0.0 if y >= 45 else 1.0 - (y - 30) / 15)
        fg_by_yl[y] = (s + 2 * prior) / (n + 2)
    return PlayCallModel(pass_table, team_pass, go_table, team_go, fg_by_yl, {},
                         float(cfg.playcall.get("pass_logit_shift", 0.0)))
