"""Correlated outcomes / same-game parlays straight from the sims.

Combo syntax (legs separated by ';'):
    "MIN:win; J.Jefferson:rec_yds>79.5; total>44.5"
Legs:
    TEAM:win | TEAM:lose | TEAM:cover-3.5 (team-perspective spread) | TEAM:pts>20.5
    total>44.5 | total<44.5 | margin>3.5 (home margin)
    Player Name:stat>line | Player Name:stat<line   (stats as in --prop, plus anytime_td>0.5)
The joint probability counts sims where every leg hits; it is shown next to the naive product of the leg
probabilities so the correlation is visible.
"""
from __future__ import annotations

import re

import numpy as np

from .report import moe


def _cmp(x: np.ndarray, op: str, v: float) -> np.ndarray:
    return x > v if op == ">" else x < v


def leg_mask(leg: str, res, players) -> tuple[str, np.ndarray]:
    leg = leg.strip()
    h, a = res.col("pts", 0), res.col("pts", 1)
    m = re.match(r"(?i)^(total|margin)\s*([<>])\s*(-?[\d.]+)$", leg)
    if m:
        x = h + a if m.group(1).lower() == "total" else h - a
        return leg, _cmp(x, m.group(2), float(m.group(3)))
    name, _, cond = leg.partition(":")
    team = name.strip().upper()
    if team in (res.home, res.away):
        mine, theirs = (h, a) if team == res.home else (a, h)
        c = cond.strip().lower()
        if c == "win":
            return leg, mine > theirs
        if c in ("lose", "loss"):
            return leg, mine < theirs
        cm = re.match(r"^cover\s*([+-]?[\d.]+)$", c)
        if cm:
            return leg, (mine - theirs) + float(cm.group(1)) > 0
        pm = re.match(r"^pts\s*([<>])\s*([\d.]+)$", c)
        if pm:
            return leg, _cmp(mine, pm.group(1), float(pm.group(2)))
        raise ValueError(f"unknown team leg {leg!r} (use win, lose, cover-3.5, pts>20.5)")
    sm = re.match(r"^\s*([a-z_]+)\s*([<>])\s*([\d.]+)\s*$", cond.lower())
    if not sm or players is None:
        raise ValueError(f"cannot parse leg {leg!r}" + ("" if players is not None else " (player legs need player projections)"))
    x = players.stat_array(name.strip(), sm.group(1))
    return leg, _cmp(x, sm.group(2), float(sm.group(3)))


def evaluate(combo: str, res, players) -> dict:
    legs = [l for l in combo.split(";") if l.strip()]
    masks = [leg_mask(l, res, players) for l in legs]
    n = res.n
    joint = np.ones(n, dtype=bool)
    out_legs = []
    naive = 1.0
    for label, mk in masks:
        p = float(mk.mean())
        out_legs.append({"leg": label, "p": p, "moe": moe(p, n)})
        naive *= p
        joint &= mk
    pj = float(joint.mean())
    return {"combo": combo, "legs": out_legs, "joint": {"p": pj, "moe": moe(pj, n)}, "naive": naive,
            "correlation_lift": pj / naive if naive > 0 else None,
            "fair_american": (f"+{100 * (1 - pj) / pj:.0f}" if 0 < pj < 0.5 else (f"{-100 * pj / (1 - pj):.0f}" if 0.5 <= pj < 1 else "n/a"))}
