"""Precomputed sampling pools for scrimmage play outcomes.

Layout: every eligible historical snap gets a row id. A *pool* is a contiguous segment of a
flat index list with its own cumulative weights, so sampling = bisect on a Python list.
Three cumulative-weight variants exist for the home-field tilt (base / home offense / away
offense). Pools exist at several bucket levels (see buckets.LEAGUE_LEVELS / TEAM_LEVELS) for
the league and for every team on each side of the ball.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np
import polars as pl

from . import buckets as B

# clock kind codes for scrimmage rows
K_INC, K_COMP, K_RUN, K_SACK, K_SCR, K_PEN = 0, 1, 2, 3, 4, 5
KIND_NAMES = ["inc", "comp", "run", "sack", "scr", "pen"]


@dataclass
class Segment:
    start: int
    end: int
    n: float  # weighted size


@dataclass
class OutcomePools:
    # row attributes (python lists indexed by row id)
    rows: dict[str, list]
    idx: list[int]
    cum: dict[int, list[float]]          # tilt variant (0 base, 1 home offense, -1 away offense) -> flat cumulative
    segs: dict[tuple, Segment]
    km: dict                             # call or (call, zone) -> survival S(g) = P(gain >= g), g = 0..100
    teams: list[str]
    n_rows: int = 0
    memo: dict = field(default_factory=dict)
    qb_dropbacks: dict = field(default_factory=dict)
    league_epa: dict = field(default_factory=dict)
    eff: dict = field(default_factory=dict)
    ref_sits: dict = field(default_factory=dict)

    def sample(self, seg: Segment, u: float, tilt: int) -> int:
        cum = self.cum[tilt]        # cumulative weights restart at 0 in every segment
        lo = seg.start
        target = u * cum[seg.end - 1]
        j = bisect_right(cum, target, lo, seg.end)
        if j >= seg.end:
            j = seg.end - 1
        return self.idx[j]


def _kind_expr() -> pl.Expr:
    return (
        pl.when(pl.col("np_pen")).then(K_PEN)
        .when(pl.col("sack") == 1).then(K_SACK)
        .when(pl.col("qb_scramble") == 1).then(K_SCR)
        .when(pl.col("incomplete_pass") == 1).then(K_INC)
        .when(pl.col("call") == "pass").then(K_COMP)
        .otherwise(K_RUN)
        .cast(pl.Int8)
    )


def scrimmage_rows(plays: pl.DataFrame) -> pl.DataFrame:
    """Rows eligible for scrimmage outcome pools (garbage time already removed by caller)."""
    df = plays.filter(
        pl.col("call").is_not_null() & pl.col("down").is_not_null() & ~pl.col("presnap")
        & (pl.col("two_point_attempt").fill_null(0) == 0) & pl.col("yardline_100").is_not_null()
        & pl.col("posteam").is_not_null() & pl.col("defteam").is_not_null()
    )
    df = B.add_bucket_columns(df)
    return df.with_columns(kind=_kind_expr())


def _km_survival(gain: np.ndarray, censored: np.ndarray, w: np.ndarray) -> list[float]:
    """Weighted Kaplan-Meier S(g) = P(G >= g) for g in 0..100, TD plays censored at their yardline."""
    S = [0.0] * 102
    unc = ~censored
    # P(G >= 0): censored values are >= 1 always
    tot = w.sum()
    S[0] = float((w[(gain >= 0) | censored]).sum() / tot) if tot > 0 else 1.0
    s = S[0]
    for g in range(0, 101):
        at_risk = w[((gain >= g) & unc) | (censored & (gain >= g))].sum()
        ev = w[unc & (gain == g)].sum()
        h = ev / at_risk if at_risk > 0 else 1.0
        if g >= 1:
            S[g] = s
        s = s * (1.0 - h)
    S[101] = 0.0
    # monotone guard
    for g in range(1, 102):
        if S[g] > S[g - 1]:
            S[g] = S[g - 1]
    return S


BACKUP_QB = "__BACKUP_QB__"


def build_outcome_pools(df: pl.DataFrame, teams: list[str], hfa_tilt: float, backup_max: float = 150.0) -> OutcomePools:
    """df: scrimmage_rows output with a 'w' (season weight) column."""
    df = df.with_row_index("rid")
    n = df.height
    g = lambda c, fill=0: df[c].fill_null(fill).to_numpy()  # noqa: E731

    yards = np.rint(g("yards")).astype(int)
    td_off = g("td_off", False).astype(bool)
    rows = {
        "yards": yards.tolist(),
        "td": td_off.tolist(),
        "censor": np.where(td_off, g("yardline_100"), -1).astype(int).tolist(),
        "turnover": g("turnover", False).astype(bool).tolist(),
        "int": (g("interception") == 1).tolist(),
        "to_gain": np.rint(g("to_gain")).astype(int).tolist(),
        "td_def": g("td_def", False).astype(bool).tolist(),
        "safety": (g("safety") == 1).tolist(),
        "auto_first": g("auto_first", False).astype(bool).tolist(),
        "np_pen": g("np_pen", False).astype(bool).tolist(),
        "kind": g("kind").astype(int).tolist(),
        "oob": (g("out_of_bounds") == 1).tolist(),
        "epa": g("epa", 0.0).astype(float).tolist(),
        "pass_yds": np.rint(g("passing_yards")).astype(int).tolist(),
        "rush_yds": np.rint(g("rushing_yards")).astype(int).tolist(),
        "pen_yds": np.rint(g("penalty_yards")).astype(int).tolist(),
        "penalty": (g("penalty") == 1).tolist(),
        "pen_on_off": (df["penalty_team"] == df["posteam"]).fill_null(False).to_numpy().tolist(),
        "air": np.rint(g("air_yards")).astype(int).tolist(),
        "passer": df["passer_player_id"].to_list(),
        "rusher": df["rusher_player_id"].to_list(),
        "receiver": df["receiver_player_id"].to_list(),
        "call_pass": (df["call"] == "pass").to_numpy().tolist(),
        "yl": g("yardline_100").astype(int).tolist(),
    }
    w = g("w", 0.0).astype(float)
    epa = np.clip(g("epa", 0.0).astype(float), -8, 8)
    wt = {0: w, 1: w * np.exp(hfa_tilt * epa), -1: w * np.exp(-hfa_tilt * epa)}

    idx_parts: list[np.ndarray] = []
    segs: dict[tuple, Segment] = {}
    pos = 0

    def add_groups(prefix: tuple, sub: pl.DataFrame, cols: tuple):
        nonlocal pos
        if cols:
            # sorted keys: segment layout must not depend on polars' (thread-dependent) group order
            grouped = sub.group_by(list(cols), maintain_order=False).agg(pl.col("rid"))
            items = sorted(((tuple(r[c] for c in cols), r["rid"]) for r in grouped.iter_rows(named=True)),
                           key=lambda kv: kv[0])
        else:
            items = [((), sub["rid"].to_list())]
        for key_vals, rids in items:
            arr = np.asarray(rids, dtype=np.int64)
            arr.sort()
            idx_parts.append(arr)
            segs[prefix + tuple(int(v) for v in key_vals)] = Segment(pos, pos + len(arr), float(w[arr].sum()))
            pos += len(arr)

    for call in ("pass", "run"):
        sub_c = df.filter(pl.col("call") == call).select(
            ["rid", "posteam", "defteam", "down", "dist_b", "zone_b", "time_b", "downgrp2", "downgrp3", "distgrp3", "rz"])
        for li, cols in enumerate(B.LEAGUE_LEVELS):
            add_groups(("L", call, li), sub_c, cols)
        for side, tcol in (("off", "posteam"), ("def", "defteam")):
            for team in teams:
                sub_t = sub_c.filter(pl.col(tcol) == team)
                if sub_t.height == 0:
                    continue
                for li, cols in enumerate(B.TEAM_LEVELS):
                    add_groups(("T", call, side, team, li), sub_t, cols)

    # QB pools (offense dropbacks by passer) + a league pool of low-sample / backup QBs
    drop = df.filter((pl.col("call") == "pass") & pl.col("qb_id").is_not_null()).select(
        ["rid", "qb_id", "down", "dist_b", "zone_b", "time_b", "downgrp2", "downgrp3", "distgrp3", "rz", "w"])
    qb_n = drop.group_by("qb_id").agg(pl.col("w").sum().alias("n"))
    qb_tot = {r["qb_id"]: r["n"] for r in qb_n.iter_rows(named=True)}
    for qb in sorted(qb_tot):
        sub_q = drop.filter(pl.col("qb_id") == qb)
        for li, cols in enumerate(B.TEAM_LEVELS):
            add_groups(("Q", qb, li), sub_q, cols)
    low = sorted(q for q, n_ in qb_tot.items() if n_ < backup_max)
    sub_b = drop.filter(pl.col("qb_id").is_in(low))
    if sub_b.height:
        for li, cols in enumerate(B.TEAM_LEVELS):
            add_groups(("Q", BACKUP_QB, li), sub_b, cols)

    idx = np.concatenate(idx_parts) if idx_parts else np.zeros(0, dtype=np.int64)
    cum: dict[int, list[float]] = {}
    starts = np.zeros(len(idx), dtype=bool)
    for seg in segs.values():
        if seg.end > seg.start:
            starts[seg.start] = True
    seg_id = np.cumsum(starts) - 1
    for variant, wv in wt.items():
        vals = wv[idx]
        c = np.cumsum(vals)
        # restart at every segment: subtract the running total at each segment's start
        first = np.where(starts)[0]
        offsets = np.concatenate([[0.0], c[first[1:] - 1]]) if len(first) else np.zeros(0)
        c = c - offsets[seg_id]
        cum[variant] = np.round(c, 9).tolist()

    # Kaplan-Meier survival of gains per call, for the TD-censoring extension
    # Estimated per field zone: the tail beyond the goal line is shorter near the goal line
    # (compressed field), so a pooled curve would over-extend red-zone TD rows.
    km = {}
    zone = g("zone_b").astype(int)
    for call in ("pass", "run"):
        m0 = (df["call"] == call).to_numpy() & ~g("turnover", False).astype(bool) & ~g("np_pen", False).astype(bool)
        km[call] = _km_survival(yards[m0], td_off[m0], w[m0])
        for z in range(5):
            m = m0 & (zone == z)
            km[(call, z)] = _km_survival(yards[m], td_off[m], w[m]) if m.sum() >= 200 else km[call]

    out = OutcomePools(rows=rows, idx=idx.tolist(), cum=cum, segs=segs, km=km, teams=teams, n_rows=n)
    out.qb_dropbacks = qb_tot

    # ---- EPA effects for the additive matchup correction (see engine.matchup_lambda)
    epa_raw = np.clip(g("epa", 0.0).astype(float), -8, 8)
    rows["epa_c"] = np.clip(epa_raw, -4, 4).tolist()
    is_pass = (df["call"] == "pass").to_numpy()
    league = {}
    for call, m in (("pass", is_pass), ("run", ~is_pass)):
        league[call] = float((epa_raw[m] * w[m]).sum() / max(w[m].sum(), 1e-9))
    eff: dict = {}
    for side, col in (("off", "posteam"), ("def", "defteam")):
        tcol = df[col].to_numpy()
        for call, m in (("pass", is_pass), ("run", ~is_pass)):
            for team in teams:
                mm = m & (tcol == team)
                n_ = float(w[mm].sum())
                if n_ > 0:
                    eff[(side, team, call)] = (float((epa_raw[mm] * w[mm]).sum() / n_) - league[call], n_)
    qcol = df["qb_id"].to_numpy() if "qb_id" in df.columns else np.array([None] * n)
    for qb in qb_tot:
        mm = is_pass & (qcol == qb)
        n_ = float(w[mm].sum())
        if n_ > 0:
            eff[("qb", qb, "pass")] = (float((epa_raw[mm] * w[mm]).sum() / n_) - league["pass"], n_)
    low_set = set(low)
    mm = is_pass & np.array([q in low_set for q in qcol])
    eff[("qb", BACKUP_QB, "pass")] = ((float((epa_raw[mm] * w[mm]).sum() / max(w[mm].sum(), 1e-9)) - league["pass"]) if mm.any() else -0.1,
                                      float(w[mm].sum()))
    out.league_epa = league
    out.eff = eff
    # reference situations (league mix) used to measure a matchup's natural EPA shift
    rng = np.random.default_rng(0)
    ref = {}
    sit = df.select(["call", "down", "ydstogo", "yardline_100", "time_b"]).to_numpy()
    for call in ("pass", "run"):
        cand = np.where(sit[:, 0] == call)[0]
        pick = rng.choice(cand, size=min(1500, len(cand)), replace=False) if len(cand) else []
        ref[call] = [(int(sit[i, 1]), int(sit[i, 2]), int(sit[i, 3]), int(sit[i, 4])) for i in pick]
    out.ref_sits = ref
    return out


def resolve(pools: OutcomePools, call: str, side: str, team: str, down: int, togo_b: int, zone: int, tb: int,
            min_n: float, k: float, league_min_n: float):
    """Return (team_segment | None, team_weight, league_segment) for a situation; memoized."""
    key = (call, side, team, down, togo_b, zone, tb, min_n, k, league_min_n)
    hit = pools.memo.get(key)
    if hit is not None:
        return hit
    sv = {
        "down": down, "dist_b": togo_b, "zone_b": zone, "time_b": tb,
        "downgrp2": 1 if down >= 3 else 0, "downgrp3": 3 if down >= 3 else down,
        "distgrp3": 2 if togo_b >= 2 else togo_b, "rz": 1 if zone >= 3 else 0,
    }
    tseg, tw = None, 0.0
    for li, cols in enumerate(B.TEAM_LEVELS):
        seg = pools.segs.get(("T", call, side, team, li) + tuple(sv[c] for c in cols))
        if seg is not None and (seg.n >= min_n or li == len(B.TEAM_LEVELS) - 1):
            tseg = seg
            tw = seg.n / (seg.n + k) if k > 0 else 1.0
            break
    lseg = None
    for li, cols in enumerate(B.LEAGUE_LEVELS):
        seg = pools.segs.get(("L", call, li) + tuple(sv[c] for c in cols))
        if seg is not None and seg.n >= league_min_n:
            lseg = seg
            break
    if lseg is None:  # last resort: any league pool for the call at the coarsest level present
        for li in range(len(B.LEAGUE_LEVELS) - 1, -1, -1):
            cols = B.LEAGUE_LEVELS[li]
            seg = pools.segs.get(("L", call, li) + tuple(sv[c] for c in cols))
            if seg is not None:
                lseg = seg
                break
    out = (tseg, tw, lseg)
    pools.memo[key] = out
    return out


def resolve_qb(pools: OutcomePools, qb: str, down: int, togo_b: int, zone: int, min_n: float, k_qb: float):
    """(qb_segment | None, qb_weight, backup_segment | None) for the starting QB's own dropbacks."""
    key = ("QB", qb, down, togo_b, zone, min_n, k_qb)
    hit = pools.memo.get(key)
    if hit is not None:
        return hit
    sv = {"downgrp2": 1 if down >= 3 else 0, "downgrp3": 3 if down >= 3 else down,
          "distgrp3": 2 if togo_b >= 2 else togo_b, "rz": 1 if zone >= 3 else 0}

    def pick(name):
        for li, cols in enumerate(B.TEAM_LEVELS):
            seg = pools.segs.get(("Q", name, li) + tuple(sv[c] for c in cols))
            if seg is not None and (seg.n >= min_n or li == len(B.TEAM_LEVELS) - 1):
                return seg
        return None

    qseg = pick(qb) if qb else None
    qw = qseg.n / (qseg.n + k_qb) if qseg is not None else 0.0
    out = (qseg, qw, pick(BACKUP_QB))
    pools.memo[key] = out
    return out


def km_extend(S: list[float], c: int, y: int, u: float) -> int:
    """Gain for a TD row censored at c applied at yardline y > c. Returns y (TD) or a gain in [c, y)."""
    sc = S[c] if c < len(S) else 0.0
    if sc <= 0:
        return y
    sy = S[y] if y < len(S) else 0.0
    p_td = sy / sc
    if u < p_td:
        return y
    # conditional on c <= G < y: invert S between sy and sc
    v = sc - (u - p_td) / (1 - p_td) * (sc - sy) if p_td < 1 else sy
    g = c
    while g + 1 < y and S[g + 1] >= v:
        g += 1
    return g


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))
