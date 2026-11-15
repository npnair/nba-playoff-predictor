"""Game-by-game score predictions for the coming week, and a record of how they turned out.

Each game's expected margin comes straight from the standings model: both teams' ratings
(healthy rating minus whoever is still out for that game) plus home court. The expected total
comes from how many points each team's games have produced this season, shrunk toward the
league average early on. Margin and total together give a predicted score.

The daily update saves every prediction for the next seven days to results/game_predictions.csv.
A game's saved prediction stops changing once its day arrives, so after it is played it can be
graded fairly against the final score.

    python -m predictor.weekly                 # print this week's predictions
    python -m predictor.weekly --save results/game_predictions.csv
"""

from __future__ import annotations

import argparse
import csv
import math
from datetime import date, timedelta
from pathlib import Path

from .data_io import load_all, load_games, load_players
from .model import HOME_COURT, Settings, injury_penalties, team_ratings, win_prob

WEEK_DAYS = 7
SCORING_PRIOR_GAMES = 10   # a team's scoring pace counts half once it has played this many games
PRED_FIELDS = ["date", "home", "away", "pred_home", "pred_away", "home_win_prob"]


def half_up(x: float) -> int:
    """Round like JavaScript's Math.round, so the web page and this file agree."""
    return math.floor(x + 0.5)


def played(g: dict) -> bool:
    return g.get("home_pts") is not None and g.get("away_pts") is not None


def next_game_day(games: list[dict]) -> str | None:
    """First date after the most recent played game that still has games to play."""
    last = max((g["date"] for g in games if played(g)), default="")
    upcoming = [g["date"] for g in games if not played(g) and g["date"] > last]
    return min(upcoming) if upcoming else None


def week_end(start: str, days: int = WEEK_DAYS) -> str:
    return (date.fromisoformat(start) + timedelta(days=days - 1)).isoformat()


def team_scoring(games: list[dict]) -> tuple[float, dict[str, float]]:
    """(league points per team per game, {team: shrunk extra combined points in its games})."""
    done = [g for g in games if played(g)]
    if not done:
        return 112.0, {}
    league = sum(g["home_pts"] + g["away_pts"] for g in done) / (2 * len(done))
    totals: dict[str, list[int]] = {}
    for g in done:
        for t in (g["home"], g["away"]):
            totals.setdefault(t, []).append(g["home_pts"] + g["away_pts"])
    extra = {t: (sum(v) / len(v) - 2 * league) * len(v) / (len(v) + SCORING_PRIOR_GAMES) for t, v in totals.items()}
    return league, extra


def remaining_index(games: list[dict]) -> dict[tuple[str, str, str], tuple[int, int]]:
    """For each unplayed game, its position in the home and away teams' remaining schedules."""
    seen: dict[str, int] = {}
    out = {}
    for g in sorted((g for g in games if not played(g)), key=lambda g: (g["date"], g["home"])):
        h, a = seen.get(g["home"], 0), seen.get(g["away"], 0)
        out[(g["date"], g["home"], g["away"])] = (h, a)
        seen[g["home"]], seen[g["away"]] = h + 1, a + 1
    return out


def predict_games(ratings: dict[str, float], pen: dict[str, list[float]], games: list[dict],
                  start: str, days: int = WEEK_DAYS) -> list[dict]:
    """Predicted score for every unplayed game from `start` through the following `days` days.

    ratings: healthy rating per team. pen[team][g]: points missing in its g-th remaining game.
    """
    end = week_end(start, days)
    league, extra = team_scoring(games)
    idx = remaining_index(games)
    out = []
    for g in sorted(games, key=lambda g: (g["date"], g["home"])):
        if played(g) or not (start <= g["date"] <= end):
            continue
        h, a = g["home"], g["away"]
        gh, ga = idx[(g["date"], h, a)]
        eh = ratings[h] - pen[h][min(gh, len(pen[h]) - 1)]
        ea = ratings[a] - pen[a][min(ga, len(pen[a]) - 1)]
        margin = eh - ea + HOME_COURT
        total = 2 * league + (extra.get(h, 0.0) + extra.get(a, 0.0)) / 2
        ph, pa = half_up(total / 2 + margin / 2), half_up(total / 2 - margin / 2)
        prob = float(win_prob(eh, ea, True))
        if ph == pa:                     # no ties in basketball: the favorite gets the extra point
            ph, pa = (ph + 1, pa) if prob >= 0.5 else (ph, pa + 1)
        out.append({"date": g["date"], "home": h, "away": a, "pred_home": ph, "pred_away": pa,
                    "home_win_prob": half_up(prob * 1000) / 1000, "margin": margin, "total": total})
    return out


def model_inputs(settings: Settings | None = None):
    """Healthy ratings and injury penalties by team, from the data folder."""
    s = settings or Settings()
    teams, injuries, moves = load_all()
    players = load_players()
    rating, _, _ = team_ratings(teams, moves, s, injuries, players)
    pens = injury_penalties(teams, injuries)
    return ({t.id: float(rating[i]) for i, t in enumerate(teams)},
            {t.id: [float(x) for x in pens[i]] for i, t in enumerate(teams)})


def grade(preds: list[dict], games: list[dict]) -> list[dict]:
    """Saved predictions joined to final scores, for games that have been played."""
    finals = {(g["date"], g["home"], g["away"]): g for g in games if played(g)}
    out = []
    for p in preds:
        g = finals.get((p["date"], p["home"], p["away"]))
        if not g:
            continue
        ph, pa = int(p["pred_home"]), int(p["pred_away"])
        out.append({**p, "home_pts": g["home_pts"], "away_pts": g["away_pts"],
                    "correct": (ph > pa) == (g["home_pts"] > g["away_pts"]),
                    "points_off": (abs(ph - g["home_pts"]) + abs(pa - g["away_pts"])) / 2})
    return out


def read_predictions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)]


def save_predictions(path: Path, preds: list[dict], start: str) -> int:
    """Keep saved rows for days before `start` (their prediction is final), replace the rest."""
    kept = [r for r in read_predictions(path) if r["date"] < start]
    rows = kept + [{k: p[k] for k in PRED_FIELDS} for p in preds]
    rows.sort(key=lambda r: (r["date"], r["home"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PRED_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--save", type=Path, help="CSV that keeps every prediction for grading")
    args = p.parse_args()

    games = load_games()
    start = next_game_day(games)
    if not start:
        print("No games left to predict.")
        return
    ratings, pen = model_inputs()
    preds = predict_games(ratings, pen, games, start)
    for g in preds:
        fav = g["home"] if g["home_win_prob"] >= 0.5 else g["away"]
        pct = max(g["home_win_prob"], 1 - g["home_win_prob"])
        print(f"{g['date']}  {g['away']:>3} {g['pred_away']:>3} @ {g['home']:<3} {g['pred_home']:>3}   {fav} {pct:.0%}")
    if args.save:
        n = save_predictions(args.save, preds, start)
        graded = grade(read_predictions(args.save), games)
        right = sum(g["correct"] for g in graded)
        print(f"\nSaved {len(preds)} predictions from {start} ({n} total). Graded so far: {right}-{len(graded) - right}")


if __name__ == "__main__":
    main()
