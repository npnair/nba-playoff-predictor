// "This week" tab: a predicted score for every game in the next seven days, and how the saved
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

  function renderUpcoming({ teams, injuries, results, players }) {
    if (!start) { $("weekDays").innerHTML = `<p class="hint">No games left on the schedule.</p>`; return; }
    const pen = M.penalties(teams, injuries);
    const ratings = {}, penBy = {};
    teams.forEach((t, i) => { ratings[t.id] = results.ratings[i].rating; penBy[t.id] = Array.from(pen[i]); });
    const preds = M.predictGames(ratings, penBy, games, start);
    $("weekRange").textContent = `${fmtShort(start)} – ${fmtShort(M.weekEnd(start))} · ${preds.length} games`;

    const days = {};
    preds.forEach((p) => (days[p.date] = days[p.date] || []).push(p));
    $("weekDays").innerHTML = Object.entries(days).map(([d, list]) => `
      <section class="day"><h3>${esc(fmtDay(d))}</h3><div class="games">${list.map((p) => {
        const homeFav = p.home_win_prob >= 0.5;
        const outH = outFor(injuries, p.home, p.homeIndex), outA = outFor(injuries, p.away, p.awayIndex);
        const bestH = bestHealthy(players, p.home, outH), bestA = bestHealthy(players, p.away, outA);
        const fav = homeFav ? p.home : p.away, favP = homeFav ? p.home_win_prob : 1 - p.home_win_prob;
        const line = (id, pts, isFav, side) => `<div class="side${isFav ? " fav" : ""}">
          <span class="abbr">${id}</span><span class="nm">${esc(nickname(id))}<small>${side}</small></span><b>${pts}</b></div>`;
        const notes = [];
        if (outA.length || outH.length) notes.push(`<div class="note out">Out: ${[...outA.map((n) => `${esc(n)} (${p.away})`), ...outH.map((n) => `${esc(n)} (${p.home})`)].join(", ")}</div>`);
        if (bestA && bestH) notes.push(`<div class="note">Top players: ${esc(bestA.player)} ${bestA.bpm >= 0 ? "+" : "−"}${Math.abs(bestA.bpm).toFixed(1)} · ${esc(bestH.player)} ${bestH.bpm >= 0 ? "+" : "−"}${Math.abs(bestH.bpm).toFixed(1)} BPM</div>`);
        return `<article class="game">
          ${line(p.away, p.pred_away, !homeFav, "away")}
          ${line(p.home, p.pred_home, homeFav, "home")}
          <div class="odds"><span>${fav} wins ${pct(favP)}</span><span class="bar"><i style="width:${(favP * 100).toFixed(1)}%;background:${homeFav ? "var(--ball)" : "var(--playin)"}"></i></span></div>
          ${notes.join("")}
        </article>`;
      }).join("")}</div></section>`).join("");
  }

  renderGraded();
  window.addEventListener("pl:results", (e) => renderUpcoming(e.detail));
})();
