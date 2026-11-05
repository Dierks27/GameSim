import pytest

from gridsim.availability import NameMatchError, _status_prob, match_player

ROSTER = [
    {"gsis_id": "1", "full_name": "Justin Jefferson", "last_name": "Jefferson", "position": "WR"},
    {"gsis_id": "2", "full_name": "Jordan Addison", "last_name": "Addison", "position": "WR"},
    {"gsis_id": "3", "full_name": "Aaron Jones Sr.", "last_name": "Jones", "position": "RB"},
    {"gsis_id": "4", "full_name": "Zavier Scott", "last_name": "Scott", "position": "RB"},
]


@pytest.mark.parametrize("name,gid", [("Justin Jefferson", "1"), ("J.Jefferson", "1"), ("jefferson", "1"),
                                      ("Aaron Jones", "3"), ("A.Jones", "3"), ("Jordan Adison", "2")])
def test_match_player(name, gid):
    assert match_player(name, ROSTER, "MIN")["gsis_id"] == gid


def test_match_player_fails_loudly():
    with pytest.raises(NameMatchError):
        match_player("Patrick Mahomes", ROSTER, "MIN")


def test_status_probs(cfg):
    ac = cfg.availability
    assert _status_prob("Out", "", ac)[0] == 0.0
    assert _status_prob("Doubtful", "", ac)[0] == 0.0
    assert _status_prob("Questionable", "full participation in practice", ac)[0] == pytest.approx(0.8625)
    assert _status_prob("Questionable", "did not participate in practice", ac)[0] == pytest.approx(0.5025)
    assert _status_prob(None, "", ac)[0] == 1.0
