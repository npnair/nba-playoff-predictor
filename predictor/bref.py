"""Basketball-Reference pages used by the playoff bracket: the season schedule (results of every
game), player advanced stats and current team rosters.

Basketball-Reference allows about 20 requests a minute, so requests are spaced out.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request

from lxml import html

BASE = "https://www.basketball-reference.com"
USER_AGENT = "nba-playoff-predictor/1.0 (https://github.com/npnair/nba-playoff-predictor)"
MONTHS = ["october", "november", "december", "january", "february", "march", "april"]
# Basketball-Reference team codes that differ from the ones in data/teams.csv
CODE_MAP = {"BRK": "BKN", "CHO": "CHA", "PHO": "PHX"}
BREF_CODES = ["ATL", "BOS", "BRK", "CHI", "CHO", "CLE", "DAL", "DEN", "DET", "GSW", "HOU", "IND", "LAC",
              "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK", "OKC", "ORL", "PHI", "PHO", "POR", "SAC",
              "SAS", "TOR", "UTA", "WAS"]
TEAM_LINK = re.compile(r"/teams/([A-Z]{3})/\d{4}\.html")
PLAYER_LINK = re.compile(r"/players/\w/([a-z0-9]+)\.html")


class FetchError(RuntimeError):
    pass


class NotFound(FetchError):
    pass


def get(path: str, retries: int = 3, timeout: float = 30) -> bytes:
    request = urllib.request.Request(BASE + path, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(path) from e
            last = e
            time.sleep(10 * (attempt + 1) * (3 if e.code == 429 else 1))
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(5 * (attempt + 1))
    raise FetchError(f"{path}: {last}")


def team_code(href: str) -> str | None:
    m = TEAM_LINK.search(href or "")
    return CODE_MAP.get(m.group(1), m.group(1)) if m else None


def _cell(row, *stats: str):
    for stat in stats:
        found = row.xpath(f"./*[@data-stat='{stat}']")
        if found:
            return found[0]
    return None


def _number(cell) -> float | None:
    text = cell.text_content().strip() if cell is not None else ""
    try:
        return float(text) if text else None
    except ValueError:
        return None


# ------------------------------------------------------------------ schedule
def parse_schedule(page: str | bytes) -> list[dict]:
    """Every game on a monthly schedule page. Future games have no scores."""
    tree = html.fromstring(page)
    tables = tree.xpath("//table[@id='schedule']")
    if not tables:
        raise ValueError("Schedule table not found")
    games = []
    for row in tables[0].xpath(".//tbody/tr[not(contains(@class,'thead'))]"):
        date_cell = _cell(row, "date_game")
        away_cell, home_cell = _cell(row, "visitor_team_name"), _cell(row, "home_team_name")
        if date_cell is None or away_cell is None or home_cell is None:
            continue
        away = team_code(next(iter(away_cell.xpath(".//a/@href")), ""))
        home = team_code(next(iter(home_cell.xpath(".//a/@href")), ""))
        if not away or not home:
            continue
        a_pts, h_pts = _number(_cell(row, "visitor_pts")), _number(_cell(row, "home_pts"))
        csk = date_cell.get("csk") or ""                     # e.g. 202610200DET
        date = f"{csk[:4]}-{csk[4:6]}-{csk[6:8]}" if len(csk) >= 8 and csk[:8].isdigit() else date_cell.text_content().strip()
        games.append({"date": date, "home": home, "away": away,
                      "home_pts": None if h_pts is None else int(h_pts),
                      "away_pts": None if a_pts is None else int(a_pts)})
    return games


def fetch_schedule(season: int, pause: float = 3.5) -> list[dict]:
    games = []
    for month in MONTHS:
        try:
            games.extend(parse_schedule(get(f"/leagues/NBA_{season}_games-{month}.html")))
        except NotFound:
            continue                                         # month not published yet
        time.sleep(pause)
    if not games:
        raise ValueError("No games found on any schedule page")
    return games


# ------------------------------------------------------------------ player advanced stats
def parse_advanced(page: str | bytes) -> list[dict]:
    """One row per player. Traded players keep their combined row."""
    tree = html.fromstring(page)
    tables = tree.xpath("//table[@id='advanced' or @id='advanced_stats']")
    if not tables:
        raise ValueError("Advanced stats table not found")
    players: dict[str, dict] = {}
    for row in tables[0].xpath(".//tbody/tr[not(contains(@class,'thead'))]"):
        name = _cell(row, "name_display", "player")
        if name is None:
            continue
        pid = name.get("data-append-csv")
        if not pid:
            m = PLAYER_LINK.search(next(iter(name.xpath(".//a/@href")), ""))
            pid = m.group(1) if m else None
        if not pid or pid in players:                         # first row is the season total
            continue
        pos_cell = _cell(row, "pos")
        players[pid] = {
            "bref_id": pid,
            "player": name.text_content().strip().rstrip("*"),
            "pos": pos_cell.text_content().strip() if pos_cell is not None else "",
            "g": _number(_cell(row, "games", "g")) or 0,
            "mp": _number(_cell(row, "mp")) or 0,
            "bpm": _number(_cell(row, "bpm")),
        }
    return list(players.values())


def fetch_advanced(season: int) -> list[dict] | None:
    """None before the season's first game."""
    try:
        page = get(f"/leagues/NBA_{season}_advanced.html")
    except NotFound:
        return None
    if b'id="advanced' not in page:
        return None
    return parse_advanced(page)


# ------------------------------------------------------------------ rosters
def parse_roster(page: str | bytes) -> list[dict]:
    tree = html.fromstring(page)
    tables = tree.xpath("//table[@id='roster']")
    if not tables:
        raise ValueError("Roster table not found")
    players = {}
    for link in tables[0].xpath(".//tbody//a[starts-with(@href, '/players/')]"):
        m = PLAYER_LINK.search(link.get("href", ""))
        if m:
            players.setdefault(m.group(1), link.text_content().strip())
    if not players:
        raise ValueError("Roster table has no players")
    return [{"bref_id": pid, "player": name} for pid, name in players.items()]


def fetch_rosters(season: int, pause: float = 3.5) -> tuple[dict[str, list[dict]], list[str]]:
    """Returns ({team: [players]}, teams that failed)."""
    rosters, failed = {}, []
    for code in BREF_CODES:
        try:
            rosters[CODE_MAP.get(code, code)] = parse_roster(get(f"/teams/{code}/{season}.html"))
        except (FetchError, ValueError):
            failed.append(CODE_MAP.get(code, code))
        time.sleep(pause)
    return rosters, failed
