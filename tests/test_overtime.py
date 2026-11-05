from gridsim.overtime import OTState


def test_first_possession_fg_does_not_end_game():
    ot = OTState(False)
    ot.possession_start(0)
    assert ot.after_score(0, [3, 0], try_pending=False) is False


def test_defensive_score_on_first_possession_ends_game():
    ot = OTState(False)
    ot.possession_start(0)
    assert ot.after_score(1, [0, 2], try_pending=False) is True


def test_second_team_takes_lead_ends_game():
    ot = OTState(False)
    ot.possession_start(0)
    ot.after_score(0, [3, 0], False)
    ot.possession_start(1)
    assert ot.after_score(1, [3, 6], try_pending=True) is True


def test_second_team_ties_goes_sudden_death():
    ot = OTState(False)
    ot.possession_start(0)
    ot.after_score(0, [7, 0], False)
    ot.possession_start(1)
    assert ot.after_score(1, [7, 6], try_pending=True) is False
    assert ot.after_score(1, [7, 7], try_pending=False) is False
    assert ot.sudden
    ot.possession_start(0)
    assert ot.after_score(0, [10, 7], False) is True


def test_second_team_fails_while_trailing_ends_game():
    ot = OTState(False)
    ot.possession_start(0)
    ot.after_score(0, [3, 0], False)
    ot.possession_start(1)
    assert ot.possession_end_no_score(1, [3, 0]) is True


def test_both_scoreless_then_sudden_death():
    ot = OTState(False)
    ot.possession_start(0)
    assert ot.possession_end_no_score(0, [0, 0]) is False
    ot.possession_start(1)
    assert ot.possession_end_no_score(1, [0, 0]) is False
    assert ot.sudden
