"""Overtime rules (verified for 2025+; unchanged by the 2026 rule changes — see DECISIONS.md).

Regular season (2025 rule): one 10-minute period. Both teams are guaranteed a possession
unless the kicking team scores on defense (safety / defensive TD) during the first
possession. After each team has possessed once, any score wins (sudden death). If time
expires the game ends — a tie if the score is level. 2 timeouts each, two-minute warning.
Postseason: same possession rules, 15-minute periods, no ties; 3 timeouts per "half" of OT.
Onside kicks are not allowed in overtime.

The second possession ends the game on the spot when the second team takes the lead; if
it ends (score or not) with the second team trailing, the first team wins; if level,
sudden death follows.
"""
from __future__ import annotations


class OTState:
    __slots__ = ("poss", "sudden", "playoff")

    def __init__(self, playoff: bool):
        self.poss: list[int] = []   # teams in order of OT possessions begun
        self.sudden = False
        self.playoff = playoff

    def possession_start(self, team: int) -> None:
        if not self.poss or self.poss[-1] != team:
            self.poss.append(team)

    def after_score(self, scorer: int, score: list[int], try_pending: bool) -> bool:
        """Return True if the game is over. Called right after points go on the board.

        try_pending: a touchdown was just scored and its try has not been attempted yet.
        """
        if self.sudden:
            return True
        n = len(self.poss)
        if n <= 1:
            first = self.poss[0] if self.poss else scorer
            return scorer != first          # defense scored on the first possession
        if n == 2:
            first, second = self.poss[0], self.poss[1]
            if scorer == first:              # defensive score during the second possession
                return True
            lead = score[second] - score[first]
            if lead > 0:
                return True                  # second team takes the lead: game over on the spot
            if try_pending:
                return False                 # trailing/level after the TD: the try decides
            if lead < 0:
                return True                  # possession over (scored) and still trailing
            self.sudden = True               # level after the second possession's score
            return False
        return True

    def possession_end_no_score(self, team: int, score: list[int]) -> bool:
        """The team with the ball lost it without scoring. True if the game is over."""
        if self.sudden:
            return False
        n = len(self.poss)
        if n == 2 and team == self.poss[1]:
            if score[0] != score[1]:
                return True
            self.sudden = True
        elif n >= 3:
            self.sudden = True
        return False
