# nflverse schema notes

Inspected with nflreadpy 0.1.5 on 2026-11-05 (pbp 2022–2026: 372 columns; schedules: 46 columns).
Everything below was checked against real rows, not assumed.

## Play-by-play (`load_pbp`)

| Column(s) | Finding | How gridsim uses it |
|---|---|---|
| `play_type` | `pass, run, no_play, kickoff, punt, extra_point, field_goal, qb_kneel, qb_spike`, and null for `GAME_START / END_QUARTER / END_GAME / COMMENT` rows | snap selection |
| `pass` / `rush` | `pass==1` = every dropback: attempts, **sacks** (play_type pass, sack 1) and **scrambles** (play_type run, qb_scramble 1). `rush==1` = designed runs only | play call: pass = dropback, run = designed run |
| `no_play` rows | penalties that wipe out the play. `pass`/`rush` flags tell what the play was. Rows with **neither** flag are pre-snap fouls (False Start, Delay of Game, Neutral Zone, Encroachment, …). Timeouts are also `no_play` with `penalty==0` and `timeout==1` | post-snap fouls live in the pass/run outcome pools; pre-snap fouls are a separate per-snap event |
| `two_point_attempt` | two-point tries look like normal pass/run rows but have `down` null | excluded from scrimmage pools; 2PT modeled separately |
| `timeout`, `*_timeouts_remaining` | timeout rows are sometimes logged *after* the next snap (e.g. "Timeout #1 by BUF at 01:45" logged after the 1:45 snap). The timeouts-remaining counters on the next snap are reliable | clock: "timeout after this snap" = timeout row before next snap **or** a drop in either team's remaining count |
| two-minute warning | **no row exists** for it | clock deltas spanning 2:00 in Q2/Q4 are dropped; the engine stops the clock itself |
| `end_yard_line` | entirely null in 2025–26 | not used; the next snap's `yardline_100` is the end spot instead |
| `yards_gained` vs next snap | next-snap geometry agrees with `yards_gained` on 99.7% of clean plays; disagreements are fumbles the offense recovered (geometry is right) | net yards come from the next snap whenever it is a scrimmage snap; fallback `yards_gained` (+ signed `penalty_yards` on no-plays) |
| kickoffs | `posteam` = **receiving** team; `yardline_100` = 35 normally (20 after a safety, 50/40/30 after penalties) | kickoff pool uses normal 35-yard kicks; receiving start = next snap |
| punts | `posteam` = punting team | punt pool keyed by line of scrimmage |
| touchbacks (2025+) | end-zone touchbacks start at the receiving 35 (`yardline_100` 65); landing-zone touchbacks at the 20 (80) | remapped to config `touchback_yardline` / `landing_zone_touchback_yardline` |
| interceptions | `air_yards` and `return_yards` reconstruct the spot (CAR 25 + 10 air = CAR 35 ✓) | turnover field movement from next snap, fallback air − return |
| `qtr`, `down`, `week`, timeouts | arrive as Float64 | cast to Int32 in `prepare_plays` |
| `wp` | offense win probability | garbage-time filter (Q4, wp < .05 or > .95) |
| `epa` | available on no_plays too | home-field tilt weights |
| `penalty_type` | first-down fouls by the defense: DPI, defensive holding, illegal contact, roughing, personal fouls; 5-yard pre-snap defensive fouls are *not* automatic | `auto_first` = defensive foul with `first_down_penalty` and type not in the non-automatic list |

## Schedules (`load_schedules`)

* `spread_line` is **positive when the home team is favored** (e.g. NYJ @ KC `spread_line = 10.0`, KC moneyline −600).
  gridsim's `--spread` uses the sportsbook convention from the handoff (home perspective, negative = home favored),
  so `--spread = −spread_line`. Checked on every 2026 week-9 game against the moneylines.
* `location == "Neutral"` marks neutral-site games (e.g. 2026_09_CIN_ATL) — `gridsim run` auto-detects this.
* `result` = home − away, `total` = home + away, `roof` / `surface` / `temp` / `wind` for weather work.

## Derived columns (gridsim `prepare_plays`)

`call, presnap, np_pen, yards, td_off, td_def, turnover, to_gain, auto_first, elapsed, n_to, crosses_2mw,
leaves_running, prev_running, rec_yl, kick_retained` — documented in `gridsim/data.py`.
