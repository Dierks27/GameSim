"""nflverse data access, local parquet caching, and play cleaning.

Everything downstream works off `prepare_plays()`, which turns raw nflverse play-by-play into
one row per snap with derived, simulation-ready fields. The key idea is that the *next snap*
in the same game tells us where the ball really ended up (penalty enforcement, fumble
recoveries, returns), so net field-position changes are derived from it whenever possible.
See SCHEMA_NOTES.md for the columns this relies on.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import polars as pl

from .config import Config

SNAP_TYPES = ["pass", "run", "punt", "field_goal", "kickoff", "extra_point", "qb_kneel", "qb_spike", "no_play"]
# next-snap types whose yardline_100 is a real line of scrimmage for geometry purposes
SPOT_TYPES = ["pass", "run", "punt", "field_goal", "qb_kneel", "qb_spike", "no_play"]

# 5-yard defensive fouls that do NOT carry an automatic first down
NON_AUTO_FIRST = {
    "Defensive Offside", "Encroachment", "Neutral Zone Infraction", "Defensive Too Many Men on Field",
    "Defensive Delay of Game", "Illegal Substitution", "Defensive 12 On-field", "Illegal Formation",
    "Running Into the Kicker", "Delay of Game", "Defensive Holding on Kick",
}

PBP_KEEP = [
    "game_id", "play_id", "season", "week", "season_type", "home_team", "away_team", "posteam", "defteam",
    "qtr", "down", "ydstogo", "yardline_100", "goal_to_go", "game_seconds_remaining", "half_seconds_remaining",
    "quarter_seconds_remaining", "score_differential", "posteam_score", "defteam_score", "wp", "epa",
    "posteam_timeouts_remaining", "defteam_timeouts_remaining", "play_type", "desc",
    "pass", "rush", "qb_dropback", "qb_scramble", "qb_kneel", "qb_spike", "sack", "yards_gained",
    "complete_pass", "incomplete_pass", "interception", "fumble", "fumble_lost", "touchdown", "td_team",
    "return_touchdown", "safety", "penalty", "penalty_team", "penalty_yards", "penalty_type",
    "first_down", "first_down_penalty", "out_of_bounds", "air_yards", "return_yards", "two_point_attempt",
    "two_point_conv_result", "extra_point_result", "field_goal_result", "kick_distance", "touchback",
    "punt_blocked", "own_kickoff_recovery", "timeout", "timeout_team", "passing_yards", "rushing_yards",
    "receiving_yards", "passer_player_id", "passer_player_name", "rusher_player_id", "rusher_player_name",
    "receiver_player_id", "receiver_player_name", "kicker_player_id", "kicker_player_name",
    "punter_player_id", "punter_player_name", "pass_attempt", "rush_attempt", "no_huddle",
    "drive", "fixed_drive", "roof", "location", "aborted_play",
]


# --------------------------------------------------------------------------------------
# Download + cache
# --------------------------------------------------------------------------------------

def current_season(today: dt.date | None = None) -> int:
    """NFL season year: games from September through February belong to the September year."""
    today = today or dt.date.today()
    return today.year if today.month >= 9 else today.year - 1


def _cache_dir(cfg: Config) -> Path:
    d = cfg.resolve_path(cfg.data["cache_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_stale(path: Path, season: int | None, cfg: Config) -> bool:
    if not path.exists():
        return True
    if season is not None and season < current_season():
        return False  # completed seasons never go stale
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    return age_h > float(cfg.data["refresh_hours"])


def load_pbp(seasons: list[int], cfg: Config, force: bool = False, verbose: bool = False) -> pl.DataFrame:
    import nflreadpy as nfl

    frames = []
    for s in sorted(set(seasons)):
        path = _cache_dir(cfg) / f"pbp_{s}.parquet"
        if force or _is_stale(path, s, cfg):
            if verbose:
                print(f"  downloading play-by-play {s} ...", flush=True)
            try:
                df = nfl.load_pbp([s])
            except Exception as exc:  # network trouble: fall back to stale cache if present
                if path.exists():
                    print(f"  warning: refresh of {s} failed ({exc}); using cached copy")
                    df = pl.read_parquet(path)
                else:
                    raise
            else:
                df.write_parquet(path)
        else:
            df = pl.read_parquet(path)
        cols = [c for c in PBP_KEEP if c in df.columns]
        frames.append(df.select(cols))
    if not frames:
        return pl.DataFrame()
    return pl.concat(frames, how="diagonal_relaxed")


def load_schedules(cfg: Config, force: bool = False, verbose: bool = False) -> pl.DataFrame:
    import nflreadpy as nfl

    path = _cache_dir(cfg) / "schedules.parquet"
    if force or _is_stale(path, None, cfg):
        if verbose:
            print("  downloading schedules ...", flush=True)
        try:
            df = nfl.load_schedules(True)
            df.write_parquet(path)
        except Exception as exc:
            if path.exists():
                print(f"  warning: schedule refresh failed ({exc}); using cached copy")
                df = pl.read_parquet(path)
            else:
                raise
    else:
        df = pl.read_parquet(path)
    return df


def refresh_all(cfg: Config, seasons: list[int] | None = None) -> None:
    cur = current_season()
    seasons = seasons or list(range(cur - 3, cur + 1))
    load_schedules(cfg, force=True, verbose=True)
    for s in seasons:
        path = _cache_dir(cfg) / f"pbp_{s}.parquet"
        load_pbp([s], cfg, force=(s == cur) or not path.exists(), verbose=True)


# --------------------------------------------------------------------------------------
# Cleaning / derivation
# --------------------------------------------------------------------------------------

def prepare_plays(raw: pl.DataFrame) -> pl.DataFrame:
    """One row per snap with derived simulation fields.

    Derived columns (all from the offense's perspective unless noted):
      call            'pass' (dropback incl. sacks/scrambles), 'run' (designed run), or None
      presnap         pre-snap foul (no_play with neither pass nor rush flag)
      np_pen          post-snap no-play penalty (the down is replayed unless a first down results)
      yards           net field-position change for the offense, penalties included
      td_off/td_def   touchdown by offense / by defense on this snap
      turnover        interception or lost fumble that changed possession
      to_gain         for turnovers: field movement (offense coords) to where the defense takes over
      auto_first      foul by the defense carrying an automatic first down
      elapsed         seconds from this snap to the next snap in the same quarter (null otherwise)
      to_after        a timeout was called between this snap and the next
      crosses_2mw     the elapsed interval spans the two-minute warning (no row exists for it)
      n_*             next snap's posteam / yardline / play type / quarter
      rec_yl          special teams: receiving team's yardline_100 at its next snap
      kick_retained   special teams: kicking team kept the ball (onside / muff / penalty)
    """
    df = raw.sort(["game_id", "play_id"])
    int_cols = [c for c in ["qtr", "down", "week", "season", "posteam_timeouts_remaining", "defteam_timeouts_remaining"]
                if c in df.columns]
    df = df.with_columns([pl.col(c).cast(pl.Int32, strict=False) for c in int_cols])
    df = df.with_columns(
        is_timeout=((pl.col("timeout") == 1) & (pl.col("play_type") == "no_play") & (pl.col("penalty") == 0)).fill_null(False),
    )
    df = df.with_columns(is_snap=(pl.col("play_type").is_in(SNAP_TYPES) & ~pl.col("is_timeout")).fill_null(False))
    df = df.with_columns(snap_seq=pl.col("is_snap").cast(pl.Int32).cum_sum().over("game_id"))
    to_after = (
        df.filter("is_timeout").group_by(["game_id", "snap_seq"]).agg(pl.len().alias("n_to"),
                                                                       pl.col("timeout_team").first().alias("to_team"))
    )
    s = df.filter("is_snap").join(to_after, on=["game_id", "snap_seq"], how="left")
    s = s.with_columns(pl.col("n_to").fill_null(0))
    # Timeout rows are sometimes logged after the following snap; a drop in either team's
    # timeouts-remaining between consecutive snaps of the same half is the robust signal.
    s = s.with_columns(
        home_to=pl.when(pl.col("posteam") == pl.col("home_team")).then(pl.col("posteam_timeouts_remaining"))
        .otherwise(pl.col("defteam_timeouts_remaining")),
        away_to=pl.when(pl.col("posteam") == pl.col("home_team")).then(pl.col("defteam_timeouts_remaining"))
        .otherwise(pl.col("posteam_timeouts_remaining")),
        half=pl.when(pl.col("qtr") <= 2).then(1).when(pl.col("qtr") <= 4).then(2).otherwise(3),
    )
    s = s.with_columns(
        to_drop=((pl.col("half").shift(-1).over("game_id") == pl.col("half"))
                 & ((pl.col("home_to").shift(-1).over("game_id") < pl.col("home_to"))
                    | (pl.col("away_to").shift(-1).over("game_id") < pl.col("away_to")))).fill_null(False))
    s = s.with_columns(n_to=pl.when(pl.col("to_drop")).then(pl.max_horizontal(pl.col("n_to"), pl.lit(1))).otherwise(pl.col("n_to")))

    nxt = {"posteam": "n_posteam", "yardline_100": "n_yl", "game_seconds_remaining": "n_gsr",
           "qtr": "n_qtr", "play_type": "n_play_type", "down": "n_down"}
    s = s.with_columns([pl.col(c).shift(-1).over("game_id").alias(a) for c, a in nxt.items()])

    f = lambda c: pl.col(c).fill_null(0)  # noqa: E731
    s = s.with_columns(
        call=pl.when(f("qb_kneel") == 1).then(None)
        .when(f("qb_spike") == 1).then(None)
        .when(f("pass") == 1).then(pl.lit("pass"))
        .when(f("rush") == 1).then(pl.lit("run"))
        .otherwise(None),
        presnap=((pl.col("play_type") == "no_play") & (f("penalty") == 1) & (f("pass") == 0) & (f("rush") == 0)),
        np_pen=((pl.col("play_type") == "no_play") & (f("penalty") == 1)),
        td_off=(f("touchdown") == 1) & (pl.col("td_team") == pl.col("posteam")),
        td_def=(f("touchdown") == 1) & (pl.col("td_team") == pl.col("defteam")),
        same_next=(pl.col("n_posteam") == pl.col("posteam")).fill_null(False),
        next_spot=pl.col("n_play_type").is_in(SPOT_TYPES).fill_null(False),
        pen_signed=pl.when(f("penalty") == 1)
        .then(pl.when(pl.col("penalty_team") == pl.col("defteam")).then(f("penalty_yards")).otherwise(-f("penalty_yards")))
        .otherwise(0.0),
    )
    # geometric gain from the next snap (offense coordinates)
    s = s.with_columns(
        gain_geo=pl.when(pl.col("next_spot") & pl.col("same_next"))
        .then(pl.col("yardline_100") - pl.col("n_yl"))
        .when(pl.col("next_spot") & ~pl.col("same_next") & pl.col("n_posteam").is_not_null())
        .then(pl.col("yardline_100") + pl.col("n_yl") - 100)
        .otherwise(None),
    )
    turnover_raw = (f("interception") == 1) | (f("fumble_lost") == 1)
    s = s.with_columns(
        turnover=(turnover_raw & (~pl.col("same_next") | pl.col("td_def") | pl.col("n_posteam").is_null())
                  & ~pl.col("td_off")),
    )
    s = s.with_columns(
        yards=pl.when(pl.col("td_off")).then(pl.col("yardline_100"))
        .when(f("safety") == 1).then(pl.col("yardline_100") - 100)
        .when(pl.col("gain_geo").is_not_null() & ~pl.col("turnover")).then(pl.col("gain_geo"))
        .otherwise(f("yards_gained") + pl.when(pl.col("np_pen")).then(pl.col("pen_signed")).otherwise(0.0)),
        to_gain=pl.when(pl.col("turnover") & pl.col("gain_geo").is_not_null()).then(pl.col("gain_geo"))
        .when(pl.col("turnover") & (f("interception") == 1)).then(f("air_yards") - f("return_yards"))
        .when(pl.col("turnover")).then(f("yards_gained"))
        .otherwise(0.0),
        auto_first=((f("penalty") == 1) & (pl.col("penalty_team") == pl.col("defteam")) & (f("first_down_penalty") == 1)
                    & ~pl.col("penalty_type").is_in(list(NON_AUTO_FIRST)).fill_null(False)),
        elapsed=pl.when(pl.col("n_qtr") == pl.col("qtr")).then(pl.col("game_seconds_remaining") - pl.col("n_gsr")).otherwise(None),
    )
    hsr_after = pl.col("half_seconds_remaining") - pl.col("elapsed")
    s = s.with_columns(
        crosses_2mw=(pl.col("qtr").is_in([2, 4]) & (pl.col("half_seconds_remaining") > 120) & (hsr_after < 120)).fill_null(False),
        # special teams: receiving team's next yardline and whether the kicking team kept it
        rec_yl=pl.when(pl.col("play_type") == "kickoff")
        .then(pl.when(pl.col("same_next")).then(pl.col("n_yl")).otherwise(100 - pl.col("n_yl")))
        .when(pl.col("play_type").is_in(["punt", "field_goal"]))
        .then(pl.when(~pl.col("same_next")).then(pl.col("n_yl")).otherwise(100 - pl.col("n_yl")))
        .otherwise(None),
        kick_retained=pl.when(pl.col("play_type") == "kickoff").then(~pl.col("same_next") & pl.col("n_posteam").is_not_null())
        .when(pl.col("play_type") == "punt").then(pl.col("same_next"))
        .otherwise(False),
    )
    # did the previous snap (same game, quarter, offense) leave the clock running?
    q, g_, h_ = pl.col("qtr"), pl.col("game_seconds_remaining"), pl.col("half_seconds_remaining")
    window = ((q == 2) & (h_ <= 120)) | ((q >= 4) & (g_ <= 300))
    leaves_running = (
        pl.col("play_type").is_in(["pass", "run", "qb_kneel"]) & (f("incomplete_pass") == 0) & ~pl.col("turnover")
        & ~pl.col("td_off") & ~pl.col("td_def") & (f("safety") == 0) & (pl.col("n_to") == 0)
        & ((f("out_of_bounds") == 0) | ~window.fill_null(False))
    ).fill_null(False)
    s = s.with_columns(leaves_running=leaves_running)
    s = s.with_columns(prev_running=(pl.col("leaves_running").shift(1).over("game_id")
                                     & (pl.col("qtr").shift(1).over("game_id") == pl.col("qtr"))
                                     & (pl.col("posteam").shift(1).over("game_id") == pl.col("posteam"))).fill_null(False))
    return s.drop(["is_timeout", "is_snap", "snap_seq"])


def season_weights(target_season: int, cfg: Config) -> dict[int, float]:
    w = cfg.data["season_weights"]
    back = int(cfg.data["seasons_back"])
    return {target_season - i: float(w[i] if i < len(w) else w[-1]) for i in range(back + 1)}


def window_filter(plays: pl.DataFrame, target_season: int, through_week: int | None, seasons: list[int]) -> pl.DataFrame:
    """No-lookahead filter: prior seasons in full, target season only through `through_week`."""
    cond = pl.col("season").is_in([s for s in seasons if s != target_season])
    if target_season in seasons:
        tgt = pl.col("season") == target_season
        if through_week is not None:
            tgt = tgt & (pl.col("week") <= through_week)
        cond = cond | tgt
    return plays.filter(cond)


def garbage_time_mask(cfg: Config) -> pl.Expr:
    gt = cfg.data["garbage_time"]
    return (pl.col("qtr").is_in(gt["quarters"]) & ((pl.col("wp") < gt["wp_low"]) | (pl.col("wp") > gt["wp_high"]))).fill_null(False)
