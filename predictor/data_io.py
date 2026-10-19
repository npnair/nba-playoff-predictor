"""Load and validate the CSV inputs in data/."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONFERENCES = ("West", "East")
GAMES = 82


@dataclass
class Team:
    id: str
    name: str
    conference: str
    win_total: float          # preseason over/under line
    last_w: int               # 2025-26 record
    last_l: int
    last_net_rating: float    # 2025-26 net rating, points per 100 possessions
    pre_w: int                # 2026 preseason (exhibition) record
    pre_l: int
    w: int = 0                # 2026-27 regular season so far
    l: int = 0
    point_diff: float = 0.0   # 2026-27 average point differential per game

    @property
    def games_played(self) -> int:
        return self.w + self.l

    @property
    def games_left(self) -> int:
        return GAMES - self.games_played


@dataclass
class Injury:
    team: str
    player: str
    impact: float    # net rating points the team loses while the player is out
    games_out: int   # remaining games the player is expected to miss


@dataclass
class Move:
    team: str
    move: str
    impact: float    # points added to (or taken from) last season's net rating


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if any(v.strip() for v in row.values())]


def load_teams(data_dir: Path = DATA_DIR) -> list[Team]:
    teams = [
        Team(
            id=r["id"].strip().upper(),
            name=r["name"].strip(),
            conference=r["conference"].strip(),
            win_total=float(r["win_total"]),
            last_w=int(r["last_w"]),
            last_l=int(r["last_l"]),
            last_net_rating=float(r["last_net_rating"]),
            pre_w=int(r["pre_w"]),
            pre_l=int(r["pre_l"]),
        )
        for r in _rows(data_dir / "teams.csv")
    ]
    by_id = {t.id: t for t in teams}
    standings = data_dir / "standings.csv"
    if standings.exists():
        for r in _rows(standings):
            team = by_id.get(r["id"].strip().upper())
            if team is None:
                raise ValueError(f"standings.csv: unknown team id {r['id']!r}")
            team.w = int(r["w"])
            team.l = int(r["l"])
            team.point_diff = float(r.get("point_diff") or 0)
    return teams


def load_injuries(data_dir: Path = DATA_DIR) -> list[Injury]:
    path = data_dir / "injuries.csv"
    if not path.exists():
        return []
    return [
        Injury(r["team"].strip().upper(), r["player"].strip(), float(r["impact"]), int(float(r["games_out"])))
        for r in _rows(path)
    ]


def load_moves(data_dir: Path = DATA_DIR) -> list[Move]:
    path = data_dir / "moves.csv"
    if not path.exists():
        return []
    return [Move(r["team"].strip().upper(), r["move"].strip(), float(r["impact"])) for r in _rows(path)]


def validate(teams: list[Team], injuries: list[Injury], moves: list[Move]) -> None:
    """Raise ValueError with a readable message if the inputs don't make sense."""
    ids = {t.id for t in teams}
    if len(teams) != 30 or len(ids) != 30:
        raise ValueError(f"Expected 30 unique teams, found {len(ids)}")
    for conf in CONFERENCES:
        n = sum(t.conference == conf for t in teams)
        if n != 15:
            raise ValueError(f"Expected 15 teams in the {conf}, found {n}")
    for t in teams:
        if t.games_played > GAMES or t.w < 0 or t.l < 0:
            raise ValueError(f"{t.id}: record {t.w}-{t.l} is not possible in an {GAMES}-game season")
    for item in [*injuries, *moves]:
        if item.team not in ids:
            raise ValueError(f"Unknown team id {item.team!r} in {type(item).__name__.lower()} list")
    for j in injuries:
        if j.impact < 0 or j.games_out < 0:
            raise ValueError(f"{j.player}: impact and games_out must be zero or more")


def load_all(data_dir: Path = DATA_DIR) -> tuple[list[Team], list[Injury], list[Move]]:
    teams, injuries, moves = load_teams(data_dir), load_injuries(data_dir), load_moves(data_dir)
    validate(teams, injuries, moves)
    return teams, injuries, moves
