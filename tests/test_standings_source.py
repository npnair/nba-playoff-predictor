import copy

import pytest

from predictor.__main__ import append_history
from predictor.data_io import load_teams
from predictor.standings_source import (
    ESPN_ALIASES, SeasonNotAvailable, StandingRow, check_progress, parse_standings,
)

KNOWN = {t.id for t in load_teams()}
TO_ESPN = {v: k for k, v in ESPN_ALIASES.items() if v in KNOWN and k not in {"PHO", "BRK", "CHO"}}


def stat(type_, value):
    return {"name": type_, "type": type_, "value": value, "displayValue": str(value)}


def feed(records: dict[str, tuple[int, int, float, float]], season=2027, season_type=2) -> dict:
    """Build a feed shaped like ESPN's: league -> conference children -> standings.entries."""
    teams = load_teams()
    children = []
    for conf in ("East", "West"):
        entries = []
        for t in teams:
            if t.conference != conf:
                continue
            w, l, pf, pa = records.get(t.id, (0, 0, 0.0, 0.0))
            entries.append({
                "team": {"abbreviation": TO_ESPN.get(t.id, t.id), "displayName": t.name},
                "stats": [stat("wins", w), stat("losses", l), stat("avgpointsfor", pf), stat("avgpointsagainst", pa),
                          {"id": "0", "name": "overall", "type": "total", "summary": f"{w}-{l}"}],
            })
        children.append({"name": f"{conf}ern Conference", "standings": {"season": season, "seasonType": season_type, "entries": entries}})
    return {"name": "National Basketball Association", "children": children}


def test_parses_and_maps_espn_abbreviations():
    rows = {r.id: r for r in parse_standings(feed({"GSW": (3, 1, 118.5, 110.25), "OKC": (1, 3, 105.0, 112.0)}), KNOWN)}
    assert len(rows) == 30
    assert rows["GSW"] == StandingRow("GSW", 3, 1, 8.25)
    assert rows["OKC"].point_diff == -7.0
    assert rows["SAC"] == StandingRow("SAC", 0, 0, 0.0)


def test_unknown_team_is_rejected():
    payload = feed({})
    payload["children"][0]["standings"]["entries"][0]["team"]["abbreviation"] = "SEA"
    with pytest.raises(ValueError, match="Unknown team"):
        parse_standings(payload, KNOWN)


def test_missing_team_is_rejected():
    payload = feed({})
    payload["children"][1]["standings"]["entries"].pop()
    with pytest.raises(ValueError, match="missing"):
        parse_standings(payload, KNOWN)


def test_unbalanced_league_record_is_rejected():
    with pytest.raises(ValueError, match="don't match"):
        parse_standings(feed({"BOS": (2, 0, 110.0, 100.0)}), KNOWN)


def test_wrong_season_is_flagged():
    with pytest.raises(SeasonNotAvailable):
        parse_standings(feed({}, season=2026), KNOWN)


def test_early_season_blowout_is_accepted():
    # Raptors 135, Bulls 92 on Oct 21, 2026: a real 43-point game
    rows = {r.id: r for r in parse_standings(feed({"TOR": (1, 0, 135.0, 92.0), "CHI": (0, 1, 92.0, 135.0)}), KNOWN)}
    assert rows["CHI"].point_diff == -43.0


def test_implausible_margin_later_in_season_is_rejected():
    with pytest.raises(ValueError, match="not plausible"):
        parse_standings(feed({"TOR": (25, 0, 140.0, 100.0), "CHI": (0, 25, 100.0, 140.0)}), KNOWN)


def test_preseason_records_are_not_used():
    with pytest.raises(SeasonNotAvailable, match="preseason"):
        parse_standings(feed({"BOS": (4, 0, 115.0, 100.0)}, season_type=1), KNOWN)


def test_records_cannot_go_backwards():
    rows = parse_standings(feed({"BOS": (1, 0, 110.0, 100.0), "NYK": (0, 1, 100.0, 110.0)}), KNOWN)
    check_progress(rows, {"BOS": (1, 0)})
    with pytest.raises(ValueError, match="backwards"):
        check_progress(rows, {"BOS": (2, 0)})


def test_history_replaces_same_date(tmp_path):
    path = tmp_path / "history.csv"
    result = {"id": "BOS", "conference": "East", "record": "1-0", "proj_wins": 54.0, "avg_seed": 1.5,
              "top6": 0.9, "play_in": 0.08, "playoffs": 0.95}
    append_history(path, "2026-10-21", [result])
    append_history(path, "2026-10-22", [result])
    later = copy.deepcopy(result) | {"record": "2-0"}
    append_history(path, "2026-10-22", [later])
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 3   # header + one row per date
    assert lines[-1].startswith("2026-10-22,BOS,East,2-0")
