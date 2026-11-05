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
gridsim refresh-data                      # first run: downloads ~5 seasons of play-by-play into data\cache
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

## Tests

```powershell
pytest -m "not slow"     # fast unit + engine tests
pytest -s                # everything, including the full-season sanity table (~15 s)
```

## How it works (short)

1. `data.py` downloads/caches nflverse data and derives one row per snap with the *real* end spot (from the next
   snap), turnover returns, penalty enforcement, clock usage, and special-teams results.
2. `pools.py` builds weighted sampling pools per play type × situation bucket for the league and for every team
   (offense and defense).
3. `engine.py` runs the game: play call (`playcall.py`), pre-snap fouls, one sampled real play applied to the
   current state, clock (`clock.py`), kicking (`kicking.py`), overtime (`overtime.py`).
4. `report.py` turns the per-sim box scores into the summary.

See `DECISIONS.md` for the back-off chain, shrinkage, clock model, kickoff era rules and calibration notes,
and `SCHEMA_NOTES.md` for the nflverse columns used.

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
  starting QB; "Re-sim with these changes" writes them to the week's `overrides\<season>_wk<week>.yaml` (the same
  file the CLI reads) and re-simulates. The Details tab shows the equivalent CLI command and downloads the HTML
  report / JSON.

The sidebar (season, week, sims per game, seed, player projections) applies to every page. Results are cached in
`out\cache`; use "Re-simulate (ignore cache)" on the Game page after refreshing data or editing an overrides file
by hand.
