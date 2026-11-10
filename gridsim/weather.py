"""Weather / roof adjustment.

Effects are fit from historical outdoor games (pbp `temp`, `wind`, `weather` text) and applied to:
  * FG and XP make probability: logit shift = b_wind * max(0, wind - 8) * dist/40 + b_cold * cold10 + b_precip * precip
  * dropback EPA: shift = (a_wind * max(0, wind - 8) + a_cold * cold10 + a_precip * precip) * weather.strength,
    added to the matchup EPA target for pass plays (both offenses)
with cold10 = max(0, 45 - temp)/10 and precip = rain/snow indicator. Domes, closed roofs and retractable roofs
skip the adjustment. Upcoming games use an Open-Meteo forecast (free, no key) at the stadium for the kickoff
hour; backtests use the recorded game-day weather (close to what forecasts said). All coefficients are
shrunk toward 0 by fitting with a ridge penalty, then scaled by the tunable `weather.strength`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.request
from dataclasses import dataclass

import numpy as np
import polars as pl

# stadium -> (lat, lon, roof) ; roof: outdoors | dome | retractable
STADIUMS = {
    "Gillette Stadium": (42.0909, -71.2643, "outdoors"), "Raymond James Stadium": (27.9759, -82.5033, "outdoors"),
    "Huntington Bank Field": (41.5061, -81.6995, "outdoors"), "Cleveland Browns Stadium": (41.5061, -81.6995, "outdoors"),
    "Estadio Banorte": (19.3029, -99.1505, "outdoors"), "Estadio Azteca": (19.3029, -99.1505, "outdoors"),
    "Mercedes-Benz Stadium": (33.7554, -84.4008, "retractable"), "GEHA Field at Arrowhead Stadium": (39.0489, -94.4839, "outdoors"),
    "Arrowhead Stadium": (39.0489, -94.4839, "outdoors"), "Melbourne Cricket Ground": (-37.8200, 144.9834, "outdoors"),
    "Allegiant Stadium": (36.0909, -115.1833, "dome"), "Paycor Stadium": (39.0955, -84.5161, "outdoors"),
    "Acrisure Stadium": (40.4468, -80.0158, "outdoors"), "SoFi Stadium": (33.9535, -118.3392, "dome"),
    "Hard Rock Stadium": (25.9580, -80.2389, "outdoors"), "Tottenham Hotspur Stadium": (51.6043, -0.0664, "outdoors"),
    "Bernabeu": (40.4531, -3.6883, "retractable"), "Santiago Bernabeu": (40.4531, -3.6883, "retractable"),
    "Stade de France": (48.9245, 2.3602, "outdoors"), "U.S. Bank Stadium": (44.9737, -93.2581, "dome"),
    "M&T Bank Stadium": (39.2780, -76.6227, "outdoors"), "Lucas Oil Stadium": (39.7601, -86.1639, "retractable"),
    "Soldier Field": (41.8623, -87.6167, "outdoors"), "Northwest Stadium": (38.9077, -76.8645, "outdoors"),
    "FedExField": (38.9077, -76.8645, "outdoors"), "Reliant Stadium": (29.6847, -95.4107, "retractable"),
    "NRG Stadium": (29.6847, -95.4107, "retractable"), "MetLife Stadium": (40.8135, -74.0745, "outdoors"),
    "Levi's Stadium": (37.4030, -121.9700, "outdoors"), "Empower Field at Mile High": (39.7439, -105.0201, "outdoors"),
    "State Farm Stadium": (33.5276, -112.2626, "retractable"), "Maracana Stadium": (-22.9122, -43.2302, "outdoors"),
    "Arena Corinthians": (-23.5453, -46.4742, "outdoors"), "AT&T Stadium": (32.7473, -97.0945, "retractable"),
    "Wembley Stadium": (51.5560, -0.2796, "outdoors"), "Lambeau Field": (44.5013, -88.0622, "outdoors"),
    "FC Bayern Munich Stadium": (48.2188, 11.6247, "outdoors"), "Allianz Arena": (48.2188, 11.6247, "outdoors"),
    "Deutsche Bank Park": (50.0686, 8.6455, "outdoors"), "Ford Field": (42.3400, -83.0456, "dome"),
    "Caesars Superdome": (29.9511, -90.0812, "dome"), "Lincoln Financial Field": (39.9008, -75.1675, "outdoors"),
    "EverBank Stadium": (30.3239, -81.6373, "outdoors"), "TIAA Bank Stadium": (30.3239, -81.6373, "outdoors"),
    "Highmark Stadium": (42.7738, -78.7870, "outdoors"), "Lumen Field": (47.5952, -122.3316, "outdoors"),
    "Nissan Stadium": (36.1665, -86.7713, "outdoors"), "Bank of America Stadium": (35.2258, -80.8528, "outdoors"),
    "Croke Park": (53.3607, -6.2512, "outdoors"),
}
PRECIP_RE = re.compile(r"(?i)rain|snow|shower|drizzle|sleet|storm|flurr")


@dataclass
class WeatherEffects:
    fg_wind: float = 0.0
    fg_cold: float = 0.0
    fg_precip: float = 0.0
    pass_wind: float = 0.0
    pass_cold: float = 0.0
    pass_precip: float = 0.0
    n_plays: int = 0


def _features(wind, temp, precip):
    return (np.maximum(0.0, np.asarray(wind, float) - 8.0), np.maximum(0.0, 45.0 - np.asarray(temp, float)) / 10.0,
            np.asarray(precip, float))


def fit_effects(raw: pl.DataFrame, ridge: float = 50.0) -> WeatherEffects:
    """raw: pbp rows with temp, wind, weather, roof, play_type, epa, pass, kick_distance, results."""
    out = raw.filter(pl.col("roof").is_in(["outdoors", "open"]) & pl.col("temp").is_not_null() & pl.col("wind").is_not_null())
    if out.height == 0:
        return WeatherEffects()
    out = out.with_columns(precip=pl.col("weather").fill_null("").str.contains(PRECIP_RE.pattern).cast(pl.Float64))
    # dropback EPA ~ features (ridge, centered)
    db = out.filter((pl.col("pass") == 1) & pl.col("epa").is_not_null() & pl.col("down").is_not_null())
    y = np.clip(db["epa"].to_numpy(), -6, 6)
    X = np.column_stack(_features(db["wind"].to_numpy(), db["temp"].to_numpy(), db["precip"].to_numpy()))
    Xc = X - X.mean(axis=0)
    beta = np.linalg.solve(Xc.T @ Xc + ridge * len(y) / 1000 * np.eye(3), Xc.T @ (y - y.mean()))
    # FG/XP make ~ logistic(distance) + weather (ridge-penalized IRLS)
    fg = out.filter(pl.col("play_type").is_in(["field_goal", "extra_point"]))
    d = np.where(fg["play_type"].to_numpy() == "extra_point", 33.0, fg["kick_distance"].fill_null(33).to_numpy().astype(float))
    made = ((fg["field_goal_result"] == "made") | (fg["extra_point_result"] == "good")).fill_null(False).to_numpy().astype(float)
    w_, c_, p_ = _features(fg["wind"].to_numpy(), fg["temp"].to_numpy(), fg["precip"].to_numpy())
    Z = np.column_stack([np.ones_like(d), d / 10, (d / 10) ** 2, w_ * d / 40, c_, p_])
    b = np.array([5.0, -0.8, 0.0, 0.0, 0.0, 0.0])
    pen = np.diag([0, 0, 0, 1, 1, 1]) * 20.0
    for _ in range(40):
        pr = 1 / (1 + np.exp(-(Z @ b)))
        W = pr * (1 - pr) + 1e-9
        step = np.linalg.solve((Z * W[:, None]).T @ Z + pen, Z.T @ (made - pr) - pen @ b)
        b = b + step
        if np.abs(step).max() < 1e-8:
            break
    return WeatherEffects(fg_wind=float(min(b[3], 0)), fg_cold=float(min(b[4], 0)), fg_precip=float(min(b[5], 0)),
                          pass_wind=float(min(beta[0], 0)), pass_cold=float(min(beta[1], 0)), pass_precip=float(min(beta[2], 0)),
                          n_plays=int(len(y)))


def is_indoor(game: dict | None, home: str | None = None) -> bool:
    if not game:
        return False
    roof = (game.get("roof") or "").lower()
    if roof in ("dome", "closed"):
        return True
    st = STADIUMS.get(game.get("stadium") or "")
    return bool(st and st[2] in ("dome", "retractable") and roof != "outdoors" and roof != "open")


def forecast(game: dict, cache_dir=None, timeout: float = 8.0) -> dict | None:
    """Open-Meteo hourly forecast for the stadium at kickoff (ET). None if unknown stadium / indoor / failure."""
    st = STADIUMS.get(game.get("stadium") or "")
    if st is None or is_indoor(game):
        return None
    lat, lon, _ = st
    day = game["gameday"]
    hour = int((game.get("gametime") or "13:00").split(":")[0])
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&hourly=temperature_2m,precipitation,snowfall,wind_speed_10m,precipitation_probability"
           f"&temperature_unit=fahrenheit&wind_speed_unit=mph&timezone=America%2FNew_York&start_date={day}&end_date={day}")
    # the last forecast that arrived is kept on disk: a failed fetch then re-uses it (up to 36 hours old) instead of
    # silently simulating without weather, which would move the numbers although nothing about the game changed
    keep = None
    if cache_dir is not None and game.get("game_id"):
        from pathlib import Path
        keep = Path(cache_dir) / "weather" / f"{game['game_id']}.json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gridsim"}), timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception as exc:
        try:
            old = json.loads(keep.read_text(encoding="utf-8")) if keep is not None and keep.exists() else None
            if old and old.get("gameday") == day and old.get("gametime") == game.get("gametime"):
                age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(old["fetched_utc"])
                if age.total_seconds() < 36 * 3600:
                    return {**old["wx"], "source": "open-meteo (earlier forecast; the service did not answer just now)"}
        except Exception:
            pass
        return {"error": str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__, "source": "open-meteo"}
    h = data.get("hourly", {})
    times = h.get("time", [])
    idx = [i for i, t in enumerate(times) if int(t[11:13]) in (hour, hour + 1, hour + 2)]
    if not idx:
        return None
    pick = lambda k: [h[k][i] for i in idx if h.get(k) and h[k][i] is not None]  # noqa: E731
    temp = float(np.mean(pick("temperature_2m"))) if pick("temperature_2m") else None
    wind = float(np.mean(pick("wind_speed_10m"))) if pick("wind_speed_10m") else None
    precip_mm = float(np.sum(pick("precipitation"))) if pick("precipitation") else 0.0
    snow = float(np.sum(pick("snowfall"))) if pick("snowfall") else 0.0
    pprob = float(np.max(pick("precipitation_probability"))) if pick("precipitation_probability") else None
    wx = {"source": "open-meteo", "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
          "temp_f": temp, "wind_mph": wind, "precip_mm_3h": precip_mm, "snow_cm_3h": snow, "precip_prob": pprob,
          "precip": 1.0 if (precip_mm >= 0.5 or snow > 0) else 0.0}
    if keep is not None and temp is not None and wind is not None:
        try:
            keep.parent.mkdir(parents=True, exist_ok=True)
            tmp = keep.with_name(keep.name + ".partial")
            tmp.write_text(json.dumps({"fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "gameday": day,
                                       "gametime": game.get("gametime"), "wx": wx}), encoding="utf-8")
            os.replace(tmp, keep)
        except OSError:
            pass
    return wx


def from_recorded(game: dict) -> dict | None:
    """Recorded game-day weather (backtests)."""
    if is_indoor(game) or game.get("temp") is None or game.get("wind") is None:
        return None
    return {"source": "recorded", "temp_f": float(game["temp"]), "wind_mph": float(game["wind"]),
            "precip": 1.0 if PRECIP_RE.search(game.get("weather") or "") else 0.0}


def adjustments(eff: WeatherEffects, wx: dict | None, strength: float) -> dict:
    """Engine inputs: fg_logit(dist) coefficients and the dropback EPA shift."""
    if not wx or wx.get("error") or wx.get("temp_f") is None or wx.get("wind_mph") is None or strength <= 0:
        return {"fg_wind": 0.0, "fg_const": 0.0, "pass_epa": 0.0}
    w, c, p = _features([wx["wind_mph"]], [wx["temp_f"]], [wx.get("precip", 0.0)])
    w, c, p = float(w[0]), float(c[0]), float(p[0])
    return {"fg_wind": strength * eff.fg_wind * w / 40.0,           # multiply by distance in yards
            "fg_const": strength * (eff.fg_cold * c + eff.fg_precip * p),
            "pass_epa": strength * (eff.pass_wind * w + eff.pass_cold * c + eff.pass_precip * p)}
