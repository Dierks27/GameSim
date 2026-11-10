# Getting started with GridSim (no console needed)

This guide takes about 20–30 minutes the first time. After that, starting GridSim is one double-click.

**What you need:** a Windows 10 or 11 PC, an internet connection, and about 2 GB of free disk space.

---

## Step 1 — Install Python (one time)

GridSim is written in Python, so Windows needs Python installed once.

1. Go to <https://www.python.org/downloads/windows/>.
2. **Skip** the "Latest Python install manager" and "Latest Python 3 Release" links at the top. Scroll to
   **Stable Releases**, find the first line that starts with **Python 3.14** or **Python 3.13** (for example
   *Python 3.14.10*), and click **Download Windows installer (64-bit)** right under it.
   Do **not** pick "install manager", "ARM64" or "32-bit" (even on an ARM / Snapdragon laptop, take the 64-bit one).
3. Open the downloaded installer.
4. **Important:** on the first screen, tick the box **"Add python.exe to PATH"** at the bottom.
5. Click **"Install Now"** and wait until it says *Setup was successful*. Close the installer.

> Already have Python? Setup (step 3) checks it for you: any 64-bit Python 3.11 or newer works.

## Step 2 — Get the GridSim files

The GridSim code lives on GitHub in the repository **Dierks27/GameSim**, on the branch
**`claude/friendly-turing-wk7xhb`**.

1. Open <https://github.com/Dierks27/GameSim/tree/claude/friendly-turing-wk7xhb> (sign in to GitHub if asked).
2. Click the green **"<> Code"** button, then **"Download ZIP"**.
3. Before opening it: right-click the downloaded ZIP → **Properties** → if there is an **"Unblock"** box at the
   bottom, tick it → **OK**. This stops Windows from warning about every file inside.
4. Right-click the ZIP → **Extract All…** → in the box, type **`C:\GridSim`** → **Extract**.
   (Don't extract into *Documents*, *Desktop* or *Downloads*: on most Windows 11 PCs those are synced to OneDrive,
   which would try to upload GridSim's ~11,000 private files.)
5. Open `C:\GridSim`. Inside is one folder named **GameSim-claude-friendly-turing-wk7xhb** — open it.
   **This is your GridSim folder**: it contains **Setup GridSim**, **Start GridSim**, **Schedule GridSim**,
   `config.yaml` and folders such as `app` and `gridsim`. Don't move or rename it after Setup (if you do, run Setup
   again).

> Prefer clicking "update" instead of re-downloading? Install **GitHub Desktop** (<https://desktop.github.com>),
> choose *File → Clone repository → URL* `Dierks27/GameSim`, set *Local path* to **`C:\GridSim\GameSim`**, then
> switch *Current branch* to `claude/friendly-turing-wk7xhb`. That folder is your GridSim folder. Later,
> **Fetch origin → Pull** gets the newest version.

## Step 3 — Run Setup (one time, 5–15 minutes)

1. In your GridSim folder, double-click **Setup GridSim**.
2. If a blue box says *"Windows protected your PC"*: click **More info → Run anyway**. (Windows says this about
   any file downloaded from the internet that isn't a signed app.)
3. A black window opens and works through five steps:
   find Python → create GridSim's own Python environment → install the libraries (the longest part — lines scroll by;
   a yellow *BuildBackendWarning* is normal) → download the NFL data → put a **GridSim** icon on your desktop.
4. When it says **Done!**, press any key to close it.

If it stops with a message starting with **[!]**, read the lines just above it — they say what to do (most often:
install Python with "Add python.exe to PATH" ticked, or check the internet connection). Running Setup again is
always safe.

## Step 4 — Start GridSim

1. Double-click the **GridSim** icon on your desktop (or **Start GridSim** in the GridSim folder).
2. A black window opens — **leave it open** while you use GridSim (you can minimize it). Closing it stops GridSim.
3. Your web browser opens GridSim at <http://localhost:8501> after a few seconds. (If it doesn't, open your browser
   and type that address.)
4. The first time, Windows may ask whether to allow Python through the **firewall**: tick **Private networks**
   and click **Allow**. That's only needed for your phone; GridSim works on the PC either way. (Windows 11 often sets a
   new home network to *Public*, and then the phone still can't connect — see *Phone can't connect* in the
   troubleshooting table.)

**Everyday basics**
- **Stopping GridSim:** close the black window. Closing the browser tab alone does not stop it — and double-clicking
  the icon again while it's still running just opens the browser to it.
- **After a few days away:** first open **Weekly routine** and press **Refresh data**.
- **Where things are saved:** everything GridSim can't download again lives in **your data folder**,
  `C:\Users\<you>\GridSim Data` — *not* in the GridSim program folder — and is backed up automatically. See
  *Your data and backups* below. Reports you download go to your browser's *Downloads* folder.

---

## A quick tour

Everything is on the left-hand menu. Below the list of pages is a small **Settings** box (season, week, sims per
game, seed, player projections — not the same as the *Settings* page). It applies to the game pages (This Week,
Game, Player props, Parlay builder, What-if & Live, Signals) and to **Lock in predictions**. 2,000 sims per game is a
good default; more sims make the numbers steadier but slower (about 3 seconds per 1,000 sims, plus a few seconds the
first time).

### This Week
Every game on this week's slate as a card: kickoff time, the betting market's spread and total, and — once
simulated — GridSim's win %, fair spread and fair total, and how far GridSim is from the market.
Press **Sim all** to simulate the whole week (a minute or two), or **Sim this game** on one card. **Open game**
goes to the details. Results are saved and reused; they are recomputed automatically after you refresh the data,
change a setting, or change that game's lineup.

### Game
One game in depth. Pick the game at the top; if it hasn't been simulated yet, press **Simulate** first.
- **Win %** with its **± margin of error** (with 2,000 sims, about ±2 percentage points). If a number is within that
  margin of 50%, GridSim labels it **"too close to call at this sim count"**.
- **Fair spread** is always shown from the **home team's** side, the way sportsbooks write it: **MIN −2.0** means
  Minnesota is favored by 2; **MIN +3.5** means Minnesota is a 3.5-point underdog.
- **Fair total** is the total points GridSim expects (half the sims go over, half under).
- **Lines & charts** tab: type any spread or total and see how often it covers in the sims — instantly.
- **Lineup** tab: who GridSim assumes is playing (injury report, depth chart). Mark a QB, running back, receiver,
  tight end or kicker **OUT** (or force him **IN**, or pick the starting QB) and press **Re-sim with these changes**.
  The change is saved for that week and used on every page, in locked-in predictions and in the Kalshi check. To
  undo, clear the boxes and press the button again.
- **Details** tab: download a one-file report (HTML) to keep or share.

### Player props
Pick a game, a team, a player and a stat (passing yards, receiving yards, receptions, rushing yards, touchdowns…);
press **Simulate with players** if asked. Move the line slider and the over/under percentage updates immediately.

### Parlay builder
Pick a *Leg type*, fill in the details and press **Add leg** for each leg (e.g. *MIN wins*, *over 45.5*, *Jefferson
over 64.5 receiving yards*), then press **Price it**. GridSim shows the chance all legs hit **together** in the same
simulated games, next to the "multiply the legs" number — the difference shows how the legs help or hurt each other.

### What-if & Live
- **What-if:** the same game with and without a player (or with a different QB), and who picks up his touches.
- **Live:** enter the score, quarter, clock, down, distance and field position of a game in progress and simulate
  the rest of it from there.

### Signals (read-only)
Press **Scan now** (takes several minutes) to compare GridSim's numbers with **Kalshi** prediction-market prices after
Kalshi's fees. GridSim **never places orders and never touches any account** — it only reads public prices.
A market type (moneyline, spread or total) only becomes an **alert** after it has earned trust: at least 50 graded
signals whose price beat Kalshi's closing price on average, without losing money. Until then everything shows as
**watchlist**. Every scan also records the prices, which is what grading needs — so with **Schedule GridSim** set up
(below), that record builds by itself over the season.

### Scorecard
GridSim's honest record: the predictions you locked in before kickoff, graded after the games, compared with the
betting market. **This is the number to trust**, more than any backtest. (It also lists the developer's test runs on
past seasons, for comparison.)

### Diagnose
A plain-English check-up of where GridSim is most likely wrong (for example "totals too high"), with details.

### Settings
Five tabs: **Config** (change a model setting — each shows its description; press **Save changes**; settings you
changed are marked *(yours)*, and **Reset to defaults** undoes them all), **Your data** (backups, restore, bringing in
an old GridSim folder — see *Your data and backups* below), **Data** (**Refresh data now**, and when each file was
downloaded), **Notifications** (test phone alerts) and **Schedule** (when things last ran). Your changes are kept in
your data folder, so updating GridSim never wipes them.

---

## Your weekly routine — the **Weekly routine** page

Open **Weekly routine** (under *Setup* in the menu). It has five numbered boxes, each with one blue button. Use them
on these days:

| When | Do this | What it does |
|---|---|---|
| Tuesday (games are over) | box 1 → **Refresh data** | gets last week's final scores and plays |
| Tuesday | box 3 → check *Week to grade* shows the week that just ended → **Grade** | scores that week's locked-in predictions (see Scorecard). The very first time it will say *nothing new to grade* — that's normal: only weeks you locked in (box 2) can be graded. |
| Tuesday | box 4 → **Run check-up** | lists the top 3 things GridSim is getting wrong lately (a few minutes) |
| Wednesday, and again Friday evening | box 1 → **Refresh data**, then box 2 → **Lock in predictions** | saves time-stamped predictions for every game that hasn't started (the injury reports come out Wednesday–Friday) |
| Sunday morning (optional) | Game → **Lineup** tab for late scratches, then box 2 → **Lock in predictions** | locking in again makes the Scorecard use the updated lineups (games already started are skipped) |
| Once a season (e.g. now, and again after the season ends), in the evening | box 5 → **Start tuning in the background** | GridSim tests better settings on the last few completed seasons (not this season's games — so running it again mid-season mostly repeats the same search). At 150 trials this takes many hours (overnight, or longer on a PC with less memory). It runs at low priority, leaves two processor cores free and keeps the whole PC under 80% memory, so the PC stays usable (Task Manager shows several *Python* entries — normal; both limits can be changed under *Settings → Config → resources*). To stop it early, restart the PC (nothing is harmed; that run's progress is lost). Come back later and press **Refresh status**. Only if the tuned settings did better do you get a **Use the tuned settings** button — you decide. |

**Want it automatic?** Double-click **Schedule GridSim** in the GridSim folder and press **A**. Windows will then,
in the background (no windows pop up):
- every **Tuesday at 9:00 am**: refresh the data, grade last week, run the check-up and lock in the new week;
- every **Friday at 7:00 pm**: lock in fresh predictions after the final injury reports;
- **Monday and Thursday–Sunday, every 30 minutes from 8 am to midnight**: check Kalshi prices (and record them for
  grading).

They run while you are signed in to Windows (a locked screen is fine). If the PC was off or asleep at that time, the
Tuesday/Friday job runs as soon as it's back on. What each job did is written to `logs\scheduled.log` in your data
folder (`C:\Users\<you>\GridSim Data`). Run **Schedule GridSim** again and press **R** to remove the schedule.

---

## Using GridSim on your phone

1. Start GridSim on the PC (step 4) and leave it running.
2. In the black GridSim window, look for the line **Network URL: http://192.168.x.x:8501**.
3. On your phone (same Wi-Fi), open the browser and type exactly that address. (If it doesn't load, see *Phone can't
   connect* in the troubleshooting table below.)

There is no password, so only do this on your own home Wi-Fi. If the phone can't connect, see the troubleshooting
table below.

## Phone alerts for Kalshi signals (optional)

1. Install the free **ntfy** app on your phone and subscribe to a topic name only you know (e.g. `gridsim-jeff-83k2`).
2. Open **Notepad**, type one line: `NTFY_TOPIC=gridsim-jeff-83k2` → *File → Save as* → go to your **data folder**
   (`C:\Users\<you>\GridSim Data`; *Settings → Your data → Open data folder* opens it) → *Save as type:*
   **All files** → *File name:* **`.env`** → **Save**. (An older `.env` in the GridSim program folder still works.)
3. In GridSim open the **Settings** page → **Notifications** tab: *ntfy push* should say **configured** (reload the
   page if not). Press **Send test notification**: it should say *Reached: ntfy…* and your phone should buzz.
   *Reached: toast* alone means only this PC was notified, not your phone.

Remember: alerts only start once a market type has earned trust (see *Signals* above); until then you'll see a
watchlist in GridSim but no phone alerts.

## Getting updates

- **With GitHub Desktop:** *Fetch origin → Pull*, then double-click **Setup GridSim** once (it's quick when nothing
  new is needed).
- **With the ZIP:** download the new ZIP (step 2) and Unblock it. Close GridSim's black window. Right-click the ZIP →
  **Extract All…** → type exactly the same place as the first time (**`C:\GridSim`** — not the GameSim-… folder
  inside it) → **Extract** → choose **Replace the files in the destination**. Then double-click **Setup GridSim** once.

Your locked-in predictions, lineup changes, Kalshi records, tuning history and setting changes live in your data
folder, so an update never touches them — even a completely fresh install in a new place finds them again
automatically.

## Your data and backups

GridSim keeps two kinds of files apart:

- **Re-downloadable** (NFL data, cached sims) stays in the GridSim program folder. Losing it costs nothing —
  **Refresh data** gets it back.
- **Irreplaceable** — locked-in predictions, the Kalshi signal record and saved prices, your lineup changes, tuning
  and check-up history, your settings and `.env` — lives in **your data folder**: `C:\Users\<you>\GridSim Data`.
  It is outside the program folder and outside OneDrive, so updating, re-installing or deleting the program folder
  never touches it.

**Automatic backups.** After every **Lock in predictions**, **Grade**, the Tuesday weekly run, lineup changes and
once a day, GridSim zips your data folder into **GridSim Backups** — in your **OneDrive** folder if you have one (so
there's always a copy off the PC), otherwise in your user folder. The newest 30 are kept (about three weeks; any backup holding something your folder no longer has is never deleted). You can choose another
place (a USB drive, for example) in *Settings → Your data → Where backups go*.

**Settings → Your data** has:
- **Back up now**, **Open data folder** and **Open backups folder**.
- **Restore a backup** — pick one and press **Restore this backup**. It *adds back* anything that's missing;
  nothing you have now is deleted or overwritten.
- **Bring in data from an old GridSim folder** — for an old install that still has your predictions and records.
  In Explorer, hold **Shift**, right-click the old folder → **Copy as path**, paste it into the box and press **Bring
  it in**. It merges: new records are added and nothing is overwritten (if you already have your own settings, they stay and the old folder's are saved beside them as `config.local.yaml.from-import`), and a backup is made first. Running it twice is harmless.
- **Recover a lost week** — only if a week's data is gone and isn't in any backup: **Fill in missing Kalshi closing
  prices** (from Kalshi's own price history, marked *backfilled*) and **Re-create that week's predictions**
  (re-simulated with only the data from before that week, marked *reconstructed*). Reconstructed predictions are
  shown separately on the Scorecard and **never count** toward your record or toward Signals trust — only predictions
  locked in before kickoff do.

**If your data folder is ever lost or replaced** (deleted by accident, a new PC with your OneDrive signed in…),
GridSim notices that the backups come from a data folder it doesn't know, and every page shows a red banner with a
**Restore the latest backup** button — press it before doing anything else. Until you decide, automatic backups pause
and the lost folder's backups are never deleted, so nothing good can be rotated away. (If you really want to start
over, the banner also has **Start fresh instead**.)

The first time you start this version, GridSim moves the data from the old place (inside the program folder) into
your data folder by itself — and does so again at every start, so anything an older copy of GridSim wrote there is
picked up too (each file only once, so something you reset or delete later stays that way). The old files are left
where they were.

## If something goes wrong

| Problem | Fix |
|---|---|
| Setup says Python wasn't found | Install Python (step 1) and make sure "Add python.exe to PATH" was ticked. If you forgot, run the Python installer again → *Modify* → *Next* → tick *Add Python to environment variables*. |
| Setup says it can't find the GridSim files | You opened it from inside the ZIP. Extract the ZIP first (step 2.4) and run Setup from the extracted folder. |
| "Windows protected your PC" | *More info → Run anyway* (or Unblock the ZIP before extracting, step 2.3). |
| GridSim shows a red "your data looks lost" banner | Press **Restore the latest backup**. If your data is in an old GridSim folder instead, use *Settings → Your data → Bring in data from an old GridSim folder*. |
| An error says a file was blocked by antivirus or an *Application Control policy* | Windows Security → *Virus & threat protection* → *Protection history* → allow / restore the file (or check your antivirus quarantine), then run Setup again. |
| Black window says *"Port 8501 is not available"* | GridSim is already open (or still starting) in another black window. Close this one, wait a few seconds, then use the open browser tab or <http://localhost:8501>. |
| Browser didn't open | Open it yourself and go to <http://localhost:8501>. |
| A page says the schedule / data is missing | Weekly routine → box 1 → **Refresh data** (or run Setup again). |
| Phone can't connect | 1) Windows *Settings → Network & internet → Wi-Fi* (or *Ethernet* if the PC is plugged in by cable) *→* your network → set *Network profile type* to **Private** (Windows often sets new networks to Public, which blocks the phone). 2) Start menu → type **Allow an app through Windows Firewall** → *Change settings* → tick **Private** on every **Python** row → OK. 3) Phone on the same Wi-Fi, GridSim's black window still open. |
| The scheduled jobs don't seem to run | Open `logs\scheduled.log` in your data folder (*Settings → Your data → Open data folder*) to see what happened; run **Schedule GridSim** again and press **A**. |
| Anything else | Close the black window and start GridSim again. If an error stays, take a screenshot of the black window. |

## How much should you trust it?

GridSim's numbers come with honest error bars, and its live record is kept on the Scorecard. In tests on the 2024 and
2025 seasons (weeks 3–18, using only data available before each game) GridSim roughly matched the closing betting line
on win chances and margins in 2025 but was worse on totals, and it was clearly behind in 2024; over both seasons the
closing line was more accurate. The market knows things the model doesn't (injuries the data hasn't caught yet,
offseason changes). Use the differences between GridSim and the market as **questions to look into**, and let the
Scorecard tell you over the season whether GridSim deserves more trust.
