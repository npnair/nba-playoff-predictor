import copy

import numpy as np
import pytest

from predictor.data_io import Injury, Move, load_all, validate
from predictor.model import Settings, clinch_status, simulate, team_ratings


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
    # 60 games vs 25 games of preseason evidence: the real margin should dominate
    expected = (prior[sac_i] * 25 + 15.0 * 60) / 85
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
