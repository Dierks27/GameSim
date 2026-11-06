# Getting started with GridSim (no console needed)

This guide takes about 20 minutes the first time. After that, starting GridSim is one double-click.

**What you need:** a Windows 10 or 11 PC, an internet connection, and about 1 GB of free disk space.

---

## Step 1 — Install Python (one time)

GridSim is written in Python, so Windows needs Python installed once.

1. Go to <https://www.python.org/downloads/windows/> and click **"Download Python 3.12.x"** (any 3.11, 3.12 or 3.13
   works; 3.12 is the safe choice).
2. Open the downloaded installer.
3. **Important:** on the first screen, tick the box **"Add python.exe to PATH"** at the bottom.
4. Click **"Install Now"** and wait until it says *Setup was successful*. Close the installer.

> Already have Python? Setup (step 3) checks the version for you and tells you if it's too old.

## Step 2 — Get the GridSim files

The GridSim code lives on GitHub in the repository **Dierks27/GameSim**, on the branch
**`claude/friendly-turing-wk7xhb`**.

1. Open <https://github.com/Dierks27/GameSim/tree/claude/friendly-turing-wk7xhb> (sign in to GitHub if asked).
2. Click the green **"<> Code"** button, then **"Download ZIP"**.
3. Before opening it: right-click the downloaded ZIP → **Properties** → tick **"Unblock"** (bottom of the
   window, if it is there) → **OK**. This stops Windows from warning about every file inside.
4. Right-click the ZIP → **Extract All…** → choose a simple place such as `C:\Users\<you>\Documents\GridSim`
   → **Extract**.
5. Open the extracted folder. You should see files like **Setup GridSim**, **Start GridSim**, `README.md` and
   folders such as `app` and `gridsim`. (If you only see one folder inside, open it — that's the GridSim folder.)

> Prefer clicking "update" instead of re-downloading? Install **GitHub Desktop** (<https://desktop.github.com>),
> choose *File → Clone repository → Dierks27/GameSim*, then switch the *Current branch* to
> `claude/friendly-turing-wk7xhb`. Later, **Fetch origin → Pull** gets the newest version.

## Step 3 — Run Setup (one time, 5–10 minutes)

1. In the GridSim folder, double-click **Setup GridSim**.
2. If a blue box says *"Windows protected your PC"*: click **More info → Run anyway**. (Windows says this about
   any file downloaded from the internet that isn't a signed app.)
3. A black window opens and works through five steps: find Python → create GridSim's own Python environment →
   install the libraries → **download the NFL data** (the longest part) → put a **GridSim** icon on your desktop.
4. When it says **Done!**, press any key to close it.

If it stops with a red `[!]` message, read the message — it says what to do (most often: install Python with
"Add python.exe to PATH" ticked, or check the internet connection). Running Setup again is always safe.

## Step 4 — Start GridSim

1. Double-click the **GridSim** icon on your desktop (or **Start GridSim** in the GridSim folder).
2. A black window opens — **leave it open** while you use GridSim; closing it stops GridSim.
3. Your web browser opens GridSim at <http://localhost:8501> after a few seconds. (If it doesn't, open your browser
   and type that address.)
4. The first time, Windows may ask whether to allow Python through the **firewall**: tick **Private networks**
   and click **Allow** (that's what lets your phone connect; GridSim still works on the PC if you decline).

---

## A quick tour

Everything is on the left-hand menu. The **Settings** box at the bottom of that menu (season, week, sims per game,
seed, player projections) applies to every page. 2,000 sims per game is a good default; more sims make the numbers
steadier but slower (about 3 seconds per 1,000 sims).

### This Week
Every game on this week's slate as a card: kickoff time, the betting market's spread and total, and — once
simulated — GridSim's win %, fair spread and fair total, and how far GridSim is from the market.
Press **Sim all** to simulate the whole week (a minute or two), or **Sim this game** on one card. **Open game**
goes to the details.

### Game
One game in depth.
- **Win %** with its **± margin of error** (with 2,000 sims, about ±2 points). If a number is within that margin
  of 50%, GridSim labels it **"too close to call at this sim count"**.
- **Fair spread** is always shown from the **home team's** side, the way sportsbooks write it: **MIN −2.0** means
  Minnesota is favored by 2; **MIN +3.5** means Minnesota is a 3.5-point underdog.
- **Fair total** is the total points GridSim expects (half the sims go over, half under).
- **Lines & charts** tab: type any spread or total and see how often it covers in the sims — instantly.
- **Lineup** tab: who GridSim assumes is playing (injury report, depth chart). Mark a player **OUT** (or force him
  **IN**, or pick the starting QB) and press **Re-sim with these changes**. Your changes are saved for that week
  and used everywhere (this is the `overrides` file for that week).
- **Details** tab: download a one-file report (HTML) to keep or share.

### Player props
Pick a player and a stat (passing yards, receiving yards, receptions, rushing yards, touchdowns…). Move the line
slider and the over/under percentage updates immediately.

### Parlay builder
Build a same-game parlay from drop-downs (e.g. *MIN wins + over 45.5 + Jefferson over 64.5 receiving yards*).
GridSim shows the chance all legs hit **together** in the same simulated games, next to the "multiply the legs"
number — the difference shows how the legs help or hurt each other.

### What-if & Live
- **What-if:** the same game with and without a player (or with a different QB), and who picks up his touches.
- **Live:** enter the score, quarter, clock, down, distance and field position of a game in progress and simulate
  the rest of it from there.

### Signals (read-only)
Compares GridSim's numbers with **Kalshi** prediction-market prices after Kalshi's fees. GridSim **never places
orders and never touches any account** — it only reads public prices. Until a market type has a proven track record
(50+ graded signals that beat the closing price), everything shows as **watchlist**, not as an alert.

### Scorecard
GridSim's honest record: the predictions you locked in before kickoff, graded after the games, compared with the
betting market. **This is the number to trust**, more than any backtest.

### Diagnose
A plain-English check-up of where GridSim is most likely wrong (for example "totals too high"), with details.

### Settings
Change any model setting (each has a description), refresh the data, test phone notifications, and see when
things last ran. A backup of your settings is saved automatically before every change.

---

## Your weekly routine — the **Weekly routine** page

Open **Weekly routine** in the menu and press the buttons in order:

| When | Button | What it does |
|---|---|---|
| Tuesday (games are over) | **1 · Refresh data** | gets last week's results and this week's injury reports |
| Tuesday | **3 · Grade** | scores last week's locked-in predictions (see Scorecard) |
| Tuesday | **4 · Run check-up** | lists the top 3 things GridSim is getting wrong lately |
| Wednesday, and again Friday evening | **1 · Refresh data**, then **2 · Lock in predictions** | saves time-stamped predictions for every game not yet started |
| Sunday morning (optional) | Game → Lineup tab | mark late scratches OUT, re-sim |
| Every few weeks, in the evening | **5 · Start tuning** | lets GridSim test better settings on past seasons (hours); you decide whether to use the result |

**Want it automatic?** Double-click **Schedule GridSim** in the GridSim folder and press **A**. Windows will then
run the Tuesday and Friday jobs by itself (your PC needs to be on), and check Kalshi prices every 30 minutes
Thursday–Sunday. Run it again and press **R** to remove the schedule.

---

## Using GridSim on your phone

1. Start GridSim on the PC (step 4) and leave it running.
2. Find the PC's address: on the PC press **Windows key**, type **cmd**, press Enter, type **ipconfig**, press Enter,
   and look for **IPv4 Address** (something like `192.168.1.23`).
3. On your phone (same Wi-Fi), open the browser and go to `http://192.168.1.23:8501` (your number).

There is no password, so only do this on your own home Wi-Fi.

## Phone alerts for Kalshi signals (optional)

1. Install the free **ntfy** app on your phone and subscribe to a topic name only you know (e.g. `gridsim-jeff-83k2`).
2. In the GridSim folder, create a text file named **`.env`** containing one line: `NTFY_TOPIC=gridsim-jeff-83k2`
   (in Notepad: *Save as* → file name `.env` → *Save as type: All files*).
3. Settings → **Send test notification** to check it arrives.

## Getting updates

- **With GitHub Desktop:** *Fetch origin → Pull*, then double-click **Setup GridSim** once (it's quick when nothing
  is new).
- **With the ZIP:** download the new ZIP (step 2) and extract it **over** your GridSim folder (choose *Replace the
  files*). Your downloaded data, predictions and Kalshi signal records are kept. If you changed settings, copy
  `config.yaml` somewhere first and put it back afterwards. Then run **Setup GridSim** once.

## If something goes wrong

| Problem | Fix |
|---|---|
| Setup says Python wasn't found | Install Python (step 1) and make sure "Add python.exe to PATH" was ticked. If you forgot, run the Python installer again → *Modify* → *Next* → tick *Add Python to environment variables*. |
| "Windows protected your PC" | *More info → Run anyway* (or Unblock the ZIP before extracting, step 2.3). |
| Browser didn't open | Open it yourself and go to <http://localhost:8501>. |
| Page says the schedule / data is missing | Weekly routine → **1 · Refresh data** (or run Setup again). |
| Phone can't connect | Same Wi-Fi? GridSim window still open on the PC? Allow Python on *Private networks* in Windows Firewall. |
| Anything else | Close the black window and start GridSim again. If an error stays, take a screenshot of the black window. |

## How much should you trust it?

GridSim's numbers come with honest error bars, and its record is kept on the Scorecard. In tests on past seasons
(using only data available before each game) it was about as accurate as the closing betting line in 2025 and
clearly behind it in 2024 — the market knows things the model doesn't (injuries the data hasn't caught yet,
offseason changes). Use the differences between GridSim and the market as **questions to look into**, and let the
Scorecard tell you over the season whether GridSim deserves more trust.
