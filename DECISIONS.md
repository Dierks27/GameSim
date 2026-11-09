# DECISIONS

Every default and modeling choice, with the reason. Tunables live in `config.yaml`; section names below match it.

## Rules research (verified 2026-11-05)

* **Overtime, regular season (2025 rule, unchanged in 2026):** one 10-minute period; both teams are guaranteed a
  possession; the second possession ends the game the moment the second team leads; if the second team's
  possession ends and it trails, the first team wins; level after both → sudden death; a defensive score ends
  the game; ties possible when time expires; 2 timeouts each; two-minute warning; no onside kicks.
  Source: [Football Zebras, 2025 OT rules](https://www.footballzebras.com/2025/04/everything-you-need-to-know-about-the-new-overtime-rules/),
  [NBC Sports / PFT](https://www.nbcsports.com/nfl/profootballtalk/news/2025-nfl-overtime-rules-how-does-it-work-rule-changes-for-2025-season-explained).
* **Overtime, postseason:** same possession rules, 15-minute periods, play continues until a winner; 3 timeouts per
  two-period "half". Implemented as `--playoff`.
* **Kickoffs:** dynamic kickoff since 2024. 2025: end-zone touchback to the 35 (verified in data: `yardline_100` 65
  after touchbacks), landing-zone touchback to the 20. 2026 changes ([Football Zebras 2026](https://www.footballzebras.com/2026/04/nfl-owners-approve-rule-changes-for-the-2026-season/)):
  onside kick may be declared at any time, touchback after a penalty-moved kickoff goes to the 20, receiving team
  setup zone limited to 5 players (5-3-2 returns). Data shows the return rate rose (73.9% → 79.7%) and touchbacks fell
  (20.9% → 16.3%) in 2026, so **2026 is its own kickoff era** (`kickoff_rules.eras`). If an era has fewer than
  `min_era_kickoffs` (400) kickoffs in the no-lookahead window, the previous era is added.
* **Spread convention:** nflverse `spread_line` is positive = home favored (verified against moneylines).
  `--spread` uses home perspective, negative = home favored, so `--spread = -spread_line`.

## Data (`data`)

* **Window:** target season (through `--through-week`) + 1 prior season, weights 1.0 / 0.35 (spec default).
  Weighted sampling (cumulative weights), never duplication. Postseason plays of prior seasons included at their
  season's weight (they are real football against good teams).
* **FG / XP / 2PT** use 3 prior seasons with weights 0.8^k: kicking rates need more data than one season gives
  and change slowly.
* **Garbage time** (Q4, offense wp < 0.05 or > 0.95) removed from outcome pools and play-call tendencies (spec).
  Kept for clock and special-teams pools. Measured effect of also filtering play calling: pass rate +2.5% vs +1.8%
  without, points unchanged — kept the spec default, configurable via `garbage_time.apply_to_playcall`.
* **Cache:** `data/cache/pbp_<season>.parquet`, `schedules.parquet`. Completed seasons never re-download; the
  current season re-downloads when older than 12 h. On network failure the stale cache is used with a warning.
* **End spot of every play comes from the next snap** (penalty enforcement, fumble recoveries, returns are all in
  it). See SCHEMA_NOTES.md for validation.

## Situation buckets (`buckets.py`)

Spec buckets: down 1–4; distance 1–2 / 3–6 / 7–10 / 11+; zone own 1–20 / own 21–50 / opp 49–21 / RZ 20–6 / goal-to-go 5–1;
score down 9+ / down 1–8 / tied / up 1–8 / up 9+; time normal / last 2:00 of a half / Q4 last 5:00 (outside 2:00).

**Score buckets are used for play calling but not outcomes.** Once play type, down, distance and field zone are
fixed, the score adds little to the outcome distribution and would thin every outcome cell 5×.

### Back-off chain (outcome pools)

Sampling a scrimmage outcome:
1. **Side:** offense's own plays with probability `w_off` (0.5), else plays the defense allowed.
2. **Team level:** the finest level with ≥ `min_bucket_n` (40) weighted plays, in order:
   `(down 1/2/3+4, distance 1–2/3–6/7+, red zone y/n)` → `(early/late down, red zone)` → `(red zone)` → `(all plays of that type)`.
3. **Team vs league:** with probability `n/(n + k_outcome)` (k = 60) sample the team pool at that level, else the
   league pool.
4. **League level:** the finest with ≥ `league_min_n` (40) weighted plays:
   `(down, distance, zone, time)` → `(down, distance, zone)` → `(early/late, distance, zone)` → `(distance, zone)` → `(zone)`.

Team pools always keep the red-zone split, because rows sampled from midfield and applied inside the 20 would
overstate red-zone efficiency.

### TD censoring (Kaplan-Meier extension)

A TD row only says the gain was ≥ its yard line. When such a row, sampled from closer to the goal line than the
sim's ball, is applied, the extra distance is drawn from P(G ≥ y | G ≥ c) using a weighted Kaplan-Meier survival
curve of gains, estimated **per field zone** (compressed field near the goal line). Without the extension, points
were −7.5% vs actual; with a single pooled curve +5%; per-zone is used.

## Play calling (`playcall`)

* League P(pass | down, distance, zone, score, time): hierarchical additive smoothing toward the parent cell
  (m = 25). On real 2025 Q1–Q3 situations the table predicts 60.4% pass vs 60.1% actual.
* Team offset in logit space = pass rate over expected, shrunk by n/(n + 300).
* 4th down: league P(go | distance, yardline bin, score, time) + shrunk team offset (k = 60 4th downs).
  FG vs punt by yardline, evaluated at a kicker-range-adjusted yardline. Forced logic: trailing in the last 2:00
  (or the second OT possession) → FG if within 3 and in range, else go; trailing by 9+ in the last 5:00 → go unless
  a FG makes it one score; tied in the last 2:00 and in range → FG. Never attempt a FG under 30% make probability.

## Clock (`clock`)

* Snap-to-snap deltas are split into **live duration** (from snaps after which the clock stopped anyway:
  incompletions, out of bounds inside the stop windows, timeout called, change of possession) and **runoff**
  (delta − mean duration of that play kind) for running-clock snaps, by tempo.
* Tempo: hurry = trailing in Q4/OT last 5:00 (or down 9+ in the last 10:00), tied in the last 2:00, everyone in the
  last 2:00 of the first half. Milk = leading in Q4 last 5:00 (or up 9+ in the last 10:00). Measured means:
  normal 31.7 s, hurry 21.4 s, milk 33.8 s runoff.
* Out of bounds stops the clock only in the last 2:00 of the first half and last 5:00 of the second half;
  otherwise the clock restarts on the ready signal (27.9 s runoff).
* Penalties: runoff after a foul is sampled only from fouls where the previous snap left the clock running.
* **Team pace:** each offense's neutral-situation runoff (normal tempo, score within 7) relative to league,
  shrunk with k = 150 snaps (2026 range: NO 0.94 to PIT 1.03).
* **`runoff_scale` = 1.03:** per-kind elapsed time matches data, but the sim's pass rate runs ~2–3% high (more
  incompletions → more clock stops), giving ~4% too many possessions. 1.03 brings drives/game to +1.6% and
  plays/game to −0.3% on the 2025 sanity run. Tunable in Phase 3.
* Two-minute warning: a play in progress at 2:00 completes; otherwise the clock stops at 2:00.

## Kicking (`kicking`)

* FG: logistic in distance and distance² (IRLS). Kicker logit offset = Σ(made − p)/(Σ p(1−p) + k_kicker), k = 15
  information units ≈ τ = 0.26 logits, "shrunk hard"; FG and XP attempts both inform it. Current kicker = the team's
  most recent FG/XP kicker in the window.
* Missed FG: ball at the spot of the kick (LOS + 7), or the 20 if the kick was from inside the 20. Blocked share of
  misses from data (12%); 8% of blocks returned for TDs (config).
* XP: league rate (95.2%) with kicker offset. 2PT: league rate (48%) over 3 seasons.
* 2-point chart (Q4+): go when the post-TD margin is −2, −5, −10, −13, +1, +5, +12; plus a 4% random rate elsewhere.
  Second OT possession down 1 or 2: go for 2.
* Punts: league rows by 5-yard line-of-scrimmage bin; net yards translated to the sim's spot; touchback if past
  the goal line; muffs/penalty retentions and return TDs from the row; punting-team net offset shrunk (k = 60).
* Kickoffs: league rows from the kickoff era only, kicking-team start-position offset shrunk (k = 150). Onside:
  Q4 trailing with ≤ 2:30 left, or trailing by 9+ with ≤ 5:30; outcomes pooled over 2024+ (129 attempts, 6% recovered).
* Safety free kick: receiving team starts 10 yards better (config) than a normal kickoff (too rare to sample).

## Pre-snap fouls

Separate per-snap event (2.6% offense, 0.5% defense league rates), team rates shrunk with k = 400 snaps; yardage
from league pre-snap rows; half-the-distance enforced near the goal lines.

## Home field (`home_field.tilt`)

Exponential tilt of scrimmage sampling weights by EPA: home offense × exp(+0.006·EPA), away offense × exp(−0.006·EPA).
Backtest sweep (2025 wk 3–18, same games/seeds): each +0.006 adds ≈ 1.75 pts of home margin. 0.006 ≈ 1.75 pts,
in line with the 2023–26 average home margin (1.9) and market HFA (~1.5–2). 2025 alone wanted more
(bias −1.2 pts at 0.006, 0 at 0.012) but Brier/log loss got slightly worse at 0.012 and 2025's home margin was
above the long-run average, so 0.006 is kept and left to the multi-season tuner.

## Additive matchup correction (`sim.matchup_strength`, `sim.k_team_epa`) — the biggest accuracy fix

The spec's matchup blend samples the offense's plays with prob w_off, else the defense's allowed plays. That is a
*mixture*: if the offense is +a better than average and the defense allows +b more, the mixture's mean shift is
w·a + (1−w)·b, i.e. **half** the additive matchup effect a + b. The first backtest showed exactly that: predicted
margins had sd 2.4 vs the market's 6.4.

Fix (keeps the mixture, so team-specific play *shapes* stay): sampled rows are EPA-tilted (rejection sampling,
accept ∝ exp(λ·EPA), EPA clipped to ±4) with λ solved per matchup and play type so the tilted mixture's mean EPA
equals league + strength × (shrunk offense effect + shrunk defense effect). The offense pass effect comes from the
*starting QB's* dropbacks (prior: backup level for a QB with no sample, fading to league average once he has
`backup_max_dropbacks`); shrinkage n/(n + k_team_epa), k = 300. λ is measured on 1,500 league-mix situations with a
separate RNG so the game stream is untouched. Weather and rest/travel shifts enter the same target.

| 2025 wk 3–18, 240 games, 300 sims | Brier | log loss | MAE margin | MAE total | corr vs spread | sd pred margin |
|---|---|---|---|---|---|---|
| mixture only (baseline) | 0.2339 | 0.6624 | 10.85 | 10.71 | 0.77 | 2.39 |
| + additive correction | **0.2087** | **0.6084** | **9.98** | 10.65 | 0.86 | 6.16 |
| closing market | 0.2151 | 0.6177 | 10.08 | 10.13 | — | 6.41 |

(First version shrank every QB toward the backup level, which tilted all matchups negative: INT rate +19% in the
sanity check. Fixed before the numbers above.) 80% interval coverage: margin 0.83, total 0.76, points 0.80.

## QB availability (`qb`)

Team-specific dropback samples come from the starting QB's own dropbacks (from any team in the window, so traded
QBs carry their history) with weight n/(n + k_qb), k = 100; otherwise from a league pool of low-sample QBs
(< 150 weighted dropbacks). Sack, INT and scramble rates therefore move with the QB. Default starter = the main
passer of the team's latest game. Backtests use the QB who actually started (public before kickoff); other
injuries are not replayed in backtests.

## Availability (`availability`)

Out / IR / reserve → 0; Doubtful → 0 (configurable); Questionable → 0.75, × 0.67 if the last practice was DNP,
× 1.15 (cap 0.95) if full; no game status yet: DNP → 0.5, Limited → 0.95. Roster status `INA` is last week's
game-day inactive list and is *not* treated as out. Each sim rolls availability independently. Overrides
(`overrides/<season>_wk<NN>.yaml`, `--out/--in`) always win; names are fuzzy-matched and fail loudly. Data freshness
printed every run (download time, latest injury week, depth-chart snapshot; nflverse release timestamps when the
GitHub API is reachable).

## Players (`players`)

Usage shares over the team's last 6 games, computed over the games each player appeared in (a missed game does not
dilute a healthy player's role); out players' shares are redistributed proportionally; players new to the team ramp
from a depth-chart prior. Yards-per-opportunity efficiency vs league, shrunk with k = 80, is applied as a
multiplier to positive gains and **normalized to a usage-weighted mean of 1 per team**, so team totals stay what
the pools say and player stats sum to the team box score. Not yet modeled: the team-efficiency effect of losing a
non-QB player (with/without splits are too noisy at this sample size; left for the tuner later).

## Kicking decisions (diagnose-driven fixes)

* Touchback yard line is per kickoff era (`legacy 25, dk2024 30, dk2025/dk2026 35`). One global 35 was wrong for 2024
  weeks: diagnose flagged kickoff start position at 8 SE.
* FG-vs-punt table smoothed ±1 yard (±3 leaked the 35–37 attempt rate across the range cliff) and the kicker range
  shift is relative to the *average current kicker* (survivorship: current kickers average +0.8 yd). 55–62-yard
  attempt rate: 23% → 17% (actual 17%). Sanity FGA +7.5% → +2.8%.

## Weather (`weather`)

Ridge-shrunk effects fit on 2022–26 outdoor games: FG/XP logit −0.010·(wind−8)·dist/40, −0.18 per 10°F below 45,
−0.04 for precipitation; dropback EPA −0.004 per mph over 8, −0.006 per 10°F below 45, −0.037 with precipitation
(a 25°F / 22 mph / rain game: FG −0.55 logit at 45 yd, −0.10 EPA per dropback). Upcoming games use Open-Meteo at
the stadium (lat/lon table in `weather.py`) averaged over kickoff +0–2 h; backtests use recorded game weather (the
schedule has temp/wind but no precipitation text, so backtests ignore rain). Domes, closed and retractable roofs
skip. `weather.strength` (default 1) is tunable.

## Rest / travel (`rest_travel`)

Off a bye +0.6 pts, short week −0.6, visitor −0.25 per time zone crossed (max 3), applied as EPA shifts (half to
each offense). Defaults from common estimates; `rest_travel.strength` is tunable and diagnose's residual breakdown
checks short-rest games.

## Coaching changes (`regime`)

`regime_changes.yaml` entries downweight a team's plays before the change date to 0.3 for play-call tendencies
only (pass rate over expected, 4th-down aggressiveness); outcome pools (talent) are unaffected.

## Reproducibility

Backtests must be identical across runs and worker counts or tuning compares noise. Found and fixed: polars joins do
not preserve order (re-sorted after every join; no join before index-sampled lists), polars group order is
thread-dependent (pool segments laid out in sorted key order with per-segment cumulative weights), tie-broken
sorts, rounded aggregate tables. Verified: identical results across 3 runs with 2 vs 3 workers.

## Markets (READ-ONLY)

Kalshi public market data only (GET, no key, no orders, no account). Series discovered by title, events matched by
team names + date. Fee = ceil_to_cent(0.07 × C × P × (1−P)) taker / 0.0175 maker (from Kalshi's fee schedule as
reported by several sources; verify on kalshi.com before relying on it — it is in config). Edge = sim prob −
(ask you'd actually pay + fee). Scanner rules in `scanner` config; a market type alerts only after ≥ 50 graded
signals with positive CLV.

## Sanity check (2025 regular season, every game × 8–20 sims, in-sample)

See `tests/test_sanity.py`. Latest: points +2.4%, plays −0.3%, pass rate +2.4%, sack rate −1.5%, INT rate −2.2%,
comp% +0.2%, YPA +0.6%, punts −4.2%, FGA +5.5%, FG% −1.7%, 3rd down −0.5%, RZ TD% +1.8%, turnovers 0.0%,
drives +1.6%, kickoff start −0.4%, punt start +0.9%.
Known: FGA runs ~5% high (FG attempts per drive +3%); the diagnose report will track it.

## Engineering

* One `numpy.random.Generator(seed)` per run; uniforms drawn in blocks of 65,536 and consumed in order, so a seed
  reproduces a run exactly (tested).
* Pools are flat Python lists of row ids and global running cumulative weights; sampling = `bisect` within a
  segment. 2,000 sims of one game take ~4–5 s.


## Tuning run 1 (2026-11-05): not promoted

`gridsim tune --train 2023-2024 --validate 2025 --trials 24 --sims 300` (every 2nd train game, weeks 3–18), output in
`tuning/20261105-074738/`. Objective = log loss + CRPS(margin)/10 + CRPS(total)/10.

| | train 2023–24 objective | validation 2025 objective | 2025 log loss | 2025 Brier | 2025 CRPS margin | 2025 CRPS total | 2025 MAE margin | 2025 MAE total |
|---|---|---|---|---|---|---|---|---|
| current config | 2.14565 | **2.08495** | **0.6143** | **0.2107** | **7.192** | **7.515** | **10.03** | 10.54 |
| best trial (11) | 2.09960 | 2.09523 | 0.6166 | 0.2135 | 7.220 | 7.567 | 10.06 | 10.54 |
| closing market (2025) | | | 0.6177 | 0.2151 | | | 10.08 | 10.13 |

The best trial (fewer passes, slower pace, less team shrinkage, more recency, more HFA) improved train by 0.046 but
validation got *worse* — flagged "likely overfit", config.yaml unchanged. With ~240 games per season the objective
noise between configs is comparable to the gains, so short tuning runs mostly chase noise; run the full
`--trials 150` with more train seasons overnight, or `--sampling-only` for a faster, lower-dimensional search.

**Held-out results for the current config (weeks 3–18, 240 games per season, no lookahead):**

| season | sim Brier | market Brier | sim log loss | market log loss | sim MAE margin | market | sim MAE total | market | sd pred margin (mkt) |
|---|---|---|---|---|---|---|---|---|---|
| 2025 | 0.2107 | 0.2151 | 0.614 | 0.618 | 10.03 | 10.08 | 10.54 | 10.13 | 6.05 (6.41) |
| 2024 | 0.2215 | 0.1933 | 0.634 | 0.572 | 10.51 | 9.55 | 9.87 | 9.79 | 4.65 (5.73) |
| both | 0.2161 | 0.2042 | 0.624 | 0.595 | 10.27 | 9.82 | 10.21 | 9.96 | |

**Honest read: the closing market is better on average.** 2025 looks slightly better than the market, 2024 clearly
worse (the market was unusually sharp in 2024, and the sim's margins were compressed: sd 4.65 vs 5.73). The likely
cause is information the model cannot see — offseason roster/QB changes (a QB new to a team with little recent
sample is shrunk toward backup level, e.g. 2024 MIN) and early-season reliance on last year's team. Treat sim-vs-market
gaps as hypotheses to grade live (`grade`, `scorecard`, signal CLV), not as edges. Totals are the main known weakness, together with the margin *shape*
(one-score games 45% sim vs 50% actual; margin exactly 3: 10.8% vs 14.2%), which diagnose now reports as "key numbers".

## Local settings and the no-console setup

- Changes made on the Settings page, and tuned settings you choose to use, are written to `config.local.yaml` (only the
  values that differ from `config.yaml`), merged over `config.yaml` at load time. It is git-ignored and not in the
  download, so updating GridSim never wipes it. With no local file the config hash is unchanged.
- Cached game results are keyed on the config hash, the two teams' lineup overrides and the modification times of the
  downloaded data files, so a data refresh or a lineup change for one game recomputes only what it affects.
- Scheduled tasks run `pythonw.exe -m gridsim ...` (no console window); output goes to `logs/scheduled.log`. Every
  `scan` saves a price snapshot, so closing-line value builds up without a separate `snapshot-markets` task.
- Setup accepts any 64-bit (win-amd64) Python 3.11+; ARM64 and 32-bit Pythons are rejected because pyarrow/polars
  wheels for them are missing or incomplete.

## Data safety (data folder, backups, recovery)

- Irreplaceable data lives in one data folder outside the code folder, `<user folder>/GridSim Data` (env
  `GRIDSIM_DATA` overrides). The spec asked for the location to be set in config, but config.yaml lives in the code
  folder that updates replace — a fixed default outside it means a fresh install finds the data with no restore
  step. It is deliberately not on OneDrive: the ledger is rewritten every 30 minutes and live-syncing a file being
  written risks conflicts; finished backup ZIPs go to OneDrive instead (written as `.partial`, then renamed).
- Configs and lineup overrides are not committed to the repository (the user updates by ZIP, not git); they are in
  the data folder and in every backup.
- Automatic backups: after predict-week (Friday's locked-in predictions are the most irreplaceable moment, so not
  only after weekly/grade as the spec listed), grade, weekly, reconstruct, lineup edits (≤ 1/h), before applying
  tuned settings or a reset, and at most once a day from scans and from opening the GUI. `backup.keep` = 40.
- Restore and "import an old folder" are the same merge: frozen predictions and snapshots are only added (never
  replaced), CSV records are unioned by key (ledger: whole row; graded: ts+ticker+side; scorecards:
  game_id+pred_file), alert state keeps the newer entry, settings merge with the current values winning, differing
  text files are kept beside yours as `*.from-import`. So restoring can never lose newer data, and running it twice
  changes nothing. Example runs shipped with the code and the developer's early week-9 test predictions are never
  imported.
- Reconstructed predictions go to `predictions/<season>/wk<NN>/reconstructed/` with `frozen.provenance =
  reconstructed` and are graded into `scorecard_reconstructed.csv`; the live grader only looks at the week folder
  itself and at files created before kickoff, so they can never enter the live scorecard or the signal trust gate.
- Backfilled Kalshi prices come from Kalshi's public hourly candlesticks: the last candle ending at or before
  kickoff gives the closing bid/ask. They are real exchange history, so they count for closing-line value; the
  snapshot file is marked `backfilled`.
- Lost-folder detection: each data folder has a random ID (`.gridsim_data_id`) recorded in every backup. Backups
  whose ID is neither this folder's nor one it restored/imported mean the data folder was lost or replaced: the CLI
  and every GUI page warn, automatic backups pause, and pruning never deletes the newest 3 backups of any other
  folder. (A first version keyed this on "folder empty", which one scheduled scan defeated — it writes a ledger
  header — after which count-based pruning would have rotated the good backups away.) "Start fresh" marks them known.
- Code-folder migration takes each file once (`.legacy_imported.json`: size + mtime), so resets and deletions stick
  while files an older GridSim copy writes there later are still picked up. Alert state is taken only if none exists.
- Scorecard: one row per game; when a game was graded in two installs the later pre-kickoff prediction counts
  (merge and readers both apply it; grade() skips games already graded).
- Resources: tuning ran one ~2.6 GB worker per CPU thread, which on the user's PC (30 workers) pushed memory to 99%
  and into swapping. Workers are now capped so the whole PC stays at or below `resources.max_ram_fraction` (0.8) of
  RAM at the start of the job and `resources.free_cores` (2) cores stay free; tuning runs at below-normal priority.
  Measured worker memory is ~1.9 / 3.1 / 3.5 GB holding 1 / 3 / 4 seasons, so the tuning pool now pins each worker to
  one season (when workers >= seasons), budgeting 1.5 + 0.55 GB per season held — about twice as many workers fit.
  These sections are excluded from the config hash.

