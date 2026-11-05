"""Parse a live game state string so a sim can start mid-game.

Format (case-insensitive, flexible spacing):
    "Q3 8:42 MIN 17 GB 13 MIN ball 2nd&6 at GB 34 timeouts 2/3"
    "Q4 1:55 KC 20 BUF 24 KC ball 1st&10 at KC 25"            (timeouts default to 3/3, or 2/2 in OT)
    "OT 6:10 DET 20 GB 20 GB ball 3rd&2 at 50 timeouts 1/2"
* Teams/scores: the first two TEAM SCORE pairs. Timeouts "a/b" are in the same order as those pairs.
* "at GB 34" = the ball is on GB's 34; "at 50" = midfield. "kickoff TEAM" instead of "ball" means TEAM is
  about to kick off.
* Optional "receives2h TEAM": who receives the second-half kickoff (otherwise a coin flip per sim).
"""
from __future__ import annotations

import re


class StateError(ValueError):
    pass


def parse_state(text: str, home: str, away: str) -> dict:
    t = " ".join(text.replace(",", " ").split())
    home, away = home.upper(), away.upper()
    m = re.match(r"(?i)^(Q[1-4]|OT\d?)\s+(\d{1,2}):(\d{2})\s+", t)
    if not m:
        raise StateError(f"state must start with quarter and clock, e.g. 'Q3 8:42 ...' (got {text!r})")
    q = m.group(1).upper()
    qtr = 5 + (int(q[2:]) - 1 if len(q) > 2 else 0) if q.startswith("OT") else int(q[1])
    clock = int(m.group(2)) * 60 + int(m.group(3))
    rest = t[m.end():]
    pairs = re.findall(r"\b([A-Za-z]{2,3})\s+(\d{1,3})\b", rest)
    pairs = [(a.upper(), int(b)) for a, b in pairs if a.upper() in (home, away)]
    if len(pairs) < 2 or {pairs[0][0], pairs[1][0]} != {home, away}:
        raise StateError(f"state needs both scores, e.g. '{home} 17 {away} 13'")
    order = [pairs[0][0], pairs[1][0]]
    score = {pairs[0][0]: pairs[0][1], pairs[1][0]: pairs[1][1]}
    out = {"qtr": qtr, "clock": float(clock), "score": [score[home], score[away]]}
    tm = re.search(r"(?i)timeouts\s+(\d)\s*/\s*(\d)", rest)
    default_to = 2 if qtr >= 5 else 3
    tos = {order[0]: int(tm.group(1)), order[1]: int(tm.group(2))} if tm else {home: default_to, away: default_to}
    out["timeouts"] = [tos[home], tos[away]]
    k = re.search(r"(?i)kickoff\s+([A-Za-z]{2,3})", rest)
    if k:
        kt = k.group(1).upper()
        if kt not in (home, away):
            raise StateError(f"kickoff team {kt} is not {home} or {away}")
        out["kickoff"] = 0 if kt == home else 1
    else:
        b = re.search(r"(?i)\b([A-Za-z]{2,3})\s+ball\b", rest)
        if not b or b.group(1).upper() not in (home, away):
            raise StateError("state needs 'TEAM ball' (or 'kickoff TEAM')")
        pos = 0 if b.group(1).upper() == home else 1
        dd = re.search(r"(?i)\b([1-4])(?:st|nd|rd|th)?\s*(?:&|and)\s*(goal|\d{1,2})\b", rest)
        if not dd:
            raise StateError("state needs down and distance, e.g. '2nd&6' or '1st&goal'")
        at = re.search(r"(?i)\bat\s+(?:([A-Za-z]{2,3})\s+)?(\d{1,2})\b", rest)
        if not at:
            raise StateError("state needs the ball spot, e.g. 'at GB 34' or 'at 50'")
        side, yd = (at.group(1) or "").upper(), int(at.group(2))
        if yd == 50 or not side:
            yl = 50
        elif side not in (home, away):
            raise StateError(f"spot team {side} is not {home} or {away}")
        else:
            own = (side == home and pos == 0) or (side == away and pos == 1)
            yl = 100 - yd if own else yd
        down = int(dd.group(1))
        togo = yl if dd.group(2).lower() == "goal" else int(dd.group(2))
        out.update({"pos": pos, "yl": int(yl), "down": down, "togo": min(int(togo), int(yl))})
    r2 = re.search(r"(?i)receives2h\s+([A-Za-z]{2,3})", rest)
    if r2:
        out["receives2h"] = 0 if r2.group(1).upper() == home else 1
    return out
