"""Monte Carlo model for NBA playoff seeding.

Pipeline:
1. Preseason rating per team (points of margin per game vs. league average), a weighted
   blend of last season's net rating adjusted for offseason moves, the preseason win
   total line, and the preseason exhibition record.
2. Blend with the 2026-27 results, weighted by games played. Results are mostly point
   differential with a little win-loss record mixed in, so closing out games earns some credit.
   Games a player has already missed are added back first, so the blended rating describes a
   healthy roster and an injury is never counted twice.
   Then 20% of the rating comes from the current roster: each team's top-10 rotation, weighted by
   minutes, rated on Box Plus/Minus (this season blended with last). It picks up trades and
   signings as they happen and reads player form directly, not just team results.
   Recent games can count more than older ones (a game from `form_half_life` games ago counts half
   as much as the latest), so a team that has turned a corner moves faster than its season average.
3. Simulate every remaining game, lowering a team's rating while injured players are out.
4. Seed each conference by wins, then play the 7-10 play-in tournament.

Home court, the spread of game results, the preseason blend, how much the preseason counts and the
recent-form half-life are tuned every day against the games already played (predictor/learn.py).
The tuned values live in data/learned.json; without that file the hand-picked defaults below apply.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .data_io import CONFERENCES, GAMES, Injury, Move, Team, load_learned

# Hand-picked starting values. predictor/learn.py replaces them with values tuned on this season's games.
DEFAULTS = {
    "home_court": 2.5,        # points
    "logistic_scale": 7.0,    # spread of single-game outcomes
    "last_share": 0.75,       # preseason blend: share on last season, the rest on the win total line
    "evidence_games": 7,      # how many games of evidence the preseason rating counts for
    "form_half_life": 0,      # games until an old result counts half as much as the latest (0 = all equal)
}
LEARNED = {**DEFAULTS, **load_learned().get("settings", {})}

LEAGUE_WINS = GAMES * 30 // 2   # 1,230 wins in a season
WINS_PER_POINT = 2.7            # one point of average margin is worth about 2.7 wins
HOME_COURT = float(LEARNED["home_court"])
LOGISTIC_SCALE = float(LEARNED["logistic_scale"])
LAST_SEASON_KEEP = 0.7          # regress last season's net rating 30% toward average
PRESEASON_GHOST_GAMES = 3       # add a 3-3 ghost record: exhibition games are noisy
RATING_SD = 3.0                 # uncertainty in a preseason rating, points
RECORD_SHARE = 0.2              # share of the in-season result taken from W-L record, rest from margin
ROSTER_SHARE = 0.2              # share of each team's rating taken from its current roster's player stats
ROSTER_SIZE = 10                # players in the rotation that the roster rating counts


@dataclass
class Settings:
    weight_last: float = round(100 * LEARNED["last_share"])
    weight_line: float = 100 - round(100 * LEARNED["last_share"])
    weight_preseason: float = 0
    evidence_games: float = LEARNED["evidence_games"]   # games of evidence the preseason rating counts for
    form_half_life: float = LEARNED["form_half_life"]   # 0 = every game this season counts the same
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


def healthy_bonus(teams: list[Team], injuries: list[Injury]) -> np.ndarray:
    """Points of margin, summed over games, that each team lost to absences so far this season.

    The 2026-27 point diff already includes these absences, but the preseason prior assumes a
    healthy roster. Adding them back puts both on the same healthy footing before blending.
    """
    idx = {t.id: i for i, t in enumerate(teams)}
    out = np.zeros(len(teams))
    for j in injuries:
        t = teams[idx[j.team]]
        out[idx[j.team]] += j.impact * min(j.games_missed, t.games_played)
    return out


def record_margin(team: Team) -> float:
    """Win-loss record expressed as points of margin per game."""
    return (team.w / team.games_played - 0.5) * GAMES / WINS_PER_POINT


def observed_margin(team: Team) -> float:
    """This season's evidence: mostly point differential, plus a little W-L record."""
    if not team.games_played:
        return 0.0
    if not team.point_diff:   # no margin in the feed: fall back to the record alone
        return record_margin(team)
    return (1 - RECORD_SHARE) * team.point_diff + RECORD_SHARE * record_margin(team)


def _weighted_observed(margins: list[float], half_life: float) -> float:
    m = np.array(margins, dtype=float)
    w = 0.5 ** (np.arange(len(m))[::-1] / half_life) if half_life else np.ones(len(m))
    diff = (w * m).sum() / w.sum()
    record = ((w * (m > 0)).sum() / w.sum() - 0.5) * GAMES / WINS_PER_POINT
    return (1 - RECORD_SHARE) * diff + RECORD_SHARE * record


def team_margins(games: list[dict] | None) -> dict[str, list[float]]:
    """Each team's game margins this season, oldest first."""
    out: dict[str, list[float]] = {}
    for g in sorted((g for g in games or [] if g.get("home_pts") is not None and g.get("away_pts") is not None),
                    key=lambda g: (g["date"], g["home"])):
        m = g["home_pts"] - g["away_pts"]
        out.setdefault(g["home"], []).append(m)
        out.setdefault(g["away"], []).append(-m)
    return out


def form_adjustment(teams: list[Team], games: list[dict] | None, half_life: float) -> np.ndarray:
    """Points to add to each team's observed margin so recent games count more than old ones.

    It's the difference between the recency-weighted and the plain average of the team's games, so
    it is zero when the half-life is off and edits to the standings themselves still count.
    """
    out = np.zeros(len(teams))
    if not half_life or not games:
        return out
    margins = team_margins(games)
    for i, t in enumerate(teams):
        m = margins.get(t.id)
        if m:
            out[i] = _weighted_observed(m, half_life) - _weighted_observed(m, 0)
    return out


def roster_ratings(teams: list[Team], players: list[dict] | None) -> np.ndarray | None:
    """Healthy-roster strength in points of margin vs average, from player Box Plus/Minus.

    Each team's top ROSTER_SIZE players by minutes, weighted so the minutes add up to five players
    on the floor. Injured players are included: injuries are applied game by game in the
    simulation. None when there is no player data.
    """
    if not players:
        return None
    out = []
    for t in teams:
        rot = sorted((p for p in players if p["team"] == t.id), key=lambda p: (-p["mpg"], p["player"]))[:ROSTER_SIZE]
        mins = sum(p["mpg"] for p in rot)
        out.append(sum(5 * p["mpg"] / mins * p["bpm"] for p in rot) if len(rot) >= 5 and mins > 0 else np.nan)
    out = np.array(out)
    if np.isnan(out).all():
        return None
    out = np.where(np.isnan(out), np.nanmean(out), out)
    return out - out.mean()


def roster_share(players: list[dict] | None) -> float:
    return ROSTER_SHARE if players else 0.0


def team_ratings(teams: list[Team], moves: list[Move], s: Settings, injuries: list[Injury] = (),
                 players: list[dict] | None = None, games: list[dict] | None = None,
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (rating, rating_sd, preseason_prior) arrays, one entry per team. Ratings are healthy.
    `games` (with scores) lets recent results count more, per `s.form_half_life`."""
    a, b, c = blend_weights(s)
    comp = components(teams, moves)
    raw = a * comp["last"] + b * comp["line"] + c * comp["preseason"]
    prior = raw - raw.mean()   # keep the league average at zero

    k = s.evidence_games
    gp = np.array([t.games_played for t in teams], dtype=float)
    observed = np.array([observed_margin(t) for t in teams])
    observed += np.where(gp > 0, form_adjustment(teams, games, s.form_half_life), 0.0)
    rating = (prior * k + observed * gp + healthy_bonus(teams, injuries)) / (k + gp)
    roster = roster_ratings(teams, players)
    if roster is not None:
        rating = (1 - ROSTER_SHARE) * rating + ROSTER_SHARE * roster
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


def injury_wins_lost(team: Team, injuries: list[Injury], evidence_games: float = Settings.evidence_games,
                     roster: float = 0.0) -> float:
    """Net wins the projection loses to injuries: games still to miss, minus the rating added back
    for games already missed (that boost applies to every remaining game). `roster` is the share of
    the rating that comes from player stats, which the add-back doesn't touch."""
    per_point = WINS_PER_POINT / GAMES
    left, gp = team.games_left, team.games_played
    return sum(
        j.impact * per_point
        * (min(left, j.games_out) - (1 - roster) * left * min(j.games_missed, gp) / (evidence_games + gp))
        for j in injuries if j.team == team.id
    )


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


def simulate(teams: list[Team], injuries: list[Injury], moves: list[Move], s: Settings | None = None,
             players: list[dict] | None = None, games: list[dict] | None = None) -> list[dict]:
    s = s or Settings()
    rng = np.random.default_rng(s.seed)
    n, N = len(teams), s.sims
    rating, sd, prior = team_ratings(teams, moves, s, injuries, players, games)
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
            "injury_wins_lost": round(injury_wins_lost(t, injuries, s.evidence_games, roster_share(players)), 2),
            "proj_wins": round(float(wins[:, i].mean()), 1),
            "top6": float(top6[i] / N),
            "play_in": float(play_in[i] / N),
            "playoffs": float(playoffs[i] / N),
            "avg_seed": float(seed_sum[i] / N),
            "status": clinch_status(t, teams),
        })
    return results
