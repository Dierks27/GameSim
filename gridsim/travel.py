"""Rest and travel edges (small, tunable via rest_travel.strength; check them in diagnose's residual breakdown).

home_edge_pts = (bye_home - bye_away) + (short_home - short_away) + rest-day difference
                + time zones travelled by the visitor * tz_pts_per_hour (negative for the visitor)
Converted to EPA/play: home offense +edge/2/plays, away offense -edge/2/plays.
"""
from __future__ import annotations

TEAM_TZ = {  # standard-time UTC offsets of home stadiums
    "ARI": -7, "ATL": -5, "BAL": -5, "BUF": -5, "CAR": -5, "CHI": -6, "CIN": -5, "CLE": -5, "DAL": -6, "DEN": -7,
    "DET": -5, "GB": -6, "HOU": -6, "IND": -5, "JAX": -5, "KC": -6, "LA": -8, "LAC": -8, "LV": -8, "MIA": -5,
    "MIN": -6, "NE": -5, "NO": -6, "NYG": -5, "NYJ": -5, "PHI": -5, "PIT": -5, "SEA": -8, "SF": -8, "TB": -5,
    "TEN": -6, "WAS": -5,
}


def edge(game: dict | None, cfg) -> dict:
    rt = cfg.rest_travel
    s = float(rt.get("strength", 1.0))
    if not game or s <= 0:
        return {"home_pts": 0.0, "detail": [], "epa": (0.0, 0.0)}
    hr, ar = game.get("home_rest"), game.get("away_rest")
    pts, detail = 0.0, []
    if hr is not None and ar is not None:
        bye = float(rt["bye_pts"])
        short = float(rt["short_week_pts"])
        for who, rest, sign in (("home", hr, 1), ("away", ar, -1)):
            if rest >= 13:
                pts += sign * bye
                detail.append(f"{who} off bye")
            elif rest <= 5:
                pts += sign * short
                detail.append(f"{who} short week")
        d = max(-3, min(3, hr - ar))
        pts += float(rt.get("rest_diff_pts_per_day", 0.0)) * d
    home, away = game.get("home_team"), game.get("away_team")
    if game.get("location") != "Neutral" and home in TEAM_TZ and away in TEAM_TZ:
        tz = min(3, abs(TEAM_TZ[home] - TEAM_TZ[away]))
        if tz:
            pts += -float(rt["tz_pts_per_hour"]) * tz   # visitor penalty is a home edge
            detail.append(f"{away} travels {tz} time zone{'s' if tz > 1 else ''}")
    elif game.get("location") == "Neutral" and float(rt.get("international_pts", 0.0)):
        detail.append("neutral/international site")
    pts *= s
    per = pts / 2.0 / float(rt.get("plays_per_team", 62))
    return {"home_pts": pts, "detail": detail, "epa": (per, -per)}
