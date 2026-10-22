"""Daily pipeline step: pull the latest standings, refresh projections and the web page's data.

    python scripts/update_standings.py            # fetch from ESPN
    python scripts/update_standings.py --file x.json   # use a saved copy of the feed (for testing)

Exit codes: 0 = updated or nothing new, 1 = feed failed validation (nothing is written).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from predictor.data_io import DATA_DIR, load_teams  # noqa: E402
from predictor.standings_source import (  # noqa: E402
    SEASON, SeasonNotAvailable, _entries, check_progress, fetch_json, parse_standings, reported_season,
    reported_season_type,
)

STANDINGS = DATA_DIR / "standings.csv"
GAMES_CSV = DATA_DIR / "games.csv"
PLAYERS_CSV = DATA_DIR / "players.csv"
PREV_STATS_CSV = DATA_DIR / "player_stats_2026.csv"
GAME_FIELDS = ["date", "home", "away", "home_pts", "away_pts"]
PLAYER_FIELDS = ["team", "bref_id", "player", "pos", "mpg", "bpm", "minutes"]
STAT_FIELDS = ["bref_id", "player", "pos", "g", "mp", "bpm"]


def read_existing() -> dict[str, tuple[int, int, float]]:
    if not STANDINGS.exists():
        return {}
    with open(STANDINGS, newline="", encoding="utf-8") as f:
        return {r["id"]: (int(r["w"]), int(r["l"]), float(r["point_diff"] or 0)) for r in csv.DictReader(f)}


def report(message: str, error: bool = True) -> None:
    """Print, and also surface the message as an annotation when running in GitHub Actions."""
    print(message, file=sys.stderr if error else sys.stdout)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::{'error' if error else 'notice'}::{message}")


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def refresh_playoff_inputs(season: int) -> bool:
    """Schedule results, player stats and rosters for the bracket. Each part keeps its last good file
    if Basketball-Reference fails, so a bad day never blocks the standings update. Returns True if
    anything changed."""
    from predictor import bref
    from predictor.playoffs import build_player_table

    before = {p: p.read_bytes() if p.exists() else b"" for p in (GAMES_CSV, PLAYERS_CSV)}
    try:
        games = sorted(bref.fetch_schedule(season), key=lambda g: (g["date"], g["home"]))
        _write(GAMES_CSV, GAME_FIELDS, games)
        report(f"Schedule: {sum(g['home_pts'] is not None for g in games)} games played of {len(games)}", error=False)
    except Exception as e:  # noqa: BLE001
        report(f"Schedule update failed, keeping the saved schedule: {e}")

    try:
        if not PREV_STATS_CSV.exists():                     # last season never changes; fetch it once
            _write(PREV_STATS_CSV, STAT_FIELDS, bref.fetch_advanced(season - 1) or [])
        prev = [{**r, "g": float(r["g"]), "mp": float(r["mp"]),
                 "bpm": float(r["bpm"]) if r["bpm"] not in ("", None) else None} for r in _read(PREV_STATS_CSV)]
        cur = bref.fetch_advanced(season)
        rosters, failed = bref.fetch_rosters(season)
        if len(rosters) < 25:
            raise RuntimeError(f"only {len(rosters)} team rosters loaded")
        rows = build_player_table(rosters, cur, prev)
        if failed:                                          # keep yesterday's players for teams that failed
            rows += [r for r in _read(PLAYERS_CSV) if r["team"] in failed]
            report(f"Rosters failed for {', '.join(failed)}; kept their saved players")
        _write(PLAYERS_CSV, PLAYER_FIELDS, sorted(rows, key=lambda r: (r["team"], -float(r["mpg"]))))
        report(f"Players: {len(rows)} rostered players with stats", error=False)
    except Exception as e:  # noqa: BLE001
        report(f"Player update failed, keeping saved players: {e}")

    return any((p.read_bytes() if p.exists() else b"") != before[p] for p in before)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", type=Path, help="read the ESPN JSON from a file instead of the network")
    p.add_argument("--season", type=int, default=SEASON)
    p.add_argument("--force", action="store_true", help="rebuild outputs even if standings didn't change")
    p.add_argument("--skip-playoff-inputs", action="store_true",
                   help="don't refresh the schedule, player stats and rosters used by the bracket")
    args = p.parse_args()

    known = {t.id for t in load_teams()}
    try:
        payload = json.loads(args.file.read_text()) if args.file else fetch_json(args.season)
    except (RuntimeError, OSError, json.JSONDecodeError) as e:
        report(f"Could not download standings, nothing written: {e}")
        return 1
    report(f"Feed reports season {reported_season(payload)}, type {reported_season_type(payload)}, "
           f"with {len(_entries(payload))} team entries", error=False)
    old = read_existing()
    try:
        rows = parse_standings(payload, known, args.season)
        check_progress(rows, {k: (v[0], v[1]) for k, v in old.items()})
    except SeasonNotAvailable as e:
        if all(w + l == 0 for w, l, _ in old.values()):
            report(f"The {args.season - 1}-{str(args.season)[2:]} season isn't in the feed yet ({e}). Nothing to do.",
                   error=False)
            return 0
        report(f"Standings feed stopped serving this season mid-year, nothing written: {e}")
        return 1
    except ValueError as e:
        report(f"Standings feed failed validation, nothing written: {e}")
        return 1
    except (KeyError, TypeError, AttributeError) as e:
        report(f"Standings feed has an unexpected format, nothing written: {e!r}")
        return 1

    new = {r.id: (r.w, r.l, r.point_diff) for r in rows}
    inputs_changed = False if args.skip_playoff_inputs or args.file else refresh_playoff_inputs(args.season)
    if new == old and not inputs_changed and not args.force:
        print("No new games since the last update.")
        return 0

    with open(STANDINGS, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "w", "l", "point_diff"])
        for r in rows:
            writer.writerow([r.id, r.w, r.l, r.point_diff])
    games = sum(r.w + r.l for r in rows) // 2
    print(f"Wrote {STANDINGS.relative_to(ROOT)}: {games} games played league-wide")

    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    # NBA games end by about 10 PM Pacific, so any earlier run reflects the previous night's games
    today = (now - timedelta(days=1)).date() if now.hour < 22 else now.date()
    label = f"Standings through {today:%b} {today.day}, {today.year}"
    py = sys.executable
    subprocess.run([py, str(ROOT / "scripts" / "build_web_data.py"), "--as-of", label], check=True, cwd=ROOT)
    subprocess.run(
        [py, "-m", "predictor", "--sims", "10000", "--seed", str(int(today.strftime("%Y%m%d"))),
         "--out", "results/projections.csv", "--history", "results/history.csv", "--date", today.isoformat()],
        check=True, cwd=ROOT,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
