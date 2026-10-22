// Playoff bracket tab. Rebuilds whenever the standings simulation reruns (the "pl:results" event).
(function () {
  "use strict";

  const PO = window.PL_PLAYOFFS, M = window.PL_MODEL, DATA = window.PL_DATA;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (v) => (v >= 0.995 ? ">99%" : v <= 0.005 ? "<1%" : Math.round(v * 100) + "%");
  const signed = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);

  const games = (DATA.games || []).map(([date, home, away, home_pts, away_pts]) => ({ date, home, away, home_pts, away_pts }));
  const players = (DATA.players || []).map(([team, bref_id, player, pos, mpg, bpm, minutes]) => ({ team, bref_id, player, pos, mpg, bpm, minutes }));

  let state = null;          // { bracket, teamsById, seedOf }
  let selected = "finals";

  // ---------- tabs ----------
  const tabs = [["standings", $("tab-standings"), $("view-standings")], ["bracket", $("tab-bracket"), $("view-bracket")]];
  function show(name, focus) {
    tabs.forEach(([n, tab, view]) => {
      const on = n === name;
      tab.setAttribute("aria-selected", on);
      tab.tabIndex = on ? 0 : -1;
      view.hidden = !on;
      if (on && focus) tab.focus();
    });
    if (name === "bracket" && location.hash !== "#bracket") history.replaceState(null, "", "#bracket");
    if (name === "standings" && location.hash) history.replaceState(null, "", location.pathname + location.search);
  }
  tabs.forEach(([n, tab], i) => {
    tab.addEventListener("click", () => show(n));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") show(tabs[(i + 1) % 2][0], true);
    });
  });
  window.addEventListener("hashchange", () => show(location.hash === "#bracket" ? "bracket" : "standings"));
  if (location.hash === "#bracket") show("bracket");

  // ---------- build from the latest simulation ----------
  function nickname(name) {
    const words = String(name).split(" ");
    return words.slice(-1)[0] === "Blazers" ? "Trail Blazers" : words.slice(-1)[0];
  }

  window.addEventListener("pl:results", (e) => {
    const { teams, injuries, results } = e.detail;
    const pen = M.penalties(teams, injuries);
    const ratings = {}, gamesLeft = {}, projWins = {};
    teams.forEach((t, i) => {
      const left = M.gamesLeft(t);
      gamesLeft[t.id] = left;
      ratings[t.id] = results.ratings[i].rating - pen[i][left];
      projWins[t.id] = results.rows[i].projWins;
    });
    const seeds = (conf) => results.rows.filter((r) => teams[r.index].conference === conf)
      .sort((a, b) => a.avgSeed - b.avgSeed).slice(0, 10).map((r) => teams[r.index].id);
    const ctx = PO.makeContext(ratings, players, games, PO.outForPlayoffs(players, injuries, gamesLeft));
    const bracket = PO.fullBracket(ctx, seeds("West"), seeds("East"), projWins);
    state = { bracket, teamsById: Object.fromEntries(teams.map((t) => [t.id, t])) };
    render();
  });

  function seriesByKey(key) {
    const b = state.bracket;
    if (key === "finals") return { s: b.finals, title: "NBA Finals", seedOf: {} };
    const [conf, round, idx] = key.split("-");
    const c = b[conf];
    const names = { r1: "first round", r2: "second round", final: "conference finals", pi: "play-in" };
    const s = round === "final" ? c.final : round === "pi" ? c.playIn[+idx] : c[round][+idx];
    return { s, title: `${conf} ${names[round]}`, seedOf: round === "pi" ? c.startSeedOf : c.seedOf, conf };
  }

  // ---------- bracket ----------
  function card(key, s, seedOf) {
    const t = state.teamsById;
    const row = (id, isWinner) => {
      const w = isWinner ? 4 : s.games - 4;
      return `<div class="team${isWinner ? " win" : ""}"><span class="sd">${seedOf[id] ?? ""}</span><span class="ab">${esc(id)}</span>
        <span class="nm">${esc(nickname(t[id].name))}</span><span class="w">${w}</span></div>`;
    };
    // higher seed (s.a) on top
    return `<button type="button" class="series${selected === key ? " sel" : ""}" data-key="${key}"
        aria-label="${esc(t[s.a].name)} vs ${esc(t[s.b].name)}: ${esc(t[s.winner].name)} in ${s.games}">
      ${row(s.a, s.winner === s.a)}${row(s.b, s.winner === s.b)}
      <div class="call">${esc(s.winner)} in ${s.games} · ${pct(s.prob)}</div></button>`;
  }

  function render() {
    if (!state) return;
    const b = state.bracket, t = state.teamsById;
    if (!players.length) {
      $("bracket").innerHTML = '<p class="empty-note">Player data arrives with the next daily update. Until then the bracket can\'t compare starting fives.</p>';
      return;
    }
    const col = (cls, title, items) => `<div class="round ${cls}"><h3>${title}</h3><div class="cards">${items.join("")}</div></div>`;
    const conf = (c) => b[c];
    $("bracket").innerHTML = [
      col("w1", "West · First round", conf("West").r1.map((s, i) => card(`West-r1-${i}`, s, conf("West").seedOf))),
      col("w2", "West · Second round", conf("West").r2.map((s, i) => card(`West-r2-${i}`, s, conf("West").seedOf))),
      col("w3", "West finals", [card("West-final-0", conf("West").final, conf("West").seedOf)]),
      col("finals", "NBA Finals", [card("finals", b.finals, {})]),
      col("e3", "East finals", [card("East-final-0", conf("East").final, conf("East").seedOf)]),
      col("e2", "East · Second round", conf("East").r2.map((s, i) => card(`East-r2-${i}`, s, conf("East").seedOf))),
      col("e1", "East · First round", conf("East").r1.map((s, i) => card(`East-r1-${i}`, s, conf("East").seedOf))),
    ].join("");

    const f = b.finals;
    $("champ").innerHTML = `<b>${esc(f.winner)}</b><span><span class="eyebrow">Predicted champion</span>
      <span class="who">${esc(t[f.winner].name)}</span><span class="how">over the ${esc(nickname(t[f.loser].name))} in ${f.games} · ${pct(f.prob)} to win the Finals</span></span>`;

    $("playin").innerHTML = ["West", "East"].map((c) => {
      const [g78, g910, last] = b[c].playIn;
      const li = (g, i, text) => `<li><button type="button" data-key="${c}-pi-${i}">${text}</button> <span class="pct">${pct(g.prob)}</span></li>`;
      return `<div class="box"><h3>${c} play-in</h3><ol>
        ${li(g78, 0, `${esc(g78.winner)} beats ${esc(g78.loser)} → 7 seed`)}
        ${li(g910, 1, `${esc(g910.winner)} beats ${esc(g910.loser)} → advances`)}
        ${li(last, 2, `${esc(last.winner)} beats ${esc(last.loser)} → 8 seed`)}</ol></div>`;
    }).join("");

    renderDetail();
  }

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-key]");
    if (!btn || !state || !btn.closest("#view-bracket")) return;
    selected = btn.dataset.key;
    render();
    $("seriesDetail").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // ---------- detail ----------
  function factor(label, weight, value, a, b, note) {
    const width = Math.min(50, Math.abs(value) * 5);
    const side = value >= 0 ? `left:50%;width:${width}%` : `right:50%;width:${width}%`;
    const who = value >= 0 ? a : b;
    return `<div class="factor"><span class="lbl">${label} <small>${weight}</small></span>
      <span class="val">${Math.abs(value) < 0.05 ? "even" : `${esc(who)} ${signed(Math.abs(value))}`}</span>
      <span class="diverge"><i class="${value < 0 ? "b" : ""}" style="${side}"></i></span>
      ${note ? `<span class="note">${note}</span>` : ""}</div>`;
  }

  function renderDetail() {
    const { s, title, seedOf } = seriesByKey(selected);
    const t = state.teamsById, a = s.a, b = s.b;
    const name = (id) => esc(nickname(t[id].name));
    const seed = (id) => (seedOf[id] ? `(${seedOf[id]}) ` : "");
    const W = PO.WEIGHTS;

    let verdict, sub;
    if (s.kind === "series") {
      verdict = `<em>${name(s.winner)}</em> in ${s.games}`;
      sub = `${pct(s.prob)} to win the series · the ${name(a)} have home court`;
    } else {
      verdict = `<em>${name(s.winner)}</em> win`;
      sub = `${pct(s.prob)} to win this single game · played at the ${name(a)}' arena`;
    }

    const h2hRecord = s.played.length
      ? (() => { const aw = s.played.filter((g) => g.margin > 0).length; return `${aw}-${s.played.length - aw} ${aw >= s.played.length - aw ? esc(a) : esc(b)}`; })()
      : null;
    const h2hNote = s.played.length
      ? `Season series ${h2hRecord} (from ${esc(a)}'s side: ${s.played.map((g) => signed(g.margin)).join(", ")}). Counted alongside 4 games' worth of team strength.`
      : `They haven't played yet this season${s.scheduled ? ` (${s.scheduled} game${s.scheduled > 1 ? "s" : ""} scheduled)` : ""}, so this follows team strength.`;

    const factors = `
      ${factor("Team strength", Math.round(W.rating * 100) + "%", s.ratingGap, a, b, "Gap in team rating from the standings model, after injuries.")}
      ${factor("Starting five", Math.round(W.lineup * 100) + "%", s.lineupGap, a, b, `${esc(s.slotsWon >= 3 ? a : b)} wins ${Math.max(s.slotsWon, s.slots.length - s.slotsWon)} of ${s.slots.length} position matchups.`)}
      ${factor("Season series", Math.round(W.h2h * 100) + "%", s.h2h, a, b, h2hNote)}
      <div class="factor total"><span class="lbl">Combined edge</span>
        <span class="val">${Math.abs(s.margin) < 0.05 ? "even" : `${esc(s.margin >= 0 ? a : b)} by ${Math.abs(s.margin).toFixed(1)} pts a game`}</span>
        <span class="note">On a neutral court. Home court adds 2.5 points to whoever is hosting.</span></div>`;

    let dist = "";
    if (s.kind === "series") {
      const cells = [[a, 4, s.outcomes.a[4]], [a, 5, s.outcomes.a[5]], [a, 6, s.outcomes.a[6]], [a, 7, s.outcomes.a[7]],
                     [b, 7, s.outcomes.b[7]], [b, 6, s.outcomes.b[6]], [b, 5, s.outcomes.b[5]], [b, 4, s.outcomes.b[4]]];
      const max = Math.max(...cells.map((c) => c[2]));
      dist = `<div><h3>Every possible result</h3><div class="dist">${cells.map(([id, g, p]) => `
        <div><span class="pc">${pct(p)}</span><span class="bar ${id === b ? "b" : ""}" style="height:${Math.round(90 * p / max)}px"></span>
        <span class="res"><b>${esc(id)}</b>4-${g - 4}</span></div>`).join("")}</div></div>`;
    }

    const posRows = s.slots.map((x) => `<tr><td>${x.slot}</td>
      <td class="pl" title="${esc(x.a.player)}">${esc(x.a.player)}</td><td class="num mono">${signed(x.a.bpm)}</td>
      <td class="pl" title="${esc(x.b.player)}">${esc(x.b.player)}</td><td class="num mono">${signed(x.b.bpm)}</td>
      <td class="num edge ${x.edge >= 0 ? "a" : "b"}">${Math.abs(x.edge) < 0.05 ? "even" : `${esc(x.edge >= 0 ? a : b)} ${signed(Math.abs(x.edge))}`}</td></tr>`).join("");
    const positions = s.slots.length ? `<div><h3>Position by position</h3>
      <div class="scroll"><table class="pos"><thead><tr><th>Pos</th><th>${esc(a)}</th><th class="num">BPM</th><th>${esc(b)}</th><th class="num">BPM</th><th class="num">Edge</th></tr></thead>
      <tbody>${posRows}</tbody></table></div>
      <p class="empty-note" style="margin-top:6px">Box Plus/Minus: points per 100 possessions above an average player. Small samples are pulled toward replacement level.</p></div>` : "";

    const gamesList = s.played.length ? `<div><h3>This season</h3><ul class="games-list">${s.played.map((g) =>
      `<li><span class="d">${esc(g.date)}</span><span>${esc(g.away)} ${g.away_pts} @ ${esc(g.home)} ${g.home_pts}</span></li>`).join("")}</ul></div>` : "";

    $("seriesDetail").innerHTML = `
      <div class="sd-head"><div><div class="eyebrow">${esc(title)}</div>
        <h2>${seed(a)}${esc(t[a].name)} vs ${seed(b)}${esc(t[b].name)}</h2></div>
        <div style="text-align:right"><div class="verdict">${verdict}</div><div class="hint">${sub}</div></div></div>
      <div class="sd-grid"><div><h3>Why</h3><div class="factors">${factors}</div></div>${dist}</div>
      <div class="sd-grid">${positions}${gamesList}</div>`;
  }
})();
