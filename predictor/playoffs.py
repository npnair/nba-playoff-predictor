"""Playoff bracket: who wins each series, and in how many games.

Each matchup's expected margin (points per game on a neutral court) blends three estimates:

  team strength   50%  the predictor's end-of-season rating, after injuries
  starting five   30%  each team's five starters compared position by position (Box Plus/Minus,
                       played at 36 minutes a night as playoff rotations tighten)
  season series   20%  results of the two teams' games against each other, adjusted for home court
                       and blended with team strength until enough games exist (4 games of weight)

Series odds then come from playing out a best-of-seven game by game with home court in the
2-2-1-1-1 format. docs/js/playoffs.js implements the same model for the web page; keep them in sync.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from .model import HOME_COURT, LOGISTIC_SCALE

WEIGHTS = {"rating": 0.50, "lineup": 0.30, "h2h": 0.20}
H2H_PRIOR_GAMES = 4          # a season series counts like this many games of team-strength evidence
STARTER_MINUTES = 36         # playoff starters' minutes per game
PRIOR_MINUTES = 250          # player BPM is shrunk toward replacement level (-2) by this many minutes
PRIOR_BPM = -2.0
CUR_HALF_GAMES = 10          # this season's numbers count half once a player has played 10 games
SLOTS = ["PG", "SG", "SF", "PF", "C"]
POS_NUMBER = {"PG": 1, "G": 1.5, "SG": 2, "GF": 2.5, "SF": 3, "F": 3.5, "PF": 4, "FC": 4.5, "C": 5}
HOME_PATTERN = [True, True, False, False, True, False, True]   # games hosted by the team with home court


# ------------------------------------------------------------------ players and lineups
def blend_player(cur: dict | None, prev: dict | None) -> dict | None:
    """Combine this season and last season for one player: minutes per game and a shrunk BPM."""
    seasons = [(s, s["g"]) for s in (cur, prev) if s and s.get("g", 0) > 0 and s.get("mp", 0) > 0]
    if not seasons:
        return None
    if cur and cur.get("g", 0) > 0 and prev and prev.get("g", 0) > 0:
        w = cur["g"] / (cur["g"] + CUR_HALF_GAMES)
    else:
        w = 1.0 if cur and cur.get("g", 0) > 0 else 0.0
    def part(s, key, default):
        if not s or not s.get("g"):
            return default
        return s["mp"] / s["g"] if key == "mpg" else (s.get("bpm") if s.get("bpm") is not None else PRIOR_BPM)
    mpg = w * part(cur, "mpg", 0) + (1 - w) * part(prev, "mpg", 0)
    bpm_raw = w * part(cur, "bpm", PRIOR_BPM) + (1 - w) * part(prev, "bpm", PRIOR_BPM)
    minutes = sum(s["mp"] for s, _ in seasons)
    bpm = (bpm_raw * minutes + PRIOR_BPM * PRIOR_MINUTES) / (minutes + PRIOR_MINUTES)
    pos = (cur or {}).get("pos") or (prev or {}).get("pos") or ""
    return {"pos": pos, "mpg": round(mpg, 1), "bpm": round(bpm, 2), "minutes": int(minutes)}


def build_player_table(rosters: dict[str, list[dict]], cur: list[dict] | None, prev: list[dict]) -> list[dict]:
    cur_by = {p["bref_id"]: p for p in cur or []}
    prev_by = {p["bref_id"]: p for p in prev}
    rows = []
    for team, players in sorted(rosters.items()):
        for p in players:
            b = blend_player(cur_by.get(p["bref_id"]), prev_by.get(p["bref_id"]))
            if b:
                rows.append({"team": team, "bref_id": p["bref_id"], "player": p["player"], **b})
    return rows


def pos_number(pos: str) -> float:
    first = (pos or "").split("-")[0].strip().upper()
    return POS_NUMBER.get(first, 3)


def normalize(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", "", re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", plain)).split("(")[0].strip()


def injury_name(text: str) -> str:
    return normalize(text.split("(")[0])


def matches_injury(player: str, injured: str) -> bool:
    """'Ben Simmons' matches 'Ben Simmons'; 'Jaylen Watkins' matches the abbreviated 'J Watkins'."""
    p, i = normalize(player).split(), injured.split()
    if not p or not i:
        return False
    if " ".join(p) == " ".join(i):
        return True
    return len(i) >= 2 and len(i[0]) == 1 and p[-1] == i[-1] and p[0][0] == i[0]


def out_for_playoffs(players: list[dict], injuries: list, games_left: dict[str, int]) -> set[str]:
    """Players whose injury runs past the end of their team's regular season."""
    out = set()
    for j in injuries:
        if j.games_out < games_left.get(j.team, 82):
            continue
        name = injury_name(j.player)
        for p in players:
            if p["team"] == j.team and matches_injury(p["player"], name):
                out.add(p["bref_id"])
    return out


def starting_five(players: list[dict], team: str, out: set[str]) -> list[dict]:
    """Top five healthy players by minutes, lined up PG to C."""
    pool = sorted((p for p in players if p["team"] == team and p["bref_id"] not in out),
                  key=lambda p: -p["mpg"])[:5]
    pool.sort(key=lambda p: (pos_number(p["pos"]), -p["mpg"]))
    return [{**p, "slot": SLOTS[k]} for k, p in enumerate(pool)]


def lineup_value(five: list[dict]) -> float:
    return sum(p["bpm"] for p in five) * STARTER_MINUTES / 48


# ------------------------------------------------------------------ season series
def season_series(games: list[dict], a: str, b: str) -> tuple[list[dict], int]:
    """(played games from a's side, games still scheduled)."""
    played, scheduled = [], 0
    for g in games:
        if {g["home"], g["away"]} != {a, b}:
            continue
        if g.get("home_pts") is None or g.get("away_pts") is None:
            scheduled += 1
            continue
        a_home = g["home"] == a
        margin = (g["home_pts"] - g["away_pts"]) * (1 if a_home else -1)
        played.append({"date": g["date"], "a_home": a_home, "margin": margin})
    return played, scheduled


def h2h_estimate(played: list[dict], rating_gap: float) -> float:
    adjusted = sum(g["margin"] - (HOME_COURT if g["a_home"] else -HOME_COURT) for g in played)
    return (adjusted + H2H_PRIOR_GAMES * rating_gap) / (len(played) + H2H_PRIOR_GAMES)


# ------------------------------------------------------------------ matchups and series
@dataclass
class Context:
    ratings: dict[str, float]                 # end-of-season team rating, points vs average
    players: list[dict]
    games: list[dict]
    out: set[str] = field(default_factory=set)
    _fives: dict = field(default_factory=dict)

    def five(self, team: str) -> list[dict]:
        if team not in self._fives:
            self._fives[team] = starting_five(self.players, team, self.out)
        return self._fives[team]


def matchup(ctx: Context, a: str, b: str) -> dict:
    """Expected margin for a over b on a neutral court, with the pieces that produced it."""
    rating_gap = ctx.ratings[a] - ctx.ratings[b]
    fa, fb = ctx.five(a), ctx.five(b)
    lineup_gap = lineup_value(fa) - lineup_value(fb) if len(fa) == 5 and len(fb) == 5 else rating_gap
    played, scheduled = season_series(ctx.games, a, b)
    h2h = h2h_estimate(played, rating_gap)
    margin = WEIGHTS["rating"] * rating_gap + WEIGHTS["lineup"] * lineup_gap + WEIGHTS["h2h"] * h2h
    slots = [{"slot": s, "a": pa, "b": pb, "edge": round(pa["bpm"] - pb["bpm"], 2)}
             for s, pa, pb in zip(SLOTS, fa, fb)]
    return {"a": a, "b": b, "rating_gap": rating_gap, "lineup_gap": lineup_gap, "h2h": h2h,
            "margin": margin, "slots": slots, "played": played, "scheduled": scheduled,
            "slots_won": sum(1 for s in slots if s["edge"] > 0)}


def game_prob(margin: float, home: bool) -> float:
    return 1 / (1 + math.exp(-(margin + (HOME_COURT if home else -HOME_COURT)) / LOGISTIC_SCALE))


def series_outcomes(margin: float, a_home_court: bool) -> dict:
    """Probability of every series result, from a's side. margin = a over b on a neutral court."""
    states = {(0, 0): 1.0}
    finished = {"a": {4: 0.0, 5: 0.0, 6: 0.0, 7: 0.0}, "b": {4: 0.0, 5: 0.0, 6: 0.0, 7: 0.0}}
    for game, top_seed_home in enumerate(HOME_PATTERN, start=1):
        p = game_prob(margin, top_seed_home == a_home_court)
        nxt: dict = {}
        for (wa, wb), prob in states.items():
            for (na, nb), q in (((wa + 1, wb), p), ((wa, wb + 1), 1 - p)):
                if na == 4:
                    finished["a"][game] += prob * q
                elif nb == 4:
                    finished["b"][game] += prob * q
                else:
                    nxt[(na, nb)] = nxt.get((na, nb), 0) + prob * q
        states = nxt
    finished["p_a"] = sum(finished["a"].values())
    return finished


def series_pick(outcomes: dict) -> tuple[str, int, float]:
    """('a' or 'b', most likely number of games for that winner, chance that side wins)."""
    side = "a" if outcomes["p_a"] >= 0.5 else "b"
    games = max(outcomes[side], key=outcomes[side].get)
    return side, games, outcomes["p_a"] if side == "a" else 1 - outcomes["p_a"]


def play_series(ctx: Context, high: str, low: str) -> dict:
    """high has home court."""
    m = matchup(ctx, high, low)
    out = series_outcomes(m["margin"], True)
    side, games, prob = series_pick(out)
    return {**m, "outcomes": out, "winner": high if side == "a" else low,
            "loser": low if side == "a" else high, "games": games, "prob": prob}


def play_game(ctx: Context, home: str, away: str) -> dict:
    m = matchup(ctx, home, away)
    p = game_prob(m["margin"], True)
    return {**m, "p_home": p, "winner": home if p >= 0.5 else away, "loser": away if p >= 0.5 else home,
            "prob": max(p, 1 - p)}


def conference_bracket(ctx: Context, seeds: list[str]) -> dict:
    """seeds: ten team ids, 1 through 10."""
    s = {k + 1: t for k, t in enumerate(seeds)}
    g78 = play_game(ctx, s[7], s[8])
    g910 = play_game(ctx, s[9], s[10])
    last = play_game(ctx, g78["loser"], g910["winner"])
    final = {**{k: s[k] for k in range(1, 7)}, 7: g78["winner"], 8: last["winner"]}
    seed_of = {t: k for k, t in final.items()}
    def series(x, y):
        hi, lo = (x, y) if seed_of[x] < seed_of[y] else (y, x)
        return play_series(ctx, hi, lo)
    r1 = [series(final[1], final[8]), series(final[4], final[5]), series(final[3], final[6]), series(final[2], final[7])]
    r2 = [series(r1[0]["winner"], r1[1]["winner"]), series(r1[2]["winner"], r1[3]["winner"])]
    cf = series(r2[0]["winner"], r2[1]["winner"])
    return {"play_in": [g78, g910, last], "seeds": final, "r1": r1, "r2": r2, "final": cf}


def full_bracket(ctx: Context, west: list[str], east: list[str], proj_wins: dict[str, float]) -> dict:
    w, e = conference_bracket(ctx, west), conference_bracket(ctx, east)
    a, b = w["final"]["winner"], e["final"]["winner"]
    hi, lo = (a, b) if proj_wins.get(a, 0) >= proj_wins.get(b, 0) else (b, a)
    return {"West": w, "East": e, "finals": play_series(ctx, hi, lo)}
