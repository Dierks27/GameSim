"""Player availability: who actually takes the field this week.

Sources (nflreadpy, cached in data/cache like play-by-play): weekly rosters, depth charts, injury reports.
Per-player play probability:
  override out -> 0, override in -> 1                      (overrides always win)
  roster status not ACT (IR / reserve / inactive)  -> 0
  report Out -> 0; Doubtful -> 0 (or doubtful_play_prob); Questionable -> 0.75, nudged by last practice
  no game status yet: last practice DNP -> 0.5, Limited -> 0.95, Full / not listed -> 1
Each simulated game rolls availability independently (the Monte Carlo way). The starting QB is the first
QB in depth-chart order (overrides first, last game's starter included) who is rolled active.
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
import yaml

from .config import Config
from .data import _cache_dir, _is_stale, current_season

SOURCES = {
    "rosters_weekly": ("load_rosters_weekly", "weekly_rosters"),
    "depth_charts": ("load_depth_charts", "depth_charts"),
    "injuries": ("load_injuries", "injuries"),
}


def _load_source(name: str, season: int, cfg: Config, force: bool = False, verbose: bool = False) -> pl.DataFrame:
    import nflreadpy as nfl

    path = _cache_dir(cfg) / f"{name}_{season}.parquet"
    if force or _is_stale(path, season, cfg):
        if verbose:
            print(f"  downloading {name} {season} ...", flush=True)
        try:
            df = getattr(nfl, SOURCES[name][0])([season])
            df.write_parquet(path)
            return df
        except Exception as exc:
            if path.exists():
                print(f"  warning: {name} refresh failed ({exc}); using cached copy")
            else:
                print(f"  warning: {name} unavailable ({exc})")
                return pl.DataFrame()
    return pl.read_parquet(path)


def refresh_availability(cfg: Config, season: int | None = None) -> None:
    season = season or current_season()
    for name in SOURCES:
        _load_source(name, season, cfg, force=True, verbose=True)


def source_freshness(cfg: Config, season: int) -> dict:
    """Local download time and (best effort) the nflverse release asset's upload time."""
    out = {}
    for name, (_, tag) in SOURCES.items():
        path = _cache_dir(cfg) / f"{name}_{season}.parquet"
        info = {"downloaded": None, "nflverse_updated": None}
        if path.exists():
            info["downloaded"] = dt.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="minutes")
        out[name] = info
    cache = _cache_dir(cfg) / "nflverse_release_times.json"
    times = {}
    if cache.exists() and time.time() - cache.stat().st_mtime < 3600:
        times = json.loads(cache.read_text())
    else:
        for name, (_, tag) in SOURCES.items():
            try:
                req = urllib.request.Request(f"https://api.github.com/repos/nflverse/nflverse-data/releases/tags/{tag}",
                                             headers={"User-Agent": "gridsim", "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    rel = json.loads(r.read())
                stamps = [a["updated_at"] for a in rel.get("assets", []) if str(season) in a.get("name", "")]
                times[name] = max(stamps) if stamps else None
            except Exception:
                times[name] = None
        try:
            cache.write_text(json.dumps(times))
        except OSError:
            pass
    for name in SOURCES:
        out[name]["nflverse_updated"] = times.get(name)
    return out


# ---- name matching ----------------------------------------------------------------------

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b\.?", "", s)
    return re.sub(r"[^a-z]", "", s)


def _abbr(full: str) -> str:
    parts = [p for p in re.split(r"\s+", full.strip()) if p]
    if len(parts) < 2:
        return _norm(full)
    last = [p for p in parts[1:] if _norm(p)]
    return _norm(parts[0][0] + "".join(last))


OUT_ROSTER_STATUSES = {"RES", "CUT", "DEV", "RET", "EXE", "SUS", "TRD", "TRC", "TRT"}


class NameMatchError(ValueError):
    pass


def match_player(name: str, roster: list[dict], team: str) -> dict:
    """roster: dicts with gsis_id, full_name. Fail loudly when nothing (or several) match."""
    q = _norm(name)
    if not q:
        raise NameMatchError(f"empty player name for {team}")
    by_full = [p for p in roster if _norm(p["full_name"]) == q]
    if len(by_full) == 1:
        return by_full[0]
    by_abbr = [p for p in roster if _abbr(p["full_name"]) == q]
    if len(by_abbr) == 1:
        return by_abbr[0]
    by_last = [p for p in roster if _norm(p["full_name"].split()[-1]) == q or _norm(p.get("last_name") or "") == q]
    if len(by_last) == 1:
        return by_last[0]
    keys = {_norm(p["full_name"]): p for p in roster}
    keys.update({_abbr(p["full_name"]): p for p in roster})
    close = difflib.get_close_matches(q, list(keys), n=3, cutoff=0.82)
    cands = {keys[c]["gsis_id"]: keys[c] for c in close}
    if len(cands) == 1:
        return next(iter(cands.values()))
    sugg = ", ".join(sorted({keys[c]["full_name"] for c in difflib.get_close_matches(q, list(keys), n=5, cutoff=0.5)}))
    raise NameMatchError(f"'{name}' does not match exactly one player on {team}" + (f" (did you mean: {sugg})" if sugg else ""))


# ---- availability model -------------------------------------------------------------------

@dataclass
class PlayerStatus:
    gsis_id: str
    name: str
    team: str
    pos: str
    p_play: float
    status: str
    reason: str


@dataclass
class TeamAvailability:
    team: str
    players: dict[str, PlayerStatus] = field(default_factory=dict)
    qb_order: list[str] = field(default_factory=list)
    roster: list[dict] = field(default_factory=list)


@dataclass
class Availability:
    teams: tuple[TeamAvailability, TeamAvailability]
    banners: list[str] = field(default_factory=list)
    freshness: dict = field(default_factory=dict)
    overrides_applied: list[str] = field(default_factory=list)
    week: int | None = None

    def roll(self, u) -> tuple[tuple, dict]:
        qbs, outs = [], {}
        for ta in self.teams:
            out = set()
            for pid, ps in ta.players.items():
                if ps.p_play <= 0 or (ps.p_play < 1 and u() >= ps.p_play):
                    out.add(pid)
            starter = next((q for q in ta.qb_order if q not in out), ta.qb_order[-1] if ta.qb_order else None)
            qbs.append(starter)
            outs[ta.team] = out
        return tuple(qbs), outs

    def qb_start_probs(self, ta: TeamAvailability) -> list[tuple[str, float]]:
        rem, res = 1.0, []
        for q in ta.qb_order:
            p = ta.players[q].p_play if q in ta.players else 1.0
            res.append((q, rem * p))
            rem *= 1 - p
        if res and rem > 0:
            q, p = res[-1]
            res[-1] = (q, p + rem)
        return res

    def describe(self) -> dict:
        out = {"week": self.week, "freshness": self.freshness, "overrides": self.overrides_applied, "teams": {}}
        for ta in self.teams:
            qb = [{"name": ta.players[q].name if q in ta.players else q, "p_start": p} for q, p in self.qb_start_probs(ta)]
            listed = [vars(ps) for ps in sorted(ta.players.values(), key=lambda x: (x.p_play, x.pos)) if ps.p_play < 1 or ps.reason]
            out["teams"][ta.team] = {"qb": qb, "listed": listed}
        return out


def default_overrides_path(cfg: Config, season: int, week: int | None) -> Path:
    return cfg.resolve_path("overrides") / f"{season}_wk{(week or 0):02d}.yaml"


def load_overrides(path: Path) -> dict:
    if path and Path(path).exists():
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    return {}


def save_overrides(path: Path, data: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=True)


def _practice_last(row: dict) -> str:
    return (row.get("practice_status") or "").lower()


def _status_prob(report: str | None, practice: str, ac: dict) -> tuple[float, str]:
    r = (report or "").lower()
    if r == "out":
        return 0.0, "Out"
    if r == "doubtful":
        return (0.0, "Doubtful") if ac["doubtful_out"] else (float(ac["doubtful_play_prob"]), "Doubtful")
    if r == "questionable":
        p = float(ac["questionable_play_prob"])
        if "did not" in practice:
            p *= float(ac["practice_dnp_factor"])
        elif "full" in practice:
            p = min(0.95, p * float(ac["practice_full_factor"]))
        return p, "Questionable"
    if "did not" in practice:
        return float(ac["no_report_dnp_play_prob"]), "DNP (no game status yet)"
    if "limited" in practice:
        return float(ac["no_report_limited_play_prob"]), "Limited (no game status yet)"
    return 1.0, ""


def build_availability(cfg: Config, season: int, home: str, away: str, model, week: int | None = None,
                       outs: list[str] | None = None, ins: list[str] | None = None, overrides_path=None,
                       game_date: str | None = None, verbose: bool = False) -> Availability:
    ac = cfg.availability
    rosters = _load_source("rosters_weekly", season, cfg, verbose=verbose)
    depth = _load_source("depth_charts", season, cfg, verbose=verbose)
    inj = _load_source("injuries", season, cfg, verbose=verbose)
    banners: list[str] = []
    fresh = source_freshness(cfg, season)
    if inj.height:
        fresh["injuries"]["latest_week_in_data"] = int(inj["week"].max())
    if depth.height:
        fresh["depth_charts"]["latest_snapshot"] = str(depth["dt"].max())
    if rosters.height:
        fresh["rosters_weekly"]["latest_week_in_data"] = int(rosters["week"].max())
    if week is None and inj.height:
        week = int(inj["week"].max())

    teams = []
    for team in (home, away):
        ta = TeamAvailability(team)
        if rosters.height:
            rt = rosters.filter((pl.col("team") == team) & (pl.col("week") <= (week or 99)))
            if rt.height:
                rt = rt.filter(pl.col("week") == rt["week"].max())
            ta.roster = [r for r in rt.select(["gsis_id", "full_name", "last_name", "position", "status"]).iter_rows(named=True)
                         if r["gsis_id"]]
        roster_by_id = {r["gsis_id"]: r for r in ta.roster}
        for r in ta.roster:
            # INA = inactive for *last* week's game, not this week's -> not treated as out
            if r["status"] in OUT_ROSTER_STATUSES and r["position"] in ("QB", "RB", "WR", "TE", "K"):
                ta.players[r["gsis_id"]] = PlayerStatus(r["gsis_id"], r["full_name"], team, r["position"], 0.0,
                                                        r["status"], f"roster status {r['status']}")
        if inj.height and week is not None:
            it = inj.filter((pl.col("team") == team) & (pl.col("week") == week))
            for r in it.iter_rows(named=True):
                p, why = _status_prob(r.get("report_status"), _practice_last(r), ac)
                if p < 1.0 and r["gsis_id"]:
                    ta.players[r["gsis_id"]] = PlayerStatus(r["gsis_id"], r["full_name"], team, r["position"], p,
                                                            r.get("report_status") or "", why)
            if it.height == 0:
                banners.append(f"no injury report yet for {team} week {week}")
        # QB order: depth chart (latest snapshot on/before the game), plus last game's starter
        order = []
        if depth.height:
            dteam = depth.filter((pl.col("team") == team) & (pl.col("pos_abb") == "QB"))
            if game_date:
                dteam = dteam.filter(pl.col("dt") <= game_date + "T23:59:59Z")
            if dteam.height:
                dteam = dteam.filter(pl.col("dt") == dteam["dt"].max()).sort("pos_rank")
                order = [g for g in dteam["gsis_id"].to_list() if g]
        last_qb = model.default_qb.get(team)
        if last_qb and last_qb not in order:
            order.insert(0, last_qb)
        if roster_by_id:
            order = [q for q in order if q in roster_by_id or q == last_qb] or order
        ta.qb_order = order
        for q in order:
            if q not in ta.players and q in roster_by_id:
                r = roster_by_id[q]
                ta.players.setdefault(q, PlayerStatus(q, r["full_name"], team, "QB", 1.0, "", ""))
        teams.append(ta)

    av = Availability((teams[0], teams[1]), banners, fresh, [], week)

    # ---- overrides: file, then CLI flags (CLI wins)
    path = Path(overrides_path) if overrides_path else default_overrides_path(cfg, season, week)
    ov = load_overrides(path)
    for ta in av.teams:
        entry = ov.get(ta.team) or {}
        _apply(av, ta, entry.get("out", []), 0.0, f"override file {path.name}")
        _apply(av, ta, entry.get("in", []), 1.0, f"override file {path.name}")
        if entry.get("qb"):
            p = match_player(entry["qb"], ta.roster, ta.team)
            ta.qb_order = [p["gsis_id"]] + [q for q in ta.qb_order if q != p["gsis_id"]]
            ta.players[p["gsis_id"]] = PlayerStatus(p["gsis_id"], p["full_name"], ta.team, "QB", 1.0, "override", "override: starting QB")
            av.overrides_applied.append(f"{ta.team} QB = {p['full_name']}")
    for flag, prob in ((outs or [], 0.0), (ins or [], 1.0)):
        for name in flag:
            team_hint, _, nm = name.partition(":") if ":" in name else ("", "", name)
            cands = [ta for ta in av.teams if not team_hint or ta.team == team_hint.upper()]
            hits, errs = [], []
            for ta in cands:
                try:
                    hits.append((ta, match_player(nm, ta.roster, ta.team)))
                except NameMatchError as e:
                    errs.append(str(e))
            if len(hits) != 1:
                raise NameMatchError(f"--{'out' if prob == 0 else 'in'} '{name}': " +
                                     ("matches players on both teams; prefix with TEAM:" if hits else "; ".join(errs)))
            _apply(av, hits[0][0], [hits[0][1]["full_name"]], prob, "command line")
    stale_h = float(ac["stale_injury_hours"])
    ts = fresh.get("injuries", {}).get("nflverse_updated")
    if ts:
        age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600
        if age > stale_h:
            banners.append(f"WARNING: nflverse injury data is {age:.0f} h old (> {stale_h:.0f} h) — check news / use overrides")
    if av.overrides_applied:
        banners.append("overrides: " + "; ".join(av.overrides_applied))
    return av


def _apply(av: Availability, ta: TeamAvailability, names: list[str], prob: float, source: str) -> None:
    for nm in names:
        p = match_player(nm, ta.roster, ta.team)
        pid = p["gsis_id"]
        ta.players[pid] = PlayerStatus(pid, p["full_name"], ta.team, p.get("position") or "", prob,
                                       "override", f"{'OUT' if prob == 0 else 'IN'} ({source})")
        if prob > 0 and p.get("position") == "QB" and pid not in ta.qb_order:
            ta.qb_order.append(pid)
        av.overrides_applied.append(f"{ta.team} {'out' if prob == 0 else 'in'}: {p['full_name']}")
