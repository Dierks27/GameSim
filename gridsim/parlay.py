"""Parlays across games, priced from the sims and Kalshi's single-contract prices. READ-ONLY: numbers, not orders.

Legs from DIFFERENT games are treated as independent, so the chance that all hit is the product of the legs'
chances. (Legs from the same game are not independent — the same-game builder counts those from the sims.) The cost
shown is the product of the legs' costs with fees: what the parlay costs if every leg is bought at today's ask and
the stake rolls from leg to leg. Kalshi's own price for a combination can differ. Chances are the ADJUSTED ones
(markets.both_sides: the market's chance moved toward the sim's by market_blend.weight); the sim's own are kept
alongside.
"""
from __future__ import annotations

import datetime as dt
import itertools
import math

ROLL_GAP_HOURS = 4.0   # a game lasts a little over 3 hours and has to be settled before its payout can buy anything


def can_roll(legs: list[dict]) -> bool:
    """Could the legs be bought one after another, each win paying for the next (kickoffs far enough apart)?"""
    try:
        kos = sorted(dt.datetime.fromisoformat(x["kickoff_utc"]) for x in legs)
    except (KeyError, TypeError, ValueError):
        return False
    return all((b - a).total_seconds() >= ROLL_GAP_HOURS * 3600 for a, b in zip(kos, kos[1:]))


def idea(r: dict) -> tuple:
    """What a leg bets on, whatever the contract: a team (to win, or to cover a small line) or over / under."""
    if r["market"] == "total":
        return (r.get("game_id"), "total", r["side"])
    return (r.get("game_id"), r.get("team") if r["side"] == "YES" else r.get("other"))


TIERS = [("safer", "Safer", 0.35, 1.01), ("middle", "Middle", 0.12, 0.35), ("long", "Long shot", 0.0, 0.12)]


def combine(legs: list[dict]) -> dict:
    """Independent legs [{"p", "moe"?, "cost"?, "p_sim"?}] -> chance all hit (± its margin of error), cost, payout
    and value per $1. "p" is the chance the numbers stand on (the adjusted one for Kalshi legs); "p_sim", when given,
    is the sim's own chance, multiplied separately as `p_sim`."""
    p = math.prod(float(x["p"]) for x in legs)
    rel = math.sqrt(sum((float(x.get("moe") or 0.0) / float(x["p"])) ** 2 for x in legs if float(x["p"]) > 0))
    out = {"p": p, "moe": p * rel, "n_legs": len(legs)}
    if all(x.get("p_sim") is not None for x in legs):
        out["p_sim"] = math.prod(float(x["p_sim"]) for x in legs)
    if all(x.get("cost") for x in legs):
        cost = math.prod(float(x["cost"]) for x in legs)
        out.update(cost=cost, pays=1.0 / cost, edge=p - cost, ev_per_dollar=p / cost - 1.0)
        if "p_sim" in out:
            out["ev_sim"] = out["p_sim"] / cost - 1.0
    return out


def candidates(board: dict, per_band: int = 2) -> dict[str, list[dict]]:
    """Per game, the sides worth putting in a parlay: an adjusted edge, or at least GridSim's own lean, of
    scanner.min_edge or more, with no liquidity / margin-of-error / freshness catch (so a price you can really
    get) — the best few costing under 50¢ and the best few at 50¢+."""
    out = {}
    for gid, g in (board.get("games") or {}).items():
        rows = [r for r in (g.get("rows") or []) if r.get("rating") in ("signal", "unproven")
                or (r.get("rating") == "lean" and not r.get("hard"))]
        keep, seen = [], set()
        for lo, hi in ((0.0, 0.5), (0.5, 1.01)):
            n = 0
            # (the adjusted edge is rounded first: at weight 0 it is the same trading cost for every side, and its
            # floating-point noise must not decide; the lean does)
            for r in sorted((r for r in rows if lo <= r["cost"] < hi),
                            key=lambda r: (-round(r["edge"], 6), -r.get("lean", 0.0))):
                # the many spread / total lines of a game are nearly one bet, and "X wins", "Y does not win" and
                # "X wins or loses by 2 or fewer" are one idea: one leg per idea and price band
                key = (idea(r), lo)
                if key in seen:
                    continue
                seen.add(key)
                keep.append(r)
                n += 1
                if n >= per_band:
                    break
        if keep:
            out[gid] = keep
    return out


def auto_parlays(board: dict, max_legs: int = 4, per_tier: int = 5, max_combos: int = 600_000) -> dict:
    """Parlays of 2..max_legs legs, one leg per game, built from the sides GridSim likes this week (see
    candidates), grouped by how often they hit by the ADJUSTED chance — safer / middle / long shot — and ranked
    inside a group by adjusted value per $1 (adjusted chance ÷ cost − 1), then by GridSim's own. With
    market_blend.weight 0 the adjusted value is what the fees and the bid/ask gap cost, so the ranking prefers the
    legs that are cheapest to trade among the ones GridSim leans to. A leg is used at most twice per group so the
    lists are not five versions of one idea (an idea = a team, or over / under, of a game: see idea()).

    Returns {"tiers": {key: [parlay, ...]}, "games_with_edges": n, "legs_considered": n, "checked": n,
    "partial": True when max_combos stopped the search early}."""
    cands = candidates(board)
    games = sorted(cands)
    found = {k: [] for k, *_ in TIERS}
    checked, full = 0, False
    for k in range(2, max_legs + 1):
        for gset in itertools.combinations(games, k):
            for legs in itertools.product(*(cands[g] for g in gset)):
                if checked >= max_combos:
                    full = True
                    break
                checked += 1
                p = cost = p_sim = 1.0
                for x in legs:
                    p *= x.get("p_adj", x["p_side"])
                    p_sim *= x["p_side"]
                    cost *= x["cost"]
                for key, _, lo, hi in TIERS:
                    if lo <= p < hi:
                        # (only what the ranking needs; the full numbers are worked out for the few that are listed)
                        found[key].append((-round(p / cost - 1.0, 4), -(p_sim / cost - 1.0), checked, legs))
                        break
            if full:
                break
        if full:
            break
    tiers = {}
    for key, *_ in TIERS:
        used, picked = {}, []
        for _, _, _, legs in sorted(found[key], key=lambda t: t[:3]):
            ids = [idea(x) for x in legs]
            if any(used.get(i, 0) >= 2 for i in ids):
                continue
            for i in ids:
                used[i] = used.get(i, 0) + 1
            c = combine([{"p": x.get("p_adj", x["p_side"]), "p_sim": x["p_side"], "moe": x.get("moe"),
                          "cost": x["cost"]} for x in legs])
            picked.append({**c, "legs": list(legs), "can_roll": can_roll(list(legs))})
            if len(picked) >= per_tier:
                break
        tiers[key] = picked
    return {"tiers": tiers, "games_with_edges": len(games), "legs_considered": sum(len(v) for v in cands.values()),
            "checked": checked, "partial": full}
