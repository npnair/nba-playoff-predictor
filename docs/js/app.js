// Page wiring for Playoff Line 2027. The model itself lives in model.js; the default data in data.js.
(function () {
  "use strict";

  const M = window.PL_MODEL;
  const DATA = window.PL_DATA;
  const STORE_KEY = "playoffline-2027-web-v1";
  const CONFS = ["West", "East"];
  const TEAM_FIELDS = [
    ["w", "W", 1, "season"], ["l", "L", 1, "season"], ["point_diff", "PD/G", 0.1, "season"],
    ["win_total", "Line", 0.1, "history"], ["last_net_rating", "Last NRtg", 0.1, "history"],
    ["pre_w", "Pre W", 1, "history"], ["pre_l", "Pre L", 1, "history"],
  ];
  const INT_FIELDS = new Set(["w", "l", "pre_w", "pre_l"]);
  const SLIDERS = ["wLast", "wLine", "wPre", "kRange"];
  // Default slider positions come from the settings the model tuned on this season's games
  // (predictor/learn.py). When those change, visitors' saved sliders reset to the new defaults.
  // kRange runs backwards from the model's evidence games so that right means "this season counts more"
  const K_FLIP = 63;   // slider min + max
  const L = M.LEARNED;
  const lastPct = Math.round(100 * L.last_share);
  const DEFAULTS = { wLast: lastPct, wLine: 100 - lastPct, wPre: 0, kRange: K_FLIP - Math.round(L.evidence_games) };
  const SETTINGS_VERSION = "4:" + JSON.stringify(DEFAULTS);

  const $ = (id) => document.getElementById(id);
  // Saved edits only apply to the exact data they were made on. The label alone isn't enough: a
  // same-day data fix (like a new injury list) keeps the label, so fingerprint the data too.
  const DATA_VERSION = (() => {
    let h = 2166136261;
    for (const ch of JSON.stringify([DATA.asOf, DATA.teams, DATA.injuries, DATA.moves])) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    return DATA.asOf + "#" + (h >>> 0).toString(36);
  })();
  const evidenceGames = () => K_FLIP - +$("kRange").value;
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const pct = (v) => (v >= 0.995 ? ">99%" : v <= 0.005 ? (v > 0 ? "<1%" : "0%") : Math.round(v * 100) + "%");
  const signed = (v) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1);

  const players = (DATA.players || []).map(([team, bref_id, player, pos, mpg, bpm]) => ({ team, bref_id, player, pos, mpg, bpm }));
  const rosterW = M.rosterShare(players);
  const games = (DATA.games || []).map(([date, home, away, home_pts, away_pts]) => ({ date, home, away, home_pts, away_pts }));

  let teams = clone(DATA.teams);
  let injuries = clone(DATA.injuries);
  let moves = clone(DATA.moves);
  let results = null;

  // ---------- persistence ----------
  function load() {
    try {
      const s = JSON.parse(localStorage.getItem(STORE_KEY) || "null");
      if (!s) return;
      // Model settings carry over between visits, unless the defaults have changed since
      if (s.settingsVersion === SETTINGS_VERSION) SLIDERS.concat(["simSel"]).forEach((k) => { if (s[k] != null) $(k).value = s[k]; });
      // Edits to the data only apply to the data they were made on. Once the daily pipeline
      // publishes newer standings, the visitor sees the new data instead of their old copy.
      if (s.dataVersion !== DATA_VERSION) return;
      if (Array.isArray(s.teams)) s.teams.forEach((x) => {
        const t = teams.find((t) => t.id === x.id);
        if (t) TEAM_FIELDS.forEach(([f]) => { if (x[f] != null && !isNaN(+x[f])) t[f] = +x[f]; });
      });
      const known = (j) => teams.some((t) => t.id === j.team);
      // Saved before the games-missed column existed: use the repo's injuries instead
      if (Array.isArray(s.injuries) && s.injuries.every((j) => "games_missed" in j)) injuries = s.injuries.filter(known);
      if (Array.isArray(s.moves)) moves = s.moves.filter(known);
      if (s.asof != null) $("asof").value = s.asof;
    } catch (e) { /* storage unavailable: use repo data */ }
  }
  function save() {
    try {
      const s = { dataVersion: DATA_VERSION, settingsVersion: SETTINGS_VERSION, teams, injuries, moves };
      SLIDERS.concat(["asof", "simSel"]).forEach((k) => { s[k] = $(k).value; });
      localStorage.setItem(STORE_KEY, JSON.stringify(s));
    } catch (e) { /* ignore */ }
  }

  // ---------- settings ----------
  function settings() {
    return {
      weightLast: $("wLast").value, weightLine: $("wLine").value, weightPreseason: $("wPre").value,
      evidenceGames: evidenceGames(), formHalfLife: L.form_half_life, sims: $("simSel").value,
    };
  }
  function showWeights() {
    const [a, b, c] = M.blendWeights(settings());
    // Overall mix for an average team today, games so far included
    const gp = teams.reduce((s, t) => s + M.gamesPlayed(t), 0) / teams.length, k = evidenceGames();
    // Team results and pre-season views share what's left after the roster's player-stats slice
    const rest = 1 - rosterW;
    const g = rest * gp / (gp + k), pr = rest - g, r = (v) => Math.round(v * 100) + "%";
    $("kVal").textContent = gp ? `${r(g)} today (pre-season = ${k} games)` : `pre-season = ${k} games`;
    [["vLast", "mix1", a], ["vLine", "mix2", b], ["vPre", "mix3", c]].forEach(([label, bar, v]) => {
      $(label).textContent = Math.round(v * 100) + "%";
      $(bar).style.width = pr * v * 100 + "%";
    });
    $("mix4").style.width = g * 100 + "%";
    $("mix5").style.width = rosterW * 100 + "%";
    $("mixkeyGames").style.display = gp ? "" : "none";
    $("mixkeyRoster").style.display = rosterW ? "" : "none";
    $("todayMix").textContent = gp ? `Today's mix (about ${Math.round(gp)} games played): ${r(g)} games so far · ${r(rosterW)} current roster's player stats · ${r(pr * a)} last season · ${r(pr * b)} win total${c * pr >= 0.005 ? ` · ${r(pr * c)} preseason games` : ""}.` : "";
  }

  // ---------- boards ----------
  const probCell = (v, cls) =>
    `<td><div class="prob">${pct(v)}<span class="bar ${cls}"><i style="width:${(v * 100).toFixed(1)}%"></i></span></div></td>`;
  function statusChip(t) {
    const s = M.clinchStatus(t, teams);
    if (s === "clinched") return '<span class="chip cl">Clinched</span>';
    if (s === "eliminated") return '<span class="chip out">Out</span>';
    return "";
  }

  function render() {
    const { ratings, rows } = results;
    CONFS.forEach((conf) => {
      const list = rows.filter((r) => teams[r.index].conference === conf).sort((a, b) => a.avgSeed - b.avgSeed);
      const body = list.map((r, k) => {
        const t = teams[r.index];
        const lost = M.injuryWinsLost(t, injuries, evidenceGames(), rosterW);
        const moved = M.moveTotal(moves, t.id);
        const injNames = injuries.filter((j) => j.team === t.id).map((j) => j.player).join("; ") || "No injuries listed";
        const moveNames = moves.filter((m) => m.team === t.id).map((m) => m.move).join("; ") || "No moves listed";
        const cls = [k < 6 ? "top" : k < 10 ? "pi" : "", k === 6 ? "cut" : "", k === 10 ? "cut2" : ""].join(" ");
        return `<tr class="${cls}">
          <td class="seed">${k + 1}</td>
          <td class="team"><span class="abbr">${t.id}</span><span class="tname">${esc(t.name)}</span>${statusChip(t)}</td>
          <td class="rec">${t.w}-${t.l}</td>
          <td class="mono">${signed(ratings[r.index].rating)}</td>
          <td class="mono" title="${esc(moveNames)}">${Math.abs(moved) < 0.05 ? "—" : signed(moved)}</td>
          <td class="inj${Math.abs(lost) < 0.05 ? " zero" : lost < 0 ? " gain" : ""}" title="${esc(injNames)}">${Math.abs(lost) < 0.05 ? "—" : signed(-lost)}</td>
          <td class="pw">${r.projWins.toFixed(1)}</td>
          ${probCell(r.top6, "b-top")}${probCell(r.playIn, "b-pi")}${probCell(r.playoffs, "b-po")}
        </tr>`;
      }).join("");
      $("board-" + conf).innerHTML = `
        <div class="board-head"><h2>${conf}ern Conference</h2><span class="eyebrow">${esc($("asof").value)}</span></div>
        <div class="picks">
          <div><span class="eyebrow">Predicted top 6 · straight to playoffs</span><ol>${list.slice(0, 6).map((r) => `<li>${esc(teams[r.index].name)}</li>`).join("")}</ol></div>
          <div><span class="eyebrow">Predicted 7–10 · play-in</span><ol start="7">${list.slice(6, 10).map((r) => `<li>${esc(teams[r.index].name)}</li>`).join("")}</ol></div>
        </div>
        <div class="scroll"><table>
          <thead><tr><th class="l">#</th><th class="l">Team</th><th>Rec</th>
            <th title="Healthy rating, points per game vs average">Rtg</th>
            <th title="Offseason moves, points added to last season's net rating">Moves</th>
            <th title="Net wins injuries cost from here: games still to miss, minus credit for games already played shorthanded">Inj</th>
            <th>Proj W</th><th>Top 6</th><th>Play-in</th><th>Playoffs</th></tr></thead>
          <tbody>${body}</tbody>
        </table></div>
        <div class="legend">
          <span><i class="sw" style="border-color:var(--ball)"></i>Playoff line (top 6)</span>
          <span><i class="sw" style="border-color:var(--playin)"></i>Play-in line (top 10)</span>
          <span>Rtg = healthy margin vs average · Moves = roster change vs last season · Inj = net wins from injuries (+ when players already missing will return)</span>
        </div>`;
    });
    const gp = teams.reduce((sum, t) => sum + M.gamesPlayed(t), 0) / teams.length;
    const k = evidenceGames();
    $("gpStat").textContent = gp.toFixed(1);
    $("wStat").textContent = Math.round((gp / (gp + k)) * 100) + "%";
    $("injStat").textContent = injuries.length;
  }

  // Same data, same settings -> same simulated seasons, so the bracket doesn't reshuffle on reload
  function seededRandom(text) {
    let h = 2166136261;
    for (const ch of String(text)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    let a = h >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function run() {
    const btn = $("runBtn");
    btn.disabled = true;
    btn.textContent = "Simulating…";
    requestAnimationFrame(() => setTimeout(() => {
      results = M.simulate(teams, injuries, moves, settings(), seededRandom(DATA.asOf), players, games);
      $("simStat").textContent = (+$("simSel").value).toLocaleString();
      render();
      window.dispatchEvent(new CustomEvent("pl:results", { detail: { teams, injuries, results, players, settings: settings() } }));
      btn.disabled = false;
      btn.textContent = "Simulate season";
      save();
    }, 20));
  }

  // ---------- editors ----------
  const teamOptions = (selected) =>
    teams.map((t) => t.id).sort().map((id) => `<option${id === selected ? " selected" : ""}>${id}</option>`).join("");

  function buildTeamEditor() {
    $("editGrid").innerHTML = CONFS.map((conf) => `<div class="scroll"><h3>${conf}</h3><table class="edit">
      <thead><tr><th></th><th class="grp" colspan="3">2026–27 season</th><th class="grp" colspan="4">History</th></tr>
      <tr><th class="l">Team</th>${TEAM_FIELDS.map(([, label]) => `<th>${label}</th>`).join("")}</tr></thead>
      <tbody>${teams.filter((t) => t.conference === conf).map((t) => `<tr>
        <td style="text-align:left"><span class="abbr">${t.id}</span></td>
        ${TEAM_FIELDS.map(([f, label, step]) => `<td><input id="in-${t.id}-${f}" data-id="${t.id}" data-f="${f}" type="number" step="${step}" value="${t[f]}" aria-label="${esc(t.name)} ${label}"></td>`).join("")}
      </tr>`).join("")}</tbody></table></div>`).join("");

    $("editGrid").querySelectorAll("input").forEach((input) => input.addEventListener("change", (e) => {
      const t = teams.find((t) => t.id === e.target.dataset.id);
      const f = e.target.dataset.f;
      let v = parseFloat(e.target.value);
      if (isNaN(v)) v = 0;
      if (INT_FIELDS.has(f)) v = Math.max(0, Math.min(M.GAMES, Math.round(v)));
      t[f] = v;
      if (t.w + t.l > M.GAMES) t[f] = M.GAMES - (f === "w" ? t.l : t.w);
      e.target.value = t[f];
      save();
    }));
  }

  // Shared editor for the injuries and moves tables.
  function buildListEditor({ tableId, msgId, list, textField, textLabel, columns }) {
    const order = list.map((item, k) => [item, k]).sort((a, b) => a[0].team.localeCompare(b[0].team));
    $(tableId).innerHTML = `<thead><tr><th class="l">Team</th><th class="l">${textLabel}</th>${columns.map((c) => `<th>${c.label}</th>`).join("")}<th></th></tr></thead>
      <tbody>${order.map(([item, k]) => `<tr>
        <td style="text-align:left"><select id="${tableId}-${k}-team" data-k="${k}" data-f="team" aria-label="Team">${teamOptions(item.team)}</select></td>
        <td style="text-align:left"><input type="text" id="${tableId}-${k}-${textField}" data-k="${k}" data-f="${textField}" value="${esc(item[textField])}" aria-label="${textLabel}"></td>
        ${columns.map((c) => `<td><input type="number" id="${tableId}-${k}-${c.field}" data-k="${k}" data-f="${c.field}" step="${c.step}" min="${c.min}" max="${c.max}" value="${item[c.field]}" aria-label="${c.label}"></td>`).join("")}
        <td><button class="x" data-del="${k}" aria-label="Remove ${esc(item[textField])}">Remove</button></td>
      </tr>`).join("")}</tbody>`;

    $(tableId).querySelectorAll("input,select").forEach((el) => el.addEventListener("change", (e) => {
      const item = list[+e.target.dataset.k];
      const f = e.target.dataset.f;
      const col = columns.find((c) => c.field === f);
      item[f] = col ? Math.max(col.min, Math.min(col.max, parseFloat(e.target.value) || 0)) : e.target.value;
      save();
      $(msgId).textContent = "Press Simulate season to apply.";
    }));
    $(tableId).querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => {
      list.splice(+b.dataset.del, 1);
      buildEditors();
      save();
      $(msgId).textContent = "Removed. Press Simulate season to apply.";
    }));
  }

  function buildEditors() {
    buildListEditor({
      tableId: "injTable", msgId: "injMsg", list: injuries, textField: "player", textLabel: "Player",
      columns: [
        { field: "impact", label: "Impact (pts)", step: 0.1, min: 0, max: 15 },
        { field: "games_out", label: "Games out", step: 1, min: 0, max: 82 },
        { field: "games_missed", label: "Missed so far", step: 1, min: 0, max: 82 },
      ],
    });
    buildListEditor({
      tableId: "movesTable", msgId: "movesMsg", list: moves, textField: "move", textLabel: "Move",
      columns: [{ field: "impact", label: "Impact (pts)", step: 0.1, min: -15, max: 15 }],
    });
  }

  // ---------- standings paste / export ----------
  function applyPaste() {
    let ok = 0;
    const bad = [];
    $("paste").value.split(/\n/).map((s) => s.trim()).filter(Boolean).forEach((line) => {
      const p = line.split(/[,\t;]+|\s(?=-?\d)/).map((s) => s.trim()).filter(Boolean);
      const t = teams.find((t) => t.id === (p[0] || "").toUpperCase());
      const w = parseInt(p[1], 10), l = parseInt(p[2], 10), pd = parseFloat(p[3]);
      if (!t || isNaN(w) || isNaN(l) || w + l > M.GAMES) { bad.push(p[0] || line); return; }
      t.w = w; t.l = l; t.point_diff = isNaN(pd) ? 0 : pd;
      ok++;
    });
    buildTeamEditor();
    save();
    $("pasteMsg").textContent = `Updated ${ok} team${ok === 1 ? "" : "s"}.` +
      (bad.length ? ` Skipped: ${bad.join(", ")}. Use abbreviations like OKC, NYK.` : " Press Simulate season to refresh.");
  }

  function exportStandings() {
    const csv = "id,w,l,point_diff\n" + teams.map((t) => `${t.id},${t.w},${t.l},${t.point_diff}`).join("\n") + "\n";
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "standings.csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    $("pasteMsg").textContent = "Saved standings.csv. Replace data/standings.csv in the repo with it.";
  }

  function restore() {
    teams = clone(DATA.teams);
    injuries = clone(DATA.injuries);
    moves = clone(DATA.moves);
    Object.entries(DEFAULTS).forEach(([id, v]) => { $(id).value = v; });
    $("asof").value = DATA.asOf;
    showWeights();
    buildTeamEditor();
    buildEditors();
    save();
    $("pasteMsg").textContent = "Restored the data published in the repo.";
    run();
  }

  // ---------- wiring ----------
  ["wLast", "wLine", "wPre", "kRange"].forEach((id) => $(id).addEventListener("input", showWeights));
  $("runBtn").addEventListener("click", run);
  $("asof").addEventListener("change", () => { save(); if (results) render(); });
  $("pasteBtn").addEventListener("click", applyPaste);
  $("exportBtn").addEventListener("click", exportStandings);
  $("resetBtn").addEventListener("click", restore);
  $("addInj").addEventListener("click", () => {
    injuries.push({ team: "OKC", player: "New injury", impact: 1, games_out: 10, games_missed: 0 });
    buildEditors(); save();
    const el = $(`injTable-${injuries.length - 1}-player`); if (el) { el.focus(); el.select(); }
  });
  $("addMove").addEventListener("click", () => {
    moves.push({ team: "ATL", move: "+ New player", impact: 1 });
    buildEditors(); save();
    const el = $(`movesTable-${moves.length - 1}-move`); if (el) { el.focus(); el.select(); }
  });

  $("asof").value = DATA.asOf;
  Object.entries(DEFAULTS).forEach(([id, v]) => { $(id).value = v; });
  load();
  showWeights();
  buildTeamEditor();
  buildEditors();
  run();
})();
