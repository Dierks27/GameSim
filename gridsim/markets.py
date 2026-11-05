"""Market comparison: Kalshi (public, no key), READ-ONLY.

This module only issues GET requests to public market-data endpoints. It never places orders, never handles
exchange account keys and never touches a portfolio.

Discovery (no hard-coded tickers): list Sports series, keep the NFL game-level ones whose titles match the
configured patterns (moneyline "Pro Football Game", spread "Pro Football Spread", total "Total Points"),
list their open events, and match events to our games by team names + date. Prices are dollars on 0-1,
read directly as probability. Mid = (bid + ask)/2; spread, volume and open interest are shown so thin markets
are obvious. Fees: Kalshi's quadratic fee  fee = ceil_cent(rate * C * P * (1 - P)) * multiplier, with the
taker rate 0.07 / maker 0.0175 in config (verify against Kalshi's current fee schedule).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import time
import urllib.parse
import urllib.request

KALSHI_NAMES = {
    "Arizona": "ARI", "Atlanta": "ATL", "Baltimore": "BAL", "Buffalo": "BUF", "Carolina": "CAR", "Chicago": "CHI",
    "Cincinnati": "CIN", "Cleveland": "CLE", "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Green Bay": "GB",
    "Houston": "HOU", "Indianapolis": "IND", "Jacksonville": "JAX", "Kansas City": "KC", "Las Vegas": "LV",
    "Los Angeles C": "LAC", "Los Angeles R": "LA", "Miami": "MIA", "Minnesota": "MIN", "New England": "NE",
    "New Orleans": "NO", "New York G": "NYG", "New York J": "NYJ", "Philadelphia": "PHI", "Pittsburgh": "PIT",
    "San Francisco": "SF", "Seattle": "SEA", "Tampa Bay": "TB", "Tennessee": "TEN", "Washington": "WAS",
}
NAME_RE = "|".join(sorted((re.escape(k) for k in KALSHI_NAMES), key=len, reverse=True))


class Kalshi:
    def __init__(self, cfg):
        k = cfg.markets["kalshi"]
        self.base = k["base_url"].rstrip("/")
        self.timeout = float(k.get("timeout", 15))
        self.cfg = k
        self._series = None

    def get(self, path: str, **params) -> dict:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}{path}" + (f"?{q}" if q else "")
        req = urllib.request.Request(url, headers={"User-Agent": "gridsim (read-only)", "Accept": "application/json"},
                                     method="GET")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        return {}

    def nfl_series(self) -> dict[str, str]:
        """{kind: series_ticker} for moneyline / spread / total, discovered from titles."""
        if self._series is not None:
            return self._series
        pats = self.cfg["series_title_patterns"]
        found: dict[str, str] = {}
        data = self.get("/series", category="Sports")
        for s in data.get("series", []):
            t, title = s.get("ticker", ""), s.get("title", "")
            if not t.startswith("KXNFL"):
                continue
            for kind, pat in pats.items():
                if kind not in found and re.fullmatch(pat, title.strip(), flags=re.I):
                    found[kind] = t
        for kind, fallback in self.cfg.get("series_fallback", {}).items():
            found.setdefault(kind, fallback)
        self._series = found
        return found

    def events(self, series: str) -> list[dict]:
        out, cursor = [], None
        for _ in range(10):
            d = self.get("/events", series_ticker=series, status="open", limit=200, with_nested_markets="true", cursor=cursor)
            out += d.get("events", [])
            cursor = d.get("cursor")
            if not cursor or not d.get("events"):
                break
        return out

    def orderbook(self, ticker: str) -> dict:
        return self.get(f"/markets/{ticker}/orderbook")


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_event_teams(ev: dict) -> tuple[str | None, str | None]:
    title = (ev.get("title") or "").split(":")[0]
    m = re.match(rf"^\s*({NAME_RE})\s+(?:vs\.?|at|@)\s+({NAME_RE})\s*$", title)
    if not m:
        return None, None
    return KALSHI_NAMES[m.group(1)], KALSHI_NAMES[m.group(2)]


def event_date(ev: dict) -> str | None:
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", ev.get("event_ticker", ""))
    if not m:
        return None
    try:
        return dt.datetime.strptime(f"20{m.group(1)}{m.group(2)}{m.group(3)}", "%Y%b%d").date().isoformat()
    except ValueError:
        return None


def quote(m: dict) -> dict:
    yb, ya = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    nb, na = _f(m.get("no_bid_dollars")), _f(m.get("no_ask_dollars"))
    mid = (yb + ya) / 2 if (yb is not None and ya is not None and ya > 0) else _f(m.get("last_price_dollars"))
    return {"ticker": m.get("ticker"), "yes_bid": yb, "yes_ask": ya, "no_bid": nb, "no_ask": na, "mid": mid,
            "spread": (ya - yb) if (ya is not None and yb is not None) else None,
            "volume": _f(m.get("volume_fp")), "volume_24h": _f(m.get("volume_24h_fp")),
            "open_interest": _f(m.get("open_interest_fp")), "yes_ask_size": _f(m.get("yes_ask_size_fp")),
            "yes_bid_size": _f(m.get("yes_bid_size_fp")),
            "strike": _f(m.get("floor_strike")), "sub_title": m.get("yes_sub_title"), "title": m.get("title"),
            "url": f"https://kalshi.com/markets/{(m.get('event_ticker') or '').split('-')[0].lower()}"}


def game_markets(k: Kalshi, game: dict) -> dict:
    """All open Kalshi game-level markets for one schedule game (moneyline, spread, total)."""
    home, away, day = game["home_team"], game["away_team"], game.get("gameday")
    out = {"moneyline": {}, "spread": [], "total": [], "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    series = k.nfl_series()
    for kind, ser in series.items():
        for ev in k.events(ser):
            a, h = parse_event_teams(ev)
            if {a, h} != {home, away}:
                continue
            ed = event_date(ev)
            if day and ed and abs((dt.date.fromisoformat(ed) - dt.date.fromisoformat(day)).days) > 1:
                continue
            for m in ev.get("markets", []):
                q = quote(m)
                team = KALSHI_NAMES.get((m.get("yes_sub_title") or "").split(" wins")[0].strip())
                if kind == "moneyline" and team:
                    out["moneyline"][team] = q
                elif kind == "spread":
                    tm = re.match(rf"^({NAME_RE}) wins by over ([\d.]+)", m.get("yes_sub_title") or m.get("title") or "")
                    if tm:
                        out["spread"].append({**q, "team": KALSHI_NAMES[tm.group(1)], "line": float(tm.group(2))})
                elif kind == "total" and q["strike"] is not None:
                    out["total"].append({**q, "line": q["strike"]})
    out["spread"].sort(key=lambda r: (r["team"], r["line"]))
    out["total"].sort(key=lambda r: r["line"])
    return out


def fee(price: float, cfg, contracts: int = 1, maker: bool = False) -> float:
    k = cfg.markets["kalshi"]
    rate = float(k["maker_fee_rate"] if maker else k["taker_fee_rate"]) * float(k.get("fee_multiplier", 1.0))
    raw = rate * contracts * price * (1 - price)
    return math.ceil(raw * 100 - 1e-9) / 100.0


def sim_probs(summary: dict, mk: dict) -> list[dict]:
    """Every Kalshi contract for the game with the sim probability of YES and the edge of buying YES or NO."""
    from .api import cover_from_hist
    H = summary["matchup"]["home"]
    n = summary["n_sims"]
    out = []
    p_home = summary["win"]["home"]["p"]
    p_away = summary["win"]["away"]["p"]
    for team, q in mk.get("moneyline", {}).items():
        p = p_home if team == H else p_away
        out.append({"market": "moneyline", "label": f"{team} wins", "p_yes": p, **q})
    for q in mk.get("spread", []):
        # "TEAM wins by over X": home perspective spread line for cover_from_hist is -X for home, +X... for away
        line = -q["line"] if q["team"] == H else q["line"]
        c = cover_from_hist(summary["hist"]["margin"], line, "spread")
        p = c["hit"] if q["team"] == H else c["miss"]
        out.append({"market": "spread", "label": f"{q['team']} wins by {q['line']}+", "p_yes": p, **q})
    for q in mk.get("total", []):
        c = cover_from_hist(summary["hist"]["total"], q["line"], "total")
        out.append({"market": "total", "label": f"over {q['line']}", "p_yes": c["hit"], **q})
    from .report import moe
    for r in out:
        r["moe"] = moe(r["p_yes"], n)
    return out


def edges(rows: list[dict], cfg) -> list[dict]:
    """Edge = sim prob - (price you'd pay + fee). Buying YES pays the yes ask; buying NO pays the no ask."""
    out = []
    for r in rows:
        best = None
        for side, price, p in (("YES", r.get("yes_ask"), r["p_yes"]), ("NO", r.get("no_ask"), 1 - r["p_yes"])):
            if price is None or price <= 0 or price >= 1:
                continue
            f = fee(price, cfg)
            e = p - (price + f)
            cand = {"side": side, "price": price, "fee": f, "edge": e, "ev_per_dollar": e / (price + f), "p_side": p}
            if best is None or cand["edge"] > best["edge"]:
                best = cand
        out.append({**r, **(best or {"side": None, "edge": None})})
    return out
