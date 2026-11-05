"""Player attribution (Phase 4).

Each sampled scrimmage play is attributed to CURRENT players:
  dropbacks      -> the game's starting QB (rolled per sim by availability)
  targets        -> receivers by target share over the team's last N games (default 6)
  designed runs  -> rushers by carry share (QB designed runs included)
Out players (availability roll for that sim) are removed and the remaining shares renormalized, i.e. their
usage flows to teammates in proportion to recent usage. Players new to the team (trades/signings) start
from a depth-chart prior share that ramps toward observed usage as games with the team accumulate.

Efficiency: a player's yards per target / per carry relative to the league, shrunk with k_player_eff, is
applied as a multiplier to the sampled play's positive yardage. Multipliers are normalized so the team's
usage-weighted mean is exactly 1, so team-level outcomes stay what the pools say; only the split moves.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .pools import K_COMP, K_INC, K_RUN, K_SACK, K_SCR

PSTATS = ["pass_att", "comp", "pass_yds", "pass_td", "ints", "rush_att", "rush_yds", "rush_td",
          "targets", "rec", "rec_yds", "rec_td"]
PI = {k: i for i, k in enumerate(PSTATS)}
STAT_ALIASES = {"rec_yds": "rec_yds", "receiving_yards": "rec_yds", "receptions": "rec", "rec": "rec",
                "rush_yds": "rush_yds", "rushing_yards": "rush_yds", "pass_yds": "pass_yds", "passing_yards": "pass_yds",
                "pass_td": "pass_td", "ints": "ints", "completions": "comp", "comp": "comp", "rush_att": "rush_att",
                "targets": "targets", "pass_att": "pass_att", "anytime_td": "anytime_td", "rec_td": "rec_td",
                "rush_td": "rush_td", "rush_rec_yds": "rush_rec_yds"}

# depth-chart prior shares for players without enough games with their current team
PRIOR_TGT = {("WR", 1): 0.22, ("WR", 2): 0.17, ("WR", 3): 0.11, ("TE", 1): 0.14, ("TE", 2): 0.04, ("RB", 1): 0.09, ("RB", 2): 0.04}
PRIOR_CAR = {("RB", 1): 0.55, ("RB", 2): 0.25, ("RB", 3): 0.06, ("QB", 1): 0.08, ("WR", 1): 0.01}


def _usage(plays: pl.DataFrame, team: str, n_games: int) -> tuple[dict, dict, dict, int]:
    t = plays.filter(pl.col("posteam") == team)
    games = (t.select(["game_id", "season", "week"]).unique().sort(["season", "week"]).tail(n_games)["game_id"].to_list())
    r = t.filter(pl.col("game_id").is_in(games) & pl.col("call").is_not_null() & ~pl.col("np_pen"))
    tg = r.filter((pl.col("call") == "pass") & pl.col("receiver_player_id").is_not_null())
    ca = r.filter((pl.col("call") == "run") & pl.col("rusher_player_id").is_not_null())
    # share = opportunities / team opportunities in the games the player appeared in (a missed game
    # should not dilute a healthy player's role)
    def shares(frame, col):
        per_game = frame.group_by("game_id").agg(pl.len().alias("team_n"))
        pg = frame.group_by([col, "game_id"]).agg(pl.len().alias("n")).join(per_game, on="game_id")
        agg = pg.group_by(col).agg(pl.col("n").sum(), pl.col("team_n").sum())
        return {k: n / max(tn, 1) for k, n, tn in agg.iter_rows()}
    tshare = shares(tg, "receiver_player_id")
    cshare = shares(ca, "rusher_player_id")
    gp = {}
    for col, frame in (("receiver_player_id", tg), ("rusher_player_id", ca)):
        for pid, n in frame.group_by(col).agg(pl.col("game_id").n_unique()).iter_rows():
            gp[pid] = max(gp.get(pid, 0), n)
    return tshare, cshare, gp, len(games)


def _efficiency(plays: pl.DataFrame, k: float) -> tuple[dict, dict]:
    """Shrunk yards-per-opportunity ratios vs league for receivers (per target) and rushers (per carry)."""
    r = plays.filter(pl.col("call").is_not_null() & ~pl.col("np_pen") & pl.col("down").is_not_null())
    tg = r.filter((pl.col("call") == "pass") & pl.col("receiver_player_id").is_not_null()).with_columns(
        y=pl.col("receiving_yards").fill_null(0))
    ca = r.filter((pl.col("call") == "run") & pl.col("rusher_player_id").is_not_null()).with_columns(
        y=pl.col("rushing_yards").fill_null(0))
    out = []
    for frame, col in ((tg, "receiver_player_id"), (ca, "rusher_player_id")):
        lg = float((frame["y"] * frame["w"]).sum() / max(frame["w"].sum(), 1e-9))
        agg = frame.group_by(col).agg((pl.col("y") * pl.col("w")).sum().alias("s"), pl.col("w").sum().alias("n"))
        out.append({pid: (s / n / lg * n + k) / (n + k) if n > 0 and lg > 0 else 1.0 for pid, s, n in agg.iter_rows()})
    return out[0], out[1]


class TeamPlayers:
    def __init__(self, team: str, tshare: dict, cshare: dict, eff_t: dict, eff_c: dict, names: dict, pos: dict):
        self.team = team
        self.tshare, self.cshare = tshare, cshare
        self.eff_t, self.eff_c = eff_t, eff_c
        self.names, self.pos = names, pos
        self._cache: dict = {}

    def dists(self, out: frozenset):
        hit = self._cache.get(out)
        if hit is not None:
            return hit
        res = []
        for share, eff in ((self.tshare, self.eff_t), (self.cshare, self.eff_c)):
            items = [(p, s) for p, s in share.items() if p not in out and s > 0]
            tot = sum(s for _, s in items) or 1.0
            ids = [p for p, _ in items]
            probs = [s / tot for _, s in items]
            effs = [eff.get(p, 1.0) for p in ids]
            norm = sum(pr * e for pr, e in zip(probs, effs)) or 1.0
            cum, c = [], 0.0
            for pr in probs:
                c += pr
                cum.append(c)
            res.append((ids, cum, [e / norm for e in effs]))
        self._cache[out] = res
        return res


class PlayerModel:
    def __init__(self, teams: tuple[TeamPlayers, TeamPlayers], qb_names: dict, n_alloc: int = 1000):
        self.teams = teams
        self.qb_names = qb_names
        self.cur: list[dict] = [{}, {}]
        self.games: list[list[dict]] = []
        self.pick = None
        self.dist = [None, None]
        self.out = [frozenset(), frozenset()]

    # ---- engine hooks ------------------------------------------------------------------
    def new_game(self, game) -> None:
        self.cur = [{}, {}]
        for t in (0, 1):
            outs = game.active.get(self.teams[t].team, set()) if game.active else set()
            self.out[t] = frozenset(outs)
            self.dist[t] = self.teams[t].dists(self.out[t])
        self.pick = None

    def _acc(self, t: int, pid: str) -> list:
        a = self.cur[t].get(pid)
        if a is None:
            a = [0.0] * len(PSTATS)
            self.cur[t][pid] = a
        return a

    def attribute(self, game, rid: int, off: int, kind: int) -> float:
        """Choose the players for this play; return the yardage multiplier for positive gains."""
        targets, carries = self.dist[off]
        u = game.u()
        if kind in (K_INC, K_COMP) or game.rows["int"][rid]:
            ids, cum, eff = targets
            if not ids:
                self.pick = ("pass", None)
                return 1.0
            i = _bisect(cum, u)
            self.pick = ("pass", ids[i])
            return eff[i] if kind == K_COMP else 1.0
        if kind == K_RUN:
            ids, cum, eff = carries
            if not ids:
                self.pick = ("run", None)
                return 1.0
            i = _bisect(cum, u)
            self.pick = ("run", ids[i])
            return eff[i]
        self.pick = ("qb", None)   # sack / scramble: the QB
        return 1.0

    def record(self, game, rid: int, gain: int, td: bool, off: int) -> None:
        R = game.rows
        kind = R["kind"][rid]
        qb = game.qb[off]
        # stat yards exclude penalty yardage tacked onto the play (same convention as the team box score)
        pen_part = R["yards"][rid] - (R["pass_yds"][rid] if kind == K_COMP else R["rush_yds"][rid])
        stat_gain = gain - pen_part
        what, pid = self.pick or ("qb", None)
        self.pick = None
        if kind == K_SACK:
            return
        if kind == K_SCR:
            if qb:
                a = self._acc(off, qb)
                a[PI["rush_att"]] += 1
                a[PI["rush_yds"]] += stat_gain
                if td:
                    a[PI["rush_td"]] += 1
            return
        if kind == K_RUN:
            if pid:
                a = self._acc(off, pid)
                a[PI["rush_att"]] += 1
                a[PI["rush_yds"]] += stat_gain
                if td:
                    a[PI["rush_td"]] += 1
            return
        # targeted pass: incomplete, complete, interception
        if qb:
            q = self._acc(off, qb)
            q[PI["pass_att"]] += 1
        if R["int"][rid]:
            if qb:
                q[PI["ints"]] += 1
            if pid:
                self._acc(off, pid)[PI["targets"]] += 1
            return
        if pid:
            r = self._acc(off, pid)
            r[PI["targets"]] += 1
        if kind == K_COMP:
            if qb:
                q[PI["comp"]] += 1
                q[PI["pass_yds"]] += stat_gain
                if td:
                    q[PI["pass_td"]] += 1
            if pid:
                r[PI["rec"]] += 1
                r[PI["rec_yds"]] += stat_gain
                if td:
                    r[PI["rec_td"]] += 1

    def kneel(self, game, off: int) -> None:
        qb = game.qb[off]
        if qb:
            a = self._acc(off, qb)
            a[PI["rush_att"]] += 1
            a[PI["rush_yds"]] -= 1

    def end_game(self, i: int) -> None:
        self.games.append(self.cur)

    # ---- results -----------------------------------------------------------------------
    def arrays(self) -> dict:
        """{(team_idx, pid): ndarray (n_sims, n_stats)}"""
        n = len(self.games)
        keys = sorted({(t, p) for g in self.games for t in (0, 1) for p in g[t]})
        out = {k: np.zeros((n, len(PSTATS))) for k in keys}
        for i, g in enumerate(self.games):
            for t in (0, 1):
                for p, a in g[t].items():
                    out[(t, p)][i] = a
        return out

    def name(self, t: int, pid: str) -> str:
        tp = self.teams[t]
        return tp.names.get(pid) or self.qb_names.get(pid) or pid

    def results(self) -> dict:
        arr = self.arrays()
        n = max(len(self.games), 1)
        res = {}
        for t in (0, 1):
            plist = []
            for (tt, pid), a in arr.items():
                if tt != t:
                    continue
                vol = a[:, PI["pass_att"]].mean() + a[:, PI["rush_att"]].mean() + a[:, PI["targets"]].mean()
                if vol < 0.5:
                    continue
                stats, hist = {}, {}
                for k in PSTATS:
                    x = a[:, PI[k]]
                    q = np.percentile(x, [10, 25, 50, 75, 90])
                    stats[k] = {"mean": float(x.mean()), "p10": float(q[0]), "p25": float(q[1]), "p50": float(q[2]),
                                "p75": float(q[3]), "p90": float(q[4])}
                    if x.any():
                        vals, cnts = np.unique(np.rint(x).astype(int), return_counts=True)
                        hist[k] = {"values": vals.tolist(), "counts": cnts.tolist()}
                x = a[:, PI["rush_yds"]] + a[:, PI["rec_yds"]]
                if x.any():
                    vals, cnts = np.unique(np.rint(x).astype(int), return_counts=True)
                    hist["rush_rec_yds"] = {"values": vals.tolist(), "counts": cnts.tolist()}
                anytd = float(np.mean((a[:, PI["rush_td"]] + a[:, PI["rec_td"]]) > 0))
                pos = self.teams[t].pos.get(pid) or ("QB" if a[:, PI["pass_att"]].mean() > 3 else "")
                plist.append({"id": pid, "name": self.name(t, pid), "pos": pos, "stats": stats, "anytime_td": anytd,
                              "volume": float(vol), "games": n, "hist": hist})
            order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3}
            plist.sort(key=lambda p: (order.get(p["pos"], 4), -p["volume"]))
            res[self.teams[t].team] = plist
        self._arrays = arr
        return res

    def find(self, name: str) -> tuple[int, str]:
        from .availability import NameMatchError, match_player
        arr = getattr(self, "_arrays", None) or self.arrays()
        hits = []
        for tt in (0, 1):
            roster = [{"gsis_id": p, "full_name": self.name(t, p), "last_name": self.name(t, p).split()[-1]}
                      for (t, p) in arr if t == tt and self.name(t, p)]
            try:
                hits.append((tt, match_player(name.strip(), roster, self.teams[tt].team)["gsis_id"]))
            except NameMatchError:
                pass
        if len(hits) != 1:
            cands = ", ".join(sorted({self.name(t, p) for (t, p) in arr}))[:400]
            raise ValueError(f"player '{name}' matched {len(hits)} players; candidates: {cands}")
        return hits[0]

    def stat_array(self, name: str, stat: str) -> np.ndarray:
        arr = getattr(self, "_arrays", None) or self.arrays()
        key = STAT_ALIASES.get(stat.lower())
        if key is None:
            raise ValueError(f"unknown stat {stat!r}; use one of {sorted(set(STAT_ALIASES))}")
        a = arr[self.find(name)]
        if key == "anytime_td":
            return ((a[:, PI["rush_td"]] + a[:, PI["rec_td"]]) > 0).astype(float)
        if key == "rush_rec_yds":
            return a[:, PI["rush_yds"]] + a[:, PI["rec_yds"]]
        return a[:, PI[key]]

    def prop_table(self, res, props: list[str]) -> list[dict]:
        from .availability import NameMatchError, match_player
        from .report import moe
        arr = getattr(self, "_arrays", None) or self.arrays()
        n = len(self.games)
        out = []
        for spec in props:
            try:
                name, stat, line = spec.rsplit(":", 2)
                line = float(line)
            except ValueError:
                raise ValueError(f"bad --prop {spec!r}; expected 'Player Name:stat:line'")
            stat = STAT_ALIASES.get(stat.strip().lower())
            if stat is None:
                raise ValueError(f"unknown prop stat in {spec!r}; use one of {sorted(set(STAT_ALIASES))}")
            hits = []
            for tt in (0, 1):
                roster = [{"gsis_id": p, "full_name": self.name(t, p), "last_name": self.name(t, p).split()[-1]}
                          for (t, p) in arr if t == tt and self.name(t, p)]
                try:
                    hits.append((tt, match_player(name.strip(), roster, self.teams[tt].team)["gsis_id"]))
                except NameMatchError:
                    pass
            if len(hits) != 1:
                cands = ", ".join(sorted({self.name(t, p) for (t, p) in arr}))[:400]
                raise ValueError(f"prop player '{name}' matched {len(hits)} players; candidates: {cands}")
            t, pid = hits[0]
            a = arr[(t, pid)]
            if stat == "anytime_td":
                x = ((a[:, PI["rush_td"]] + a[:, PI["rec_td"]]) > 0).astype(float)
            elif stat == "rush_rec_yds":
                x = a[:, PI["rush_yds"]] + a[:, PI["rec_yds"]]
            else:
                x = a[:, PI[stat]]
            ov = float(np.mean(x > line))
            push = float(np.mean(x == line))
            out.append({"player": self.name(t, pid), "team": self.teams[t].team, "stat": stat, "line": line,
                        "over": {"p": ov, "moe": moe(ov, n), "close": abs(ov - 0.5) <= moe(ov, n)},
                        "under": 1 - ov - push, "push": push, "median": float(np.median(x)), "mean": float(np.mean(x))})
        return out


def _bisect(cum: list, u: float) -> int:
    lo, hi = 0, len(cum) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if cum[mid] < u:
            lo = mid + 1
        else:
            hi = mid
    return lo


def build_player_model(cfg, model, season: int, through_week: int | None, home: str, away: str, avail=None) -> PlayerModel:
    from . import data as D
    from .api import _MODEL_CACHE
    from .model import _with_weights, load_prepared
    plays = _MODEL_CACHE.get(("plays", season))
    if plays is None:
        plays = load_prepared(season, cfg)
        _MODEL_CACHE[("plays", season)] = plays
    weights = D.season_weights(season, cfg)
    win = _with_weights(D.window_filter(plays, season, through_week, list(weights)), weights)
    pc = cfg.players
    n_games, k = int(pc["usage_games"]), float(pc["k_player_eff"])
    eff_t, eff_c = _efficiency(win, k)
    names: dict = {}
    for col, ncol in (("receiver_player_id", "receiver_player_name"), ("rusher_player_id", "rusher_player_name"),
                      ("passer_player_id", "passer_player_name")):
        for pid, nm in win.filter(pl.col(col).is_not_null()).group_by(col).agg(pl.col(ncol).last()).iter_rows():
            names[pid] = nm
    teams = []
    for ti, team in enumerate((home, away)):
        tshare, cshare, gp, ng = _usage(win, team, n_games)
        pos: dict = {}
        roster_ids = None
        if avail is not None:
            ta = avail.teams[ti]
            roster_ids = {r["gsis_id"] for r in ta.roster}
            for r in ta.roster:
                names[r["gsis_id"]] = r["full_name"]
                pos[r["gsis_id"]] = r["position"]
        if roster_ids:
            # players no longer on the roster (traded/cut) lose their share; new players ramp in from a prior
            tshare = {p: s for p, s in tshare.items() if p in roster_ids}
            cshare = {p: s for p, s in cshare.items() if p in roster_ids}
            rank: dict = {}
            for r in sorted(avail.teams[ti].roster, key=lambda r: r["full_name"]):
                rank.setdefault(r["position"], [])
            depth_rank = _depth_ranks(cfg, season, team)
            for pid, (p_pos, rk) in depth_rank.items():
                if pid not in roster_ids:
                    continue
                ramp = min(gp.get(pid, 0) / max(n_games, 1), 1.0)
                for share, prior in ((tshare, PRIOR_TGT), (cshare, PRIOR_CAR)):
                    pr = prior.get((p_pos, rk))
                    if pr and ramp < 1.0:
                        share[pid] = ramp * share.get(pid, 0.0) + (1 - ramp) * pr
                pos.setdefault(pid, p_pos)
        min_share = float(pc["min_share"])
        tshare = {p: s for p, s in tshare.items() if s >= min_share}
        cshare = {p: s for p, s in cshare.items() if s >= min_share}
        teams.append(TeamPlayers(team, tshare, cshare, eff_t, eff_c, names, pos))
    return PlayerModel((teams[0], teams[1]), model.qb_names)


def _depth_ranks(cfg, season: int, team: str) -> dict:
    """{gsis_id: (position, rank)} for skill players on the latest depth chart snapshot."""
    try:
        from .availability import _load_source
        d = _load_source("depth_charts", season, cfg)
    except Exception:
        return {}
    if d.height == 0:
        return {}
    d = d.filter((pl.col("team") == team) & pl.col("pos_abb").is_in(["QB", "RB", "WR", "TE"]))
    if d.height == 0:
        return {}
    d = d.filter(pl.col("dt") == d["dt"].max())
    out = {}
    for pos in ("QB", "RB", "WR", "TE"):
        sub = d.filter(pl.col("pos_abb") == pos).sort("pos_rank")
        for i, gid in enumerate(sub["gsis_id"].to_list(), start=1):
            if gid and gid not in out:
                out[gid] = (pos, i)
    return out


def onoff_effects(win: pl.DataFrame, team: str, player_ids: list[str], k: float, min_share: float = 0.08) -> dict:
    """Team offensive EPA/play with vs without each player (games after his first appearance for the team),
    shrunk: effect = n_without/(n_without + k) * (epa_without - epa_with). Negative = the offense is worse
    without him. Only players with a meaningful role (>= min_share of opportunities when active) qualify."""
    t = win.filter((pl.col("posteam") == team) & pl.col("call").is_not_null() & pl.col("epa").is_not_null()
                   & pl.col("down").is_not_null())
    if t.height == 0:
        return {}
    t = t.with_columns(e=pl.col("epa").clip(-4, 4))
    games = t.select(["game_id", "season", "week"]).unique().sort(["season", "week"])
    order = {g: i for i, g in enumerate(games["game_id"].to_list())}
    out = {}
    for pid in player_ids:
        inv = t.filter((pl.col("receiver_player_id") == pid) | (pl.col("rusher_player_id") == pid))
        if inv.height == 0:
            continue
        with_g = set(inv["game_id"].unique().to_list())
        first = min(order[g] for g in with_g)
        share = inv.height / max(t.filter(pl.col("game_id").is_in(list(with_g))).height, 1)
        if share < min_share:
            continue
        after = t.filter(pl.col("game_id").replace_strict(order, return_dtype=pl.Int64) >= first)
        w = after.filter(pl.col("game_id").is_in(list(with_g)))
        wo = after.filter(~pl.col("game_id").is_in(list(with_g)))
        if wo.height == 0:
            continue
        ew = float((w["e"] * w["w"]).sum() / w["w"].sum())
        eo = float((wo["e"] * wo["w"]).sum() / wo["w"].sum())
        n = float(wo["w"].sum())
        out[pid] = round(n / (n + k) * (eo - ew), 6)
    return out
