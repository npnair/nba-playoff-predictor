from predictor.weekly import grade, next_game_day, predict_games, save_predictions, read_predictions


def g(date, home, away, hp=None, ap=None):
    return {"date": date, "home": home, "away": away, "home_pts": hp, "away_pts": ap}


GAMES = [
    g("2026-11-01", "AAA", "BBB", 120, 100), g("2026-11-01", "CCC", "DDD", 100, 110),
    g("2026-11-02", "AAA", "CCC"), g("2026-11-03", "BBB", "DDD"), g("2026-11-20", "AAA", "DDD"),
]
RATINGS = {"AAA": 5.0, "BBB": -3.0, "CCC": 0.0, "DDD": 1.0}
NO_PEN = {t: [0.0] * 83 for t in RATINGS}


def test_next_game_day_and_week_window():
    assert next_game_day(GAMES) == "2026-11-02"
    preds = predict_games(RATINGS, NO_PEN, GAMES, "2026-11-02")
    assert [(p["home"], p["away"]) for p in preds] == [("AAA", "CCC"), ("BBB", "DDD")]   # Nov 20 is next week


def test_better_team_and_home_court_favored():
    p = predict_games(RATINGS, NO_PEN, GAMES, "2026-11-02")[0]
    assert p["pred_home"] > p["pred_away"] and p["home_win_prob"] > 0.5
    assert abs((p["pred_home"] - p["pred_away"]) - (5.0 - 0.0 + 2.5)) <= 1


def test_injury_for_that_game_counts():
    pen = {**NO_PEN, "AAA": [10.0] + [0.0] * 82}           # AAA's star sits only its next game
    hurt = predict_games(RATINGS, pen, GAMES, "2026-11-02")[0]
    assert hurt["home_win_prob"] < 0.5


def test_saved_predictions_freeze_and_grade(tmp_path):
    path = tmp_path / "preds.csv"
    first = predict_games(RATINGS, NO_PEN, GAMES, "2026-11-02")
    save_predictions(path, first, "2026-11-02")
    # Next day: the Nov 2 game is final and must keep its original prediction
    changed = predict_games({**RATINGS, "AAA": -20.0}, NO_PEN, GAMES, "2026-11-03")
    save_predictions(path, changed, "2026-11-03")
    rows = read_predictions(path)
    nov2 = next(r for r in rows if r["date"] == "2026-11-02")
    assert int(nov2["pred_home"]) == first[0]["pred_home"]
    played = GAMES[:2] + [g("2026-11-02", "AAA", "CCC", 115, 108)] + GAMES[3:]
    graded = grade(rows, played)
    assert len(graded) == 1 and graded[0]["correct"] is True


def test_espn_scoreboard_finals_only():
    from predictor.standings_source import parse_scoreboard
    def ev(home, away, hs, as_, done):
        return {"competitions": [{"status": {"type": {"completed": done}}, "competitors": [
            {"homeAway": "home", "team": {"abbreviation": home}, "score": str(hs)},
            {"homeAway": "away", "team": {"abbreviation": away}, "score": str(as_)}]}]}
    payload = {"events": [ev("UTAH", "SA", 101, 118, True), ev("PHX", "LAL", 60, 58, False)]}
    got = parse_scoreboard(payload, "2026-11-15", {"UTA", "SAS", "PHX", "LAL"})
    assert got == [{"date": "2026-11-15", "home": "UTA", "away": "SAS", "home_pts": 101, "away_pts": 118}]
