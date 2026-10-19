"""Command line entry point: python -m predictor"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from .data_io import CONFERENCES, DATA_DIR, load_all
from .model import Settings, simulate


def pct(v: float) -> str:
    if v >= 0.995:
        return ">99%"
    if v <= 0.005:
        return "<1%" if v > 0 else "0%"
    return f"{round(v * 100)}%"


def print_conference(conf: str, rows: list[dict]) -> None:
    rows = sorted((r for r in rows if r["conference"] == conf), key=lambda r: r["avg_seed"])
    print(f"\n{conf.upper()}ERN CONFERENCE")
    print(f"{'#':>2}  {'Team':<24}{'Rec':>7}{'Rtg':>7}{'Moves':>7}{'Inj':>6}{'ProjW':>7}{'Top6':>7}{'PlayIn':>8}{'Playoffs':>10}")
    for k, r in enumerate(rows, start=1):
        if k in (7, 11):
            print("    " + "-" * 86)
        tag = {"clinched": " *", "eliminated": " x"}.get(r["status"], "")
        inj = f"-{r['injury_wins_lost']:.1f}" if r["injury_wins_lost"] >= 0.05 else "-"
        print(
            f"{k:>2}  {r['name'] + tag:<24}{r['record']:>7}{r['rating']:>+7.1f}{r['moves']:>+7.1f}{inj:>6}"
            f"{r['proj_wins']:>7.1f}{pct(r['top6']):>7}{pct(r['play_in']):>8}{pct(r['playoffs']):>10}"
        )
    print(f"\n  Predicted top 6: {', '.join(r['name'] for r in rows[:6])}")
    print(f"  Predicted play-in (7-10): {', '.join(r['name'] for r in rows[6:10])}")


def main() -> None:
    p = argparse.ArgumentParser(description="Simulate the 2026-27 NBA season and project playoff seeds.")
    p.add_argument("--sims", type=int, default=5000, help="number of simulated seasons (default 5000)")
    p.add_argument("--seed", type=int, default=None, help="random seed for repeatable results")
    p.add_argument("--weights", type=float, nargs=3, metavar=("LAST", "LINE", "PRESEASON"), default=(30, 60, 10),
                   help="relative weights for last season, win total line and preseason games (default 30 60 10)")
    p.add_argument("--evidence", type=float, default=25,
                   help="games of evidence the preseason rating counts for (default 25)")
    p.add_argument("--data-dir", type=Path, default=DATA_DIR, help="folder holding the CSV inputs")
    p.add_argument("--out", type=Path, default=None, help="optional CSV file for the full results")
    p.add_argument("--history", type=Path, default=None,
                   help="optional CSV to append one row per team for this run, to track odds over time")
    p.add_argument("--date", default=None, help="date to record in --history (default today, YYYY-MM-DD)")
    args = p.parse_args()

    teams, injuries, moves = load_all(args.data_dir)
    settings = Settings(*args.weights, evidence_games=args.evidence, sims=args.sims, seed=args.seed)
    results = simulate(teams, injuries, moves, settings)

    gp = sum(t.games_played for t in teams) / len(teams)
    print(f"Simulated {args.sims:,} seasons. Average games played: {gp:.1f}. "
          f"Weight on 2026-27 games: {gp / (gp + args.evidence):.0%}.")
    for conf in CONFERENCES:
        print_conference(conf, results)
    print("\n  * clinched a top-6 seed   x eliminated from the play-in   Rtg and Moves in points per game")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(results[0]))
            writer.writeheader()
            writer.writerows(sorted(results, key=lambda r: (r["conference"], r["avg_seed"])))
        print(f"\nWrote {args.out}")

    if args.history:
        append_history(args.history, args.date or date.today().isoformat(), results)
        print(f"Updated {args.history}")


HISTORY_FIELDS = ["date", "id", "conference", "record", "proj_wins", "avg_seed", "top6", "play_in", "playoffs"]


def append_history(path: Path, run_date: str, results: list[dict]) -> None:
    """Keep one row per team per date. Rerunning on the same date replaces that date's rows."""
    rows: list[dict] = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r["date"] != run_date]
    for r in sorted(results, key=lambda r: (r["conference"], r["avg_seed"])):
        rows.append({
            "date": run_date, "id": r["id"], "conference": r["conference"], "record": r["record"],
            "proj_wins": r["proj_wins"], "avg_seed": round(r["avg_seed"], 2),
            "top6": round(r["top6"], 4), "play_in": round(r["play_in"], 4), "playoffs": round(r["playoffs"], 4),
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
