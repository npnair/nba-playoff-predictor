"""Parsers for the Basketball-Reference pages, tested on samples shaped like the real pages."""

import pytest

from predictor import bref

SCHEDULE = """<html><body><table id="schedule"><thead><tr><th>Date</th></tr></thead><tbody>
<tr><th data-stat="date_game" csk="202610200DET"><a>Tue, Oct 20, 2026</a></th>
  <td data-stat="visitor_team_name"><a href="/teams/BOS/2027.html">Boston Celtics</a></td><td data-stat="visitor_pts">113</td>
  <td data-stat="home_team_name"><a href="/teams/DET/2027.html">Detroit Pistons</a></td><td data-stat="home_pts">121</td></tr>
<tr class="thead"><th>Date</th></tr>
<tr><th data-stat="date_game" csk="202610220BRK"><a>Thu, Oct 22, 2026</a></th>
  <td data-stat="visitor_team_name"><a href="/teams/PHO/2027.html">Phoenix Suns</a></td><td data-stat="visitor_pts"></td>
  <td data-stat="home_team_name"><a href="/teams/BRK/2027.html">Brooklyn Nets</a></td><td data-stat="home_pts"></td></tr>
</tbody></table></body></html>"""


def test_schedule_parses_results_and_future_games():
    games = bref.parse_schedule(SCHEDULE)
    assert games == [
        {"date": "2026-10-20", "home": "DET", "away": "BOS", "home_pts": 121, "away_pts": 113},
        {"date": "2026-10-22", "home": "BKN", "away": "PHX", "home_pts": None, "away_pts": None},
    ]


def test_schedule_without_table_is_an_error():
    with pytest.raises(ValueError):
        bref.parse_schedule("<html><body>nothing</body></html>")


def test_advanced_keeps_season_total_for_traded_players():
    page = """<table id="advanced"><tbody>
    <tr><td data-stat="name_display" data-append-csv="hardeja01"><a href="/players/h/hardeja01.html">James Harden</a></td>
        <td data-stat="team_name_abbr">2TM</td><td data-stat="pos">PG</td><td data-stat="games">60</td>
        <td data-stat="mp">2000</td><td data-stat="bpm">4.1</td></tr>
    <tr><td data-stat="name_display" data-append-csv="hardeja01"><a href="/players/h/hardeja01.html">James Harden</a></td>
        <td data-stat="team_name_abbr">LAC</td><td data-stat="pos">PG</td><td data-stat="games">40</td>
        <td data-stat="mp">1400</td><td data-stat="bpm">3.0</td></tr>
    </tbody></table>"""
    rows = bref.parse_advanced(page)
    assert rows == [{"bref_id": "hardeja01", "player": "James Harden", "pos": "PG", "g": 60, "mp": 2000, "bpm": 4.1}]


def test_roster_parse():
    page = ('<table id="roster"><tbody>'
            '<tr><td data-stat="player"><a href="/players/j/jamesle01.html">LeBron James</a></td>'
            '<td><a href="/friv/colleges.fcgi?college=none">None</a></td></tr></tbody></table>')
    assert bref.parse_roster(page) == [{"bref_id": "jamesle01", "player": "LeBron James"}]
