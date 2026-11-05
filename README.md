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
