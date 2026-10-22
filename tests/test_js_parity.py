"""The web page's bracket model (docs/js/playoffs.js) must match predictor/playoffs.py exactly."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from predictor.data_io import Injury
from predictor.playoffs import Context, full_bracket, out_for_playoffs

ROOT = Path(__file__).resolve().parent.parent
WEST = [f"W{i}" for i in range(10)]
EAST = [f"E{i}" for i in range(10)]


def fixture():
    teams = WEST + EAST
    ratings = {t: round(7 - (i % 10) * 1.3 + (0.4 if t[0] == "E" else 0), 2) for i, t in enumerate(teams)}
    positions = ["PG", "SG", "SF", "PF", "C", "SG-PG", "F", "C", "G"]
    players = [{"team": t, "bref_id": f"{t}p{k}", "player": f"Player {t} {k}", "pos": positions[k],
                "mpg": 36 - 3 * k, "bpm": round(((i * 7 + k * 3) % 11) - 4.5, 2), "minutes": 900}
               for i, t in enumerate(teams) for k in range(9)]
    games = [{"date": f"2026-11-{d:02d}", "home": teams[d % 20], "away": teams[(d * 3 + 1) % 20],
              "home_pts": 100 + d % 17, "away_pts": 98 + (d * 5) % 23} for d in range(1, 29)
             if teams[d % 20] != teams[(d * 3 + 1) % 20]]
    games.append({"date": "2027-03-01", "home": "W0", "away": "W1", "home_pts": None, "away_pts": None})
    injuries = [Injury("W0", "Player W0 0 (knee, season)", 1.0, 82), Injury("E3", "Player E3 1 (ankle)", 1.0, 5)]
    wins = {t: 50 - (i % 10) for i, t in enumerate(teams)}
    return ratings, players, games, injuries, wins


def summarize(b):
    def s(x):
        return [x["winner"], x["loser"], x.get("games"), round(x["prob"], 9), round(x["margin"], 9)]
    out = {"finals": s(b["finals"])}
    for conf in ("West", "East"):
        c = b[conf]
        out[conf] = {"play_in": [s(g) for g in (c["play_in"] if "play_in" in c else c["playIn"])],
                     "r1": [s(x) for x in c["r1"]], "r2": [s(x) for x in c["r2"]], "final": s(c["final"])}
    return out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_js_and_python_brackets_match():
    ratings, players, games, injuries, wins = fixture()
    left = {t: 60 for t in ratings}
    py = summarize(full_bracket(Context(ratings, players, games, out_for_playoffs(players, injuries, left)), WEST, EAST, wins))

    payload = json.dumps({"ratings": ratings, "players": players, "games": games, "wins": wins, "left": left,
                          "injuries": [{"team": j.team, "player": j.player, "games_out": j.games_out} for j in injuries],
                          "west": WEST, "east": EAST})
    script = f"""
      require({json.dumps(str(ROOT / 'docs/js/playoffs.js'))});
      const P = globalThis.PL_PLAYOFFS, d = JSON.parse(require('fs').readFileSync(0, 'utf8'));
      const ctx = P.makeContext(d.ratings, d.players, d.games, P.outForPlayoffs(d.players, d.injuries, d.left));
      const b = P.fullBracket(ctx, d.west, d.east, d.wins);
      const r = (x) => Math.round(x * 1e9) / 1e9;
      const s = (x) => [x.winner, x.loser, x.kind === 'series' ? x.games : null, r(x.prob), r(x.margin)];
      const out = {{ finals: s(b.finals) }};
      for (const c of ['West', 'East']) out[c] = {{ play_in: b[c].playIn.map(s), r1: b[c].r1.map(s), r2: b[c].r2.map(s), final: s(b[c].final) }};
      console.log(JSON.stringify(out));
    """
    result = subprocess.run(["node", "-e", script], input=payload, capture_output=True, text=True, check=True)
    js = json.loads(result.stdout)
    assert js == json.loads(json.dumps(py))
