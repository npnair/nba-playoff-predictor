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
  const RECORD_SHARE = 0.2;   // share of the in-season result taken from W-L record, rest from margin
  const ROSTER_SHARE = 0.2;   // share of each team's rating taken from its current roster's player stats
  const ROSTER_SIZE = 10;     // players in the rotation that the roster rating counts

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

  // Margin points (summed over games) each team lost to absences so far. The point diff already
  // includes them but the preseason prior assumes a healthy roster, so they are added back.
  function healthyBonus(t, injuries) {
    return injuries.filter((j) => j.team === t.id)
      .reduce((sum, j) => sum + (+j.impact || 0) * Math.min(Math.max(0, +j.games_missed || 0), gamesPlayed(t)), 0);
  }

  // This season's evidence: mostly point differential, plus a little W-L record.
  function observedMargin(t) {
    const gp = gamesPlayed(t);
    if (!gp) return 0;
    const record = (t.w / gp - 0.5) * GAMES / WINS_PER_POINT;
    if (!t.point_diff) return record;   // no margin in the feed: record alone
    return (1 - RECORD_SHARE) * t.point_diff + RECORD_SHARE * record;
  }

  // Healthy-roster strength from player BPM: top-10 rotation, minutes scaled to five on the floor,
  // centered on the league. null when there is no player data. Mirrors roster_ratings in model.py.
  function rosterRatings(teams, players) {
    if (!players || !players.length) return null;
    const byName = (a, b) => (a.player < b.player ? -1 : a.player > b.player ? 1 : 0);
    const raw = teams.map((t) => {
      const rot = players.filter((p) => p.team === t.id).sort((a, b) => b.mpg - a.mpg || byName(a, b)).slice(0, ROSTER_SIZE);
      const mins = rot.reduce((s, p) => s + p.mpg, 0);
      return rot.length >= 5 && mins > 0 ? rot.reduce((s, p) => s + 5 * p.mpg / mins * p.bpm, 0) : null;
    });
    const known = raw.filter((v) => v !== null);
    if (!known.length) return null;
    const fill = known.reduce((a, b) => a + b, 0) / known.length;
    const vals = raw.map((v) => (v === null ? fill : v));
    const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
    return vals.map((v) => v - mean);
  }
  const rosterShare = (players) => (players && players.length ? ROSTER_SHARE : 0);

  function ratings(teams, moves, s, injuries = [], players = null) {
    const [a, b, c] = blendWeights(s);
    const raw = components(teams, moves).map((m) => a * m.last + b * m.line + c * m.preseason);
    const mean = raw.reduce((x, y) => x + y, 0) / raw.length;
    const k = +s.evidenceGames;
    const roster = rosterRatings(teams, players);
    return teams.map((t, i) => {
      const prior = raw[i] - mean;
      const gp = gamesPlayed(t);
      const observed = observedMargin(t);
      const blended = (prior * k + observed * gp + healthyBonus(t, injuries)) / (k + gp);
      return {
        prior,
        roster: roster ? roster[i] : null,
        rating: roster ? (1 - ROSTER_SHARE) * blended + ROSTER_SHARE * roster[i] : blended,
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

  // Net wins lost: games still to miss, minus the rating added back for games already missed.
  function injuryWinsLost(team, injuries, evidenceGames = 7, roster = 0) {
    const left = gamesLeft(team), gp = gamesPlayed(team), k = +evidenceGames;
    return injuries
      .filter((j) => j.team === team.id)
      .reduce((sum, j) => {
        const missed = Math.min(Math.max(0, +j.games_missed || 0), gp);
        const games = Math.min(left, Math.max(0, j.games_out)) - (1 - roster) * left * missed / (k + gp);
        return sum + (+j.impact || 0) * games * (WINS_PER_POINT / GAMES);
      }, 0);
  }

  // ---------- weekly game predictions (mirrors predictor/weekly.py) ----------
  const WEEK_DAYS = 7;
  const SCORING_PRIOR_GAMES = 10;
  const isPlayed = (g) => g.home_pts != null && g.away_pts != null;
  const byDateHome = (a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : a.home < b.home ? -1 : a.home > b.home ? 1 : 0);

  function nextGameDay(games) {
    const last = games.filter(isPlayed).reduce((m, g) => (g.date > m ? g.date : m), "");
    const up = games.filter((g) => !isPlayed(g) && g.date > last).map((g) => g.date).sort();
    return up.length ? up[0] : null;
  }

  function weekEnd(start, days = WEEK_DAYS) {
    const d = new Date(start + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + days - 1);
    return d.toISOString().slice(0, 10);
  }

  // [league points per team per game, {team: shrunk extra combined points in its games}]
  function teamScoring(games) {
    const done = games.filter(isPlayed);
    if (!done.length) return [112, {}];
    const league = done.reduce((s, g) => s + g.home_pts + g.away_pts, 0) / (2 * done.length);
    const totals = {};
    done.forEach((g) => [g.home, g.away].forEach((t) => (totals[t] = totals[t] || []).push(g.home_pts + g.away_pts)));
    const extra = {};
    Object.entries(totals).forEach(([t, v]) => {
      extra[t] = (v.reduce((a, b) => a + b, 0) / v.length - 2 * league) * v.length / (v.length + SCORING_PRIOR_GAMES);
    });
    return [league, extra];
  }

  // Position of each unplayed game in the home and away teams' remaining schedules.
  function remainingIndex(games) {
    const seen = {}, out = {};
    games.filter((g) => !isPlayed(g)).sort(byDateHome).forEach((g) => {
      const h = seen[g.home] || 0, a = seen[g.away] || 0;
      out[`${g.date}|${g.home}|${g.away}`] = [h, a];
      seen[g.home] = h + 1; seen[g.away] = a + 1;
    });
    return out;
  }

  // ratings: {team: healthy rating}; pen: {team: [points missing in its g-th remaining game]}
  function predictGames(ratings, pen, games, start, days = WEEK_DAYS) {
    const end = weekEnd(start, days);
    const [league, extra] = teamScoring(games);
    const idx = remainingIndex(games);
    return games.slice().sort(byDateHome).filter((g) => !isPlayed(g) && g.date >= start && g.date <= end).map((g) => {
      const [gh, ga] = idx[`${g.date}|${g.home}|${g.away}`];
      const ph_ = pen[g.home], pa_ = pen[g.away];
      const eh = ratings[g.home] - ph_[Math.min(gh, ph_.length - 1)];
      const ea = ratings[g.away] - pa_[Math.min(ga, pa_.length - 1)];
      const margin = eh - ea + HOME_COURT;
      const total = 2 * league + ((extra[g.home] || 0) + (extra[g.away] || 0)) / 2;
      let ph = Math.round(total / 2 + margin / 2), pa = Math.round(total / 2 - margin / 2);
      const prob = winProb(eh, ea, true);
      if (ph === pa) { if (prob >= 0.5) ph++; else pa++; }
      return { date: g.date, home: g.home, away: g.away, pred_home: ph, pred_away: pa,
        home_win_prob: Math.round(prob * 1000) / 1000, margin, total, homeIndex: gh, awayIndex: ga };
    });
  }

  // Saved predictions joined to final scores, for games that have been played.
  function grade(preds, games) {
    const finals = {};
    games.filter(isPlayed).forEach((g) => { finals[`${g.date}|${g.home}|${g.away}`] = g; });
    return preds.map((p) => {
      const g = finals[`${p.date}|${p.home}|${p.away}`];
      if (!g) return null;
      const ph = +p.pred_home, pa = +p.pred_away;
      return { ...p, home_pts: g.home_pts, away_pts: g.away_pts,
        correct: (ph > pa) === (g.home_pts > g.away_pts),
        points_off: (Math.abs(ph - g.home_pts) + Math.abs(pa - g.away_pts)) / 2 };
    }).filter(Boolean);
  }

  // "clinched" a top-6 seed, "eliminated" from the top 10, or "". Ignores tiebreakers.
  function clinchStatus(team, teams) {
    if (!gamesPlayed(team)) return "";
    const rivals = teams.filter((o) => o.conference === team.conference && o.id !== team.id);
    if (rivals.filter((o) => o.w + gamesLeft(o) >= team.w).length <= 5) return "clinched";
    if (rivals.filter((o) => o.w > team.w + gamesLeft(team)).length >= 10) return "eliminated";
    return "";
  }

  function simulate(teams, injuries, moves, s, rand = Math.random, players = null) {
    const R = ratings(teams, moves, s, injuries, players);
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
    GAMES, gamesPlayed, gamesLeft, blendWeights, moveTotal, components, healthyBonus, observedMargin, rosterRatings, nextGameDay, weekEnd, teamScoring, predictGames, grade, isPlayed, rosterShare, ROSTER_SHARE, ratings,
    penalties, winProb, simulate, injuryWinsLost, clinchStatus,
  };
})(typeof window !== "undefined" ? window : globalThis);
