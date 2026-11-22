"""Tune the model's settings against the games it has already predicted.

Every played game is replayed in date order: the model rates each team using only the games before
that day, predicts the game, and is scored on the real result. That's done for every combination of
the settings below, and the combination whose win probabilities were closest to the real results
(lowest log loss) becomes the model's settings for the next day's standings, weekly picks and
bracket. The search is redone every morning, so the model keeps adjusting as the season goes on.

What it can learn:
  home_court       points the home team gets
  logistic_scale   how spread out single-game results are (sets how confident the picks are)
  last_share       how the preseason rating splits between last season and the win total line
  evidence_games   how many games the preseason rating counts for (how fast it trusts this season)
  form_half_life   how fast older games fade, so recent form counts more (0 = all games equal)

Why not just bump a team up after a miss? The season's point differential already moves every
team's rating after each game, so a separate "fix the miss" bonus would count that game twice.
Tuning the settings is what actually changes how the model reacts to what it got wrong.

The check is honest: `walk_forward` picks settings using only earlier days and grades them on the
next day, the same way they're used for real, and compares that with the fixed defaults.

Limits: the replay doesn't know which players were injured on each past date, and it uses today's
roster stats throughout.

    python -m predictor.learn              # print what it would learn
    python -m predictor.learn --write      # save data/learned.json
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

from .data_io import DATA_DIR, Team, load_all, load_games, load_players
from .model import DEFAULTS, components, form_adjustment, observed_margin, roster_ratings, roster_share

GRID = {
    "last_share": (0.5, 0.6, 0.75, 0.9),
    "evidence_games": (5, 7, 10, 14, 20, 25, 30),
    "home_court": (1.0, 1.5, 2.0, 2.5, 3.0, 3.5),
    "form_half_life": (0, 6, 10, 15, 20, 30),
    "logistic_scale": (6.0, 6.5, 7.0, 7.5, 8.0),
}
MIN_GAMES = 100          # below this, keep the defaults: too few games to tell settings apart
CHECK_FROM_GAMES = 60    # the honest check starts once this many games are available to learn from
NUDGE = 0.003            # pull toward the defaults: stops one-week flukes from swinging the settings


def _played(games: list[dict]) -> list[dict]:
    return sorted((g for g in games if g["home_pts"] is not None and g["away_pts"] is not None),
                  key=lambda g: (g["date"], g["home"]))


def as_of(teams: list[Team], games: list[dict]) -> list[Team]:
    """Each team's record and average margin from these games only."""
    rec = {t.id: [0, 0, 0.0] for t in teams}
    for g in games:
        m = g["home_pts"] - g["away_pts"]
        for team, mm in ((g["home"], m), (g["away"], -m)):
            r = rec[team]
            r[0 if mm > 0 else 1] += 1
            r[2] += mm
    return [replace(t, w=rec[t.id][0], l=rec[t.id][1],
                    point_diff=rec[t.id][2] / max(1, rec[t.id][0] + rec[t.id][1])) for t in teams]


class Replay:
    """Everything the grid search needs, computed once: per game day, each team's record and
    observed margin as of that morning, for every half-life."""

    def __init__(self, teams, moves, players, games):
        self.teams, self.games = teams, _played(games)
        self.days = sorted({g["date"] for g in self.games})
        ix = {t.id: i for i, t in enumerate(teams)}
        day_ix = {d: k for k, d in enumerate(self.days)}
        self.g_day = np.array([day_ix[g["date"]] for g in self.games])
        self.g_home = np.array([ix[g["home"]] for g in self.games])
        self.g_away = np.array([ix[g["away"]] for g in self.games])
        self.g_margin = np.array([g["home_pts"] - g["away_pts"] for g in self.games], dtype=float)
        self.comp = components(teams, moves)
        self.roster = roster_ratings(teams, players)
        self.rs = roster_share(players) if self.roster is not None else 0.0

        D, n = len(self.days), len(teams)
        self.gp = np.zeros((D, n))
        self.obs = {h: np.zeros((D, n)) for h in GRID["form_half_life"]}
        for k, d in enumerate(self.days):
            before = [g for g in self.games if g["date"] < d]
            snap = as_of(teams, before)
            self.gp[k] = [t.games_played for t in snap]
            base = np.array([observed_margin(t) for t in snap])
            for h in self.obs:
                self.obs[h][k] = base + np.where(self.gp[k] > 0, form_adjustment(snap, before, h), 0.0)

    def ratings(self, last_share, evidence_games, form_half_life) -> np.ndarray:
        """Day x team healthy ratings, the same math as model.team_ratings with no injuries."""
        raw = last_share * self.comp["last"] + (1 - last_share) * self.comp["line"]
        prior = raw - raw.mean()
        k, gp = evidence_games, self.gp
        r = (prior * k + self.obs[form_half_life] * gp) / (k + gp)
        if self.roster is not None:
            r = (1 - self.rs) * r + self.rs * self.roster
        return r

    def score_all(self):
        """For every setting combination: (names, per-game log loss, per-game pick right, per-game margin error)."""
        combos, losses, rights, errs = [], [], [], []
        hc = np.array(GRID["home_court"])[:, None]
        for ls, k, h in itertools.product(GRID["last_share"], GRID["evidence_games"], GRID["form_half_life"]):
            r = self.ratings(ls, k, h)
            gap = r[self.g_day, self.g_home] - r[self.g_day, self.g_away]
            margin = gap[None, :] + hc                                   # home court x game
            won = self.g_margin > 0
            err = (self.g_margin[None, :] - margin) ** 2
            right = (margin > 0) == won[None, :]
            for j, hca in enumerate(GRID["home_court"]):
                for sc in GRID["logistic_scale"]:
                    p = 1 / (1 + np.exp(-margin[j] / sc))
                    combos.append({"last_share": ls, "evidence_games": k, "home_court": hca,
                                   "form_half_life": h, "logistic_scale": sc})
                    losses.append(-np.log(np.where(won, p, 1 - p)))
                    rights.append(right[j])
                    errs.append(err[j])
        return combos, np.array(losses), np.array(rights), np.array(errs)


def nudge(c: dict) -> float:
    """Distance from the defaults, so a near-tie keeps the familiar setting."""
    d = DEFAULTS
    return NUDGE * ((c["last_share"] - d["last_share"]) ** 2 / 0.0625
                    + np.log(c["evidence_games"] / d["evidence_games"]) ** 2
                    + (c["home_court"] - d["home_court"]) ** 2
                    + (c["form_half_life"] != d["form_half_life"]) * 0.5
                    + (c["logistic_scale"] - d["logistic_scale"]) ** 2 / 4)


def learn(teams, moves, players, games) -> dict:
    rp = Replay(teams, moves, players, games)
    n = len(rp.games)
    combos, loss, right, err = rp.score_all()
    pen = np.array([nudge(c) for c in combos])
    default_i = next(i for i, c in enumerate(combos) if all(c[k] == DEFAULTS[k] for k in DEFAULTS))

    def pick(mask) -> int:
        return int(np.argmin(loss[:, mask].mean(axis=1) + pen)) if mask.any() else default_i

    best = pick(np.ones(n, bool)) if n >= MIN_GAMES else default_i

    # The honest check: each day, settings tuned on earlier days only, graded on that day.
    chosen = np.full(n, default_i)
    for k in range(len(rp.days)):
        train = rp.g_day < k
        if train.sum() >= CHECK_FROM_GAMES:
            chosen[rp.g_day == k] = pick(train)
    first = next((i for i in range(n) if (rp.g_day < rp.g_day[i]).sum() >= CHECK_FROM_GAMES), n)
    tested = np.arange(n) >= first

    def summary(idx: np.ndarray) -> dict:
        m = tested.sum()
        if not m:
            return {"right": 0, "games": 0, "pct": None, "margin_off": None, "log_loss": None}
        g = np.arange(n)[tested]
        return {"right": int(right[idx[tested], g].sum()), "games": int(m),
                "pct": round(float(right[idx[tested], g].mean()), 4),
                "margin_off": round(float(np.sqrt(err[idx[tested], g].mean())), 2),
                "log_loss": round(float(loss[idx[tested], g].mean()), 4)}

    return {
        "updated": date.today().isoformat(),
        "games": n,
        "settings": combos[best],
        "defaults": dict(DEFAULTS),
        "check": {"since": rp.games[first]["date"] if first < n else None,
                  "learned": summary(chosen), "fixed": summary(np.full(n, default_i))},
    }


def describe(result: dict) -> str:
    s, d, c = result["settings"], result["defaults"], result["check"]
    lines = [f"Learned from {result['games']} games:"]
    names = {"home_court": "Home court (pts)", "logistic_scale": "Game spread", "last_share": "Last season share",
             "evidence_games": "Preseason counts for (games)", "form_half_life": "Recent-form half-life (games)"}
    for k, label in names.items():
        lines.append(f"  {label:<31}{d[k]:>6} -> {s[k]}")
    if c["learned"]["games"]:
        L, F = c["learned"], c["fixed"]
        lines.append(f"Check on {L['games']} games since {c['since']} (tuned only on earlier games):")
        lines.append(f"  learning: {L['right']}-{L['games'] - L['right']} ({L['pct']:.1%}), margin off {L['margin_off']}")
        lines.append(f"  fixed:    {F['right']}-{F['games'] - F['right']} ({F['pct']:.1%}), margin off {F['margin_off']}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--write", action="store_true", help="save the result to data/learned.json")
    p.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = p.parse_args()
    teams, _, moves = load_all(args.data_dir)
    result = learn(teams, moves, load_players(args.data_dir), load_games(args.data_dir))
    print(describe(result))
    if args.write:
        path = args.data_dir / "learned.json"
        path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
