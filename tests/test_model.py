import copy

import numpy as np
import pytest

from predictor.data_io import Injury, Move, load_all, validate
from predictor.model import Settings, clinch_status, injury_wins_lost, simulate, team_ratings


@pytest.fixture(scope="module")
def data():
    return load_all()


def by_id(results):
    return {r["id"]: r for r in results}


def test_data_loads(data):
    teams, injuries, moves = data
    assert len(teams) == 30
    assert sum(t.conference == "West" for t in teams) == 15
    assert injuries and moves


def test_ratings_are_centered(data):
    teams, _, moves = data
    rating, sd, prior = team_ratings(teams, moves, Settings())
    assert abs(prior.mean()) < 1e-9
    assert np.all(sd > 0)


def test_exactly_sixteen_playoff_teams(data):
    teams, injuries, moves = data
    results = simulate(teams, injuries, moves, Settings(sims=400, seed=1))
    assert sum(r["playoffs"] for r in results) == pytest.approx(16)
    for conf in ("West", "East"):
        conf_rows = [r for r in results if r["conference"] == conf]
        assert sum(r["top6"] for r in conf_rows) == pytest.approx(6)
        assert sum(r["play_in"] for r in conf_rows) == pytest.approx(4)


def test_total_wins_close_to_league_total(data):
    teams, injuries, moves = data
    results = simulate(teams, injuries, moves, Settings(sims=400, seed=2))
    assert sum(r["proj_wins"] for r in results) == pytest.approx(1230, abs=15)


def test_injury_lowers_projection(data):
    teams, injuries, moves = data
    base = by_id(simulate(teams, injuries, moves, Settings(sims=1000, seed=3)))
    hurt = injuries + [Injury("BOS", "Test star", 8.0, 82)]
    worse = by_id(simulate(teams, hurt, moves, Settings(sims=1000, seed=3)))
    assert worse["BOS"]["proj_wins"] < base["BOS"]["proj_wins"] - 10


def test_offseason_move_raises_rating(data):
    teams, _, moves = data
    before, _, _ = team_ratings(teams, moves, Settings())
    after, _, _ = team_ratings(teams, moves + [Move("SAC", "+ Test star", 6.0)], Settings())
    sac = [t.id for t in teams].index("SAC")
    assert after[sac] > before[sac]


def test_season_results_take_over(data):
    teams, _, moves = data
    played = copy.deepcopy(teams)
    sac = next(t for t in played if t.id == "SAC")
    sac.w, sac.l, sac.point_diff = 60, 0, 15.0
    rating, _, prior = team_ratings(played, moves, Settings())
    sac_i = [t.id for t in played].index("SAC")
    # 60 games vs the preseason rating's games of evidence: the real margin should dominate
    k = Settings().evidence_games
    expected = (prior[sac_i] * k + 15.0 * 60) / (k + 60)
    assert rating[sac_i] == pytest.approx(expected)
    assert rating[sac_i] > 8


def test_clinch_and_elimination(data):
    teams = copy.deepcopy(data[0])
    west = [t for t in teams if t.conference == "West"]
    for k, t in enumerate(west):
        t.w, t.l = (70 - 4 * k, 10 + 4 * k) if k < 6 else (20, 60)
    assert clinch_status(west[0], teams) == "clinched"
    assert clinch_status(west[-1], teams) == ""   # still mathematically alive for the play-in
    for t in west[:10]:
        t.w, t.l = 75, 5
    assert clinch_status(west[-1], teams) == "eliminated"


def test_validate_rejects_bad_record(data):
    teams = copy.deepcopy(data[0])
    teams[0].w = 83
    with pytest.raises(ValueError):
        validate(teams, [], [])


def test_games_already_missed_are_not_counted_twice(data):
    """A player out since opening night: his absence is already in the point diff, so the
    rating adds it back and only the remaining games he misses lower the team."""
    teams, _, moves = data
    played = copy.deepcopy(teams)
    sac = next(t for t in played if t.id == "SAC")
    sac.w, sac.l, sac.point_diff = 5, 5, -3.0
    i = [t.id for t in played].index("SAC")
    s = Settings()
    k = s.evidence_games

    star = Injury("SAC", "Test star", 4.0, 20, games_missed=10)
    healthy, _, prior = team_ratings(played, moves, s, [star])
    # Shorthanded margin of -3 over 10 games, worth -3 + 4 = +1 with him
    assert healthy[i] == pytest.approx((prior[i] * k + 1.0 * 10) / (k + 10))

    # Nothing missed yet: no add-back, same as before this change
    fresh = Injury("SAC", "Test star", 4.0, 20)
    plain, _, _ = team_ratings(played, moves, s)
    assert team_ratings(played, moves, s, [fresh])[0][i] == pytest.approx(plain[i])

    # Net wins lost shrink once the games he already missed are credited back
    assert injury_wins_lost(sac, [star], k) < injury_wins_lost(sac, [fresh], k)
    # A player who missed a long stretch but is back next game is a net gain
    assert injury_wins_lost(sac, [Injury("SAC", "Back now", 4.0, 0, games_missed=10)], k) < 0


def test_games_missed_column_is_optional(tmp_path):
    (tmp_path / "injuries.csv").write_text("team,player,impact,games_out\nSAC,Someone,1.0,5\n")
    from predictor.data_io import load_injuries
    assert load_injuries(tmp_path)[0].games_missed == 0
