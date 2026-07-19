"use strict";

// --- helpers --------------------------------------------------------------
let adminAuth = sessionStorage.getItem("adminAuth") || null;
let myEntryId = localStorage.getItem("myEntryId") || "";  // viewer's chosen team

async function api(path, opts) {
  const headers = { "Content-Type": "application/json", ...(opts && opts.headers) };
  if (adminAuth) headers["Authorization"] = adminAuth;
  const res = await fetch(path, { ...opts, headers });
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
function timeAgo(iso) {
  if (!iso) return "not yet";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return d.toLocaleDateString();
}
// Ask the server to re-pull results if they're stale (server throttles this).
async function maybeRefresh() {
  try { const r = await api("/api/custom/refresh", { method: "POST" }); return !!r.synced; }
  catch { return false; }
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

async function populateMePicker() {
  const sel = el("mePicker");
  if (!sel) return;
  let players = [];
  try {
    const st = await api("/api/custom/status");
    if (st.divisions) players = st.divisions.flatMap((d) => d.entries || []);
  } catch { /* leave empty */ }
  sel.innerHTML = '<option value="">Pick your team…</option>' +
    players.map((p) => `<option value="${p.entry_id}">${esc(p.name)}</option>`).join("");
  if (myEntryId) sel.value = myEntryId;
  sel.onchange = () => {
    myEntryId = sel.value;
    if (myEntryId) localStorage.setItem("myEntryId", myEntryId);
    else localStorage.removeItem("myEntryId");
    const active = document.querySelector("#tabs button.active");
    if (active) setView(active.dataset.view);
  };
}

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

async function ensureAdmin() {
  if (!adminAuth) return false;
  try { await api("/api/custom/auth-check"); return true; }
  catch { adminAuth = null; sessionStorage.removeItem("adminAuth"); return false; }
}

function renderLogin() {
  app().innerHTML = help("Admin login",
    "<p>The Setup page is admin-only. The <b>Fixtures</b> and <b>League</b> tabs are open to everyone.</p>") + `
    <div class="card" style="max-width:360px">
      <h3>Log in</h3>
      <label>Username</label><input id="lg-user" autocomplete="username">
      <label>Password</label><input id="lg-pass" type="password" autocomplete="current-password">
      <div class="btns"><button class="btn" id="lg-btn">Log in</button></div>
    </div>`;
  const submit = async () => {
    const h = "Basic " + btoa(el("lg-user").value + ":" + el("lg-pass").value);
    try {
      const res = await fetch("/api/custom/auth-check", { headers: { Authorization: h } });
      if (!res.ok) throw 0;
    } catch { return toast("Invalid username or password", true); }
    adminAuth = h; sessionStorage.setItem("adminAuth", h);
    toast("Logged in"); refreshBadge(); views.setup();
  };
  el("lg-btn").onclick = submit;
  el("lg-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
}

views.setup = async function () {
  if (!(await ensureAdmin())) { renderLogin(); return; }
  const st = await api("/api/custom/status");
  const helpBox = help("Setup - enter teams, set rivalries, then generate",
    `<ol>
       <li><b>Team IDs</b> - put 7 per division (the number in a team's URL). Names are
         pulled from the site automatically; invalid ids are rejected on save.</li>
       <li><b>Rivalries</b> - optionally pair everyone into derbies; each pair plays an
         extra game. The other 2 extra games are random.</li>
       <li><b>Generate fixtures</b> - builds the random 35-gameweek schedule and <b>locks
         it</b> (one time only). Use <b>Start over</b> to redo.</li>
     </ol>`);

  const divs = st.divisions || [{ entries: [] }, { entries: [] }];
  const entriesA = (divs[0] && divs[0].entries) || [];
  const entriesB = (divs[1] && divs[1].entries) || [];
  const allPlayers = [...entriesA, ...entriesB];
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

  // --- rivalries section ---
  const canRiv = st.both_filled && st.sizes_equal;
  const npairs = Math.floor(allPlayers.length / 2);
  const rivPrefill = st.rivalries || [];
  const optsFor = (selId) => allPlayers.map((p) =>
    `<option value="${p.entry_id}" ${selId == p.entry_id ? "selected" : ""}>${esc(p.name)}</option>`).join("");
  const rivRows = canRiv ? Array.from({ length: npairs }, (_, i) => {
    const pr = rivPrefill[i] || {};
    return `<div class="row" style="gap:8px;align-items:center;margin-bottom:6px">
        <div style="flex:1"><select id="riv-a-${i}" ${locked ? "disabled" : ""}>${optsFor(pr.a)}</select></div>
        <div style="flex:0;color:var(--muted)">vs</div>
        <div style="flex:1"><select id="riv-b-${i}" ${locked ? "disabled" : ""}>${optsFor(pr.b)}</select></div>
      </div>`;
  }).join("") : "";
  const rivStatus = !canRiv ? "" : st.rivalries_valid
    ? '<p class="up">✅ Rivalries set - every player has one derby.</p>'
    : (rivPrefill.length ? '<p class="down">⚠ Rivalries incomplete - save a full set or leave blank for all-random extras.</p>'
                         : '<p class="muted">Not set - all 3 extra games will be random.</p>');

  app().innerHTML = helpBox + `
    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
      <h2 style="margin:0">Setup</h2>
      <button class="btn small" id="logoutBtn">Log out</button>
    </div>
    <div class="card" style="margin-top:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
        <h3 style="margin:0">1. Your two divisions</h3>
        ${locked ? "" : '<button class="btn small" id="fillTest" title="Fill with random valid IDs for testing">🎲 Fill test IDs</button>'}
      </div>
      <div class="two-col">
        <div><h4 style="color:var(--purple)">Division A ${locked ? "" : '<button class="btn small" id="shuffle-a" title="Randomise order">🔀</button>'}</h4>
          <div class="row" style="gap:8px"><div style="flex:1"><label>Team ID</label></div><div style="flex:2"><label>Player</label></div></div>
          ${rows("a", entriesA)}</div>
        <div><h4 style="color:var(--purple)">Division B ${locked ? "" : '<button class="btn small" id="shuffle-b" title="Randomise order">🔀</button>'}</h4>
          <div class="row" style="gap:8px"><div style="flex:1"><label>Team ID</label></div><div style="flex:2"><label>Player</label></div></div>
          ${rows("b", entriesB)}</div>
      </div>
      ${sizeNote}
      ${locked ? '<p class="muted">🔒 Teams locked. Use “Start over” to change them.</p>'
               : '<div class="btns"><button class="btn" id="saveBtn">Save teams</button></div>'}
    </div>

    <div class="card" style="margin-top:18px">
      <h3>2. Rivalries <span class="muted" style="font-size:13px">(derbies - optional)</span></h3>
      ${canRiv
        ? `<p class="muted">Pair everyone up (each player once). Each pair plays one extra
             "derby" game; the other 2 extra games stay random.</p>
           ${rivStatus}${rivRows}
           ${locked ? "" : '<div class="btns"><button class="btn" id="rivRandom">Randomise pairs</button><button class="btn green" id="rivSave">Save rivalries</button></div>'}`
        : '<p class="empty">Save both divisions (equal size) first, then you can set rivalries.</p>'}
    </div>

    <div class="card" style="margin-top:18px">
      <h3>3. Generate the season</h3>
      ${locked
        ? "<p>🔒 <b>Fixtures generated and locked.</b> The schedule is fixed for the season.</p>"
        : '<p class="muted">Enabled once both divisions are saved and equal size.</p>'}
      <div class="btns">
        ${locked
          ? '<button class="btn green" id="syncBtn">Sync latest results</button>'
          : `<button class="btn green" id="genBtn" ${st.can_generate ? "" : "disabled"}>Generate fixtures (one time only)</button>`}
        ${st.has_season ? '<button class="btn pink" id="resetBtn">Start over</button>' : ""}
      </div>
    </div>`;

  el("logoutBtn").onclick = () => {
    adminAuth = null; sessionStorage.removeItem("adminAuth");
    toast("Logged out"); refreshBadge(); views.setup();
  };

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

  // Shuffle a column's row order (id + resolved name move together).
  function shuffleColumn(side) {
    const vals = [];
    for (let i = 0; i < rowCount; i++) {
      const span = el(`${side}-nm-${i}`);
      vals.push({ id: el(`${side}-id-${i}`).value, nm: span.innerHTML, cls: span.className });
    }
    for (let i = vals.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1)); [vals[i], vals[j]] = [vals[j], vals[i]];
    }
    for (let i = 0; i < rowCount; i++) {
      el(`${side}-id-${i}`).value = vals[i].id;
      const span = el(`${side}-nm-${i}`); span.innerHTML = vals[i].nm; span.className = vals[i].cls;
    }
  }
  if (el("shuffle-a")) el("shuffle-a").onclick = () => shuffleColumn("a");
  if (el("shuffle-b")) el("shuffle-b").onclick = () => shuffleColumn("b");

  if (el("fillTest")) el("fillTest").onclick = async () => {
    const btn = el("fillTest"); btn.disabled = true; btn.textContent = "Fetching…";
    let r;
    try { r = await api(`/api/custom/sample-ids?n=${rowCount * 2}`); }
    catch (e) { btn.disabled = false; btn.textContent = "🎲 Fill test IDs"; return toast(e.message, true); }
    r.ids.forEach((it, idx) => {
      const side = idx < rowCount ? "a" : "b", i = idx % rowCount;
      const inp = el(`${side}-id-${i}`), span = el(`${side}-nm-${i}`);
      if (inp) { inp.value = it.entry_id; span.textContent = it.name; span.className = ""; }
    });
    btn.disabled = false; btn.textContent = "🎲 Fill test IDs";
    toast(`Filled ${r.ids.length} test IDs - review and Save`);
  };

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

  if (el("rivRandom")) el("rivRandom").onclick = () => {
    const ids = allPlayers.map((p) => p.entry_id);
    for (let i = ids.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1)); [ids[i], ids[j]] = [ids[j], ids[i]];
    }
    for (let i = 0; i < npairs; i++) {
      el(`riv-a-${i}`).value = ids[2 * i]; el(`riv-b-${i}`).value = ids[2 * i + 1];
    }
  };
  if (el("rivSave")) el("rivSave").onclick = async () => {
    const pairs = [], seen = new Set();
    for (let i = 0; i < npairs; i++) {
      const a = Number(el(`riv-a-${i}`).value), b = Number(el(`riv-b-${i}`).value);
      if (a === b) return toast(`Pair ${i + 1}: a player can't be their own rival`, true);
      if (seen.has(a) || seen.has(b)) return toast(`A player is picked twice (pair ${i + 1})`, true);
      seen.add(a); seen.add(b); pairs.push([a, b]);
    }
    try { await api("/api/custom/rivalries", { method: "POST", body: JSON.stringify({ pairs }) }); }
    catch (e) { return toast(e.message, true); }
    toast("Rivalries saved"); refreshBadge(); views.setup();
  };

  if (el("genBtn")) el("genBtn").onclick = async () => {
    const rivMsg = st.rivalries_valid ? "Rivalries are set." : "No rivalries set (all 3 extra games random).";
    if (!confirm(`Generate the fixture list? ${rivMsg}\n\nThis is random and can only be done ONCE - it locks the schedule.`)) return;
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
    if (!confirm("Start over? This clears the schedule, results, rivalries and all teams.")) return;
    try { await api("/api/custom/reset", { method: "POST" }); }
    catch (e) { return toast(e.message, true); }
    toast("Reset - enter your teams again"); refreshBadge(); views.setup();
  };
};

// ============================ FIXTURES ====================================
views.fixtures = async function () {
  await renderFixtures();
  if (await maybeRefresh()) { toast("Results updated"); renderFixtures(); }
};

async function renderFixtures() {
  const rulesPanel = `<div class="rules">
      <div class="rule"><div class="rule-ic">🏟️</div><div><b>Two divisions</b>
        <p>14 managers in two divisions of 7, each drafted separately on FPL Draft.</p></div></div>
      <div class="rule"><div class="rule-ic">📅</div><div><b>35-game season</b>
        <p>One match a week: your division ×3, the other division ×2, plus 3 extras (one derby + two random).</p></div></div>
      <div class="rule"><div class="rule-ic">⚖️</div><div><b>Head-to-head scoring</b>
        <p>Win 3, draw 1. Ranked on points, then total FPL points (PF). Only finished gameweeks count.</p></div></div>
      <div class="rule"><div class="rule-ic">🏆</div><div><b>Playoffs · GW36-38</b>
        <p>Top 4 overall. Semis #1v#4 &amp; #2v#3 over GW36+37, then the final on GW38.</p></div></div>
    </div>`;
  const header = `<h2>Fixtures</h2>` + rulesPanel;
  const data = await api("/api/custom/fixtures");
  if (!data.gameweeks.length) {
    app().innerHTML = header + '<p class="empty">No fixtures yet - generate them on the Setup tab.</p>';
    return;
  }
  const fmt = (iso, withTime) => {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d)) return "";
    const opts = withTime
      ? { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
      : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
    return d.toLocaleString(undefined, opts);
  };
  const lockbar = data.generated_at
    ? `<div class="lockbar">🔒 Fixtures locked in on <b>${fmt(data.generated_at, true)}</b></div>` : "";
  const updated = `<div class="muted" style="margin:-4px 0 14px">Results updated ${timeAgo(data.last_updated)}</div>`;
  const legend = `<div class="legend">
      <span><span class="dot" style="background:var(--accent)"></span> Current gameweek</span>
      <span><span class="dot" style="background:var(--accent-soft)"></span> Upcoming</span>
      <span><span class="dot" style="background:#cbd0d6"></span> Finished</span>
    </div>`;
  const tagText = { finished: "Finished", current: "Live", upcoming: "Upcoming" };
  const me = String(myEntryId || "");
  const matchHtml = (m) => {
    const homeMine = me && String(m.home_id) === me;
    const awayMine = me && String(m.away_id) === me;
    const played = m.home_points != null && m.away_points != null;
    const homeWin = played && m.home_points > m.away_points;
    const awayWin = played && m.away_points > m.home_points;
    const row = (name, pts, win, mine) =>
      `<div class="mr${win ? " win" : ""}"><span class="nm${mine ? " me" : ""}">${esc(name)}</span>` +
      `<span class="pt">${pts != null ? pts : ""}</span></div>`;
    return `<div class="match${homeMine || awayMine ? " mine" : ""}">
        ${row(m.home, m.home_points, homeWin, homeMine)}
        ${row(m.away, m.away_points, awayWin, awayMine)}
      </div>`;
  };
  app().innerHTML = header + lockbar + updated + legend + `<div class="gw-grid">${
    data.gameweeks.map((w) => `<div class="gw-card gw-${w.status}" id="gwc-${w.gameweek}">
      <h4>Gameweek ${w.gameweek} <span class="gw-tag ${w.status}">${tagText[w.status] || ""}</span></h4>
      ${w.deadline ? `<div class="gw-deadline">deadline ${fmt(w.deadline, false)}</div>` : ""}${
      w.matches.map(matchHtml).join("")
    }</div>`).join("")}</div>`;

  const cur = data.gameweeks.find((w) => w.status === "current");
  if (cur) { const n = el(`gwc-${cur.gameweek}`); if (n) n.scrollIntoView({ behavior: "smooth", block: "center" }); }
};

// ============================ RULES =======================================
views.rules = async function () {
  const st = await api("/api/custom/status").catch(() => ({ has_season: false }));
  const sizes = (st.divisions || []).map((d) => d.teams).filter((n) => n > 0);
  const k = sizes[0] || 7;                 // per-division size (falls back to the planned 7)
  const teams = k * 2;
  const divGames = (k - 1) * 3;
  const crossGames = k * 2;
  const extra = 3;
  const totalGws = divGames + crossGames + extra;

  app().innerHTML = `
    <h2>Rules</h2>
    ${help("How Branksbowl works",
      `<p>Drafting, transfers and weekly lineups all happen on the official <b>FPL Draft</b> site -
       nothing is drafted here. This site mirrors each manager's real gameweek points from FPL Draft
       and uses them to run our own custom <b>two-division head-to-head league</b>, complete with its
       own fixture list, standings and end-of-season playoff.</p>`)}

    <div class="rules">
      <div class="rule"><div class="rule-ic">🏟️</div><div><b>${teams} managers, 2 divisions</b>
        <p>Division A and Division B, ${k} managers each - each is its own real FPL Draft mini-league, drafted separately.</p></div></div>
      <div class="rule"><div class="rule-ic">📅</div><div><b>${totalGws}-gameweek season</b>
        <p>A random schedule, generated once and locked for the rest of the season.</p></div></div>
      <div class="rule"><div class="rule-ic">⚖️</div><div><b>Head-to-head scoring</b>
        <p>Win = 3pts, draw = 1pt, loss = 0. Real FPL points decide who wins each match-up.</p></div></div>
      <div class="rule"><div class="rule-ic">🏆</div><div><b>Top-4 playoff</b>
        <p>The top 4 in the combined table go into a knockout, GW36-38.</p></div></div>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>1. The format</h3>
      <ul>
        <li><b>${teams} managers</b> split into two divisions, <b>Division A</b> and <b>Division B</b>, of ${k} each.</li>
        <li>Each division is a genuine FPL Draft mini-league - drafting, waivers/free agents and each manager's
          weekly lineup are all managed on the official FPL Draft site, not here.</li>
        <li>This site links to both leagues by team ID, pulls each manager's real per-gameweek points, and builds
          the fixtures, tables and playoff bracket that FPL Draft itself doesn't offer.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>2. The season - ${totalGws} gameweeks, one match a week</h3>
      <p class="muted">Every manager plays exactly one match every gameweek. Over the season each manager faces:</p>
      <table>
        <tbody>
          <tr><td>Each of the ${k - 1} rivals in their own division</td><td class="num">×3</td><td class="num">${divGames} games</td></tr>
          <tr><td>Each of the ${k} teams in the other division</td><td class="num">×2</td><td class="num">${crossGames} games</td></tr>
          <tr><td>Extra games</td><td class="num">-</td><td class="num">${extra} games</td></tr>
          <tr><td><b>Total</b></td><td></td><td class="num"><b>${totalGws} games</b></td></tr>
        </tbody>
      </table>
      <p class="muted" style="margin-top:10px">The ${extra} extra games: if <b>rivalries</b> (derbies) are set up,
        one is a guaranteed match against your rival; the other two are always drawn at random. With no rivalries
        set, all ${extra} extras are random. The whole schedule is drawn once, randomly (no team is advantaged),
        then <b>locked</b> - the only way to redo it is a full "Start over" on Setup, which also wipes the results.</p>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>3. Scoring &amp; standings</h3>
      <ul>
        <li>A manager's score for a gameweek is their real total FPL points for that week, pulled straight from
          the official FPL API via their FPL Draft team - the same score they see on FPL Draft.</li>
        <li>Match result: higher score wins. <b>Win = 3 points, draw = 1 point, loss = 0 points.</b></li>
        <li>Only <b>finished</b> gameweeks count towards the table - live/in-progress gameweeks aren't scored yet.</li>
        <li>Ranking order: <b>head-to-head points</b> first; ties are broken by <b>total FPL points scored all
          season (PF)</b>.</li>
        <li>Division A and Division B each get their own table, and everyone also appears in one <b>combined
          overall table</b> - it's the combined table that decides the playoffs.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>4. Playoffs - gameweeks 36 to 38</h3>
      <ul>
        <li>The <b>top 4</b> in the combined overall table qualify, seeded #1-#4.</li>
        <li><b>Semi-finals:</b> #1 vs #4 and #2 vs #3, aggregated over <b>GW36 + GW37</b> (both gameweeks' points
          are added together - it isn't decided on a single week).</li>
        <li><b>Final:</b> the two semi-final winners meet on <b>GW38</b> to decide the champion.</li>
        <li><b>Tie-break:</b> if a tie is level after its gameweek(s), the <b>higher seed</b> (the team that
          finished higher in the regular season) goes through.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>5. Setup &amp; admin rules</h3>
      <ul>
        <li>The <b>League</b> and <b>Fixtures</b> tabs are open to everyone; only <b>Setup</b> needs an admin login.</li>
        <li>Each division's FPL Draft team IDs are entered on Setup - both divisions must be the <b>same size</b>,
          and every ID is checked against FPL Draft before it can be saved.</li>
        <li><b>Generating fixtures is random and one-time only</b> - once generated, the schedule (and any
          rivalries) are locked for the season. The only way to change teams or rivalries afterwards is
          <b>"Start over"</b>, which wipes fixtures, results, rivalries and teams completely.</li>
        <li>Results refresh automatically for anyone viewing the site once they're more than 30 minutes old; an
          admin can also force an immediate sync from Setup or League.</li>
      </ul>
    </div>`;
};

// ============================ LEAGUE ======================================
views.league = async function () {
  const isAdmin = await ensureAdmin();
  const rulesPanel = `<div class="rules">
      <div class="rule"><div class="rule-ic">📊</div><div><b>Reading the table</b>
        <p>Head-to-head: win 3, draw 1. Ranked on points, then total FPL points (PF). Top 4 overall (highlighted) reach the playoffs.</p></div></div>
      <div class="rule"><div class="rule-ic">🏆</div><div><b>Playoffs · GW36-38</b>
        <p>Semi-finals #1v#4 &amp; #2v#3 aggregated over GW36+37, then the final on GW38.</p></div></div>
    </div>`;
  app().innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
      <h2 style="margin:0">League</h2>
      ${isAdmin ? '<button class="btn small green" id="syncBtn2">Sync latest results</button>' : ""}
    </div>
    <div class="muted" id="lastUpd" style="margin:2px 0 10px"></div>` + rulesPanel + `
    <div id="tables"></div>
    <h3 style="margin-top:26px">Playoffs (GW36-38)</h3>
    <div id="bracket"></div>`;
  if (el("syncBtn2")) el("syncBtn2").onclick = async () => {
    try { const r = await api("/api/custom/sync-points", { method: "POST" });
      toast(`Synced ${r.teams} teams`); } catch (e) { return toast(e.message, true); }
    renderTables(); renderBracket();
  };
  await Promise.all([renderTables(), renderBracket()]);
  if (await maybeRefresh()) { toast("Results updated"); renderTables(); renderBracket(); }
};

async function renderTables() {
  const data = await api("/api/custom/table");
  const lu = el("lastUpd");
  if (lu) lu.textContent = data.last_updated ? `Results updated ${timeAgo(data.last_updated)}` : "No results synced yet";
  if (!data.combined || !data.combined.length) {
    el("tables").innerHTML = '<p class="empty">No table yet - generate fixtures and sync results first.</p>';
    return;
  }
  const top4 = new Set(data.combined.slice(0, 4).map((r) => r.entry_id));
  const me = String(myEntryId || "");
  const tbl = (title, rows) => `<div class="card"><h3>${esc(title)}</h3>
    <table><thead><tr><th>#</th><th>Manager</th><th class="num">P</th><th class="num">W</th>
      <th class="num">D</th><th class="num">L</th><th class="num">PF</th><th class="num">Pts</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="${top4.has(r.entry_id) ? "qualified" : ""}${String(r.entry_id) === me ? " mine" : ""}">
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
populateMePicker();
refreshBadge();
setView("league");
