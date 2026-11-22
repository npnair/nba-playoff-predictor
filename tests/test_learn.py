import numpy as np
import pytest

from predictor.data_io import load_all, load_games, load_players
from predictor.learn import GRID, MIN_GAMES, Replay, as_of, learn
from predictor.model import DEFAULTS, Settings, form_adjustment, team_ratings


@pytest.fixture(scope="module")
def real():
    teams, _, moves = load_all()
    games = [g for g in load_games() if g["home_pts"] is not None]
    if len(games) < MIN_GAMES:
        pytest.skip("not enough played games yet")
    return teams, moves, load_players(), games


def test_replay_matches_the_real_model(real):
    """The replay must rate teams exactly like model.team_ratings would have on that morning."""
    teams, moves, players, games = real
    rp = Replay(teams, moves, players, games)
    day = len(rp.days) - 1
    before = [g for g in games if g["date"] < rp.days[day]]
    s = Settings(weight_last=60, weight_line=40, weight_preseason=0, evidence_games=14, form_half_life=10)
    expected, _, _ = team_ratings(as_of(teams, before), moves, s, [], players, before)
    assert rp.ratings(0.6, 14, 10)[day] == pytest.approx(expected, abs=1e-9)


def test_learned_settings_stay_in_the_allowed_range(real):
    r = learn(*real)
    for k, options in GRID.items():
        assert r["settings"][k] in options
    c = r["check"]
    assert c["learned"]["games"] == c["fixed"]["games"] > 0


def test_learns_a_bigger_home_court_when_home_teams_win_more(real):
    teams, moves, players, games = real
    boosted = [{**g, "home_pts": g["home_pts"] + 8} for g in games]
    assert learn(teams, moves, players, boosted)["settings"]["home_court"] > learn(*real)["settings"]["home_court"]


def test_keeps_defaults_with_too_few_games(real):
    teams, moves, players, games = real
    assert learn(teams, moves, players, games[: MIN_GAMES - 1])["settings"] == DEFAULTS


def test_recent_form_counts_more_only_when_switched_on(real):
    teams = real[0]
    t = teams[0]
    games = [{"date": f"2026-11-{d:02d}", "home": t.id, "away": teams[1].id,
              "home_pts": 100 + (15 if d > 10 else -15), "away_pts": 100} for d in range(1, 16)]
    assert form_adjustment(teams, games, 0)[0] == 0
    assert form_adjustment(teams, games, 5)[0] > 0       # a team that turned it around lately
    assert np.all(form_adjustment(teams, games, 5)[2:] == 0)
