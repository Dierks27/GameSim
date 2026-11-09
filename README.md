# gridsim — NFL single-game Monte Carlo simulator

Plays one NFL matchup out snap by snap thousands of times by **resampling real historical plays** from nflverse
play-by-play, then reports win %, score distributions, spread/total cover rates, and team stat ranges.
Accuracy over speed, every tunable in `config.yaml`, every modeling choice in `DECISIONS.md`.

## Install (Windows 11, PowerShell)

Requires Python 3.11+ ([python.org](https://www.python.org/downloads/windows/), tick "Add python.exe to PATH").

```powershell
cd C:\path\to\GameSim
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

If PowerShell blocks the activation script, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

## Run

```powershell
gridsim refresh-data                      # first run: downloads play-by-play (2019+), schedules, rosters, injuries
gridsim run --home MIN --away BUF --sims 2000 --seed 42
gridsim run --home MIN --away GB --spread -2.5 --total 44.5 --json out\MIN_GB.json --report out\MIN_GB.html
gridsim run --home BAL --away JAX --market-lines          # use the nflverse schedule's spread/total
gridsim run --home KC --away BUF --playoff --neutral       # Super Bowl style
gridsim run --home DET --away GB --season 2025 --through-week 9   # honest historical sim (data through week 9 only)
gridsim sanity --season 2025 --sims 20                     # league-average sanity table, sim vs actual
```

`python -m gridsim ...` works the same as `gridsim ...`.

* `--spread` is from the **home** team's perspective, negative = home favored (sportsbook convention;
  nflverse `spread_line` uses the opposite sign).
* Every percentage is printed with its 95% Monte Carlo margin of error; anything within the error of 50% is
  flagged "too close to call at this sim count".
* Every run pulls rosters / depth charts / injury reports, prints who it assumed in, out and questionable (with the
  play % used) and how fresh each source is, adds the stadium forecast (Open-Meteo, outdoor games only), rest/travel
  edges, and — for upcoming games — the Kalshi prices next to the sim (read-only).

### Players, props, combos, what-if, live games

```powershell
gridsim run --home MIN --away BUF --prop "J.Jefferson:rec_yds:64.5" --prop "Josh Allen:pass_yds:229.5"
gridsim run --home MIN --away BUF --out "J.Jefferson" --in "BUF:Joshua Palmer"      # injury overrides (CLI wins)
gridsim run --home MIN --away BUF --combo "BUF:win; Josh Allen:pass_yds>229.5; total>45.5"
gridsim run --home MIN --away BUF --state "Q4 1:55 MIN 20 BUF 24 MIN ball 1st&10 at MIN 25 timeouts 2/1"
gridsim impact --home KC --away NYJ --player "Patrick Mahomes"       # with vs without, same seed
gridsim impact --home BAL --away JAX --player "Lamar Jackson" --qb "Tyler Huntley"   # QB swap
```

Weekly overrides live in `overrides\<season>_wk<NN>.yaml` in the data folder (`%USERPROFILE%\GridSim Data`; the GUI's lineup toggles write the same file):

```yaml
MIN: {out: ["J.Jefferson"], qb: "S.Darnold"}
BUF: {in: ["Joshua Palmer"]}
```

Names are fuzzy-matched to that team's roster and fail loudly if they match nobody (or more than one player).
Coaching / play-caller changes go in `regime_changes.yaml`.

### Backtest, diagnose, tune

```powershell
gridsim backtest --season 2025 --weeks 3-18 --sims 1000     # walk-forward, no lookahead; saved to backtests\
gridsim diagnose --seasons 2024-2025                         # where is it wrong? HTML + JSON in diagnose\
gridsim tune --train 2022-2024 --validate 2025 --trials 150  # Optuna; writes tuning\<ts>\ and config.tuned.yaml
gridsim tune --apply                                         # promote config.tuned.yaml (backs up config.yaml)
```

### Live grading (the honest scoreboard)

```powershell
gridsim predict-week --season 2026 --week 10   # frozen, timestamped predictions in predictions\ (never edited)
gridsim grade --season 2026 --week 10          # score the pre-kickoff predictions + Kalshi signals once final
gridsim scorecard                              # cumulative live performance by config version
gridsim weekly [--tune]                        # refresh -> grade -> diagnose -> (tune) -> predict next week
```

### Markets, scanner, alerts (READ-ONLY: gridsim never places orders)

```powershell
gridsim scan [--notify]          # open Kalshi NFL markets vs the latest sims; strong / moderate / watchlist
gridsim signals --season 2026    # signal ledger record: CLV, hit rate, flat-unit P&L by market type
gridsim snapshot-markets         # save Kalshi prices (run near kickoff for closing-line value)
gridsim slate --week 10          # every game of the week in parallel, one combined report
gridsim season                   # playoff / division / seed odds from the remaining schedule
```

Push alerts: create a `.env` file in the data folder (`%USERPROFILE%\GridSim Data`, never committed) with
`NTFY_TOPIC=your-private-topic-name` and install the free ntfy app on your phone, subscribed to that topic.
Optional: `DISCORD_WEBHOOK_URL=...`. A market type (moneyline, spread, total) only sends alerts after it has a
positive live record (≥ 50 graded signals with positive closing-line value); until then it shows as "watchlist".

## Scheduling on Windows (Task Scheduler)

Easiest: double-click **`Schedule GridSim.bat`** and press **A** (press **R** to remove). It creates three tasks
that run windowless with `.venv\Scripts\pythonw.exe` while you are signed in, catch up a run missed while the PC
was off or asleep, and append their output to `logs\scheduled.log` in the data folder:

| Task | When | Arguments | What it does |
|---|---|---|---|
| GridSim Weekly | Tuesday 9:00 | `-m gridsim weekly` | refresh data, grade last week, diagnose, predict the next week |
| GridSim Predict | Friday 19:00 (after final injury reports) | `-m gridsim predict-week` | fresh frozen predictions with Friday statuses |
| GridSim Scan | Mon + Thu–Sun every 30 min, 8:00–24:00 | `-m gridsim scan --notify` | re-price Kalshi markets, save price snapshots (closing-line value), push alerts |

Optional extras you can add by hand (Task Scheduler → *Create Basic Task* → *Start a program*, program
`C:\path\to\GameSim\.venv\Scripts\gridsim.exe`, *Start in* the GridSim folder):

| When | Arguments | What it does |
|---|---|---|
| ~60 min before each kickoff window (Thu 19:15, Sun 12:00 / 15:05 / 19:20, Mon 19:15) | `snapshot-markets` | extra closing-price snapshots |
| Every few weeks, overnight | `weekly --tune` | adds a tuning run (hours) |

`scan` re-sims only when inputs changed (the cache key includes the config, the two teams' lineup overrides and the
version of the downloaded data) or the
sim is older than `scanner.max_sim_age_hours`; otherwise it re-prices against the cached sim.

## Your data, backups and recovery

Irreplaceable data (frozen `predictions/`, `signals/` ledger + graded + Kalshi price snapshots, `overrides/`,
`tuning/`, `backtests/`, `diagnose/`, `logs/`, `config.local.yaml`, `config.tuned.yaml`, your `regime_changes.yaml`,
`.env`) lives in the **data folder**, `%USERPROFILE%\GridSim Data` (set `GRIDSIM_DATA` to move it). Relative paths
for those names resolve there (`Config.resolve_path`); re-downloadable caches stay in the code folder. Older versions
kept it in the code folder: every start merges anything found there into the data folder.

```powershell
gridsim where                       # data folder, backup folder, what they hold, how many tuning workers fit
gridsim backup                      # zip the data folder now (also automatic: after predict-week, grade, weekly, daily)
gridsim restore --latest            # or: gridsim restore <zip>   (merges: nothing current is lost)
gridsim import "C:\old\GameSim-claude-friendly-turing-wk7xhb"   # bring in an old install (merges, never overwrites)
gridsim backfill-prices --season 2026 --week 9   # missing Kalshi closing prices from Kalshi's history (marked backfilled)
gridsim reconstruct --season 2026 --week 9       # re-sim a lost week with data before it (marked reconstructed;
                                                 # never counted in the scorecard or the signal trust gate)
```

Backups go to `backup.dir` (default `%OneDrive%\GridSim Backups`, else `%USERPROFILE%\GridSim Backups`), newest
`backup.keep` kept. If backups exist from a data folder this one does not know (the folder was lost or replaced), every CLI command and every GUI page says so and automatic backups pause until you restore or choose to start fresh.
Long jobs (tune / backtest / slate) start only as many workers as fit `resources.max_ram_fraction` (whole PC ≤ 80% of
RAM; tuning workers hold one season each, ~2 GB) and `resources.free_cores`, at low priority. `backup` and `resources` are not part of the config
hash.

## Tests

```powershell
pytest -m "not slow"     # fast unit + engine tests
pytest -s                # everything, including the full-season sanity table (~15 s)
```

## How it works (short)

1. `data.py` downloads/caches nflverse data and derives one row per snap with the *real* end spot (from the next
   snap), turnover returns, penalty enforcement, clock usage, and special-teams results.
2. `pools.py` builds weighted sampling pools per play type x situation bucket for the league, every team (offense and
   defense) and every QB, with an empirical-Bayes team/league blend and a back-off chain for thin buckets.
3. `engine.py` runs the game snap by snap: play call (`playcall.py`), pre-snap fouls, one sampled *real* play applied
   to the current down/distance/field position, clock (`clock.py`), kicking (`kicking.py`), overtime (`overtime.py`).
   An EPA tilt makes the offense/defense mixture additive (see DECISIONS.md) and carries home field, weather,
   rest/travel and the starting QB.
4. `availability.py` decides who plays (injury report, depth chart, overrides), `players.py` splits every play among
   current players, `report.py` / `html_report.py` / `app/` present the result, `backtest.py` / `diagnose.py` /
   `tune.py` / `live.py` keep score.

How good is it? Out of sample (no lookahead) it is roughly market-level in 2025 and clearly behind the closing
market in 2024 — see DECISIONS.md. Use the live scorecard and closing-line value to decide how much to trust it.

See `DECISIONS.md` for every modeling choice and the backtest numbers, and `SCHEMA_NOTES.md` for the nflverse columns
used.

## GUI

A browser GUI (Streamlit) sits on the same service layer as the CLI (`gridsim/api.py`), so the same matchup,
sims and seed give identical numbers in the GUI and in `gridsim run`.

* **Install**: the install steps above already include Streamlit and Plotly (`pip install -e ".[dev]"`).
* **Start**: double-click **`Start GridSim.bat`** in the repo folder. It activates `.venv` if present, starts the
  GUI on port 8501 and opens your browser at http://localhost:8501. Close the console window to stop it.
  (Manual equivalent: `python -m streamlit run app\streamlit_app.py`.)
* **Desktop shortcut**: right-click `Start GridSim.bat` → *Show more options* → *Send to* → *Desktop (create
  shortcut)*. Optionally rename it and pick an icon under *Properties* → *Change Icon*.
* **Phone / tablet**: on the same home network, open `http://<PC-LAN-IP>:8501` (find the IP with `ipconfig`,
  "IPv4 Address", e.g. `http://192.168.1.23:8501`). The first start shows a **Windows Defender Firewall** prompt for
  Python: allow it on **Private networks** (not Public), otherwise other devices cannot connect. There is no login,
  so only do this on a network you trust.

Pages:

* **This Week**: the week's slate as cards: kickoff, market spread / total (home perspective) and, once simulated
  at the sidebar's sims/seed, sim win % ± margin of error, fair spread and total, and the sim-vs-market gap.
  "Sim this game" per card or "Sim all" (cached results are reused); sort by kickoff or biggest spread gap.
* **Game**: one matchup in depth: win %, fair lines, market comparison, margin/total histograms, a cover
  calculator for any spread or total you type (computed from the stored sims, no re-sim), alt lines, quarters,
  team stat ranges, top scores, availability. The **Lineup** tab lets you rule players OUT, force them IN or pick the
  starting QB; "Re-sim with these changes" writes them to the week's `overrides\<season>_wk<week>.yaml` in the data folder (the same
  file the CLI reads) and re-simulates. The Details tab shows the equivalent CLI command and downloads the HTML
  report / JSON.

The sidebar (season, week, sims per game, seed, player projections) applies to every page. Results are cached in
`out\cache`; use "Re-simulate (ignore cache)" on the Game page after refreshing data or editing an overrides file
by hand.
