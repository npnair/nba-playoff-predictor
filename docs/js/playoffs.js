// Playoff bracket model. Mirrors predictor/playoffs.py; keep the two in sync.
(function (global) {
  "use strict";

  const HOME_COURT = 2.5;
  const LOGISTIC_SCALE = 7.0;
  const WEIGHTS = { rating: 0.5, lineup: 0.3, h2h: 0.2 };
  const H2H_PRIOR_GAMES = 4;
  const STARTER_MINUTES = 36;
  const SLOTS = ["PG", "SG", "SF", "PF", "C"];
  const POS_NUMBER = { PG: 1, G: 1.5, SG: 2, GF: 2.5, SF: 3, F: 3.5, PF: 4, FC: 4.5, C: 5 };
  const HOME_PATTERN = [true, true, false, false, true, false, true];

  // ---------- players ----------
  const posNumber = (pos) => POS_NUMBER[String(pos || "").split("-")[0].trim().toUpperCase()] ?? 3;

  function normalize(name) {
    const plain = String(name).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
    return plain.replace(/\b(jr|sr|ii|iii|iv)\b\.?/g, "").replace(/[^a-z (]/g, "").split("(")[0].replace(/\s+/g, " ").trim();
  }
  const injuryName = (text) => normalize(String(text).split("(")[0]);
  function matchesInjury(player, injured) {
    const p = normalize(player).split(" ").filter(Boolean), i = injured.split(" ").filter(Boolean);
    if (!p.length || !i.length) return false;
    if (p.join(" ") === i.join(" ")) return true;
    return i.length >= 2 && i[0].length === 1 && p[p.length - 1] === i[i.length - 1] && p[0][0] === i[0];
  }

  function outForPlayoffs(players, injuries, gamesLeft) {
    const out = new Set();
    injuries.forEach((j) => {
      if (j.games_out < (gamesLeft[j.team] ?? 82)) return;
      const name = injuryName(j.player);
      players.forEach((p) => { if (p.team === j.team && matchesInjury(p.player, name)) out.add(p.bref_id); });
    });
    return out;
  }

  function startingFive(players, team, out) {
    const pool = players.filter((p) => p.team === team && !out.has(p.bref_id)).sort((a, b) => b.mpg - a.mpg).slice(0, 5);
    pool.sort((a, b) => posNumber(a.pos) - posNumber(b.pos) || b.mpg - a.mpg);
    return pool.map((p, k) => ({ ...p, slot: SLOTS[k] }));
  }
  const lineupValue = (five) => five.reduce((s, p) => s + p.bpm, 0) * STARTER_MINUTES / 48;

  // ---------- season series ----------
  function seasonSeries(games, a, b) {
    const played = [];
    let scheduled = 0;
    games.forEach((g) => {
      if (!((g.home === a && g.away === b) || (g.home === b && g.away === a))) return;
      if (g.home_pts == null || g.away_pts == null) { scheduled++; return; }
      const aHome = g.home === a;
      played.push({ date: g.date, a_home: aHome, margin: (g.home_pts - g.away_pts) * (aHome ? 1 : -1),
                    home: g.home, away: g.away, home_pts: g.home_pts, away_pts: g.away_pts });
    });
    return { played, scheduled };
  }
  function h2hEstimate(played, ratingGap) {
    const adjusted = played.reduce((s, g) => s + g.margin - (g.a_home ? HOME_COURT : -HOME_COURT), 0);
    return (adjusted + H2H_PRIOR_GAMES * ratingGap) / (played.length + H2H_PRIOR_GAMES);
  }

  // ---------- matchups and series ----------
  function makeContext(ratings, players, games, out) {
    const fives = {};
    return { ratings, players, games, out, five: (t) => (fives[t] ||= startingFive(players, t, out)) };
  }

  function matchup(ctx, a, b) {
    const ratingGap = ctx.ratings[a] - ctx.ratings[b];
    const fa = ctx.five(a), fb = ctx.five(b);
    const lineupGap = fa.length === 5 && fb.length === 5 ? lineupValue(fa) - lineupValue(fb) : ratingGap;
    const { played, scheduled } = seasonSeries(ctx.games, a, b);
    const h2h = h2hEstimate(played, ratingGap);
    const margin = WEIGHTS.rating * ratingGap + WEIGHTS.lineup * lineupGap + WEIGHTS.h2h * h2h;
    const slots = SLOTS.map((slot, k) => fa[k] && fb[k] ? { slot, a: fa[k], b: fb[k], edge: fa[k].bpm - fb[k].bpm } : null).filter(Boolean);
    return { a, b, ratingGap, lineupGap, h2h, margin, slots, played, scheduled,
             slotsWon: slots.filter((s) => s.edge > 0).length };
  }

  const gameProb = (margin, home) => 1 / (1 + Math.exp(-(margin + (home ? HOME_COURT : -HOME_COURT)) / LOGISTIC_SCALE));

  function seriesOutcomes(margin, aHomeCourt) {
    let states = new Map([["0,0", 1]]);
    const fin = { a: { 4: 0, 5: 0, 6: 0, 7: 0 }, b: { 4: 0, 5: 0, 6: 0, 7: 0 } };
    HOME_PATTERN.forEach((topSeedHome, i) => {
      const game = i + 1, p = gameProb(margin, topSeedHome === aHomeCourt), next = new Map();
      states.forEach((prob, key) => {
        const [wa, wb] = key.split(",").map(Number);
        [[wa + 1, wb, p], [wa, wb + 1, 1 - p]].forEach(([na, nb, q]) => {
          if (na === 4) fin.a[game] += prob * q;
          else if (nb === 4) fin.b[game] += prob * q;
          else next.set(`${na},${nb}`, (next.get(`${na},${nb}`) || 0) + prob * q);
        });
      });
      states = next;
    });
    fin.p_a = Object.values(fin.a).reduce((s, v) => s + v, 0);
    return fin;
  }

  function seriesPick(out) {
    const side = out.p_a >= 0.5 ? "a" : "b";
    const games = +Object.keys(out[side]).reduce((x, y) => (out[side][y] > out[side][x] ? y : x));
    return { side, games, prob: side === "a" ? out.p_a : 1 - out.p_a };
  }

  function playSeries(ctx, high, low) {
    const m = matchup(ctx, high, low);
    const outcomes = seriesOutcomes(m.margin, true);
    const { side, games, prob } = seriesPick(outcomes);
    return { ...m, kind: "series", outcomes, winner: side === "a" ? high : low, loser: side === "a" ? low : high, games, prob };
  }
  function playGame(ctx, home, away) {
    const m = matchup(ctx, home, away);
    const p = gameProb(m.margin, true);
    return { ...m, kind: "game", p_home: p, winner: p >= 0.5 ? home : away, loser: p >= 0.5 ? away : home, prob: Math.max(p, 1 - p) };
  }

  function conferenceBracket(ctx, seeds) {
    const s = {};
    seeds.forEach((t, k) => { s[k + 1] = t; });
    const g78 = playGame(ctx, s[7], s[8]);
    const g910 = playGame(ctx, s[9], s[10]);
    const last = playGame(ctx, g78.loser, g910.winner);
    const final = { 1: s[1], 2: s[2], 3: s[3], 4: s[4], 5: s[5], 6: s[6], 7: g78.winner, 8: last.winner };
    const seedOf = Object.fromEntries(Object.entries(final).map(([k, t]) => [t, +k]));
    const series = (x, y) => (seedOf[x] < seedOf[y] ? playSeries(ctx, x, y) : playSeries(ctx, y, x));
    const r1 = [series(final[1], final[8]), series(final[4], final[5]), series(final[3], final[6]), series(final[2], final[7])];
    const r2 = [series(r1[0].winner, r1[1].winner), series(r1[2].winner, r1[3].winner)];
    const cf = series(r2[0].winner, r2[1].winner);
    const startSeedOf = Object.fromEntries(seeds.map((t, k) => [t, k + 1]));
    return { playIn: [g78, g910, last], seeds: final, seedOf, startSeedOf, r1, r2, final: cf };
  }

  function fullBracket(ctx, west, east, projWins) {
    const w = conferenceBracket(ctx, west), e = conferenceBracket(ctx, east);
    const a = w.final.winner, b = e.final.winner;
    const [hi, lo] = (projWins[a] ?? 0) >= (projWins[b] ?? 0) ? [a, b] : [b, a];
    return { West: w, East: e, finals: playSeries(ctx, hi, lo) };
  }

  global.PL_PLAYOFFS = {
    WEIGHTS, SLOTS, HOME_COURT, posNumber, normalize, injuryName, matchesInjury, outForPlayoffs, startingFive,
    lineupValue, seasonSeries, h2hEstimate, makeContext, matchup, gameProb, seriesOutcomes, seriesPick,
    playSeries, playGame, conferenceBracket, fullBracket,
  };
})(typeof window !== "undefined" ? window : globalThis);
