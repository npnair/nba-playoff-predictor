// Monte Carlo playoff model. Mirrors predictor/model.py; keep the two in sync.
(function (global) {
  "use strict";

  const GAMES = 82;
  const LEAGUE_WINS = 1230;
  const WINS_PER_POINT = 2.7;
  const HOME_COURT = 2.5;
  const LOGISTIC_SCALE = 7.0;
  const LAST_SEASON_KEEP = 0.7;
  const PRESEASON_GHOST_GAMES = 3;
  const RATING_SD = 3.0;

  const gamesPlayed = (t) => t.w + t.l;
  const gamesLeft = (t) => Math.max(0, GAMES - gamesPlayed(t));

  function blendWeights(s) {
    let a = +s.weightLast, b = +s.weightLine, c = +s.weightPreseason;
    const total = a + b + c;
    if (!(total > 0)) return [1 / 3, 1 / 3, 1 / 3];
    return [a / total, b / total, c / total];
  }

  function moveTotal(moves, id) {
    return moves.filter((m) => m.team === id).reduce((sum, m) => sum + (+m.impact || 0), 0);
  }

  // Each team's three preseason views, in points of margin per game.
  function components(teams, moves) {
    const scale = LEAGUE_WINS / teams.reduce((sum, t) => sum + t.win_total, 0);
    const g = PRESEASON_GHOST_GAMES;
    return teams.map((t) => ({
      last: LAST_SEASON_KEEP * t.last_net_rating + moveTotal(moves, t.id),
      line: (t.win_total * scale - GAMES / 2) / WINS_PER_POINT,
      preseason: ((t.pre_w + g) / (t.pre_w + t.pre_l + 2 * g) - 0.5) * GAMES / WINS_PER_POINT,
    }));
  }

  function ratings(teams, moves, s) {
    const [a, b, c] = blendWeights(s);
    const raw = components(teams, moves).map((m) => a * m.last + b * m.line + c * m.preseason);
    const mean = raw.reduce((x, y) => x + y, 0) / raw.length;
    const k = +s.evidenceGames;
    return teams.map((t, i) => {
      const prior = raw[i] - mean;
      const gp = gamesPlayed(t);
      const observed = t.point_diff || !gp ? t.point_diff : (t.w / gp - 0.5) * GAMES / WINS_PER_POINT;
      return {
        prior,
        rating: (prior * k + observed * gp) / (k + gp),
        sd: RATING_SD * Math.sqrt(k / (k + gp)),
      };
    });
  }

  // pen[i][g] = rating points team i is missing in its g-th remaining game.
  function penalties(teams, injuries) {
    return teams.map((t) => {
      const pen = new Float64Array(GAMES + 1);
      injuries.filter((j) => j.team === t.id).forEach((j) => {
        const n = Math.min(GAMES, Math.max(0, Math.round(j.games_out)));
        for (let g = 0; g < n; g++) pen[g] += +j.impact || 0;
      });
      return pen;
    });
  }

  function winProb(a, b, home) {
    return 1 / (1 + Math.exp(-((a - b) + (home ? HOME_COURT : -HOME_COURT)) / LOGISTIC_SCALE));
  }

  function gauss(rand) {
    let u = 0, v = 0;
    while (!u) u = rand();
    while (!v) v = rand();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
  }

  function injuryWinsLost(team, injuries) {
    const left = gamesLeft(team);
    return injuries
      .filter((j) => j.team === team.id)
      .reduce((sum, j) => sum + (+j.impact || 0) * Math.min(left, Math.max(0, j.games_out)) * (WINS_PER_POINT / GAMES), 0);
  }

  // "clinched" a top-6 seed, "eliminated" from the top 10, or "". Ignores tiebreakers.
  function clinchStatus(team, teams) {
    if (!gamesPlayed(team)) return "";
    const rivals = teams.filter((o) => o.conference === team.conference && o.id !== team.id);
    if (rivals.filter((o) => o.w + gamesLeft(o) >= team.w).length <= 5) return "clinched";
    if (rivals.filter((o) => o.w > team.w + gamesLeft(team)).length >= 10) return "eliminated";
    return "";
  }

  function simulate(teams, injuries, moves, s, rand = Math.random) {
    const R = ratings(teams, moves, s);
    const P = penalties(teams, injuries);
    const n = teams.length, N = +s.sims;
    const tally = teams.map(() => ({ top6: 0, playIn: 0, playoffs: 0, seed: 0, wins: 0 }));
    const strength = new Float64Array(n);
    const wins = new Float64Array(n);
    const left = teams.map(gamesLeft);
    const endOfSeason = (i) => strength[i] - P[i][left[i]];
    const play = (home, away) => (rand() < winProb(endOfSeason(home), endOfSeason(away), true) ? home : away);

    for (let sim = 0; sim < N; sim++) {
      for (let i = 0; i < n; i++) strength[i] = R[i].rating + gauss(rand) * R[i].sd;

      for (let i = 0; i < n; i++) {
        let w = teams[i].w;
        for (let g = 0; g < left[i]; g++) {
          let o = (rand() * (n - 1)) | 0;
          if (o >= i) o++;
          if (rand() < winProb(strength[i] - P[i][g], strength[o] - P[o][g], rand() < 0.5)) w++;
        }
        wins[i] = w;
        tally[i].wins += w;
      }

      for (const conf of ["West", "East"]) {
        const seeded = [];
        for (let i = 0; i < n; i++) if (teams[i].conference === conf) seeded.push([i, wins[i] + rand() * 0.01]);
        seeded.sort((x, y) => y[1] - x[1]);
        seeded.forEach(([i], k) => {
          tally[i].seed += k + 1;
          if (k < 6) { tally[i].top6++; tally[i].playoffs++; }
          else if (k < 10) tally[i].playIn++;
        });
        const [s7, s8, s9, s10] = seeded.slice(6, 10).map((x) => x[0]);
        const w78 = play(s7, s8);
        const l78 = w78 === s7 ? s8 : s7;
        const last = play(l78, play(s9, s10));
        tally[w78].playoffs++;
        tally[last].playoffs++;
      }
    }

    return {
      ratings: R,
      rows: tally.map((x, i) => ({
        index: i,
        top6: x.top6 / N,
        playIn: x.playIn / N,
        playoffs: x.playoffs / N,
        avgSeed: x.seed / N,
        projWins: x.wins / N,
      })),
    };
  }

  global.PL_MODEL = {
    GAMES, gamesPlayed, gamesLeft, blendWeights, moveTotal, components, ratings,
    penalties, winProb, simulate, injuryWinsLost, clinchStatus,
  };
})(typeof window !== "undefined" ? window : globalThis);
