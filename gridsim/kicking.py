"""Kicking game: field goals, extra points, two-point tries, punts, kickoffs.

FG:   league logistic P(make | distance) = sigmoid(b0 + b1*d + b2*d^2), fit by IRLS on recent
      seasons; per-kicker logit offset, empirical-Bayes shrunk hard toward 0:
          offset = sum(make - p) / (sum p(1-p) + k_kicker)
      (FG and XP attempts both inform the offset.)
XP:   league make rate, kicker offset applied in logit space. 2PT: league success rate.
Punts: league pool by 5-yard line-of-scrimmage bin; each row = net field-position change plus
      flags (touchback, return TD, muff retained). Punting team net offset shrunk to league.
Kickoffs: league pool restricted to the current kickoff-rules era; receiving start position,
      return TDs, kicking-team recoveries. Onside attempts use the onside rows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def fit_fg_logistic(dist: np.ndarray, made: np.ndarray, w: np.ndarray) -> np.ndarray:
    X = np.column_stack([np.ones_like(dist), dist / 10.0, (dist / 10.0) ** 2])
    beta = np.array([5.0, -0.8, 0.0])
    for _ in range(50):
        p = _sigmoid(X @ beta)
        W = w * p * (1 - p) + 1e-9
        grad = X.T @ (w * (made - p))
        H = (X * W[:, None]).T @ X + np.eye(3) * 1e-6
        step = np.linalg.solve(H, grad)
        beta = beta + step
        if np.abs(step).max() < 1e-8:
            break
    return beta


@dataclass
class PuntRow:
    net: int            # yards from line of scrimmage to where receiving team takes over (punting coords)
    touchback: bool
    ret_td: bool        # receiving team scored
    kick_td: bool       # punting team scored (muff recovered in end zone)
    retained: bool      # punting team keeps the ball (muff / penalty first down)
    elapsed: float


@dataclass
class KickingModel:
    fg_beta: np.ndarray
    kicker_offset: dict[str, float]
    team_kicker: dict[str, str]
    team_kicker_name: dict[str, str]
    xp_rate: float
    two_pt_rate: float
    fg_block_share: float         # share of non-made FGs that were blocked
    punt_bins: dict[int, list[PuntRow]]
    punt_team_offset: dict[str, float]
    ko_rows: list[tuple]          # (rec_yl, kick_retained, ret_td, kick_td, elapsed)
    ko_team_offset: dict[str, float]
    onside_rows: list[tuple]
    onside_rate_fallback: float
    tb_yl: int
    lz_tb_yl: int

    def p_fg(self, dist: float, kicker: str | None) -> float:
        d = dist / 10.0
        x = self.fg_beta[0] + self.fg_beta[1] * d + self.fg_beta[2] * d * d
        x += self.kicker_offset.get(kicker, 0.0) if kicker else 0.0
        return 1.0 / (1.0 + math.exp(-x))

    def p_xp(self, kicker: str | None) -> float:
        p = min(max(self.xp_rate, 1e-4), 1 - 1e-4)
        x = math.log(p / (1 - p)) + (self.kicker_offset.get(kicker, 0.0) if kicker else 0.0)
        return 1.0 / (1.0 + math.exp(-x))

    def kicker_range_shift(self, kicker: str | None) -> float:
        """Yards of extra range implied by the kicker's offset at ~50 yards (used for FG-vs-punt)."""
        off = self.kicker_offset.get(kicker, 0.0) if kicker else 0.0
        d = 5.0
        slope = -(self.fg_beta[1] + 2 * self.fg_beta[2] * d) / 10.0  # logit per yard (positive)
        return off / slope if slope > 1e-6 else 0.0


def punt_bin(yl: int) -> int:
    return min(max(int(yl) // 5, 0), 19)


def build_kicking(plays: pl.DataFrame, fg_plays: pl.DataFrame, ko_plays: pl.DataFrame, cfg, last_kicker_plays: pl.DataFrame,
                  onside_plays: pl.DataFrame) -> KickingModel:
    kcfg = cfg.kicking
    # ---- field goals + kicker offsets
    fg = fg_plays.filter((pl.col("play_type") == "field_goal") & pl.col("kick_distance").is_not_null())
    dist = fg["kick_distance"].to_numpy().astype(float)
    made = (fg["field_goal_result"] == "made").fill_null(False).to_numpy().astype(float)
    w = fg["w"].to_numpy().astype(float)
    beta = fit_fg_logistic(dist, made, w)
    p = _sigmoid(beta[0] + beta[1] * dist / 10 + beta[2] * (dist / 10) ** 2)

    xp = fg_plays.filter(pl.col("play_type") == "extra_point")
    xp_made = (xp["extra_point_result"] == "good").fill_null(False).to_numpy().astype(float)
    xp_w = xp["w"].to_numpy().astype(float)
    xp_rate = float((xp_made * xp_w).sum() / max(xp_w.sum(), 1e-9))

    kick_df = pl.concat([
        pl.DataFrame({"kicker": fg["kicker_player_id"], "y": made, "p": p, "w": w}),
        pl.DataFrame({"kicker": xp["kicker_player_id"], "y": xp_made, "p": np.full(len(xp_made), xp_rate), "w": xp_w}),
    ]).filter(pl.col("kicker").is_not_null())
    agg = kick_df.group_by("kicker").agg(((pl.col("y") - pl.col("p")) * pl.col("w")).sum().alias("r"),
                                         (pl.col("p") * (1 - pl.col("p")) * pl.col("w")).sum().alias("info"))
    k_k = float(kcfg["k_kicker"])
    kicker_offset = {r["kicker"]: r["r"] / (r["info"] + k_k) for r in agg.iter_rows(named=True)}

    non_made = fg.filter(pl.col("field_goal_result") != "made")
    block_share = float((non_made["field_goal_result"] == "blocked").mean() or 0.0) if non_made.height else 0.1

    tp = fg_plays.filter(pl.col("two_point_attempt").fill_null(0) == 1)
    tp_ok = (tp["two_point_conv_result"] == "success").fill_null(False).to_numpy().astype(float)
    tp_w = tp["w"].to_numpy().astype(float)
    two_pt = float((tp_ok * tp_w).sum() / max(tp_w.sum(), 1e-9)) if tp.height else 0.47

    # current kicker for each team: most recent kick attempt
    lk = (last_kicker_plays.filter(pl.col("play_type").is_in(["field_goal", "extra_point"]) & pl.col("kicker_player_id").is_not_null())
          .sort(["season", "week", "game_id", "play_id"]).group_by("posteam").agg(pl.col("kicker_player_id").last(), pl.col("kicker_player_name").last()))
    team_kicker = {r["posteam"]: r["kicker_player_id"] for r in lk.iter_rows(named=True)}
    team_kicker_name = {r["posteam"]: r["kicker_player_name"] for r in lk.iter_rows(named=True)}

    # ---- punts (league pool by bin + punting team offset)
    pu = plays.filter((pl.col("play_type") == "punt") & pl.col("rec_yl").is_not_null())
    pu = pu.with_columns(
        net=(pl.col("yardline_100") - (100 - pl.col("rec_yl"))),
        tb=(pl.col("touchback").fill_null(0) == 1) & ~pl.col("kick_retained"),
        ret_td=(pl.col("touchdown").fill_null(0) == 1) & (pl.col("td_team") == pl.col("defteam")),
        kick_td=(pl.col("touchdown").fill_null(0) == 1) & (pl.col("td_team") == pl.col("posteam")),
        el=pl.col("elapsed").fill_null(8.0).clip(0, 25),
        pb=(pl.col("yardline_100") // 5).clip(0, 19).cast(pl.Int32),
    )
    punt_bins: dict[int, list[PuntRow]] = {}
    for r in pu.select(["pb", "net", "tb", "ret_td", "kick_td", "kick_retained", "el", "w"]).iter_rows():
        punt_bins.setdefault(r[0], []).append(PuntRow(int(round(r[1])), bool(r[2]), bool(r[3]), bool(r[4]), bool(r[5]), float(r[6])))
    bin_mean = pu.group_by("pb").agg(((pl.col("net") * pl.col("w")).sum() / pl.col("w").sum()).alias("bm"))
    pu = pu.join(bin_mean, on="pb").with_columns(res=pl.col("net") - pl.col("bm"))
    ag = pu.filter(~pl.col("tb")).group_by("posteam").agg((pl.col("res") * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
    kp = float(kcfg["k_punter"])
    punt_team_offset = {r["posteam"]: r["s"] / (r["n"] + kp) for r in ag.iter_rows(named=True)}

    # ---- kickoffs (era-filtered)
    kr = cfg.kickoff_rules
    tb_yl, lz_yl = int(kr["touchback_yardline"]), int(kr["landing_zone_touchback_yardline"])
    ko = ko_plays.filter((pl.col("play_type") == "kickoff") & pl.col("rec_yl").is_not_null())
    ko = ko.with_columns(onside=pl.col("desc").str.contains("(?i)onside").fill_null(False),
                         ret_td=(pl.col("touchdown").fill_null(0) == 1) & (pl.col("td_team") == pl.col("posteam")),
                         kick_td=(pl.col("touchdown").fill_null(0) == 1) & (pl.col("td_team") == pl.col("defteam")),
                         el=pl.col("elapsed").fill_null(5.0).clip(0, 20))
    normal = ko.filter(~pl.col("onside") & (pl.col("yardline_100") == 35))
    # remap touchback spots to the configured yard lines
    normal = normal.with_columns(
        rec_yl=pl.when((pl.col("touchback").fill_null(0) == 1) & ~pl.col("kick_retained") & (pl.col("rec_yl") <= 70) & (pl.col("rec_yl") >= 60))
        .then(100 - tb_yl)
        .when((pl.col("touchback").fill_null(0) == 1) & ~pl.col("kick_retained") & (pl.col("rec_yl") == 80))
        .then(100 - lz_yl).otherwise(pl.col("rec_yl")))
    ko_rows = [(int(r[0]), bool(r[1]), bool(r[2]), bool(r[3]), float(r[4]))
               for r in normal.select(["rec_yl", "kick_retained", "ret_td", "kick_td", "el"]).iter_rows()]
    mean_rec = float(normal["rec_yl"].mean()) if normal.height else 70.0
    agk = normal.filter(~pl.col("kick_retained")).group_by("defteam").agg(
        ((pl.col("rec_yl") - mean_rec) * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
    kk = float(kcfg["k_kickoff_team"])
    ko_team_offset = {r["defteam"]: r["s"] / (r["n"] + kk) for r in agk.iter_rows(named=True)}
    # onside outcomes: pool every dynamic-kickoff season (2024+) available; too rare per era
    onside = onside_plays.filter((pl.col("play_type") == "kickoff") & pl.col("rec_yl").is_not_null()
                                 & pl.col("desc").str.contains("(?i)onside").fill_null(False) & (pl.col("season") >= 2024))
    onside = onside.with_columns(ret_td=(pl.col("touchdown").fill_null(0) == 1) & (pl.col("td_team") == pl.col("posteam")),
                                 kick_td=(pl.col("touchdown").fill_null(0) == 1) & (pl.col("td_team") == pl.col("defteam")),
                                 el=pl.col("elapsed").fill_null(5.0).clip(0, 20))
    onside_rows = [(int(r[0]), bool(r[1]), bool(r[2]), bool(r[3]), float(r[4]))
                   for r in onside.select(["rec_yl", "kick_retained", "ret_td", "kick_td", "el"]).iter_rows()]

    return KickingModel(beta, kicker_offset, team_kicker, team_kicker_name, xp_rate, two_pt, block_share,
                        punt_bins, punt_team_offset, ko_rows, ko_team_offset, onside_rows, 0.10, tb_yl, lz_yl)
