import pytest

from predictor.data_io import Injury
from predictor.playoffs import (
    Context, blend_player, conference_bracket, h2h_estimate, matches_injury, injury_name, matchup,
    out_for_playoffs, season_series, series_outcomes, series_pick, starting_five,
)

TEAMS = [f"T{i:02d}" for i in range(10)]


def player(team, pid, pos, mpg, bpm, name=None):
    return {"team": team, "bref_id": pid, "player": name or pid, "pos": pos, "mpg": mpg, "bpm": bpm, "minutes": 1500}


def roster(team, bpm):
    return [player(team, f"{team}{k}", pos, 34 - k, bpm) for k, pos in enumerate(["C", "PG", "PF", "SG", "SF"])] + \
           [player(team, f"{team}b{k}", "SF", 15, -3) for k in range(4)]


# ---------------------------------------------------------------- series math
@pytest.mark.parametrize("margin", [-12, -3, 0, 2.5, 9])
@pytest.mark.parametrize("home", [True, False])
def test_series_outcomes_sum_to_one(margin, home):
    out = series_outcomes(margin, home)
    assert sum(out["a"].values()) + sum(out["b"].values()) == pytest.approx(1)


def test_even_teams_home_court_matters():
    assert series_outcomes(0, True)["p_a"] > 0.5 > series_outcomes(0, False)["p_a"]


def test_series_is_symmetric():
    a = series_outcomes(4, True)
    b = series_outcomes(-4, False)
    assert a["p_a"] == pytest.approx(1 - b["p_a"])
    assert a["a"][6] == pytest.approx(b["b"][6])


def test_lopsided_series_is_usually_short():
    side, games, prob = series_pick(series_outcomes(15, True))
    assert side == "a" and games in (4, 5) and prob > 0.95


def test_close_series_goes_long():
    side, games, prob = series_pick(series_outcomes(0.5, True))
    assert games in (6, 7) and prob < 0.65


# ---------------------------------------------------------------- players and lineups
def test_blend_uses_last_season_early_and_this_season_later():
    prev = {"g": 70, "mp": 2400, "bpm": 6.0, "pos": "PG"}
    early = blend_player({"g": 2, "mp": 70, "bpm": -5.0, "pos": "PG"}, prev)
    late = blend_player({"g": 60, "mp": 2000, "bpm": -1.0, "pos": "PG"}, prev)
    assert early["bpm"] > 3 and late["bpm"] < 1


def test_small_samples_are_shrunk():
    tiny = blend_player({"g": 1, "mp": 4, "bpm": 30.0, "pos": "C"}, None)
    assert tiny["bpm"] < 0


def test_starting_five_lines_up_pg_to_c():
    five = starting_five(roster("T00", 1), "T00", set())
    assert [p["slot"] for p in five] == ["PG", "SG", "SF", "PF", "C"]
    assert [p["pos"] for p in five] == ["PG", "SG", "SF", "PF", "C"]


def test_injured_starter_is_replaced():
    players = roster("T00", 1)
    five = starting_five(players, "T00", {"T000"})       # the center is out
    assert "T000" not in [p["bref_id"] for p in five] and len(five) == 5


def test_injury_name_matching():
    assert matches_injury("Ben Simmons", injury_name("Ben Simmons (Achilles, season)"))
    assert matches_injury("Jaylen Watkins", injury_name("J. Watkins (ACL, season)"))
    assert matches_injury("Nikola Jokić", injury_name("Nikola Jokic (knee)"))
    assert not matches_injury("Jalen Williams", injury_name("J. Watkins (ACL, season)"))


def test_only_season_long_injuries_count_for_playoffs():
    players = [player("T00", "a", "PG", 30, 5, "Ben Simmons"), player("T00", "b", "C", 30, 5, "Dereck Lively")]
    injuries = [Injury("T00", "Ben Simmons (Achilles, season)", 0.3, 82),
                Injury("T00", "Dereck Lively (foot)", 1.5, 20)]
    assert out_for_playoffs(players, injuries, {"T00": 80}) == {"a"}


# ---------------------------------------------------------------- season series
def test_season_series_from_both_sides():
    games = [{"date": "2026-11-01", "home": "A", "away": "B", "home_pts": 110, "away_pts": 100},
             {"date": "2026-12-01", "home": "B", "away": "A", "home_pts": 105, "away_pts": 99},
             {"date": "2027-02-01", "home": "A", "away": "B", "home_pts": None, "away_pts": None},
             {"date": "2027-02-02", "home": "A", "away": "C", "home_pts": 90, "away_pts": 80}]
    played, scheduled = season_series(games, "A", "B")
    assert [g["margin"] for g in played] == [10, -6] and scheduled == 1


def test_h2h_falls_back_to_team_strength_without_games():
    assert h2h_estimate([], 3.0) == 3.0
    sweep = [{"a_home": True, "margin": 20}] * 4
    assert 3.0 < h2h_estimate(sweep, 3.0) < 20


# ---------------------------------------------------------------- matchups and bracket
def ctx_for(strengths: dict[str, float], games=()):
    players = [p for t, s in strengths.items() for p in roster(t, s)]
    return Context(dict(strengths), players, list(games))


def test_better_starting_five_shifts_the_margin():
    ctx = ctx_for({"A": 0, "B": 0})
    ctx.players = roster("A", 4) + roster("B", 0)
    m = matchup(ctx, "A", "B")
    assert m["margin"] > 0 and m["slots_won"] == 5


def test_bracket_is_complete_and_consistent():
    strengths = {t: 6 - i for i, t in enumerate(TEAMS)}
    b = conference_bracket(ctx_for(strengths), TEAMS)
    assert len(b["r1"]) == 4 and len(b["r2"]) == 2
    assert {s["winner"] for s in b["r1"]} >= {b["r2"][0]["winner"], b["r2"][1]["winner"]}
    assert b["final"]["winner"] == "T00"                    # strongest team wins
    for s in b["r1"] + b["r2"] + [b["final"]]:
        assert s["games"] in (4, 5, 6, 7) and 0.5 <= s["prob"] <= 1


def test_season_series_can_flip_a_close_series():
    strengths = {"A": 0.5, "B": 0.0}
    even = matchup(ctx_for(strengths), "A", "B")
    beatdowns = [{"date": f"2026-1{k}-01", "home": "A" if k % 2 else "B", "away": "B" if k % 2 else "A",
                  "home_pts": 90 if k % 2 else 125, "away_pts": 125 if k % 2 else 90} for k in range(4)]
    flipped = matchup(ctx_for(strengths, beatdowns), "A", "B")
    assert even["margin"] > 0 > flipped["margin"]
