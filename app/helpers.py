"""Pure helpers for the GUI pages (no Streamlit, no modeling): string builders for the library's own input
syntaxes (combo legs, live state), a line-preserving config.yaml editor, small parsers for library text
output, and a clearly-labeled percentile interpolation used only when a stored per-sim histogram is missing.

Everything here is unit-tested in tests/test_app_smoke.py.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import re
import shutil
from pathlib import Path

import yaml

# ---- combo / parlay legs (syntax documented in gridsim/combos.py) --------------------------------------

LEG_KINDS = {
    "team_win": "Team wins",
    "team_lose": "Team loses",
    "team_cover": "Team covers a spread",
    "team_pts": "Team points over / under",
    "total": "Game total over / under",
    "margin": "Home margin over / under",
    "player": "Player stat over / under",
}


def _num(x: float) -> str:
    """Compact number text for leg strings: 44.5, 3, -2.5."""
    x = float(x)
    return str(int(x)) if x == int(x) else f"{x:g}"


def leg_string(kind: str, team: str | None = None, op: str = ">", line: float | None = None,
               player: str | None = None, stat: str | None = None) -> str:
    """One combo leg in gridsim.combos syntax.

    team_win  -> "MIN:win"            team_lose -> "MIN:lose"
    team_cover (team-perspective sportsbook spread, e.g. -3.5) -> "MIN:cover-3.5"
    team_pts  -> "MIN:pts>20.5"       total -> "total>44.5"      margin -> "margin>3.5" (home margin)
    player    -> "J.Jefferson:rec_yds>79.5"
    """
    if op not in (">", "<"):
        raise ValueError("op must be '>' or '<'")
    if kind in ("team_win", "team_lose", "team_cover", "team_pts"):
        if not team or not re.fullmatch(r"[A-Za-z]{2,3}", team):
            raise ValueError("team legs need a 2-3 letter team code")
        t = team.upper()
        if kind == "team_win":
            return f"{t}:win"
        if kind == "team_lose":
            return f"{t}:lose"
        if line is None:
            raise ValueError("this leg needs a line")
        if kind == "team_cover":
            return f"{t}:cover{'+' if float(line) >= 0 else '-'}{_num(abs(float(line)))}"
        if float(line) < 0:
            raise ValueError("points line must be >= 0")
        return f"{t}:pts{op}{_num(line)}"
    if kind in ("total", "margin"):
        if line is None:
            raise ValueError("this leg needs a line")
        if kind == "total" and float(line) < 0:
            raise ValueError("total line must be >= 0")
        return f"{kind}{op}{_num(line)}"
    if kind == "player":
        if not player or not stat or line is None:
            raise ValueError("player legs need a player, a stat and a line")
        if any(c in player for c in ":;"):
            raise ValueError("player name may not contain ':' or ';'")
        if not re.fullmatch(r"[a-z_]+", stat):
            raise ValueError(f"bad stat {stat!r}")
        if float(line) < 0:
            raise ValueError("player lines must be >= 0")
        return f"{player.strip()}:{stat}{op}{_num(line)}"
    raise ValueError(f"unknown leg kind {kind!r}")


def combo_string(legs: list[str]) -> str:
    legs = [l.strip() for l in legs if l and l.strip()]
    if not legs:
        raise ValueError("add at least one leg")
    return "; ".join(legs)


# ---- live game state (format documented in gridsim/state.py) -----------------------------------------

def _ordinal(d: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}[int(d)]


def state_string(quarter: str, minutes: int, seconds: int, home: str, home_score: int, away: str, away_score: int,
                 possession: str, down: int = 1, distance: int | str = 10, spot_side: str | None = None,
                 spot_yard: int = 25, home_timeouts: int = 3, away_timeouts: int = 3, kickoff: bool = False,
                 receives2h: str | None = None) -> str:
    """Build e.g. "Q3 8:42 MIN 17 GB 13 MIN ball 2nd&6 at GB 34 timeouts 2/3".

    quarter: "Q1".."Q4" or "OT". possession: team code with the ball (or, with kickoff=True, the team about to
    kick off). spot_side: team code whose side of the field the ball is on (None or yard 50 = midfield).
    distance: yards or "goal". Timeouts are written in the same order as the score pairs (home first).
    """
    q = quarter.upper()
    if not re.fullmatch(r"Q[1-4]|OT\d?", q):
        raise ValueError("quarter must be Q1-Q4 or OT")
    minutes, seconds = int(minutes), int(seconds)
    if not (0 <= seconds < 60) or minutes < 0 or minutes * 60 + seconds > (600 if q.startswith("OT") else 900):
        raise ValueError("clock must be between 0:00 and 15:00 (10:00 in OT)")
    H, A, P = home.upper(), away.upper(), possession.upper()
    if P not in (H, A):
        raise ValueError("possession must be the home or away team")
    for v in (home_score, away_score):
        if int(v) < 0:
            raise ValueError("scores must be >= 0")
    for v in (home_timeouts, away_timeouts):
        if not 0 <= int(v) <= 3:
            raise ValueError("timeouts must be 0-3")
    parts = [f"{q} {minutes}:{seconds:02d}", f"{H} {int(home_score)} {A} {int(away_score)}"]
    if kickoff:
        parts.append(f"kickoff {P}")
    else:
        if int(down) not in (1, 2, 3, 4):
            raise ValueError("down must be 1-4")
        dist = "goal" if str(distance).lower() == "goal" else str(int(distance))
        if dist != "goal" and not 1 <= int(dist) <= 99:
            raise ValueError("distance must be 1-99 or goal")
        y = int(spot_yard)
        if not 1 <= y <= 50:
            raise ValueError("ball spot yard line must be 1-50")
        if y == 50 or not spot_side:
            at = "at 50"
        else:
            S = spot_side.upper()
            if S not in (H, A):
                raise ValueError("ball spot side must be the home or away team")
            at = f"at {S} {y}"
        parts.append(f"{P} ball {_ordinal(down)}&{dist} {at}")
    parts.append(f"timeouts {int(home_timeouts)}/{int(away_timeouts)}")
    if receives2h:
        parts.append(f"receives2h {receives2h.upper()}")
    return " ".join(parts)


# ---- props: per-sim histogram (preferred) or approximate percentile interpolation --------------------

PCT_KEYS = ((10, "p10"), (25, "p25"), (50, "p50"), (75, "p75"), (90, "p90"))


def approx_over_from_percentiles(stats: dict, line: float) -> float:
    """APPROXIMATE P(stat > line) from the five stored percentiles (p10..p90) by linear interpolation of the
    CDF, extended past p10/p90 with the neighbouring segment's slope and clamped to [0, 1]. Used only when a
    summary has no per-sim histogram for the stat; the GUI labels it "approximate (percentile interpolation)"."""
    pts: dict[float, float] = {}
    for q, k in PCT_KEYS:
        if k in stats and stats[k] is not None:
            x = float(stats[k])
            pts[x] = max(pts.get(x, 0.0), q / 100.0)    # tied percentiles (discrete stats): keep the top one
    if not pts:
        raise ValueError("no percentiles")
    xs = sorted(pts)
    fs = [pts[x] for x in xs]
    L = float(line)
    if len(xs) == 1:
        F = 0.0 if L < xs[0] else fs[0] if L == xs[0] else 1.0
    elif L <= xs[0]:
        slope = (fs[1] - fs[0]) / (xs[1] - xs[0])
        F = fs[0] - slope * (xs[0] - L)
    elif L >= xs[-1]:
        slope = (fs[-1] - fs[-2]) / (xs[-1] - xs[-2])
        F = fs[-1] + slope * (L - xs[-1])
    else:
        F = None
        for (x0, f0), (x1, f1) in zip(zip(xs, fs), zip(xs[1:], fs[1:])):
            if x0 <= L <= x1:
                F = f0 + (f1 - f0) * (L - x0) / (x1 - x0)
                break
    return float(min(1.0, max(0.0, 1.0 - F)))


def default_prop_line(median: float) -> float:
    """Half-point line just above the median (no pushes), e.g. 63 -> 63.5, 0 -> 0.5."""
    return math.floor(float(median)) + 0.5


def prop_market_rows(rows: list[dict], player: str) -> list[dict]:
    """Kalshi rows whose label mentions the player's full or last name (case-insensitive)."""
    name = player.strip().lower()
    last = name.split()[-1] if name.split() else name
    out = []
    for r in rows or []:
        lab = str(r.get("label") or "").lower()
        if name and (name in lab or (len(last) >= 3 and re.search(rf"\b{re.escape(last)}\b", lab))):
            out.append(r)
    return out


# ---- signals_report text -> rows ----------------------------------------------------------------------

def parse_signals_report(text: str) -> tuple[list[dict], str]:
    """gridsim.scanner.signals_report output -> ([{market, confidence, n, hit_pct, avg_clv, pnl}], trust line)."""
    rows, trust = [], ""
    for line in (text or "").splitlines():
        if line.startswith("trust:"):
            trust = line[len("trust:"):].strip()
            continue
        parts = line.split()
        if len(parts) == 6 and parts[0] != "market":
            try:
                rows.append({"market": parts[0], "confidence": parts[1], "n": int(parts[2]),
                             "hit_pct": float(parts[3]), "avg_clv": float(parts[4]), "pnl": float(parts[5])})
            except ValueError:
                continue
    return rows, trust


# ---- README scheduling section, file freshness ------------------------------------------------------

DEFAULT_SCHEDULE = """| When | Arguments | What it does |
|---|---|---|
| Tuesday 9:00 | `weekly` | refresh, grade last week, diagnose, predict the next week |
| Friday 19:00 | `predict-week` | fresh frozen predictions with Friday statuses |
| Thu–Sun every 30 min (game weeks) | `scan --notify` | re-price Kalshi markets, push alerts |"""


def readme_section(text: str, heading_prefix: str = "## Scheduling") -> str | None:
    """The body of the README section whose heading starts with heading_prefix (up to the next '## ')."""
    lines = (text or "").splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(heading_prefix)), None)
    if start is None:
        return None
    body = []
    for l in lines[start + 1:]:
        if l.startswith("## "):
            break
        body.append(l)
    return "\n".join(body).strip() or None


def newest_file(d: Path, pattern: str = "**/*") -> tuple[Path, dt.datetime] | None:
    d = Path(d)
    if not d.exists():
        return None
    best = None
    for p in d.glob(pattern):
        if p.is_file():
            m = p.stat().st_mtime
            if best is None or m > best[1]:
                best = (p, m)
    return (best[0], dt.datetime.fromtimestamp(best[1])) if best else None


def is_flag(v) -> bool:
    """diagnose.json stores numpy bools as strings ("True") — accept both."""
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


# ---- config.yaml: comment-aware line parser and line-preserving writer ------------------------------

_KEY_RE = re.compile(r"^(?P<indent>[ ]*)(?P<key>[^\s#:\-][^:#]*?|\"[^\"]+\"|'[^']+'):(?=\s|$)(?P<rest>.*)$")


def _split_comment(rest: str) -> tuple[int, int, str]:
    """For the text after 'key:', return (value_start, value_end, comment) as offsets into rest.
    A comment starts at '#' that is outside quotes and at the start or preceded by whitespace (YAML rule)."""
    quote = None
    cpos = None
    for i, ch in enumerate(rest):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and (i == 0 or rest[:i].strip() == ""):
            quote = ch
        elif ch == "#" and (i == 0 or rest[i - 1] in " \t"):
            cpos = i
            break
    val_part = rest if cpos is None else rest[:cpos]
    lead = len(val_part) - len(val_part.lstrip())
    vs, ve = lead, len(val_part.rstrip())
    comment = "" if cpos is None else rest[cpos + 1:].strip()
    return vs, max(vs, ve), comment


def _kind(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "str"
    if v is None:
        return "null"
    if isinstance(v, list):
        return "list"
    return "map"


def parse_config(text: str) -> list[dict]:
    """One entry per 'key: value' line of a block-style YAML file:
    {line, path (tuple of keys), key, depth, raw (value text), value (parsed), kind, comment, doc (full-line
    comments right above), section (top-level key), editable (scalar)}. Mapping headers (no value) are
    returned with kind "section"."""
    lines = text.splitlines()
    out: list[dict] = []
    stack: list[tuple[int, str]] = []
    doc: list[str] = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            doc = []
            continue
        if s.startswith("#"):
            doc.append(s.lstrip("#").strip())
            continue
        m = _KEY_RE.match(line)
        if not m:
            doc = []
            continue
        indent = len(m.group("indent"))
        key = m.group("key").strip().strip("\"'")
        rest = m.group("rest")
        vs, ve, comment = _split_comment(rest)
        raw = rest[vs:ve]
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = tuple(k for _, k in stack) + (key,)
        col = m.end("key") + 1          # offset of rest in the line
        if raw == "":
            out.append({"line": i, "path": path, "key": key, "depth": len(stack), "raw": "", "value": None,
                        "kind": "section", "comment": comment, "doc": " ".join(doc), "section": path[0],
                        "editable": False, "vstart": col + vs, "vend": col + ve})
            stack.append((indent, key))
        else:
            try:
                val = yaml.safe_load(raw)
            except yaml.YAMLError:
                val = raw
            kind = _kind(val)
            out.append({"line": i, "path": path, "key": key, "depth": len(stack), "raw": raw, "value": val,
                        "kind": kind, "comment": comment, "doc": " ".join(doc), "section": path[0],
                        "editable": kind in ("bool", "int", "float", "str"), "vstart": col + vs, "vend": col + ve})
        doc = []
    return out


def format_value(new, kind: str, raw: str) -> str:
    """YAML text for a new scalar that parses back to exactly `new` with the same type."""
    if kind == "bool":
        text = "true" if bool(new) else "false"
        return text.capitalize() if raw[:1].isupper() else text
    if kind == "int":
        return str(int(new))
    if kind == "float":
        f = float(new)
        if math.isnan(f) or math.isinf(f):
            raise ValueError("NaN / infinity are not allowed")
        s = repr(f)
        if "e" in s or "E" in s:                     # YAML 1.1 (PyYAML) reads 1e-05 as a string
            s = format(f, ".15f").rstrip("0")
            if s.endswith("."):
                s += "0"
        return s
    if kind == "str":
        new = str(new)
        if "\n" in new:
            raise ValueError("multi-line strings are not supported")
        if raw.startswith("'"):
            return "'" + new.replace("'", "''") + "'"
        if raw.startswith('"'):
            return json.dumps(new, ensure_ascii=False)
        try:
            plain_ok = yaml.safe_load(f"k: {new}") == {"k": new} and " #" not in new and new.strip() == new and new != ""
        except yaml.YAMLError:
            plain_ok = False
        return new if plain_ok else json.dumps(new, ensure_ascii=False)
    raise ValueError(f"cannot edit a {kind} value")


def _key(d: dict, k: str):
    """Block keys come back from the line parser as text; YAML may have loaded them as numbers/bools."""
    if k in d:
        return k
    for cand in d:
        if str(cand) == k or (isinstance(cand, bool) and str(cand).lower() == k.lower()):
            return cand
    raise KeyError(k)


def get_path(d, path):
    for k in path:
        d = d[_key(d, k)]
    return d


def _set_path(d, path, v):
    for k in path[:-1]:
        d = d[_key(d, k)]
    d[_key(d, path[-1])] = v


def rewrite_config(text: str, changes: dict[tuple, object]) -> str:
    """Return `text` with the value of each changed key replaced IN PLACE on its own line: indentation, the
    key, spacing, the inline comment, every other line and the line endings stay byte-identical. Verifies by
    re-parsing that exactly the requested values changed (and nothing else)."""
    if not changes:
        return text
    entries = {e["path"]: e for e in parse_config(text)}
    lines = text.splitlines(keepends=True)
    for path, new in changes.items():
        path = tuple(path)
        e = entries.get(path)
        if e is None or not e["editable"]:
            raise ValueError(f"{'.'.join(path)} is not an editable scalar")
        if e["kind"] in ("int", "float") and (isinstance(new, bool) or not isinstance(new, (int, float))):
            raise ValueError(f"{'.'.join(path)} must be a number")
        if e["kind"] == "int" and isinstance(new, float):
            if new != int(new):
                raise ValueError(f"{'.'.join(path)} must be a whole number")
            new = int(new)
        line = lines[e["line"]]
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        newtext = format_value(new, e["kind"], e["raw"])
        after = body[e["vend"]:]
        if after.lstrip(" ").startswith("#"):          # keep the inline comment's column when possible
            gap = len(after) - len(after.lstrip(" "))
            delta = len(newtext) - (e["vend"] - e["vstart"])
            gap2 = max(1, gap - delta) if delta > 0 else gap - delta
            after = " " * gap2 + after.lstrip(" ")
        lines[e["line"]] = body[:e["vstart"]] + newtext + after + ending
    out = "".join(lines)
    # validate: the new file parses, the changed keys hold the new values, everything else is unchanged
    before, after = yaml.safe_load(text), yaml.safe_load(out)
    for path, new in changes.items():
        _set_path(before, tuple(path), int(new) if entries[tuple(path)]["kind"] == "int" else new)
    if before != after:
        raise ValueError("rewrite check failed: the edited file does not parse to the expected values")
    return out


def save_config(path: Path, changes: dict[tuple, object], backup_suffix: str = ".bak") -> Path:
    """Rewrite `path` with `changes` (line-preserving), keeping a backup copy next to it first.
    Returns the backup path. Line endings and encoding are preserved (bytes in, bytes out)."""
    path = Path(path)
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    new = rewrite_config(text, changes)
    bak = path.with_name(path.name + backup_suffix)
    shutil.copy2(path, bak)
    path.write_bytes(new.encode("utf-8"))
    return bak
