"""Season sim: playoff, division and seed probabilities from the remaining schedule.

Fast mode: one model (current data window), each remaining game simmed `sims_per_game` times (no player
attribution), then `n_seasons` season draws pick one sim outcome per game. Played games use real results.
Tiebreakers (simplified NFL order): win %, head-to-head win % among the tied teams, division win % (division
ties), conference win %, strength of victory, strength of schedule, coin flip. Common-games and points-based
steps are skipped. Game outcomes are independent across games (no shared team-strength uncertainty), which
makes extreme records slightly too rare.
"""
from __future__ import annotations

import numpy as np
import polars as pl

DIVISIONS = {
    "AFC East": ["BUF", "MIA", "NE", "NYJ"], "AFC North": ["BAL", "CIN", "CLE", "PIT"],
    "AFC South": ["HOU", "IND", "JAX", "TEN"], "AFC West": ["DEN", "KC", "LV", "LAC"],
    "NFC East": ["DAL", "NYG", "PHI", "WAS"], "NFC North": ["CHI", "DET", "GB", "MIN"],
    "NFC South": ["ATL", "CAR", "NO", "TB"], "NFC West": ["ARI", "LA", "SF", "SEA"],
}
TEAM_DIV = {t: d for d, ts in DIVISIONS.items() for t in ts}


def _wpct(w, l, t):
    g = w + l + t
    return (w + 0.5 * t) / g if g else 0.0


class Standings:
    def __init__(self, games: list[tuple[str, str, float]]):
        """games: (home, away, home margin)."""
        self.games = games
        self.rec = {t: [0, 0, 0] for t in TEAM_DIV}
        self.div = {t: [0, 0, 0] for t in TEAM_DIV}
        self.conf = {t: [0, 0, 0] for t in TEAM_DIV}
        self.beat: dict[str, list[str]] = {t: [] for t in TEAM_DIV}
        self.opp: dict[str, list[str]] = {t: [] for t in TEAM_DIV}
        self.h2h: dict[tuple, list] = {}
        for h, a, m in games:
            for team, other, res in ((h, a, np.sign(m)), (a, h, -np.sign(m))):
                idx = 0 if res > 0 else (1 if res < 0 else 2)
                self.rec[team][idx] += 1
                if TEAM_DIV[team] == TEAM_DIV[other]:
                    self.div[team][idx] += 1
                if TEAM_DIV[team][:3] == TEAM_DIV[other][:3]:
                    self.conf[team][idx] += 1
                self.opp[team].append(other)
                if res > 0:
                    self.beat[team].append(other)
                self.h2h.setdefault((team, other), [0, 0, 0])[idx] += 1

    def wp(self, t):
        return _wpct(*self.rec[t])

    def rank(self, teams: list[str], rng, division: bool) -> list[str]:
        teams = list(teams)
        out = []
        while teams:
            best = self._best(teams, rng, division)
            out.append(best)
            teams.remove(best)
        return out

    def _best(self, teams, rng, division):
        cand = list(teams)
        steps = [
            lambda t, c: self.wp(t),
            lambda t, c: _wpct(*np.sum([self.h2h.get((t, o), [0, 0, 0]) for o in c if o != t], axis=0)) if len(c) > 1 else 0,
            (lambda t, c: _wpct(*self.div[t])) if division else (lambda t, c: 0.0),
            lambda t, c: _wpct(*self.conf[t]),
            lambda t, c: float(np.mean([self.wp(o) for o in self.beat[t]])) if self.beat[t] else 0.0,
            lambda t, c: float(np.mean([self.wp(o) for o in self.opp[t]])) if self.opp[t] else 0.0,
        ]
        for f in steps:
            vals = {t: f(t, cand) for t in cand}
            top = max(vals.values())
            cand = [t for t in cand if abs(vals[t] - top) < 1e-12]
            if len(cand) == 1:
                return cand[0]
        return cand[int(rng.random() * len(cand))]

    def seeds(self, conf: str, rng) -> list[str]:
        divs = [d for d in DIVISIONS if d.startswith(conf)]
        winners = [self.rank(DIVISIONS[d], rng, True)[0] for d in divs]
        winners = self.rank(winners, rng, False)
        rest = [t for d in divs for t in DIVISIONS[d] if t not in winners]
        wild = self.rank(rest, rng, False)[:3]
        return winners + wild


def season_sim(cfg, season: int, n_seasons: int = 2000, sims_per_game: int = 200, seed: int = 11,
               verbose: bool = True) -> dict:
    from .api import get_model
    from .backtest import game_seed
    from .data import load_schedules
    from .engine import simulate
    sched = load_schedules(cfg).filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
    played = sched.filter(pl.col("home_score").is_not_null())
    todo = sched.filter(pl.col("home_score").is_null())
    model = get_model(cfg, season, None)
    fixed = [(r["home_team"], r["away_team"], float(r["home_score"] - r["away_score"])) for r in played.iter_rows(named=True)]
    rem, samples = [], []
    for i, r in enumerate(todo.iter_rows(named=True)):
        res = simulate(model, r["home_team"], r["away_team"], sims_per_game, game_seed(seed, r["game_id"]),
                       neutral=r.get("location") == "Neutral")
        rem.append((r["home_team"], r["away_team"]))
        samples.append(res.col("pts", 0) - res.col("pts", 1))
        if verbose and (i + 1) % 20 == 0:
            print(f"  simmed {i + 1}/{todo.height} remaining games", flush=True)
    rng = np.random.default_rng(seed)
    teams = sorted(TEAM_DIV)
    wins = {t: [] for t in teams}
    seed_ct = {t: np.zeros(8) for t in teams}   # index 0 = missed, 1..7 seeds
    div_ct = {t: 0 for t in teams}
    for _ in range(n_seasons):
        games = list(fixed)
        for (h, a), smp in zip(rem, samples):
            games.append((h, a, float(smp[int(rng.random() * len(smp))])))
        st = Standings(games)
        for t in teams:
            wins[t].append(st.rec[t][0] + 0.5 * st.rec[t][2])
        for conf in ("AFC", "NFC"):
            sd = st.seeds(conf, rng)
            for i, t in enumerate(sd):
                seed_ct[t][i + 1] += 1
                if i < 4:
                    div_ct[t] += 1
            for t in teams:
                if TEAM_DIV[t].startswith(conf) and t not in sd:
                    seed_ct[t][0] += 1
    out = {}
    for t in teams:
        w = np.array(wins[t])
        out[t] = {"division": TEAM_DIV[t], "mean_wins": float(w.mean()), "p10_wins": float(np.percentile(w, 10)),
                  "p90_wins": float(np.percentile(w, 90)), "playoffs": float(1 - seed_ct[t][0] / n_seasons),
                  "division_title": div_ct[t] / n_seasons, "top_seed": float(seed_ct[t][1] / n_seasons),
                  "seed_dist": (seed_ct[t] / n_seasons).tolist()}
    return {"season": season, "n_seasons": n_seasons, "games_played": played.height, "games_remaining": todo.height,
            "teams": out}


def format_season(res: dict) -> str:
    L = [f"Season {res['season']}: {res['games_played']} played, {res['games_remaining']} remaining, {res['n_seasons']:,} season sims"]
    for div in DIVISIONS:
        L.append(f"\n{div:<10} {'W':>5} {'p10-p90':>9} {'playoffs':>9} {'division':>9} {'#1 seed':>8}")
        for t in sorted(DIVISIONS[div], key=lambda x: -res["teams"][x]["mean_wins"]):
            d = res["teams"][t]
            L.append(f"  {t:<8} {d['mean_wins']:>5.1f} {d['p10_wins']:>4.0f}-{d['p90_wins']:<4.0f} {100 * d['playoffs']:>8.1f}% "
                     f"{100 * d['division_title']:>8.1f}% {100 * d['top_seed']:>7.1f}%")
    return "\n".join(L)
