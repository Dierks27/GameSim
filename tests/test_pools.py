from gridsim.pools import km_extend


def test_km_extend_bounds():
    S = [1.0] + [max(0.0, 1 - g / 60) for g in range(1, 101)] + [0.0]
    for u in (0.0, 0.2, 0.5, 0.9, 0.999):
        g = km_extend(S, 8, 18, u)
        assert 8 <= g <= 18
    # survival ratio S(18)/S(8) -> small u gives TD
    assert km_extend(S, 8, 18, 0.01) == 18
