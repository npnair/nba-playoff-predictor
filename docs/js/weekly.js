// "This week" tab: a predicted score for every game this week, and how the saved
// predictions did once games were played. Upcoming games follow the Standings tab's inputs and
// redraw on every simulation (the "pl:results" event). Graded games use the prediction saved
// each morning before tipoff, so they never change after the fact.
(function () {
  "use strict";

  const M = window.PL_MODEL, DATA = window.PL_DATA;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const DAY = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
  const SHORT = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  const fmtDay = (d) => DAY.format(new Date(d + "T00:00:00Z"));
  const fmtShort = (d) => SHORT.format(new Date(d + "T00:00:00Z"));
  const addDays = (d, n) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };

  const games = (DATA.games || []).map(([date, home, away, home_pts, away_pts]) => ({ date, home, away, home_pts, away_pts }));
  const saved = (DATA.predictions || []).map(([date, home, away, pred_home, pred_away, home_win_prob]) => ({ date, home, away, pred_home, pred_away, home_win_prob }));
  const start = DATA.weekStart || M.nextGameDay(games);
  const names = Object.fromEntries((DATA.teams || []).map((t) => [t.id, t.name]));
  const nickname = (id) => {
    const w = String(names[id] || id).split(" ");
    return w.slice(-1)[0] === "Blazers" ? "Trail Blazers" : w.slice(-1)[0];
  };
  const pct = (v) => Math.round(v * 100) + "%";

  // ---------- graded predictions (fixed, so drawn once) ----------
  function renderGraded() {
    const graded = M.grade(saved, games).sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
    const record = (list) => {
      const w = list.filter((g) => g.correct).length;
      return { w, l: list.length - w, off: list.length ? list.reduce((s, g) => s + g.points_off, 0) / list.length : 0 };
    };
    const season = record(graded);
    const firstSaved = saved.length ? saved.map((p) => p.date).sort()[0] : null;
    $("weekRecord").innerHTML = graded.length
      ? `<b>${season.w}–${season.l}</b><span><span class="who">Picks this season · ${pct(season.w / graded.length)}</span><span class="how">Scores off by ${season.off.toFixed(1)} points per team on average</span></span>`
      : `<b>0–0</b><span><span class="who">Picks this season</span><span class="how">${firstSaved ? `Tracking started ${fmtShort(firstSaved)}. The first results land after those games are played.` : "No saved predictions yet."}</span></span>`;

    const from = start ? addDays(start, -7) : "";
    const recent = graded.filter((g) => g.date >= from);
    const r = record(recent);
    const rows = recent.map((g) => {
      const predWinner = g.pred_home > g.pred_away ? g.home : g.away;
      return `<tr>
        <td class="l">${esc(fmtDay(g.date))}</td>
        <td class="l"><span class="abbr">${g.away}</span>@ <span class="abbr">${g.home}</span></td>
        <td>${g.pred_away}–${g.pred_home}</td>
        <td><b>${g.away_pts}–${g.home_pts}</b></td>
        <td class="l">${predWinner} <span class="pick ${g.correct ? "ok" : "miss"}">${g.correct ? "✓" : "✗"}</span></td>
        <td>${g.points_off.toFixed(1)}</td>
      </tr>`;
    }).join("");
    $("weekGraded").innerHTML = `<h2>Last 7 days</h2>
      <p class="hint">${recent.length
        ? `The model went <b>${r.w}–${r.l}</b> picking winners over the last week, and its scores were off by ${r.off.toFixed(1)} points per team on average. Each prediction here was saved the morning of the game, before tipoff.`
        : "Nothing graded yet. Each morning the daily update saves that day's predictions before tipoff; once the games are final they show up here with the real score."}</p>
      ${recent.length ? `<div class="scroll"><table class="graded"><thead><tr><th class="l">Date</th><th class="l">Game</th><th>Predicted</th><th>Final</th><th class="l">Pick</th><th title="Average points off per team">Off by</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}`;
  }

  // ---------- upcoming (redrawn with every simulation) ----------
  function outFor(injuries, team, index) {
    return injuries.filter((j) => j.team === team && j.games_out > index && (+j.impact || 0) >= 0.5)
      .map((j) => String(j.player).split("(")[0].trim());
  }
  function bestHealthy(players, team, out) {
    const outSet = new Set(out.map((n) => n.toLowerCase()));
    const rot = players.filter((p) => p.team === team && !outSet.has(String(p.player).toLowerCase()))
      .sort((a, b) => b.mpg - a.mpg).slice(0, 8);
    return rot.sort((a, b) => b.bpm - a.bpm)[0];
  }

  // The week runs in 7-day blocks from the first saved prediction (Nov 14 - 20, Nov 21 - 27, ...), so a
  // game stays on the page after it's played and its prediction gets graded in place.
  const daysBetween = (a, b) => Math.round((new Date(b + "T00:00:00Z") - new Date(a + "T00:00:00Z")) / 86400000);
  const anchor = saved.length ? saved.map((p) => p.date).sort()[0] : start;
  const weekFrom = start && anchor ? addDays(anchor, 7 * Math.floor(Math.max(0, daysBetween(anchor, start)) / 7)) : start;
  const weekTo = weekFrom ? addDays(weekFrom, 6) : null;
  const winner = (homeWins, g) => (homeWins ? [g.home, g.away] : [g.away, g.home]);

  function summaryItem(g) {
    if (g.kind === "upcoming") {
      const [w, l] = winner(g.pred_home > g.pred_away, g);
      const p = Math.max(g.home_win_prob, 1 - g.home_win_prob);
      return `<li class="pick-line"><span class="who"><b>${esc(nickname(w))}</b> beat ${esc(nickname(l))}</span>
        <span class="det">${Math.max(g.pred_home, g.pred_away)}–${Math.min(g.pred_home, g.pred_away)} · ${pct(p)}</span></li>`;
    }
    const [aw, al] = winner(g.home_pts > g.away_pts, g);
    const final = `Final: ${esc(nickname(aw))} ${Math.max(g.home_pts, g.away_pts)}–${Math.min(g.home_pts, g.away_pts)}`;
    if (!g.pred) return `<li class="pick-line"><span class="who"><b>${esc(nickname(aw))}</b> beat ${esc(nickname(al))}</span><span class="det">${final} · no prediction saved</span></li>`;
    const [w, l] = winner(g.pred.pred_home > g.pred.pred_away, g);
    return `<li class="pick-line ${g.correct ? "ok" : "miss"}"><span class="who"><span class="mark">${g.correct ? "✓" : "✗"}</span><b>${esc(nickname(w))}</b> beat ${esc(nickname(l))}</span>
      <span class="det">${final}</span></li>`;
  }

  function gameCard(g, injuries, players) {
    const side = (id, pts, strong, label) => `<div class="side${strong ? " fav" : ""}">
      <span class="abbr">${id}</span><span class="nm">${esc(nickname(id))}<small>${label}</small></span><b>${pts}</b></div>`;
    if (g.kind === "played") {
      const homeWon = g.home_pts > g.away_pts;
      const pred = g.pred ? `<div class="note">Predicted ${esc(nickname(g.pred.pred_home > g.pred.pred_away ? g.home : g.away))} ${Math.max(g.pred.pred_home, g.pred.pred_away)}–${Math.min(g.pred.pred_home, g.pred.pred_away)}
        <span class="pick ${g.correct ? "ok" : "miss"}">${g.correct ? "✓ right winner" : "✗ wrong winner"}</span> · off by ${g.points_off.toFixed(1)} pts</div>` : `<div class="note">No prediction was saved for this game.</div>`;
      return `<article class="game played${g.pred ? (g.correct ? " ok" : " miss") : ""}">
        ${side(g.away, g.away_pts, !homeWon, "away")}${side(g.home, g.home_pts, homeWon, "home")}
        <div class="odds"><span>Final</span></div>${pred}</article>`;
    }
    const homeFav = g.home_win_prob >= 0.5;
    const outH = outFor(injuries, g.home, g.homeIndex), outA = outFor(injuries, g.away, g.awayIndex);
    const bestH = bestHealthy(players, g.home, outH), bestA = bestHealthy(players, g.away, outA);
    const fav = homeFav ? g.home : g.away, favP = homeFav ? g.home_win_prob : 1 - g.home_win_prob;
    const signedBpm = (p) => `${p.bpm >= 0 ? "+" : "−"}${Math.abs(p.bpm).toFixed(1)}`;
    const notes = [];
    if (outA.length || outH.length) notes.push(`<div class="note out">Out: ${[...outA.map((n) => `${esc(n)} (${g.away})`), ...outH.map((n) => `${esc(n)} (${g.home})`)].join(", ")}</div>`);
    if (bestA && bestH) notes.push(`<div class="note">Top players: ${esc(bestA.player)} ${signedBpm(bestA)} · ${esc(bestH.player)} ${signedBpm(bestH)} BPM</div>`);
    return `<article class="game">
      ${side(g.away, g.pred_away, !homeFav, "away")}${side(g.home, g.pred_home, homeFav, "home")}
      <div class="odds"><span>${fav} wins ${pct(favP)}</span><span class="bar"><i style="width:${(favP * 100).toFixed(1)}%;background:${homeFav ? "var(--ball)" : "var(--playin)"}"></i></span></div>
      ${notes.join("")}</article>`;
  }

  function renderWeek({ teams, injuries, results, players }) {
    if (!start) { $("weekDays").innerHTML = `<p class="hint">No games left on the schedule.</p>`; return; }
    const pen = M.penalties(teams, injuries);
    const ratings = {}, penBy = {};
    teams.forEach((t, i) => { ratings[t.id] = results.ratings[i].rating; penBy[t.id] = Array.from(pen[i]); });
    // Upcoming games: live predictions that follow the Standings tab's inputs
    const upcoming = M.predictGames(ratings, penBy, games, start, daysBetween(start, weekTo) + 1).map((p) => ({ ...p, kind: "upcoming" }));
    // Played games this week: the prediction saved before tipoff, graded against the final
    const graded = Object.fromEntries(M.grade(saved, games).map((g) => [`${g.date}|${g.home}|${g.away}`, g]));
    const played = games.filter((g) => M.isPlayed(g) && g.date >= weekFrom && g.date <= weekTo).map((g) => {
      const gr = graded[`${g.date}|${g.home}|${g.away}`];
      return { ...g, kind: "played", pred: gr || null, correct: gr ? gr.correct : null, points_off: gr ? gr.points_off : null };
    });
    const all = [...played, ...upcoming];
    const right = played.filter((g) => g.pred && g.correct).length, total = played.filter((g) => g.pred).length;
    $("weekRange").textContent = `${fmtShort(weekFrom)} – ${fmtShort(weekTo)} · ${all.length} games${total ? ` · picks ${right}–${total - right} so far` : ""}`;

    const days = {};
    all.forEach((g) => (days[g.date] = days[g.date] || []).push(g));
    $("weekDays").innerHTML = Object.keys(days).sort().map((d) => {
      const list = days[d];
      const dayGraded = list.filter((g) => g.kind === "played" && g.pred);
      const dayRight = dayGraded.filter((g) => g.correct).length;
      return `<section class="day"><h3>${esc(fmtDay(d))}${dayGraded.length ? ` <span class="dayrec">${dayRight}–${dayGraded.length - dayRight}</span>` : ""}</h3>
        <div class="day-body">
          <div class="games">${list.map((g) => gameCard(g, injuries, players)).join("")}</div>
          <aside class="day-sum"><h4>${list.some((g) => g.kind === "upcoming") ? "Predicted winners" : "Results"}</h4><ul>${list.map(summaryItem).join("")}</ul></aside>
        </div></section>`;
    }).join("");
  }

  renderGraded();
  window.addEventListener("pl:results", (e) => renderWeek(e.detail));
})();
