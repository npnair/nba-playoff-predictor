import pytest

from predictor.playoff_history import MAX_ADJ, build_history, season_shifts
from predictor.playoffs import Context, lineup_value, matchup, playoff_bpm


def row(pid, bpm, mp, g=10):
    return {"bref_id": pid, "player": pid.title(), "bpm": bpm, "mp": mp, "g": g}


def test_shifts_are_centered_on_the_seasons_average_dip():
    regular = [row("a", 5, 2500), row("b", 5, 2500)]
    playoffs = [row("a", 4, 500), row("b", 2, 500)]          # everyone dips; a dips less
    s = {x["bref_id"]: x["shift"] for x in season_shifts(regular, playoffs)}
    assert s["a"] == pytest.approx(1.0) and s["b"] == pytest.approx(-1.0)


def test_players_without_a_regular_season_row_are_skipped():
    assert season_shifts([row("a", 1, 100)], [row("z", 3, 100)]) == []


def two_player_season(a_po, b_po, mp=400):
    return ([row("a", 2, 2500), row("b", 2, 2500), row("c", 2, 2500)],
            [row("a", a_po, mp), row("b", b_po, mp), row("c", 0, mp)])


def test_riser_is_positive_faller_negative_and_both_are_shrunk():
    h = {r["bref_id"]: r for r in build_history({2025: two_player_season(4, -2)})}
    assert h["a"]["playoff_adj"] > 0 > h["b"]["playoff_adj"]
    assert abs(h["a"]["playoff_adj"]) < abs(h["a"]["raw_shift"])     # 400 minutes isn't enough to trust fully


def test_more_playoff_minutes_means_less_shrinkage():
    small = {r["bref_id"]: r for r in build_history({2025: two_player_season(4, -2, mp=100)})}
    big = {r["bref_id"]: r for r in build_history({2025: two_player_season(4, -2, mp=1500)})}
    assert big["a"]["playoff_adj"] > small["a"]["playoff_adj"]


def test_recent_postseasons_count_more():
    rise_recent = build_history({2022: two_player_season(-2, 4), 2026: two_player_season(4, -2)})
    adj = {r["bref_id"]: r["playoff_adj"] for r in rise_recent}
    assert adj["a"] > 0 > adj["b"]


def test_adjustment_is_capped():
    h = build_history({2025: two_player_season(40, -40, mp=5000)})
    assert all(abs(r["playoff_adj"]) <= MAX_ADJ for r in h)


def five(team, bpm, adj=0.0):
    pos = ["PG", "SG", "SF", "PF", "C"]
    return [{"team": team, "bref_id": f"{team}{k}", "player": f"{team} {k}", "pos": pos[k], "mpg": 34 - k,
             "bpm": bpm, "minutes": 2000, "po_adj": adj} for k in range(5)]


def test_playoff_form_moves_the_lineup_and_the_series():
    assert playoff_bpm({"bpm": 2.0, "po_adj": 1.5}) == 3.5
    assert playoff_bpm({"bpm": 2.0}) == 2.0                  # missing history counts as 0
    even = Context({"A": 0, "B": 0}, five("A", 1) + five("B", 1), [])
    risers = Context({"A": 0, "B": 0}, five("A", 1, adj=1.0) + five("B", 1), [])
    assert matchup(even, "A", "B")["margin"] == pytest.approx(0)
    assert matchup(risers, "A", "B")["margin"] > 0
    assert lineup_value(risers.five("A")) > lineup_value(even.five("A"))
