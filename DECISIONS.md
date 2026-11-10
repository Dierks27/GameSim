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
   `(down 1/2/3+4, distance 1–2/3–6/7+, field zone)` → `(early/late down, field zone)` → `(field zone)`, where the team
   field zone is outside the 20 / red zone 20–6 / goal-to-go 5–1. The last level is used however thin it is (its
   team weight n/(n + k) is then small); there is no all-field level.
3. **Team vs league:** with probability `n/(n + k_outcome)` (k = 60) sample the team pool at that level, else the
   league pool.
4. **League level:** the finest with ≥ `league_min_n` (40) weighted plays:
   `(down, distance, zone, time)` → `(down, distance, zone)` → `(early/late, distance, zone)` → `(distance, zone)` → `(zone)`.

Team pools always keep the red-zone **and goal-to-go** split, because rows sampled from farther out and applied
closer to the goal line overstate red-zone efficiency: a 4-yard run from the 15 applied at the 3 is a TD.
(Until 2026-11 the team chain was `(…, red zone y/n)` → … → `(all plays)`. The all-field fallback was hit whenever a
team had < 40 weighted red-zone plays, i.e. most of every early season, and red zone 20–6 and goal-to-go shared one
cell. Replaying every real 2024–26 snap through the model built before its week: goal-to-go designed-run TD rate
sim 52–54% vs actual 39–41%; red zone 20–6 8.1–8.3% vs 6.2–6.5%. With the 3-way field zone and no all-field level:
goal-to-go 44–47%, total backtest points −0.3 to −0.5 per team, 2024–25–26 total bias +0.29/+0.96/+1.05 →
−0.52/+0.34/+0.05, log loss −0.008/−0.003/−0.016. The remaining goal-line run excess comes from within-zone
mixing (rows from the 15 applied at the 8); not pursued because aggregate red-zone TD% is on target pooled over
2023–26 (−0.3%). It is not on target every season: −5% in 2024 (flagged), and field-goal attempts run +3.4% pooled,
+5% in 2024–25, since a goal-line TD that no longer happens is often a field goal instead. Both are on the open list.)

Diagnose splits `ypc` (all rushes incl. QB scrambles, the official definition, same in sim and data) into
`ypc_designed` (run pools) and `scramble_rate` (scrambles per dropback, QB pools): the aggregate mixes two sub-models.

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
  tuned settings or a reset, and at most once a day from scans and from opening the GUI. `backup.keep` = 30 (late-season backups are ~40 MB each because of the price snapshots, so 30 is ~1.2 GB on OneDrive).
- Restore and "import an old folder" are the same merge: frozen predictions and snapshots are only added (never
  replaced), CSV records are unioned by key (ledger: whole row; graded: ts+ticker+side; scorecards: game_id, the later
  pre-kickoff prediction winning), alert state is only seeded, your current settings are never changed (the imported
  ones are kept beside them as `config.local.yaml.from-import`; with none of your own, they are used unless you
  pressed Reset to defaults after they were saved), differing text files are kept beside yours as `*.from-import`. So restoring can never lose newer data, and running it twice
  changes nothing. Example runs shipped with the code and the developer's early week-9 test predictions are never
  imported.
- Reconstructed predictions go to `predictions/<season>/wk<NN>/reconstructed/` with `frozen.provenance =
  reconstructed` and are graded into `scorecard_reconstructed.csv`; the live grader only looks at the week folder
  itself and at files created before kickoff, so they can never enter the live scorecard or the signal trust gate.
- Backfilled Kalshi prices come from Kalshi's public hourly candlesticks: the last candle ending at or before
  kickoff gives the closing bid/ask. They are real exchange history, so they count for closing-line value; the
  snapshot file is marked `backfilled`.
- Lost-data detection (third design, after two adversarial review rounds): every backup's manifest carries an
  inventory of what it holds — the names of frozen predictions, price snapshots and run files (things GridSim never
  deletes), and for the append-only record files the row count + newest time stamp (ledger, graded) or the graded
  game IDs (scorecards). "Data looks lost" = some backup holds an item the folder no longer has, whatever has been
  added since. Counting totals (the first two designs) was defeated by routine scans/lock-ins that re-grew the totals
  after a partial loss. Backups considered: this computer's, this folder's own / known folders' (whatever the
  computer name — PCs get renamed), and other computers' backups made before this folder existed (a new PC).
  Backups made before inventories existed fall back to comparing totals. The inventory is built from the bytes
  actually zipped (no race with a scan writing meanwhile). Pruning deletes only this folder's own oldest backups and
  never one holding something missing; "start fresh" sets aside the existing backups by name (time zones and clock
  changes can't break it) and leaves the program folder's old data where it is. Ledger / graded / scorecard writes
  and merges share a small lock file so a restore never drops rows a scan is appending.
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

## Lock-in days and daily price checks

- Every pre-kickoff lock-in is graded into `predictions/scorecard_by_lockin.csv` (tagged with its US-Eastern weekday
  and hours before kickoff); the live record (`scorecard.csv`) still uses only the last pre-kickoff lock-in per game.
  Each day is shown next to the final lock-in on the same games, so a day isn't flattered by its game mix.
- Signals are also summarised by the weekday they were first seen (each signal once per day).
- The scheduled Kalshi price check runs every day (was Mon + Thu–Sun), so early-week prices are recorded for
  closing-line value (`scanner.schedule_note` says so since model revision 2, when the hash changed anyway).

## Trust gate counting and the position watch

- The trust gate counted graded ledger ROWS, but the ledger logs every signal again at every 30-minute price check
  and a game has ~25 spread lines: one contract could reach "50 graded signals" in two days. It now counts graded
  GAMES per market type (first sighting of each contract, averaged within a game). `trust_min_graded` stays 50.
- Position watch: user-entered positions only (no exchange account, no keys, no orders — the read-only rule stands;
  automatic trading was asked about and deliberately not built while the model trails the market). Alerts compare
  with the value at the last alert (not the previous check), so slow drifts are caught and nothing repeats. An
  injury alert already carries the re-simulated numbers, so no separate "GridSim's number" alert is sent with it.
  Positions stop being watched at kickoff (the pre-game sim says nothing about a game in play) and are settled once
  the schedule has the final score. Thresholds live in the `watch` config section (excluded from the config hash).
- The sim cache key now uses the CONTENT of the data files instead of their modification time, so the watch can
  re-download the injury report every 45 minutes without forcing every game to re-simulate when nothing changed.
- Kalshi events are fetched once per price check instead of once per game (3 requests instead of ~42).


## "Favorites under-rated" is mostly a measurement artifact (2026-11-10)

The check-up flagged margin error +6.69 in 'away favorite' games (2026 wk 6–10, n=25). Of that, the closing line
missed by +4.52 on the same games; the sim's own part (sim − market) is +2.17 ± 0.65. Grouping games by the
*market's* favorite conditions on a better-informed forecast, so any calibrated forecast that knows less than the
market (less spread out: sd 4.7–6.1 vs 5.7–6.4) shows a "favorite" bias there in every season (sim − market toward
the market favorite −1.3 to −1.9 pts, 2022–2025). The calibration test is the slope of the result, or far less noisy
of the closing line, on the sim's predicted margin: closing line vs sim 0.90 / 0.90 / 0.97 / 0.90 / 0.87
(2022 / 23 / 24 / 25 / 26 wk 3–10), result vs sim 0.58 / 0.87 / 1.12 / 1.12 / 1.10. Those closing-line slopes were
flattened by the per-game tilt noise fixed in the next section; without it they are 0.98–1.08 (calibrated to slightly
compressed). Diagnose now reports the closing line's error and the sim-vs-market part next to every margin and
total group, adds groups picked by the sim's own forecast, and replaces "sd(sim) < 0.75 sd(market)" with the slope test.
Margin rows of the market-picked groups (home/away favorite, spread buckets) stay in the table but are kept out of the
"top 3": their paired sim-vs-market SE is small, so over a full season they would rank first every time although
the gap is expected of a calibrated, less-informed forecast.

Decomposition (offline, 2020–2026): the sim's predicted margin = 0.87 × EPA strength index (Σ shrunk off+def
effects × plays) + a 2.1-pt-sd remainder. The market weights the index part 1.08 and the remainder ~0, so the strength
signal is ~8% under-scaled while ~2 pts of non-strength variation (not w_off, k_outcome, kicking/pace/play-call
offsets, rest/travel or weather: all tested) pads the spread. `matchup_strength` 1.1 was A/B-tested (2022–2025,
300 sims, seed 2024): log loss −0.006 / −0.001 / +0.000 / −0.003, CRPS margin +0.02 / −0.04 / −0.04 / −0.03, all
within 1–1.3 paired SE, favorite bias −0.3 to −0.4 pts, but the closing-line slope falls to 0.83–0.87 (more over-dispersed).
Not adopted: the default stays 1.0 and the tuner keeps owning it. w_off and k_outcome change only play shapes,
not the spread of margins (the additive correction re-targets the mean), so they are not compression levers.

## Matchup tilt measured with 16 draws per reference situation (2026-11-10)

The EPA tilt (`matchup_lambda`) is measured once per game, offense and play type from ONE draw per reference
situation (1,500), and the result is shared by every sim of the game. Its sampling error is therefore a per-game
error that more sims never average out: about 1.8 pts sd on the predicted margin and ~1 pt on the total. Seed-to-seed
sd of a game's predicted margin at 300 sims: 1.95 pts → 0.88 with 16 draws (pure sim noise is 0.76), so 16 draws cut
the per-game tilt error from 1.8 to about 0.45 pts (1.3 points of win chance); they do not remove it. This is the
"remainder" that carried no weight with the market or results and made the sim look less compressed than it is.
`sim.tilt_ref_draws: 16` (seed 7777, 300 sims, weeks 3–18, 2023–2025 + 2026 wk 3–10, n=824): squared distance to the
closing margin −3.6 ± 0.5 pt², closing total −1.4 ± 0.4; CRPS margin −0.077 ± 0.043, CRPS total −0.072 ± 0.032, log loss
−0.001 ± 0.005, total 80% coverage +1.5 pts. Closing line vs sim margin slope moves from 0.88–0.91 to 0.96–1.08.
Runtime +10–25% at 300 sims. It is a numerical-accuracy setting, not tuned.

16 is the minimum. `simulate()` uses `max(16, n_sims // 32)` draws (62 at 2,000 sims, 250 at 8,000), so the
leftover tilt error shrinks together with the simulation noise instead of becoming the floor: measured at 2,000
sims, the real seed-to-seed spread of a win chance was 1.6–1.7× the printed ± with 16 draws and 1.1× with 64
(mean win chance unchanged at 16 / 64 / 256 draws; +12% runtime). Without this the scanner's re-sim with 4× the
sims shrank the printed ± while the real error stayed put. Backtests and tuning (300 sims) keep 16.

## Total interval coverage (check-up 2026-11-10: "80% total intervals cover 72%")

Not a variance problem. Backtests (300 sims, seed 2024, weeks 3-18; 2026 weeks 1-10): 80% total coverage 0.775 / 0.808
/ 0.783 / 0.779 (2023 / 24 / 25 / 26), each within 1 SE of 0.80 (SE 0.026 at 240 games, 0.053 at the check-up's ~50).
Mean sim sd of the total 12.4-13.0 equals the closing market's residual sd (12.7-13.3); team-points coverage is 0.80 in
every season. The misses are lopsided toward actual totals *below* the 10th percentile (2025 12.5% vs 9.2% above,
2026 14% vs 8%) because the sim total runs high (+1.0 / +0.3 / +1.0 / +1.7; ypc +6% / +1% / +4% / +7%). With the sim's
own spread re-centred on the market total, coverage is 0.80-0.82 in every full season. A per-game common EPA shock
(`sim.day_shock_game`, prototyped) brought coverage to 0.86 / 0.83 in 2024 / 2025 but did not improve CRPS total
(-0.01 / +0.09) and added +0.7 pts of total bias; ideal post-hoc widening (normal noise added to the samples) changes
CRPS total by at most ±0.03. Diagnose now flags coverage only beyond 2 binomial SE and says which tail the misses fall in.


## Model revision 2 (2026-11-10): what shipped, and how it was checked

Four changes to what the sim predicts, shipped together as `MODEL_REV = 2` (part of the config hash, so the Scorecard
does not pool them with earlier predictions and older cached sims are not reused):
1. **Goal-line / red-zone team pools** (field zone 0 / 1 / 2 instead of one red-zone cell with an all-field fallback).
2. **Matchup tilt with a quarter of the per-game noise at backtest sim counts, less at higher ones**
   (`sim.tilt_ref_draws: 16` as the minimum, `max(16, n_sims // 32)` draws: more than 16 from 544 sims).
3. **Kickoff rules for seasons before the first listed era** use that era (2022 and earlier were getting the 2026
   touchback spot: +3.9 points of total bias in 2022 backtests, which is a tuning season).
4. **Same seed, same sim**: the player model iterated an unordered `group_by`, so target/carry shares came out in a
   different order each run and the same request with the same seed moved the home win chance by up to 12 points
   (on top of 2.). Shares are now sorted. HOU @ CLE, 2000 sims, seed 42: two processes × two runs give the same value four times
   (0.417 with 16 tilt draws; 0.398 with the 62 draws a 2,000-sim run uses now, weather off).

Combined A/B against the previous code (same games, seed 777, 300 sims, weeks 3–18; 2026 = weeks 3–10):

| | 2022 | 2023 | 2024 | 2025 | 2026 | pooled 2023–26 (paired) |
|---|---|---|---|---|---|---|
| RMSE sim − closing margin | 3.80 → 3.21 | 3.34 → 2.80 | 3.58 → 3.12 | 3.60 → 2.94 | 3.56 → 3.04 | sq. distance −3.57 ± 0.48 |
| RMSE sim − closing total | 4.86 → 2.87 | 3.25 → 3.13 | 3.48 → 2.90 | 3.69 → 3.06 | 4.04 → 3.30 | sq. distance −3.24 ± 0.38 |
| total bias (sim − actual) | +3.88 → +0.96 | +0.97 → +0.65 | +0.22 → −0.47 | +0.94 → +0.36 | +1.18 → −0.12 | |
| log loss | 0.6386 → 0.6386 | 0.6475 → 0.6461 | 0.6344 → 0.6232 | 0.6236 → 0.6204 | 0.6381 → 0.6237 | −0.0064 ± 0.0048 |
| CRPS margin | 6.562 → 6.606 | 7.616 → 7.690 | 7.540 → 7.419 | 7.360 → 7.236 | 6.650 → 6.488 | −0.070 ± 0.044 |
| CRPS total | 7.954 → 7.488 | 7.726 → 7.622 | 7.173 → 7.219 | 7.450 → 7.513 | 7.514 → 7.463 | −0.005 ± 0.036 |
| closing line ~ sim margin slope | 0.91 → 1.00 | 0.94 → 1.03 | 0.92 → 1.06 | 0.92 → 1.00 | 0.82 → 0.97 | |

The distance to the closing line is the low-noise measure and it improves by 7 SE; accuracy against results is
unchanged within noise (two 300-sim runs of one season differ by about 0.01 in log loss on luck). An independent
replication with another seed (31337, 2024 + 2025) reproduced the closing-line distance (margin −2.9 ± 0.65 pt²,
total −3.9 ± 0.49), the total bias and the slopes, and gave log loss +0.003 ± 0.006 where seed 777 gave −0.007 ± 0.006. The goal-line fix alone
(same seed) gave log loss −0.012 ± 0.005; adding the tilt fix to it changed log loss by +0.006 ± 0.005 and the
squared distance to the closing margin by −3.1 ± 0.5. Removing noise that is independent of the result cannot hurt
in expectation, so the +0.006 is read as luck, but it is not evidence of a gain on results either. What it does
change for certain: every win chance used to carry about 5 points of error that no number of sims removed, and
most "edges" against Kalshi prices of that size were this noise.
After the fix the result ~ sim margin slope is above 1 in 2024–2026 (1.38 / 1.19 / 1.28): predicted margins are, if
anything, too timid. That is `matchup_strength` / `k_team_epa` territory and stays with the tuner (1.1 by hand gave
mixed held-out results).

Still open: designed-run yards per carry run +3–6% high in 2023 / 2025 / 2026 (now reported separately as
`ypc_designed`, with `scramble_rate`); thin starter-QB red-zone cells shrink toward the backup-QB pool, not the league
pool; total 80% coverage is fine (0.79–0.83), its misses come from the centre, not the width; red-zone TD% −5% in
2024 and field-goal attempts +3–5% (see the back-off chain note).

## Tuning: a gain must exceed its margin of error; old results are not offered

`recommended` was `validation gain > 0`. At 300 sims the validation objective moves by about 0.01 between two runs of
the same config, more than a typical tuning gain, so a coin-flip could promote a config. The gain is now compared
with its paired per-game standard error on the held-out season (`paired_gain_se`) and must exceed it. A tuning result
whose `summary.json` carries a different `model_rev` (or none) is "stale": the page explains it and `apply_tuned`
refuses it — its settings compensate for the 2022 kickoff bias, the tilt noise and the goal-line bias.

The same reasoning applies to an old-model tuning that was already promoted: its values sit in config.local.yaml and
an update does not touch that file. `stale_tuned_in_use` finds them by value (every `best_params` value of an
old-`model_rev` run equals the value in use) and the Weekly page says so and offers one button that puts exactly
those keys back to the shipped values (`drop_stale_tuned`; other settings stay, backup first).

Promoting writes only the tuned parameters on top of the settings in use now (it used to write the whole config as
it was when the tune started, undoing every setting changed in the many hours since). "Applied" means the tuned
values are the ones in use, not that the whole config hash matches, so a later unrelated settings change does not
bring the offer back. Only the newest run can be offered or promoted: an older run's `config.tuned.yaml` is never
promoted on the strength of a newer, not-recommended run. A tune started before an update and still running is
flagged on the page (`model_rev` in `gui_tune.pid`).

## Position watch: review round (2026-11-10)

An independent review (logic, end-to-end in a browser, Windows/ops; every finding re-verified) found and this fixes:
- sim alerts on noise (see 4. above); now also require the move to exceed `sim_move` + the two sims' combined 95%
  margin of error, and a scan with its own sim count is not handed to the watch. The default sim count spelled out
  is the same cache key as none.
- nflreadpy keeps downloads in memory for 24 h per process: in an app left open, a forced refresh wrote the morning's
  injury report over a newer file and raised a reversed alert. Its cache is switched off (`data.nfl_client`); data
  files are written to `.partial` and swapped in, and left untouched when the content is identical.
- a tied moneyline settles at 50¢ (Kalshi's rule), for positions and for graded signals.
- practice-squad / cut / reserve bookkeeping was pushed as "lineup news" (85% of team-weeks have some): only QB
  changes, injury-report changes and players the sim had in its plan are reported, in words instead of roster codes.
- kickoff time comes from the current schedule (week 18 is all placeholders until flexed), not the time stored with
  the position.
- an app-initiated scan no longer runs the watch (its alert was logged but never pushed, and the reference moved).
- the 45-minute injury-refresh stamp was overwritten by the watch's own state write (three downloads every run); it
  has its own file. An alert is marked reported only after it was logged or pushed.
- "Add a position": the price and contract boxes kept values from the previous contract / game (shared widget keys).
- toast text goes through environment variables (PowerShell expanded `$2` and did not decode `\uXXXX`); ntfy is
  published as JSON so titles keep "¢" and "—".
- Kalshi unreachable: reported on the page and in the log, last numbers kept. The 10-minute task returns before the
  data check when no position is open; the lost-data check streams the ledger instead of loading it (0.8 GB at 140 MB).
- a removed position stays removed after a restore (`removed` ids in positions.json).


## Second review round (2026-11-10): what it found in the whole bundle

Model code, upgrade path, end-to-end in a browser and an independent replication (every finding re-verified):
- **Grading a week of signals took hours.** Every ledger row re-read every saved price snapshot; with a price check
  every 30 minutes that is ~25,000 rows × hundreds of 300 KB files (measured 0.55 s per row at 224 files). The
  snapshots are now read once per grading run into `{(game, ticker): [(time, mid)]}` and only the files of the weeks
  being graded are opened: 160 rows × 224 files 88 s → under 1 s, same closing prices.
- **The price check re-simulated borderline games at 4× the sims on every run** (a CPU-minute and 1.2 GB every
  30 minutes). The bigger sim is cached like any other and reused while the data is unchanged and it is younger than
  `scanner.max_sim_age_hours`. The cache key also treats the default seed spelled out as the default, so the app
  finds the sims the scheduled check made.
- **Position watch.** A number made with another model or other settings (after an update, a promoted tuning or a
  settings change) is not "the latest data": the alert says the settings or model changed, and the new number
  becomes the reference either way (`ref_hash`). A player on a reserve list who shows up on the injury report (the
  return window; about five a week league-wide) is "now on the injury report (was off the active roster at the last
  check)", not "back on the active roster", and roster statuses are stated as statuses ("is on a reserve list"), not
  as moves. A "could not be read" report from a full check survives the 10-minute task for games that task skips and
  is shown only for games still held. "Check now" says so when a scheduled check is running or when the alerts could
  not be saved (alerts.csv open in Excel) instead of "nothing new". Alert text is shown as plain text (two "$" in one
  alert were rendered as a formula). When Kalshi answers but nobody bids for your side, the selling price is blank,
  not the last one seen. The state files are written with the same short retry as the data files. A lineup snapshot
  saved by the first watch version (no roster flags) is compared for the QB only.
- **Check-up.** A residual group is listed under "most likely wrong" only when the sim's own part (after the closing
  line's miss on the same games) is beyond 2 SE. Components are scored on the rows that have them, so old and new
  backtests can be mixed.
- **Scorecard.** Config versions are listed in the order they were first used.
- **Update steps.** Scheduled jobs are not changed by an update; GETTING_STARTED now says to run Schedule GridSim
  again after one.
Not changed: the trust table still counts graded signals of earlier model versions (few so far; they age out of
relevance as new ones are graded).


## Third review round (2026-11-10): check of the second round's fixes

Four lenses (watch, tuning, grading/scan, tilt), each finding re-verified. Confirmed: 300-sim results are
bit-identical to before the tilt scaling (16 draws up to 543 sims), the run-to-run spread at 2,000 sims is 1.15× the
printed ± (was 1.57×), grading old vs new gives byte-identical graded files, the re-sim reuse and the watch fixes do
what the section above says. Fixed from it:
- **Tuning by value vs the Settings page.** Four tuned settings ship as whole numbers (`k_outcome`, `k_team_epa`,
  `k_team_4th`, `k_kicker`); the Settings form showed a tuned 63.67 as 63 and wrote it back as a "change" on every
  save, so the by-value checks stopped recognising a tuning the first time anything was saved there. The form now
  shows such a value as it is and leaves it alone, and `_in_use` accepts the whole-number form already written to
  existing settings files. A tuned key the shipped config no longer has is handled (reset = removed; promote refuses).
- `gridsim tune --train … --apply` promotes on top of the settings as they are when the run ends.
- A finished tune whose process number Windows has handed to another program is no longer shown as running (the log's
  last line and the program name are checked; the pid file is removed once the run is definitely over — a process
  check that gives no answer changes nothing).
- **Watch wording.** Your own lineup change on the Game page (out, force in, starting QB) is reported as that, at
  normal priority, not as injury news. Lineup news in the same check as a settings/model change says that the difference is not only the news. The
  sim alert after such a change says "settings or model changed … the difference may come from that" (any setting
  outside backups / PC resources / the watch gives a new code, also ones the sim does not read).
- **Check-up.** The 2-SE own-part rule now also covers total-error groups (was margin groups only).
- **Price check.** The questionable-QB variants (QB out / QB in) are cached like the bigger sim.
- Scorecard caption and two stale numbers in this file corrected.
Not changed: opening `/positions` directly in a browser tab before the Home page has loaded once after a restart
shows an unstyled page until Home is opened (Streamlit serves `app/pages` on its own; moving the folder would leave
the old one behind on an update-in-place install). A settings change the sim does not read still resets the watch's
reference number.
