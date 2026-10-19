"""Monte Carlo model for NBA playoff seeding.

Pipeline:
1. Preseason rating per team (points of margin per game vs. league average), a weighted
   blend of last season's net rating adjusted for offseason moves, the preseason win
   total line, and the preseason exhibition record.
2. Blend with the 2026-27 point differential, weighted by games played.
3. Simulate every remaining game, lowering a team's rating while injured players are out.
4. Seed each conference by wins, then play the 7-10 play-in tournament.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data_io import CONFERENCES, GAMES, Injury, Move, Team

LEAGUE_WINS = GAMES * 30 // 2   # 1,230 wins in a season
WINS_PER_POINT = 2.7            # one point of average margin is worth about 2.7 wins
HOME_COURT = 2.5                # points
LOGISTIC_SCALE = 7.0            # spread of single-game outcomes
LAST_SEASON_KEEP = 0.7          # regress last season's net rating 30% toward average
PRESEASON_GHOST_GAMES = 3       # add a 3-3 ghost record: exhibition games are noisy
RATING_SD = 3.0                 # uncertainty in a preseason rating, points


@dataclass
class Settings:
    weight_last: float = 30
    weight_line: float = 60
    weight_preseason: float = 10
    evidence_games: float = 25    # how many games of evidence the preseason rating counts for
    sims: int = 5000
    seed: int | None = None


def blend_weights(s: Settings) -> tuple[float, float, float]:
    a, b, c = s.weight_last, s.weight_line, s.weight_preseason
    total = a + b + c
    if total <= 0:
        return (1 / 3, 1 / 3, 1 / 3)
    return (a / total, b / total, c / total)


def move_totals(teams: list[Team], moves: list[Move]) -> np.ndarray:
    idx = {t.id: i for i, t in enumerate(teams)}
    out = np.zeros(len(teams))
    for m in moves:
        out[idx[m.team]] += m.impact
    return out


def components(teams: list[Team], moves: list[Move]) -> dict[str, np.ndarray]:
    """Each team's three preseason views, all in points of margin per game."""
    lines = np.array([t.win_total for t in teams])
    scale = LEAGUE_WINS / lines.sum()   # lines don't sum to exactly 1,230
    pre_w = np.array([t.pre_w for t in teams], dtype=float)
    pre_g = np.array([t.pre_w + t.pre_l for t in teams], dtype=float)
    g = PRESEASON_GHOST_GAMES
    return {
        "last": LAST_SEASON_KEEP * np.array([t.last_net_rating for t in teams]) + move_totals(teams, moves),
        "line": (lines * scale - GAMES / 2) / WINS_PER_POINT,
        "preseason": ((pre_w + g) / (pre_g + 2 * g) - 0.5) * GAMES / WINS_PER_POINT,
    }


def team_ratings(teams: list[Team], moves: list[Move], s: Settings) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (rating, rating_sd, preseason_prior) arrays, one entry per team."""
    a, b, c = blend_weights(s)
    comp = components(teams, moves)
    raw = a * comp["last"] + b * comp["line"] + c * comp["preseason"]
    prior = raw - raw.mean()   # keep the league average at zero

    k = s.evidence_games
    gp = np.array([t.games_played for t in teams], dtype=float)
    observed = np.array([
        t.point_diff if (t.point_diff or not t.games_played)
        else (t.w / t.games_played - 0.5) * GAMES / WINS_PER_POINT
        for t in teams
    ])
    rating = (prior * k + observed * gp) / (k + gp)
    sd = RATING_SD * np.sqrt(k / (k + gp))
    return rating, sd, prior


def injury_penalties(teams: list[Team], injuries: list[Injury]) -> np.ndarray:
    """pen[i, g] = rating points team i is missing in its g-th remaining game."""
    idx = {t.id: i for i, t in enumerate(teams)}
    pen = np.zeros((len(teams), GAMES + 1))
    for j in injuries:
        n = min(GAMES, max(0, j.games_out))
        pen[idx[j.team], :n] += j.impact
    return pen


def win_prob(a, b, home):
    """Probability the team rated `a` beats the team rated `b`."""
    edge = np.where(home, HOME_COURT, -HOME_COURT)
    return 1.0 / (1.0 + np.exp(-((a - b) + edge) / LOGISTIC_SCALE))


def injury_wins_lost(team: Team, injuries: list[Injury]) -> float:
    per_point = WINS_PER_POINT / GAMES
    return sum(j.impact * min(team.games_left, j.games_out) * per_point for j in injuries if j.team == team.id)


def clinch_status(team: Team, teams: list[Team]) -> str:
    """'clinched' a top-6 seed, 'eliminated' from the top 10, or ''. Ignores tiebreakers."""
    if team.games_played == 0:
        return ""
    rivals = [o for o in teams if o.conference == team.conference and o.id != team.id]
    can_catch = sum(o.w + o.games_left >= team.w for o in rivals)
    if can_catch <= 5:
        return "clinched"
    if sum(o.w > team.w + team.games_left for o in rivals) >= 10:
        return "eliminated"
    return ""


def simulate(teams: list[Team], injuries: list[Injury], moves: list[Move], s: Settings | None = None) -> list[dict]:
    s = s or Settings()
    rng = np.random.default_rng(s.seed)
    n, N = len(teams), s.sims
    rating, sd, prior = team_ratings(teams, moves, s)
    pen = injury_penalties(teams, injuries)
    left = np.array([t.games_left for t in teams])

    # Each simulated season draws its own version of every team's true strength.
    strength = rating + rng.standard_normal((N, n)) * sd
    wins = np.tile(np.array([t.w for t in teams], dtype=float), (N, 1))
    rows = np.arange(N)

    for i in range(n):
        for g in range(left[i]):
            opp = rng.integers(0, n - 1, N)
            opp += opp >= i                                   # never play yourself
            home = rng.random(N) < 0.5
            p = win_prob(strength[:, i] - pen[i, g], strength[rows, opp] - pen[opp, g], home)
            wins[:, i] += rng.random(N) < p

    top6 = np.zeros(n)
    play_in = np.zeros(n)
    playoffs = np.zeros(n)
    seed_sum = np.zeros(n)

    def end_of_season(t):
        return strength[rows, t] - pen[t, left[t]]

    def play(home_team, away_team):
        home_wins = rng.random(N) < win_prob(end_of_season(home_team), end_of_season(away_team), True)
        return np.where(home_wins, home_team, away_team)

    for conf in CONFERENCES:
        idx = np.array([k for k, t in enumerate(teams) if t.conference == conf])
        key = wins[:, idx] + rng.random((N, len(idx))) * 0.01   # random tiebreak
        seeded = idx[np.argsort(-key, axis=1)]                  # N x 15 team indices, best first
        seed_sum += np.bincount(seeded.ravel(), weights=np.tile(np.arange(1, 16), N), minlength=n)
        top6 += np.bincount(seeded[:, :6].ravel(), minlength=n)
        play_in += np.bincount(seeded[:, 6:10].ravel(), minlength=n)

        s7, s8, s9, s10 = (seeded[:, k] for k in range(6, 10))
        w78 = play(s7, s8)                      # winner is the 7 seed
        l78 = np.where(w78 == s7, s8, s7)
        w910 = play(s9, s10)
        last = play(l78, w910)                  # winner is the 8 seed
        playoffs += np.bincount(w78, minlength=n) + np.bincount(last, minlength=n)

    playoffs += top6
    moved = move_totals(teams, moves)
    results = []
    for i, t in enumerate(teams):
        results.append({
            "id": t.id,
            "name": t.name,
            "conference": t.conference,
            "record": f"{t.w}-{t.l}",
            "rating": round(float(rating[i]), 2),
            "moves": round(float(moved[i]), 2),
            "injury_wins_lost": round(injury_wins_lost(t, injuries), 2),
            "proj_wins": round(float(wins[:, i].mean()), 1),
            "top6": float(top6[i] / N),
            "play_in": float(play_in[i] / N),
            "playoffs": float(playoffs[i] / N),
            "avg_seed": float(seed_sum[i] / N),
            "status": clinch_status(t, teams),
        })
    return results
