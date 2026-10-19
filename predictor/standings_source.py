"""Fetch 2026-27 standings from ESPN's public standings feed and turn them into standings.csv rows.

The feed is unofficial (no documentation or uptime guarantee), so everything here validates
aggressively and raises instead of writing questionable data.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

ESPN_URL = "https://site.api.espn.com/apis/v2/sports/basketball/nba/standings?season={season}&seasontype=2"
REGULAR_SEASON = 2  # ESPN season types: 1 = preseason, 2 = regular season, 3 = playoffs
SEASON = 2027  # ESPN labels a season by the year it ends: 2027 = the 2026-27 season

# ESPN abbreviations that differ from the ones used in data/teams.csv
ESPN_ALIASES = {
    "GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA",
    "WSH": "WAS", "PHO": "PHX", "BRK": "BKN", "CHO": "CHA",
}


class SeasonNotAvailable(ValueError):
    """The feed is still serving a different season (for example, before opening night)."""


@dataclass
class StandingRow:
    id: str
    w: int
    l: int
    point_diff: float  # average point differential per game


def fetch_json(season: int = SEASON, retries: int = 3, timeout: float = 20) -> dict:
    url = ESPN_URL.format(season=season)
    request = urllib.request.Request(url, headers={"User-Agent": "nba-playoff-predictor (github.com/npnair)"})
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Could not fetch standings from {url}: {last_error}")


def _stat(stats: list[dict], *keys: str) -> float | None:
    """Find a stat by its `type` or `name`, ignoring case."""
    wanted = {k.lower() for k in keys}
    for s in stats:
        if str(s.get("type", "")).lower() in wanted or str(s.get("name", "")).lower() in wanted:
            value = s.get("value")
            if value is not None:
                return float(value)
    return None


def _entries(payload: dict) -> list[dict]:
    """Collect team entries from the conference blocks (or a flat league table)."""
    blocks = payload.get("children") or [payload]
    entries: list[dict] = []
    for block in blocks:
        entries.extend((block.get("standings") or {}).get("entries", []))
    return entries


def reported_season(payload: dict) -> int | None:
    for block in payload.get("children") or [payload]:
        season = (block.get("standings") or {}).get("season")
        if season is not None:
            return int(season)
    return None


def reported_season_type(payload: dict) -> int | None:
    for block in payload.get("children") or [payload]:
        season_type = (block.get("standings") or {}).get("seasonType")
        if season_type is not None:
            return int(season_type)
    return None


def parse_standings(payload: dict, known_ids: set[str], season: int = SEASON) -> list[StandingRow]:
    """Convert ESPN's JSON into one row per team. Raises ValueError if anything looks wrong."""
    got_season = reported_season(payload)
    if got_season is not None and got_season != season:
        raise SeasonNotAvailable(f"Feed returned season {got_season}, expected {season}")
    got_type = reported_season_type(payload)
    if got_type is not None and got_type != REGULAR_SEASON:
        raise SeasonNotAvailable(f"Feed returned season type {got_type} (1 = preseason), expected regular season")

    entries = _entries(payload)
    if not entries:
        raise SeasonNotAvailable("Feed has no team entries yet")
    if all(_stat(e.get("stats") or [], "wins") is None for e in entries):
        raise SeasonNotAvailable("Feed lists teams but no records yet")

    rows: dict[str, StandingRow] = {}
    for entry in entries:
        team = entry.get("team") or {}
        raw_id = str(team.get("abbreviation", "")).upper()
        team_id = ESPN_ALIASES.get(raw_id, raw_id)
        if team_id not in known_ids:
            raise ValueError(f"Unknown team abbreviation {raw_id!r} ({team.get('displayName')})")

        stats = entry.get("stats") or []
        w = _stat(stats, "wins")
        l = _stat(stats, "losses")
        if w is None or l is None:
            raise ValueError(f"{team_id}: wins or losses missing from feed")
        games = int(w) + int(l)

        pf = _stat(stats, "avgpointsfor", "avgPointsFor")
        pa = _stat(stats, "avgpointsagainst", "avgPointsAgainst")
        if pf is not None and pa is not None:
            diff = pf - pa
        else:
            total = _stat(stats, "pointdifferential")
            diff = total / games if (total is not None and games) else 0.0
        if not games:
            diff = 0.0

        rows[team_id] = StandingRow(team_id, int(w), int(l), round(diff, 2))

    missing = known_ids - rows.keys()
    if missing:
        raise ValueError(f"Feed is missing {len(missing)} teams: {', '.join(sorted(missing))}")
    for r in rows.values():
        if r.w + r.l > 82 or r.w < 0 or r.l < 0:
            raise ValueError(f"{r.id}: impossible record {r.w}-{r.l}")
        if abs(r.point_diff) > 40:
            raise ValueError(f"{r.id}: point differential {r.point_diff} per game is not plausible")
    total_w, total_l = sum(r.w for r in rows.values()), sum(r.l for r in rows.values())
    if total_w != total_l:
        raise ValueError(f"League wins ({total_w}) and losses ({total_l}) don't match")
    return [rows[k] for k in sorted(rows)]


def check_progress(new: list[StandingRow], old: dict[str, tuple[int, int]]) -> None:
    """Games played can only go up during a season; anything else means the feed is wrong."""
    for r in new:
        if r.id in old:
            ow, ol = old[r.id]
            if r.w < ow or r.l < ol:
                raise ValueError(f"{r.id}: record went backwards from {ow}-{ol} to {r.w}-{r.l}")
