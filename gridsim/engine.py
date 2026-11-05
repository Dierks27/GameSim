"""Play-by-play game engine.

One `GameSim` instance simulates many games of a single matchup. Per snap:
  1. clock runoff since the previous snap (if the clock is running; hurry/milk tempo aware)
  2. game management: kneel-out, spike, end-of-half field goal, forced 4th-down logic
  3. play call (run / pass / punt / FG / go) from the play-call model
  4. pre-snap foul check, then one historical play row is sampled:
       side   = offense's own plays with prob w_off, else plays the defense allowed
       source = team bucket pool with prob n/(n+k), else league bucket pool
       weights tilted by EPA for home-field advantage
  5. the row's net yards / first down / TD / turnover / clock effect are applied to *this*
     game's state (so first downs and touchdowns are re-evaluated against our down, distance
     and field position, with a Kaplan-Meier extension for TD rows sampled closer to goal)
Scores trigger tries and kickoffs; quarters, halftime, the two-minute warning and overtime
are handled by the state machine. All randomness comes from one seeded numpy Generator.
"""
from __future__ import annotations

import math
import time

import numpy as np

from . import buckets as B
from .clock import T_HURRY, T_NORMAL, in_stop_window, tempo_of
from .model import SimModel
from .overtime import OTState
from .pools import K_COMP, K_INC, K_PEN, K_RUN, K_SACK, K_SCR, km_extend, resolve, resolve_qb

# ---- team stat indices ------------------------------------------------------------------
STATS = [
    "pts", "q1", "q2", "q3", "q4", "ot",
    "pass_att", "comp", "pass_yds_gross", "sacks", "sack_yds", "pass_td", "ints",
    "rush_att", "rush_yds", "rush_td", "fum_lost", "plays", "top",
    "third_att", "third_conv", "fourth_att", "fourth_conv", "first_downs",
    "punts", "fga", "fgm", "xpa", "xpm", "two_att", "two_conv", "tds", "def_st_td",
    "penalties", "pen_yds", "kneels", "spikes", "timeouts_used", "drives",
    "rz_trips", "rz_tds", "ko_starts", "ko_start_yl", "punt_starts", "punt_start_yl", "fourth_go",
]
S = {name: i for i, name in enumerate(STATS)}
NS = len(STATS)
(PTS, Q1, Q2, Q3, Q4, QOT, PATT, COMP, PYDS, SACKS, SACKY, PTD, INTS, RATT, RYDS, RTD, FUML, PLAYS, TOP,
 TATT, TCONV, FATT, FCONV, FD, PUNTS, FGA, FGM, XPA, XPM, TWOA, TWOC, TDS, DSTTD, PENS, PENY, KNEELS,
 SPIKES, TOUSED, DRIVES, RZT, RZTD, KOS, KOSYL, PUS, PUSYL, FGO) = range(NS)
QIDX = {1: Q1, 2: Q2, 3: Q3, 4: Q4}


class GameSim:
    def __init__(self, model: SimModel, home: str, away: str, neutral: bool = False, playoff: bool = False,
                 seed: int = 42, players=None, availability=None, starters: tuple | None = None,
                 start_state: dict | None = None, weather: dict | None = None, edge: tuple | None = None,
                 onoff: dict | None = None):
        for t in (home, away):
            if t not in model.teams:
                raise ValueError(f"unknown team {t!r}; known: {', '.join(model.teams)}")
        self.m = model
        self.cfg = model.cfg
        self.teams = (home, away)            # index 0 = home, 1 = away
        self.neutral = neutral
        self.playoff = playoff
        self.seed = seed
        self.gen = np.random.default_rng(seed)
        self._buf: list[float] = []
        self._bi = 0
        self.players = players               # optional PlayerModel (phase 4)
        self.avail = availability            # optional Availability: per-game rolls of who plays
        self.start_state = start_state       # optional live state (gridsim.state.parse_state)
        self.wx = weather or {"fg_wind": 0.0, "fg_const": 0.0, "pass_epa": 0.0}
        self.edge = tuple(edge) if edge else (0.0, 0.0)   # rest/travel EPA shift per offense (home, away)
        self.onoff = onoff or {}                           # pid -> (team idx, EPA shift when he is out)
        self.avail_shift = [0.0, 0.0]
        self.k_qb = float(self.cfg.qb["k_qb"])
        self.m_strength = float(self.cfg.sim.get("matchup_strength", 0.0))
        self.k_epa = float(self.cfg.sim.get("k_team_epa", 300))
        self.lam_cache: dict = {}
        self.lam_slope: dict = {}
        self.score_fx = model.score_effects if float(self.cfg.sim.get("score_effect_strength", 0.0)) > 0 else {}
        self.score_strength = float(self.cfg.sim.get("score_effect_strength", 0.0))
        self.aux_gen = np.random.default_rng(seed + 7919)
        if starters is None:
            starters = tuple(model.default_qb.get(t) for t in (home, away))
        self.starters = tuple(starters)
        self.qb = list(self.starters)
        self.active = None
        self.qb_starts = [{}, {}]

        sim = self.cfg.sim
        self.w_off = float(sim["w_off"])
        self.min_n = float(sim["min_bucket_n"])
        self.k_out = float(sim["k_outcome"])
        self.league_min_n = float(sim["league_min_n"])
        self.km_fix = bool(sim.get("td_censoring_fix", True))
        self.cc = self.cfg.clock
        self.tp = self.cfg.two_point
        self.pc = model.playcall
        self.kk = model.kicking
        self.ck = model.clock
        self.pools = model.pools
        self.rows = model.pools.rows
        self.pre = model.presnap
        self.kickers = tuple(self.kk.team_kicker.get(t) for t in self.teams)
        self.ot_minutes = float(self.cfg.overtime["playoff_minutes" if playoff else "regular_season_minutes"])
        self.fg_min = float(self.cfg.playcall["fg_min_make_prob"])
        self.pass_shift = float(self.cfg.playcall.get("pass_logit_shift", 0.0))
        # range shift relative to the average current kicker: the league FG-vs-punt table was learned from
        # decisions made by (mostly) these kickers, so only the deviation from that average should move it
        cur = [self.kk.kicker_range_shift(k) for k in self.kk.team_kicker.values()]
        mean_shift = sum(cur) / len(cur) if cur else 0.0
        self.range_shift = tuple(self.kk.kicker_range_shift(k) - mean_shift for k in self.kickers)
        self.punt_off = tuple(self.kk.punt_team_offset.get(t, 0.0) for t in self.teams)
        self.ko_off = tuple(self.kk.ko_team_offset.get(t, 0.0) for t in self.teams)
        self.pre_off = tuple(self.pre.p_off.get(t, self.pre.league_off) for t in self.teams)
        self.pre_def = tuple(self.pre.p_def.get(t, self.pre.league_def) for t in self.teams)
        self.tilt = (0, 0) if neutral else (1, -1)
        self.punt_bin_list = {b: v for b, v in self.kk.punt_bins.items() if v}
        scale = float(self.cc.get("runoff_scale", 1.0))
        self.pace = tuple(scale * self.ck.team_pace.get(t, 1.0) for t in self.teams)
        self.runoff_scale = scale

    # ---- randomness -----------------------------------------------------------------------
    def u(self) -> float:
        i = self._bi
        if i >= len(self._buf):
            self._buf = self.gen.random(1 << 16).tolist()
            i = 0
        self._bi = i + 1
        return self._buf[i]

    def pick(self, seq):
        return seq[int(self.u() * len(seq))]

    # ---- time helpers ----------------------------------------------------------------------
    def gsec(self) -> float:
        return self.clock + 900 * (4 - self.qtr) if self.qtr <= 4 else self.clock

    def hsec(self) -> float:
        return self.clock + 900 if self.qtr in (1, 3) else self.clock

    def has_2mw(self) -> bool:
        return (self.qtr == 2 or self.qtr >= 4) and not self.mw_used

    def run_clock(self, secs: float, live: bool) -> None:
        """Burn `secs` off the clock. live=True: a play is in progress (completes past 2:00)."""
        if secs <= 0:
            return
        before = self.clock
        if self.has_2mw() and before > 120 and before - secs <= 120:
            self.mw_used = True
            self.clock_running = False
            used = secs if live else before - 120
            self.clock = max(before - used, 0.0)
            self.stats[self.pos][TOP] += used
            return
        used = min(secs, before)
        self.clock = before - used
        self.stats[self.pos][TOP] += used

    # ---- game setup ---------------------------------------------------------------------
    def reset(self) -> None:
        self.qtr = 1
        self.clock = 900.0
        self.score = [0, 0]
        self.to = [3, 3]
        self.pos = 0
        self.yl = 75
        self.down = 1
        self.togo = 10
        self.clock_running = False
        self.last_oob_restart = False
        self.last_pen_restart = False
        self.last_presnap_restart = False
        self.mw_used = False
        self.over = False
        self.pending_kick = None   # (kicking_team, safety_kick)
        self.ot = None
        self.drive_rz = False
        self.drive_counted = True
        self.stats = [[0.0] * NS, [0.0] * NS]
        if self.avail is not None:
            qbs, self.active = self.avail.roll(self.u)
            self.qb = [qbs[0] or self.starters[0], qbs[1] or self.starters[1]]
        self.avail_shift = [0.0, 0.0]
        if self.onoff and self.active:
            for pid, (ti, eff) in self.onoff.items():
                if pid in self.active.get(self.teams[ti], ()):
                    self.avail_shift[ti] += eff
        for t in (0, 1):
            self.qb_starts[t][self.qb[t]] = self.qb_starts[t].get(self.qb[t], 0) + 1
        if self.players is not None:
            self.players.new_game(self)

    def set_poss(self, team: int, yl: float) -> None:
        self.pos = team
        self.yl = int(min(max(round(yl), 1), 99))
        self.down = 1
        self.togo = 10 if self.yl > 10 else self.yl
        self.clock_running = False
        self.last_oob_restart = False
        self.last_pen_restart = False
        self.drive_counted = False
        self.drive_rz = False
        if self.ot is not None:
            self.ot.possession_start(team)

    def turnover_poss(self, new_team: int, yl: float) -> None:
        """Possession changes without a score (turnover, downs, punt, missed FG)."""
        old = self.pos
        if self.ot is not None and self.ot.possession_end_no_score(old, self.score):
            self.over = True
            return
        self.set_poss(new_team, yl)

    def add_points(self, team: int, pts: int) -> None:
        self.score[team] += pts
        st = self.stats[team]
        st[PTS] += pts
        st[QIDX[self.qtr] if self.qtr <= 4 else QOT] += pts

    # ---- main loop ------------------------------------------------------------------------
    def play_game(self) -> tuple[list[float], list[float], list[int]]:
        self.reset()
        self.opening_receiver = 0 if self.u() < 0.5 else 1
        self.pending_kick = (1 - self.opening_receiver, False)
        if self.start_state is not None:
            self.apply_state(self.start_state)
        guard = 0
        while not self.over:
            guard += 1
            if guard > 2000:  # pragma: no cover - safety valve
                break
            if self.pending_kick is not None:
                kicker, safety = self.pending_kick
                self.pending_kick = None
                self.kickoff(kicker, safety)
            else:
                self.snap()
            if not self.over and self.clock <= 0:
                self.end_period()
        return self.stats[0], self.stats[1], list(self.score)

    def apply_state(self, s: dict) -> None:
        """Start from a live state instead of the opening kickoff. Points already scored are included in the
        final score; all other box-score stats count only from this state on."""
        self.qtr, self.clock = int(s["qtr"]), float(s["clock"])
        self.score = list(s["score"])
        for t in (0, 1):
            self.stats[t][PTS] = float(self.score[t])
        self.to = list(s["timeouts"])
        self.mw_used = (self.qtr == 2 or self.qtr >= 4) and self.clock <= 120
        if "receives2h" in s:
            self.opening_receiver = 1 - s["receives2h"]
        if self.qtr >= 5:
            self.ot = OTState(self.playoff)
            self.ot.sudden = True   # mid-OT: treat as sudden death (possession guarantees already resolved)
        if "kickoff" in s:
            self.pending_kick = (s["kickoff"], False)
            return
        self.pending_kick = None
        self.pos = s["pos"]
        self.yl, self.down, self.togo = int(s["yl"]), int(s["down"]), int(s["togo"])
        self.clock_running = False
        self.drive_counted = False
        self.drive_rz = False

    def end_period(self) -> None:
        q = self.qtr
        if q in (1, 3):
            self.qtr += 1
            self.clock = 900.0
            self.mw_used = False
            return
        if q == 2:
            self.qtr = 3
            self.clock = 900.0
            self.mw_used = False
            self.to = [3, 3]
            self.clock_running = False
            self.pending_kick = (self.opening_receiver, False)
            return
        if q == 4:
            if self.score[0] != self.score[1]:
                self.over = True
                return
            self.start_ot()
            return
        # end of an OT period
        if not self.playoff or self.score[0] != self.score[1]:
            self.over = True
            return
        self.qtr += 1
        self.clock = 900.0
        self.mw_used = False
        if (self.qtr - 5) % 2 == 0:
            self.to = [3, 3]

    def start_ot(self) -> None:
        self.qtr = 5
        self.clock = self.ot_minutes * 60.0
        self.mw_used = False
        self.to = [3, 3] if self.playoff else [2, 2]
        self.ot = OTState(self.playoff)
        winner = 0 if self.u() < 0.5 else 1
        receiver = winner if self.u() < float(self.cfg.overtime["coin_toss_winner_receives"]) else 1 - winner
        self.pending_kick = (1 - receiver, False)

    # ---- scoring --------------------------------------------------------------------------
    def touchdown(self, team: int, offensive: bool) -> None:
        self.add_points(team, 6)
        self.stats[team][TDS] += 1
        if not offensive:
            self.stats[team][DSTTD] += 1
        if self.ot is not None:
            if self.ot.after_score(team, self.score, try_pending=True):
                self.over = True
                return
        lead = self.score[team] - self.score[1 - team]
        if not (self.qtr == 4 and self.clock <= 0 and lead > 2):
            self.extra_point(team)
        if self.ot is not None:
            if self.ot.after_score(team, self.score, try_pending=False):
                self.over = True
                return
        self.pending_kick = (team, False)

    def go_for_two(self, team: int) -> bool:
        diff = self.score[team] - self.score[1 - team]
        if self.ot is not None and len(self.ot.poss) == 2 and team == self.ot.poss[1]:
            return diff in (-1, -2)
        if self.qtr >= int(self.tp["late_quarter"]) and diff in self.tp["chart"]:
            return True
        return self.u() < float(self.tp["base_rate"])

    def extra_point(self, team: int) -> None:
        st = self.stats[team]
        if self.go_for_two(team):
            st[TWOA] += 1
            if self.u() < self.kk.two_pt_rate:
                st[TWOC] += 1
                self.add_points(team, 2)
        else:
            st[XPA] += 1
            if self.u() < self.wx_adjust(self.kk.p_xp(self.kickers[team]), 33):
                st[XPM] += 1
                self.add_points(team, 1)

    def wx_adjust(self, p: float, dist: float) -> float:
        shift = self.wx["fg_wind"] * dist + self.wx["fg_const"]
        if shift == 0.0:
            return p
        p = min(max(p, 1e-6), 1 - 1e-6)
        return 1.0 / (1.0 + math.exp(-(math.log(p / (1 - p)) + shift)))

    def safety(self, conceding: int) -> None:
        scorer = 1 - conceding
        self.add_points(scorer, 2)
        if self.ot is not None and self.ot.after_score(scorer, self.score, try_pending=False):
            self.over = True
            return
        self.pending_kick = (conceding, True)

    def field_goal_made(self, team: int) -> None:
        self.add_points(team, 3)
        if self.ot is not None and self.ot.after_score(team, self.score, try_pending=False):
            self.over = True
            return
        self.pending_kick = (team, False)

    # ---- kicking plays ----------------------------------------------------------------------
    def kickoff(self, kicking: int, safety_kick: bool) -> None:
        rec = 1 - kicking
        self.pos = kicking
        gsec = self.gsec()
        deficit = self.score[rec] - self.score[kicking]
        onside = (self.qtr == 4 and deficit > 0 and self.ot is None and not safety_kick
                  and (gsec <= 150 or (deficit >= 9 and gsec <= 330)))
        rows = self.kk.onside_rows if (onside and self.kk.onside_rows) else self.kk.ko_rows
        rec_yl, retained, ret_td, kick_td, el = self.pick(rows)
        if not onside:
            rec_yl = rec_yl + self.ko_off[kicking]
            if safety_kick:
                rec_yl -= float(self.cfg.kickoff_rules["safety_kick_shift"])
        self.pos = rec
        if el > 0 and not (rec_yl == 100 - self.kk.tb_yl):
            self.run_clock(el, live=True)
        self.clock_running = False
        if ret_td:
            self.set_poss(rec, 50)
            self.touchdown(rec, offensive=False)
            return
        if kick_td:
            self.pos = kicking
            self.touchdown(kicking, offensive=False)
            return
        if retained:
            self.set_poss(kicking, 100 - rec_yl)
            return
        self.set_poss(rec, rec_yl)
        st = self.stats[rec]
        st[KOS] += 1
        st[KOSYL] += self.yl

    def punt(self) -> None:
        off, dfn = self.pos, 1 - self.pos
        st = self.stats[off]
        st[PUNTS] += 1
        b = min(max(self.yl // 5, 0), 19)
        rows = self.punt_bin_list.get(b)
        if not rows:
            near = min(self.punt_bin_list, key=lambda k: abs(k - b))
            rows = self.punt_bin_list[near]
        r = self.pick(rows)
        self.run_clock(r.elapsed, live=True)
        self.clock_running = False
        p_end = self.yl - r.net - self.punt_off[off]
        if r.ret_td:
            self.set_poss(dfn, 50)
            self.touchdown(dfn, offensive=False)
            return
        if r.kick_td:
            self.touchdown(off, offensive=False)
            return
        if r.retained:
            # muff recovered / penalty: punting team keeps it with a new set of downs
            self.yl = int(min(max(round(p_end), 1), 99))
            self.down, self.togo = 1, (10 if self.yl > 10 else self.yl)
            return
        if r.touchback or p_end <= 0:
            self.turnover_poss(dfn, 80)
        else:
            self.turnover_poss(dfn, 100 - p_end)
        if not self.over:
            sr = self.stats[dfn]
            sr[PUS] += 1
            sr[PUSYL] += self.yl

    def field_goal(self) -> None:
        off, dfn = self.pos, 1 - self.pos
        st = self.stats[off]
        st[FGA] += 1
        dist = self.yl + 17
        p = self.wx_adjust(self.kk.p_fg(dist, self.kickers[off]), dist)
        self.run_clock(self.pick(self.ck.fg_dur), live=True)
        self.clock_running = False
        if self.u() < p:
            st[FGM] += 1
            self.field_goal_made(off)
            return
        spot = self.yl + 7
        if self.u() < self.kk.fg_block_share and self.u() < float(self.cfg.kicking["block_return_td_rate"]):
            self.pos = dfn
            self.touchdown(dfn, offensive=False)
            return
        self.turnover_poss(dfn, 80 if spot <= 20 else 100 - spot)

    # ---- game management ------------------------------------------------------------------
    def can_kneel_out(self) -> bool:
        """Leader can end the game (or half) by kneeling on the remaining downs."""
        downs_left = 4 - self.down + 1
        def_to = self.to[1 - self.pos]
        gaps = downs_left - 1
        stops = def_to + (1 if (self.has_2mw() and self.clock > 120) else 0)
        burn = (38.0 if self.clock_running else 0.0) + downs_left * 1.5 + max(0, gaps - stops) * 39.0
        return self.clock <= burn

    def needs_points(self, diff: int) -> bool:
        if self.qtr == 2:
            return True
        if self.qtr >= 4:
            return diff <= 0
        return False

    def fg_useful(self, diff: int) -> bool:
        if self.qtr == 2:
            return True
        if self.qtr >= 4:
            return -3 <= diff <= 0 or diff > 0
        return True

    def maybe_defense_timeout(self) -> None:
        """Trailing defense stops a running clock late in the game."""
        dfn = 1 - self.pos
        if not self.clock_running or self.to[dfn] <= 0 or self.qtr < 4:
            return
        deficit = self.score[self.pos] - self.score[dfn]
        if deficit <= 0 or deficit > int(self.cc["def_timeout_max_deficit"]):
            return
        if self.gsec() <= float(self.cc["def_timeout_seconds"]):
            if self.score[self.pos] - self.score[dfn] > 0 and self.can_kneel_out():
                return
            self.to[dfn] -= 1
            self.stats[dfn][TOUSED] += 1
            self.clock_running = False

    def maybe_offense_timeout(self, diff: int) -> None:
        off = self.pos
        if not self.clock_running or self.to[off] <= 0:
            return
        if self.qtr not in (2,) and self.qtr < 4:
            return
        if not self.needs_points(diff):
            return
        if self.qtr == 2 and self.yl > 60:
            return  # not threatening before half: let it run
        if self.hsec() <= float(self.cc["off_timeout_seconds"]):
            self.to[off] -= 1
            self.stats[off][TOUSED] += 1
            self.clock_running = False

    # ---- the snap -------------------------------------------------------------------------
    def snap(self) -> None:
        off = self.pos
        dfn = 1 - off
        diff = self.score[off] - self.score[dfn]
        gsec = self.gsec()
        hsec = self.hsec()
        tempo = tempo_of(self.qtr, gsec, hsec, diff, self.cc)

        # victory formation / end-of-half kneel
        kneel = False
        if self.qtr >= 4 and diff > 0 and self.can_kneel_out():
            kneel = True
        elif self.qtr == 2 and hsec <= float(self.cc["q2_kneel_seconds"]) and self.yl >= 50 and self.can_kneel_out():
            kneel = True
        elif self.qtr >= 4 and diff == 0 and self.ot is None and gsec <= 25 and self.yl >= 70 and self.can_kneel_out():
            kneel = True

        # spike: hurry-up, clock running, no timeouts, little time
        spike = False
        if (not kneel and self.clock_running and tempo == T_HURRY and self.to[off] == 0 and self.down < 4
                and self.needs_points(diff) and hsec <= float(self.cc["spike_seconds"]) and hsec > 8):
            spike = True

        # runoff before this snap
        if self.clock_running:
            if kneel:
                ro = 38.0 + self.u() * 3.0
            elif spike:
                ro = 7.0 + self.u() * 6.0
            elif self.last_oob_restart:
                ro = self.pick(self.ck.oob_runoff)
            elif self.last_pen_restart:
                ro = self.pick(self.ck.pen_runoff)
            elif self.last_presnap_restart:
                ro = self.pick(self.ck.presnap_elapsed)
            elif tempo == T_NORMAL:
                ro = self.pick(self.ck.runoff[tempo]) * self.pace[off]
            else:
                ro = self.pick(self.ck.runoff[tempo]) * self.runoff_scale
            if ro >= self.clock and not (self.has_2mw() and self.clock > 120):
                self.stats[off][TOP] += self.clock
                self.clock = 0.0
                return
            self.run_clock(ro, live=False)
            if self.clock <= 0:
                return
            hsec = self.hsec()
            gsec = self.gsec()
        self.last_oob_restart = False
        self.last_pen_restart = False
        self.last_presnap_restart = False
        st = self.stats[off]
        if not self.drive_counted:
            self.drive_counted = True
            st[DRIVES] += 1

        if kneel:
            if self.players is not None:
                self.players.kneel(self, off)
            st[KNEELS] += 1
            st[RATT] += 1
            st[RYDS] -= 1
            st[PLAYS] += 1
            self.run_clock(1.5, live=True)
            self.clock_running = True
            self.yl = min(self.yl + 1, 99)
            self.advance_down(-1, False)
            if self.clock_running:
                self.maybe_defense_timeout()
            return
        if spike:
            st[SPIKES] += 1
            st[PATT] += 1
            st[PLAYS] += 1
            self.run_clock(1.0, live=True)
            self.clock_running = False
            self.advance_down(0, False)
            return

        tb = B.time_b(self.qtr, hsec, gsec)
        p_make = None
        # end-of-half / end-of-game field goal
        if (self.qtr == 2 or self.qtr >= 4) and hsec <= 12 and self.fg_useful(diff):
            p_make = self.kk.p_fg(self.yl + 17, self.kickers[off])
            if p_make >= 0.15 and (self.down == 4 or hsec <= 6 or (not self.clock_running and self.to[off] == 0 and hsec <= 9)):
                self.field_goal()
                return

        if self.down == 4:
            decision = self.fourth_down(diff, gsec, hsec, tb)
            if decision == "go":
                self.stats[off][FGO] += 1
            if decision == "punt":
                self.punt()
                return
            if decision == "fg":
                self.field_goal()
                return

        # pre-snap fouls
        u = self.u()
        if u < self.pre_off[off] + self.pre_def[dfn]:
            if u < self.pre_off[off]:
                yds, af = self.pick(self.pre.off_rows)
                yds = -min(abs(yds), (100 - self.yl) // 2) if yds < 0 else yds
            else:
                yds, af = self.pick(self.pre.def_rows)
                if yds >= self.yl:
                    yds = self.yl - max(1, self.yl // 2)
            st_pen = self.stats[off if yds < 0 else dfn]
            st_pen[PENS] += 1
            st_pen[PENY] += abs(yds)
            self.yl -= yds
            if yds >= self.togo or af:
                self.first_down(off, by_penalty=True)
            else:
                self.togo -= yds
            if self.clock_running and in_stop_window(self.qtr, gsec, hsec):
                self.clock_running = False
            elif self.clock_running:
                self.last_presnap_restart = True
            return

        p_pass = self.pc.p_pass(self.teams[off], min(self.down, 4), self.togo, self.yl, diff, tb, self.pass_shift)
        call = "pass" if self.u() < p_pass else "run"
        self.run_play(call, diff, gsec, hsec, tb)

    def fourth_down(self, diff: int, gsec: float, hsec: float, tb: int) -> str:
        off = self.pos
        yl = self.yl
        dist = yl + 17
        p_make = self.kk.p_fg(dist, self.kickers[off])
        in_range = p_make >= self.fg_min
        late = self.qtr >= 4
        if late and diff < 0:
            if gsec <= 120 or (self.ot is not None and len(self.ot.poss) >= 2):
                return "fg" if (diff >= -3 and in_range) else "go"
            if gsec <= 300 and diff <= -9:
                return "fg" if (diff >= -11 and in_range and yl <= 30) else "go"
            if gsec <= 420 and diff <= -12:
                return "go"
        if late and diff == 0 and gsec <= 120 and in_range:
            return "fg"
        if self.u() < self.pc.p_go(self.teams[off], self.togo, yl, diff, tb):
            return "go"
        yl_eff = yl - self.range_shift[off]
        if in_range and self.u() < self.pc.p_fg(yl_eff):
            return "fg"
        if yl <= 35 and not in_range:
            return "go" if self.togo <= 4 else "punt"
        return "punt"

    # ---- applying a sampled scrimmage row -------------------------------------------------
    # ---- additive matchup correction ------------------------------------------------------
    def _shr(self, key, toward: float = 0.0) -> float:
        e = self.pools.eff.get(key)
        if e is None:
            return toward
        eff, n = e
        return (n * eff + self.k_epa * toward) / (n + self.k_epa)

    def matchup_lambda(self, off: int, call: str) -> float:
        """EPA tilt making the offense/defense mixture's mean shift additive: strength*(off + def)."""
        qb = self.qb[off] if call == "pass" else None
        key = (off, call, qb)
        lam = self.lam_cache.get(key)
        if lam is not None:
            return lam
        wx_shift = (self.wx["pass_epa"] if call == "pass" else 0.0) + self.edge[off]
        if (self.m_strength <= 0 and wx_shift == 0.0 and not self.score_fx) or not self.pools.ref_sits.get(call):
            self.lam_cache[key] = 0.0
            self.lam_slope[key] = 0.0
            return 0.0
        if call == "pass" and qb is not None:
            # prior for a QB: backup level when he has no sample, fading to league average once he has
            # backup_max_dropbacks of history (established starters are shrunk toward 0, not toward backups)
            backup = self.pools.eff.get(("qb", "__BACKUP_QB__", "pass"), (0.0, 0))[0]
            n_qb = self.pools.eff.get(("qb", qb, "pass"), (0.0, 0.0))[1]
            prior = backup * max(0.0, 1.0 - n_qb / float(self.cfg.qb["backup_max_dropbacks"]))
            off_eff = self._shr(("qb", qb, "pass"), toward=prior)
        else:
            off_eff = self._shr(("off", self.teams[off], call))
        def_eff = self._shr(("def", self.teams[1 - off], call))
        target = self.pools.league_epa[call] + self.m_strength * (off_eff + def_eff) + wx_shift
        # measure the untilted mixture over league-mix situations (separate RNG: game stream untouched)
        saved = (self.down, self.togo, self.yl, self.pos, self._buf, self._bi)
        self._buf, self._bi = self.aux_gen.random(1 << 16).tolist(), 0
        self.pos = off
        E = self.rows["epa_c"]
        es = []
        for down, togo, yl, tb in self.pools.ref_sits[call]:
            self.down, self.togo, self.yl = down, togo, yl
            es.append(E[self._draw(call, tb, 0)])
        self.down, self.togo, self.yl, self.pos, self._buf, self._bi = saved
        es = np.asarray(es)
        lo, hi = -0.6, 0.6
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            wts = np.exp(mid * es)
            if (es * wts).sum() / wts.sum() < target:
                lo = mid
            else:
                hi = mid
        lam = 0.5 * (lo + hi)
        wts = np.exp(lam * es)
        mu = (es * wts).sum() / wts.sum()
        var = max(((es - mu) ** 2 * wts).sum() / wts.sum(), 1e-3)
        self.lam_cache[key] = lam
        self.lam_slope[key] = 1.0 / var          # d(lambda)/d(target) for small extra shifts
        return lam

    def sample_row(self, call: str, tb: int) -> int:
        off = self.pos
        lam = (self.matchup_lambda(off, call)
               if (self.m_strength > 0 or self.wx["pass_epa"] != 0.0 or self.edge[off] != 0.0 or self.score_fx) else 0.0)
        if self.avail_shift[off]:
            qb = self.qb[off] if call == "pass" else None
            if (off, call, qb) not in self.lam_slope:
                lam = self.matchup_lambda(off, call)
            lam += self.avail_shift[off] * self.lam_slope.get((off, call, qb), 0.0)
        if self.score_fx:
            phase = 0 if self.qtr <= 2 else (1 if self.qtr == 3 else 2)
            d = self.score[off] - self.score[1 - off]
            fx = self.score_fx.get((phase, B.score_b(d), call), 0.0)
            if fx:
                qb = self.qb[off] if call == "pass" else None
                lam += self.score_strength * fx * self.lam_slope.get((off, call, qb), 0.0)
        variant = self.tilt[off]
        if lam == 0.0:
            return self._draw(call, tb, variant)
        E = self.rows["epa_c"]
        top = math.exp(abs(lam) * 4.0)
        for _ in range(60):
            rid = self._draw(call, tb, variant)
            if self.u() * top < math.exp(lam * E[rid]):
                return rid
        return rid

    def _draw(self, call: str, tb: int, variant: int) -> int:
        off = self.pos
        team_side = self.teams[off] if self.u() < self.w_off else self.teams[1 - off]
        side = "off" if team_side == self.teams[off] else "def"
        down = min(self.down, 4)
        db, zb = B.dist_b(self.togo), B.zone_b(self.yl)
        tseg, tw, lseg = resolve(self.pools, call, side, team_side, down, db, zb, tb,
                                 self.min_n, self.k_out, self.league_min_n)
        if tseg is not None and self.u() < tw:
            seg = tseg
            qb = self.qb[off]
            if call == "pass" and side == "off" and qb is not None:
                # team-specific dropbacks come from the starting QB (or the backup-QB pool)
                qseg, qw, bseg = resolve_qb(self.pools, qb, down, db, zb, self.min_n, self.k_qb)
                if qseg is not None and self.u() < qw:
                    seg = qseg
                elif bseg is not None:
                    seg = bseg
        else:
            seg = lseg
        if seg is None:
            seg = tseg
        return self.pools.sample(seg, self.u(), variant)

    def run_play(self, call: str, diff: int, gsec: float, hsec: float, tb: int) -> None:
        off, dfn = self.pos, 1 - self.pos
        R = self.rows
        rid = self.sample_row(call, tb)
        st = self.stats[off]
        sd = self.stats[dfn]
        kind = R["kind"][rid]
        down0 = self.down
        if self.yl <= 20 and not self.drive_rz:
            self.drive_rz = True
            st[RZT] += 1

        # penalties (counted for the penalized team)
        if R["penalty"][rid]:
            pt = st if R["pen_on_off"][rid] else sd
            pt[PENS] += 1
            pt[PENY] += R["pen_yds"][rid]

        # --- no-play penalty: replay the down
        if R["np_pen"][rid]:
            yds = R["yards"][rid]
            if yds < 0:
                yds = -min(-yds, (100 - self.yl) // 2)
            elif yds >= self.yl:
                yds = self.yl - max(1, self.yl // 2)
            self.yl -= yds
            dur = self.pick(self.ck.dur[K_PEN])
            self.run_clock(dur, live=True)
            if yds >= self.togo or R["auto_first"][rid]:
                self.first_down(off, by_penalty=True)
            else:
                self.togo -= yds
            if self.clock_running:
                if in_stop_window(self.qtr, self.gsec(), self.hsec()):
                    self.clock_running = False
                else:
                    self.last_pen_restart = True
            return

        mult = self.players.attribute(self, rid, off, kind) if self.players is not None else 1.0
        st[PLAYS] += 1
        is_pass_play = kind in (K_INC, K_COMP) or R["int"][rid]
        if is_pass_play:
            st[PATT] += 1
        elif kind == K_SACK:
            st[SACKS] += 1
        else:
            st[RATT] += 1
        if down0 == 3:
            st[TATT] += 1
        elif down0 == 4:
            st[FATT] += 1

        dur = self.pick(self.ck.dur[kind])

        # --- turnovers
        if R["turnover"][rid]:
            if R["int"][rid]:
                st[INTS] += 1
            else:
                st[FUML] += 1
            self.run_clock(dur, live=True)
            self.clock_running = False
            # a completion or run that ends in a lost fumble still counts (yards up to the fumble)
            if kind == K_COMP and not R["int"][rid]:     # interception rows also carry kind K_COMP
                st[COMP] += 1
                st[PYDS] += R["pass_yds"][rid]
            elif kind in (K_RUN, K_SCR):
                st[RYDS] += R["rush_yds"][rid]
            if self.players is not None:
                self.players.record(self, rid, R["yards"][rid] if kind in (K_COMP, K_RUN, K_SCR) else 0, False, off)
            if R["td_def"][rid]:
                self.pos = dfn
                self.touchdown(dfn, offensive=False)
                return
            p_end = self.yl - R["to_gain"][rid]
            if p_end >= 100:
                self.pos = dfn
                self.touchdown(dfn, offensive=False)
                return
            self.turnover_poss(dfn, 80 if p_end <= 0 else 100 - p_end)
            return

        # --- yardage (with TD censoring extension; player efficiency split on positive gains)
        gain = R["yards"][rid]
        if mult != 1.0 and gain > 0 and not R["td"][rid] and kind in (K_COMP, K_RUN):
            gain = int(round(gain * mult))
        if R["td"][rid]:
            c = R["censor"][rid]
            if c < self.yl:
                gain = km_extend(self.pools.km[(call, B.zone_b(self.yl))], c, self.yl, self.u()) if self.km_fix else c
            else:
                gain = self.yl
        adj = gain - R["yards"][rid]
        td = gain >= self.yl
        if td:
            gain = self.yl
            adj = gain - R["yards"][rid]
        new_yl = self.yl - gain

        if kind == K_COMP and R["int"][rid]:
            # interception where the offense got the ball back (e.g. fumbled return): an INT, not a completion
            st[INTS] += 1
        elif kind == K_COMP:
            st[COMP] += 1
            st[PYDS] += R["pass_yds"][rid] + adj
        elif kind == K_SACK:
            st[SACKY] += -gain
        elif kind in (K_RUN, K_SCR):
            st[RYDS] += R["rush_yds"][rid] + adj
        if self.players is not None:
            self.players.record(self, rid, gain, td, off)

        self.run_clock(dur, live=True)

        if td:
            if kind == K_COMP:
                st[PTD] += 1
            else:
                st[RTD] += 1
            if down0 == 3:
                st[TCONV] += 1
            elif down0 == 4:
                st[FCONV] += 1
            st[FD] += 1
            if self.drive_rz:
                st[RZTD] += 1
            self.clock_running = False
            self.yl = 0
            self.touchdown(off, offensive=True)
            return
        if new_yl >= 100:
            self.clock_running = False
            self.safety(off)
            return

        self.yl = new_yl
        first = gain >= self.togo or R["auto_first"][rid]
        if first:
            if down0 == 3:
                st[TCONV] += 1
            elif down0 == 4:
                st[FCONV] += 1
        # clock status after the play
        if kind == K_INC:
            self.clock_running = False
        elif R["oob"][rid]:
            if in_stop_window(self.qtr, self.gsec(), self.hsec()):
                self.clock_running = False
            else:
                self.clock_running = True
                self.last_oob_restart = True
        else:
            self.clock_running = True
        if first:
            self.first_down(off, by_penalty=False)
        else:
            self.advance_down(gain, False)
            if self.over or self.pos != off:
                return
        if self.clock_running and self.clock > 0:
            self.maybe_defense_timeout()
            if self.clock_running:
                self.maybe_offense_timeout(diff)

    def first_down(self, off: int, by_penalty: bool) -> None:
        self.stats[off][FD] += 1
        self.down = 1
        self.togo = 10 if self.yl > 10 else self.yl

    def advance_down(self, gain: int, _unused: bool) -> None:
        self.togo -= gain
        if self.down >= 4:
            # turnover on downs at the spot
            self.clock_running = False
            self.turnover_poss(1 - self.pos, 100 - self.yl)
            return
        self.down += 1
        if self.togo <= 0:
            self.togo = 1


# --------------------------------------------------------------------------------------
# Running many games
# --------------------------------------------------------------------------------------

class SimResults:
    def __init__(self, home: str, away: str, stats: np.ndarray, meta: dict, players: dict | None = None):
        self.home, self.away = home, away
        self.stats = stats          # shape (n_sims, 2, NS); [:,0] home, [:,1] away
        self.meta = meta
        self.players = players

    @property
    def n(self) -> int:
        return self.stats.shape[0]

    def col(self, name: str, team: int) -> np.ndarray:
        return self.stats[:, team, S[name]]


def simulate(model: SimModel, home: str, away: str, n_sims: int, seed: int, neutral: bool = False,
             playoff: bool = False, players=None, availability=None, starters: tuple | None = None,
             progress=None, start_state: dict | None = None, weather: dict | None = None,
             edge: tuple | None = None, onoff: dict | None = None) -> SimResults:
    t0 = time.time()
    g = GameSim(model, home, away, neutral=neutral, playoff=playoff, seed=seed, players=players,
                availability=availability, starters=starters, start_state=start_state, weather=weather, edge=edge,
                onoff=onoff)
    out = np.zeros((n_sims, 2, NS), dtype=np.float64)
    for i in range(n_sims):
        h, a, _ = g.play_game()
        out[i, 0] = h
        out[i, 1] = a
        if players is not None:
            players.end_game(i)
        if progress is not None and (i + 1) % 100 == 0:
            progress((i + 1) / n_sims)
    names = model.qb_names
    qb_used = [{names.get(q, q): c / n_sims for q, c in sorted(g.qb_starts[t].items(), key=lambda x: -x[1])} for t in (0, 1)]
    meta = {"runtime_seconds": round(time.time() - t0, 2), "seed": seed, "n_sims": n_sims,
            "neutral": neutral, "playoff": playoff, "qb_starts": {home: qb_used[0], away: qb_used[1]},
            "start_state": start_state}
    return SimResults(home, away, out, meta, players.results() if players is not None else None)
