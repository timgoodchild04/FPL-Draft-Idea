"use strict";

// --- helpers --------------------------------------------------------------
async function api(path, opts) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}
const el = (id) => document.getElementById(id);
const app = () => el("app");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function toast(msg, isErr) {
  const t = el("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast"), 3200);
}
function help(title, bodyHtml) {
  return `<div class="help"><h4>ℹ️ ${esc(title)}</h4>${bodyHtml}</div>`;
}

// --- routing --------------------------------------------------------------
const views = {};
function setView(name) {
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  (views[name] || (() => {}))();
}
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

async function refreshBadge() {
  try {
    const st = await api("/api/custom/status");
    el("statusBadge").textContent = !st.has_season ? "Not set up"
      : st.fixtures_generated ? "🔒 Fixtures locked"
      : st.can_generate ? "Ready to generate"
      : st.both_filled ? "Check team counts" : "Awaiting teams";
  } catch { el("statusBadge").textContent = ""; }
}

// ============================ SETUP =======================================
const ROSTER_ROWS = 7;

views.setup = async function () {
  const st = await api("/api/custom/status");
  const helpBox = help("Setup - enter your two divisions, then generate the season",
    `<ol>
       <li>Put <b>7 team IDs</b> in each division - the number from a team's URL,
         e.g. <code>.../entry/<b>254949</b>/...</code>. That's all: the player's name is
         pulled from the site automatically.</li>
       <li>Hit <b>Save teams</b>. Every id is checked - if one isn't a real FPL Draft
         team, the save is rejected so you can fix it.</li>
       <li>With both divisions equal size, hit <b>Generate fixtures</b> - it builds the
         random 35-gameweek schedule <b>and locks it</b> (one time only).</li>
     </ol>
     <p>Then see <b>Fixtures</b> and <b>League</b>, and use <b>Sync latest results</b> after
     each gameweek. <b>Start over</b> clears everything to redo a season.</p>`);

  const divs = st.divisions || [{ entries: [] }, { entries: [] }];
  const entriesA = (divs[0] && divs[0].entries) || [];
  const entriesB = (divs[1] && divs[1].entries) || [];
  const locked = st.has_season && st.fixtures_generated;
  const rowCount = Math.max(ROSTER_ROWS, entriesA.length, entriesB.length);

  const rows = (side, entries) => Array.from({ length: rowCount }, (_, i) => {
    const e = entries[i] || {};
    const iv = (e.entry_id != null) ? `value="${e.entry_id}"` : "";
    const nm = e.name ? esc(e.name) : "&mdash;";
    return `<div class="row" style="gap:8px;margin-bottom:6px;align-items:center">
        <div style="flex:1"><input id="${side}-id-${i}" placeholder="Team ID ${i + 1}" inputmode="numeric" ${iv} ${locked ? "disabled" : ""}></div>
        <div style="flex:2"><span class="muted" id="${side}-nm-${i}">${nm}</span></div>
      </div>`;
  }).join("");

  let sizeNote = "";
  if (st.both_filled && !st.sizes_equal)
    sizeNote = `<p class="down">⚠ The divisions have different sizes (${divs[0].teams} vs ${divs[1].teams}). They must be equal to generate fixtures.</p>`;

  app().innerHTML = helpBox + `
    <h2>Setup</h2>
    <div class="card">
      <h3>1. Your two divisions</h3>
      <div class="two-col">
        <div><h4 style="color:var(--purple)">Division A</h4>
          <div class="row" style="gap:8px"><div style="flex:1"><label>Team ID</label></div><div style="flex:2"><label>Player</label></div></div>
          ${rows("a", entriesA)}</div>
        <div><h4 style="color:var(--purple)">Division B</h4>
          <div class="row" style="gap:8px"><div style="flex:1"><label>Team ID</label></div><div style="flex:2"><label>Player</label></div></div>
          ${rows("b", entriesB)}</div>
      </div>
      ${sizeNote}
      ${locked ? '<p class="muted">🔒 Teams locked (fixtures generated). Use “Start over” to change them.</p>'
               : '<br><button class="btn" id="saveBtn">Save teams</button>'}
    </div>

    <div class="card" style="margin-top:18px">
      <h3>2. Generate the season</h3>
      ${locked
        ? `<p>🔒 <b>Fixtures generated and locked.</b> The schedule is fixed for the season.</p>
           <button class="btn green" id="syncBtn">Sync latest results</button>`
        : `<p class="muted">Enabled once both divisions are saved and equal size.</p>
           <button class="btn green" id="genBtn" ${st.can_generate ? "" : "disabled"}>Generate fixtures (one time only)</button>`}
      ${st.has_season ? '<button class="btn pink small" id="resetBtn" style="margin-left:8px">Start over</button>' : ""}
    </div>`;

  const collect = (side) => {
    const out = [];
    for (let i = 0; i < rowCount; i++) {
      const idv = el(`${side}-id-${i}`).value.trim();
      if (!idv) continue;
      if (!/^\d+$/.test(idv)) throw new Error(`Row ${i + 1}: team ID must be a number.`);
      out.push(Number(idv));
    }
    return out;
  };

  // Live name lookup: as soon as an id is entered, fetch and show the manager.
  async function lookupName(side, i) {
    const inp = el(`${side}-id-${i}`), span = el(`${side}-nm-${i}`);
    if (!inp || !span) return;
    const v = inp.value.trim();
    if (!v) { span.textContent = "—"; span.className = "muted"; return; }
    if (!/^\d+$/.test(v)) { span.textContent = "must be a number"; span.className = "down"; return; }
    span.textContent = "looking up…"; span.className = "muted";
    try {
      const r = await api(`/api/custom/lookup?entry_id=${v}`);
      span.textContent = r.name; span.className = "";
    } catch { span.textContent = "not found"; span.className = "down"; }
  }
  if (!locked) ["a", "b"].forEach((side) => {
    for (let i = 0; i < rowCount; i++) {
      const inp = el(`${side}-id-${i}`);
      if (inp) inp.addEventListener("change", () => lookupName(side, i));
    }
  });

  if (el("saveBtn")) el("saveBtn").onclick = async () => {
    let a, b;
    try { a = collect("a"); b = collect("b"); }
    catch (e) { return toast(e.message, true); }
    const btn = el("saveBtn"); btn.disabled = true; btn.textContent = "Checking team IDs…";
    try {
      await api("/api/custom/teams", { method: "POST",
        body: JSON.stringify({ division_a: a, division_b: b }) });
    } catch (e) { btn.disabled = false; btn.textContent = "Save teams"; return toast(e.message, true); }
    toast(`Saved ${a.length} + ${b.length} teams`); refreshBadge(); views.setup();
  };
  if (el("genBtn")) el("genBtn").onclick = async () => {
    if (!confirm("Generate the fixture list? This is random and can only be done ONCE - it locks the schedule for the season.")) return;
    try {
      await api("/api/custom/generate", { method: "POST" });
      await api("/api/custom/sync-points", { method: "POST" });
    } catch (e) { return toast(e.message, true); }
    toast("Fixtures generated and results synced"); refreshBadge(); views.setup();
  };
  if (el("syncBtn")) el("syncBtn").onclick = async () => {
    try { const r = await api("/api/custom/sync-points", { method: "POST" });
      toast(`Synced ${r.teams} teams` + (r.failed && r.failed.length ? ` (${r.failed.length} failed)` : "")); }
    catch (e) { toast(e.message, true); }
  };
  if (el("resetBtn")) el("resetBtn").onclick = async () => {
    if (!confirm("Start over? This clears the schedule, results and all teams for the season.")) return;
    try { await api("/api/custom/reset", { method: "POST" }); }
    catch (e) { return toast(e.message, true); }
    toast("Reset - enter your teams again"); refreshBadge(); views.setup();
  };
};

// ============================ FIXTURES ====================================
views.fixtures = async function () {
  const helpBox = help("Fixtures - who plays who, every gameweek",
    `<p>The frozen 35-gameweek schedule. Each team plays once a week: your division
      rivals 3× each, the other division 2× each, plus 3 random extra games.
      <b>GW36-38 aren't here</b> - those are the playoffs, shown on the League tab.</p>`);
  const data = await api("/api/custom/fixtures");
  if (!data.gameweeks.length) {
    app().innerHTML = helpBox + '<p class="empty">No fixtures yet - generate them on the Setup tab.</p>';
    return;
  }
  app().innerHTML = helpBox + `<h2>Fixtures</h2><div class="gw-grid">${
    data.gameweeks.map((w) => `<div class="gw-card"><h4>Gameweek ${w.gameweek}</h4>${
      w.matches.map((m) => `<div class="match"><span>${esc(m.home)} <span class="muted">v</span> ${esc(m.away)}</span>
        <span class="k ${m.kind === "cross" ? "cross" : ""}">${m.kind}</span></div>`).join("")
    }</div>`).join("")}</div>`;
};

// ============================ LEAGUE ======================================
views.league = async function () {
  const helpBox = help("League - standings and the playoffs",
    `<p>Each division's head-to-head table (win 3, draw 1; ranked on points, then total
      FPL points scored). The <b>top 4 overall</b> qualify for the knockout, highlighted
      in green. Below, the bracket: semi-finals over GW36+GW37 (aggregate), final on GW38.</p>
     <p>Hit <b>Sync latest results</b> to pull the newest gameweek scores.</p>`);
  app().innerHTML = helpBox + `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
      <h2 style="margin:0">League</h2>
      <button class="btn small green" id="syncBtn2">Sync latest results</button>
    </div>
    <div id="tables" style="margin-top:14px"></div>
    <h3 style="margin-top:26px">Playoffs (GW36-38)</h3>
    <div id="bracket"></div>`;
  el("syncBtn2").onclick = async () => {
    try { const r = await api("/api/custom/sync-points", { method: "POST" });
      toast(`Synced ${r.teams} teams`); } catch (e) { return toast(e.message, true); }
    renderTables(); renderBracket();
  };
  await Promise.all([renderTables(), renderBracket()]);
};

async function renderTables() {
  const data = await api("/api/custom/table");
  if (!data.combined || !data.combined.length) {
    el("tables").innerHTML = '<p class="empty">No table yet - generate fixtures and sync results first.</p>';
    return;
  }
  const top4 = new Set(data.combined.slice(0, 4).map((r) => r.entry_id));
  const tbl = (title, rows) => `<div class="card"><h3>${esc(title)}</h3>
    <table><thead><tr><th>#</th><th>Manager</th><th class="num">P</th><th class="num">W</th>
      <th class="num">D</th><th class="num">L</th><th class="num">PF</th><th class="num">Pts</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="${top4.has(r.entry_id) ? "qualified" : ""}">
      <td>${r.rank}</td><td>${esc(r.manager)}${top4.has(r.entry_id) ? '<span class="qtag">PO</span>' : ""}</td>
      <td class="num">${r.played}</td><td class="num">${r.won}</td><td class="num">${r.drawn}</td>
      <td class="num">${r.lost}</td><td class="num">${r.points_for}</td>
      <td class="num"><b>${r.h2h_points}</b></td></tr>`).join("")}</tbody></table></div>`;
  el("tables").innerHTML = `<div class="two-col">
    ${tbl("Division A", data.division_a)}${tbl("Division B", data.division_b)}</div>`;
}

async function renderBracket() {
  const po = await api("/api/custom/playoffs");
  if (!po.ready) {
    el("bracket").innerHTML = `<p class="empty">${esc(po.reason || "Playoffs not available yet.")}</p>`;
    return;
  }
  const line = (team, pts, isWin, seedTxt) => team
    ? `<div class="competitor ${isWin ? "win" : ""}"><span><span class="seed">${seedTxt || ""}</span>${esc(team.manager)}</span><span>${pts ?? ""}</span></div>`
    : `<div class="competitor pending">TBD</div>`;

  const semiHtml = (sf) => {
    const w = sf.winner;
    const hiWin = w && w.entry_id === sf.high_seed.entry_id;
    const loWin = w && w.entry_id === sf.low_seed.entry_id;
    return `<div><div class="round-title">${esc(sf.name)} · GW36+37</div><div class="tie">
      ${line(sf.high_seed, sf.high_points, hiWin, "#" + sf.high_seed.rank)}
      ${line(sf.low_seed, sf.low_points, loWin, "#" + sf.low_seed.rank)}
    </div>${sf.status === "pending" ? '<div class="muted" style="font-size:12px;margin-top:4px">in progress</div>' : ""}</div>`;
  };

  const f = po.final;
  const fa = f.team_a, fb = f.team_b, w = f.winner;
  const finalHtml = `<div><div class="round-title">Final · GW38</div><div class="tie">
      ${line(fa, f.a_points, w && fa && w.entry_id === fa.entry_id, fa ? "#" + fa.rank : "")}
      ${line(fb, f.b_points, w && fb && w.entry_id === fb.entry_id, fb ? "#" + fb.rank : "")}
    </div></div>`;

  const champHtml = po.champion
    ? `<div class="champ-box">🏆 Champion<br>${esc(po.champion.manager)}</div>`
    : `<div class="champ-pending">🏆<br>Champion<br>TBD</div>`;

  el("bracket").innerHTML = `<div class="bracket">
      <div class="round">${semiHtml(po.semis[0])}${semiHtml(po.semis[1])}</div>
      <div class="round">${finalHtml}</div>
      <div class="round">${champHtml}</div>
    </div>`;
}

// --- boot -----------------------------------------------------------------
refreshBadge();
setView("setup");
