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
function loadingHtml(label) {
  return `<div class="loading"><span class="spinner"></span> ${esc(label || "Loading…")}</div>`;
}
function errorHtml(msg) {
  return `<div class="error-box">⚠ ${esc(msg || "Something went wrong loading this.")}
    <span class="muted">Try refreshing, or check back in a moment.</span></div>`;
}
// Distinguishes "the app itself hit an error" from "Render's free tier spun this
// site down and it just needs a moment to wake back up" - /health reports how
// long the process has been running, so a very low number means it (almost
// certainly) just cold-started in response to this very visit. Being unable to
// reach /health at all points the same way - the container is still booting.
async function isLikelyColdStart() {
  try {
    const res = await fetch("/health", { cache: "no-store" });
    if (!res.ok) return true;
    const data = await res.json().catch(() => null);
    if (!data || typeof data.uptime_seconds !== "number") return true;
    return data.uptime_seconds < 90;
  } catch { return true; }
}
async function friendlyErrorHtml(genericMsg) {
  if (await isLikelyColdStart()) {
    return `<div class="error-box">💤 This site is hosted on Render's free tier, which spins down after a
      period of inactivity.
      <span class="muted">If it's been a while since your last visit, this page just needs a moment to
      wake up - refresh and try again.</span></div>`;
  }
  return errorHtml(genericMsg);
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
let selectedSeasonId = null;  // null = the current (active) season
let seasonsCache = [];
let managerProfileEntryId = null;   // set just before showing the profile "sub-page"
let previousViewBeforeProfile = "league";  // tab to return to via its Back button
// Bumped on every navigation. Async view/render functions capture the value
// current when they *started* and check it again right before touching the
// DOM - if it's changed, the user has since navigated elsewhere, so a slow
// response (a real-world issue on Render, where round-trips can lag) gets
// dropped instead of clobbering whatever's now on screen with stale content.
let renderToken = 0;
function setView(name) {
  renderToken++;
  document.querySelectorAll("#tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  // Setup only ever administers the current season - drop any archived selection.
  if (name === "setup" && selectedSeasonId !== null) {
    selectedSeasonId = null;
    const sel = el("seasonPicker"), cur = seasonsCache.find((s) => s.is_current);
    if (sel && cur) sel.value = String(cur.id);
  }
  if (views[name]) sessionStorage.setItem("activeView", name);
  (views[name] || (() => {}))();
}
document.querySelectorAll("#tabs button").forEach((b) =>
  b.addEventListener("click", () => setView(b.dataset.view)));

// A past (archived) season is picked by id; the current one is always "no param".
function seasonParam() { return selectedSeasonId ? `season_id=${selectedSeasonId}` : ""; }
function withSeason(path) {
  const qs = seasonParam();
  return qs ? path + (path.includes("?") ? "&" : "?") + qs : path;
}
function isArchivedSelected() { return selectedSeasonId !== null; }

async function populateSeasonPicker() {
  const sel = el("seasonPicker");
  if (!sel) return;
  try { seasonsCache = await api("/api/custom/seasons"); } catch { seasonsCache = []; }
  if (seasonsCache.length <= 1) { sel.style.display = "none"; return; }
  sel.style.display = "";
  sel.innerHTML = seasonsCache.map((s) =>
    `<option value="${s.id}">${esc(s.name)}${s.is_current ? "" : " (archived)"}</option>`).join("");
  const cur = seasonsCache.find((s) => s.is_current);
  sel.value = String(selectedSeasonId || (cur && cur.id) || seasonsCache[0].id);
  sel.onchange = () => {
    const chosen = seasonsCache.find((s) => String(s.id) === sel.value);
    selectedSeasonId = chosen && !chosen.is_current ? chosen.id : null;
    const active = document.querySelector("#tabs button.active");
    if (active) setView(active.dataset.view);
  };
}

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

function initThemeToggle() {
  const btn = el("themeToggle");
  if (!btn) return;
  const sync = () => { btn.textContent = document.documentElement.getAttribute("data-theme") === "dark" ? "☀️" : "🌙"; };
  sync();
  btn.onclick = () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
    sync();
  };
}

// Setup is reached via the cog icon rather than a nav tab. Already-authenticated
// admins go straight to Setup; everyone else gets a popup login that then does.
function openLoginModal() {
  const modal = el("loginModal");
  modal.style.display = "flex";
  el("modal-user").value = ""; el("modal-pass").value = "";
  el("modal-user").focus();
}
function closeLoginModal() { el("loginModal").style.display = "none"; }

function initSetupCog() {
  const cog = el("setupCog");
  if (!cog) return;
  cog.onclick = async () => {
    if (await ensureAdmin()) setView("setup");
    else openLoginModal();
  };
  el("loginModalClose").onclick = closeLoginModal;
  el("loginModal").addEventListener("click", (e) => { if (e.target.id === "loginModal") closeLoginModal(); });
  const submit = async () => {
    const h = "Basic " + btoa(el("modal-user").value + ":" + el("modal-pass").value);
    try {
      const res = await fetch("/api/custom/auth-check", { headers: { Authorization: h } });
      if (!res.ok) throw 0;
    } catch { return toast("Invalid username or password", true); }
    adminAuth = h; sessionStorage.setItem("adminAuth", h);
    closeLoginModal(); toast("Logged in"); refreshBadge(); setView("setup");
  };
  el("modal-login-btn").onclick = submit;
  el("modal-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
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
  const token = renderToken;
  app().innerHTML = loadingHtml();
  const isAdmin = await ensureAdmin();
  if (token !== renderToken) return;
  if (!isAdmin) { renderLogin(); return; }
  let st, seasons;
  try {
    st = await api("/api/custom/status");
    seasons = await api("/api/custom/seasons").catch(() => []);
  } catch (e) {
    const msg = await friendlyErrorHtml("Couldn't load Setup - " + e.message);
    if (token === renderToken) app().innerHTML = msg;
    return;
  }
  if (token !== renderToken) return;
  const archivedSeasons = seasons.filter((sn) => !sn.is_current);
  const helpBox = help("Setup - enter teams, set rivalries, then generate",
    `<ol>
       <li><b>Team IDs</b> - put 7 per division (the number in a team's URL). Names are
         pulled from the site automatically; invalid ids are rejected on save.</li>
       <li><b>Rivalries</b> - optionally pair everyone into derbies; each pair plays an
         extra game. The other 2 extra games are random.</li>
       <li><b>Generate fixtures</b> - builds the random 35-gameweek schedule and <b>locks
         it</b> for the season (one time only). To redo it, start a new season instead.</li>
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
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          ${locked ? "" : '<button class="btn small" id="shuffleAcross" title="Shuffle teams across Division A and B">🔀 Shuffle across divisions</button>'}
          ${locked ? "" : '<button class="btn small" id="fillTest" title="Fill with random valid IDs for testing">🎲 Fill test IDs</button>'}
        </div>
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
      ${locked ? '<p class="muted">🔒 Teams locked for the season. Start a new season to change them.</p>'
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
      </div>
    </div>

    ${locked ? `<div class="card" style="margin-top:18px">
      <h3>Start a new season</h3>
      <p class="muted">Archives this season - its table, fixtures and results stay viewable forever
        via the season picker in the top nav - and opens a blank Setup for a new one.</p>
      <label>New season name</label>
      <input id="newSeasonName" placeholder="e.g. Branksbowl 27/28">
      <div class="btns"><button class="btn" id="newSeasonBtn">Start new season</button></div>
    </div>` : ""}

    ${archivedSeasons.length ? `<div class="card" style="margin-top:18px">
      <h3>Archived seasons</h3>
      <p class="muted">Deleting a season removes its fixtures, results and rivalries for good -
        there's no undo.</p>
      <table><tbody>${archivedSeasons.map((sn) => `<tr>
          <td>${esc(sn.name)}</td>
          <td class="muted" style="font-size:12px">archived ${timeAgo(sn.archived_at)}</td>
          <td class="num"><button class="btn small pink" data-del-season="${sn.id}">Delete</button></td>
        </tr>`).join("")}</tbody></table>
    </div>` : ""}

    <div class="card" style="margin-top:18px">
      <h3>Backup</h3>
      <p class="muted">Downloads every season's teams, fixtures, results and rivalries as a JSON file - the
        part of this site that can't be regenerated if it ever breaks or needs re-hosting elsewhere. FPL
        reference data (players, teams, gameweeks) re-syncs on its own and doesn't need backing up.</p>
      <div class="btns"><button class="btn small" id="exportBtn">⬇️ Export data</button></div>

      <h4 style="margin-top:20px">Restore from a backup</h4>
      <p class="down" style="font-size:13px;margin:6px 0">⚠ Replaces <b>all</b> current league data (every
        season) with the file's contents. There's no undo - only do this to recover from data loss.</p>
      <label>Export file</label><input type="file" id="importFile" accept="application/json">
      <div class="btns"><button class="btn small pink" id="importBtn">⬆️ Import data</button></div>
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

  // Shuffle both columns together, so teams can move between Division A and B.
  function shuffleAcrossDivisions() {
    const vals = [];
    ["a", "b"].forEach((side) => {
      for (let i = 0; i < rowCount; i++) {
        const span = el(`${side}-nm-${i}`);
        vals.push({ id: el(`${side}-id-${i}`).value, nm: span.innerHTML, cls: span.className });
      }
    });
    for (let i = vals.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1)); [vals[i], vals[j]] = [vals[j], vals[i]];
    }
    let k = 0;
    ["a", "b"].forEach((side) => {
      for (let i = 0; i < rowCount; i++, k++) {
        el(`${side}-id-${i}`).value = vals[k].id;
        const span = el(`${side}-nm-${i}`); span.innerHTML = vals[k].nm; span.className = vals[k].cls;
      }
    });
  }
  if (el("shuffleAcross")) el("shuffleAcross").onclick = shuffleAcrossDivisions;

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
  if (el("newSeasonBtn")) el("newSeasonBtn").onclick = async () => {
    const name = el("newSeasonName").value.trim();
    if (!confirm(`Start a new season${name ? ` named "${name}"` : ""}? This archives the current `
      + "one (its data stays viewable, read-only, via the season picker) and gives you a blank "
      + "Setup to draft a new one.")) return;
    try { await api("/api/custom/seasons/new", { method: "POST", body: JSON.stringify({ name }) }); }
    catch (e) { return toast(e.message, true); }
    toast("New season started - set up your teams"); refreshBadge(); populateSeasonPicker(); views.setup();
  };
  document.querySelectorAll("[data-del-season]").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.delSeason;
      const name = (archivedSeasons.find((sn) => String(sn.id) === id) || {}).name || `season ${id}`;
      if (!confirm(`Permanently delete "${name}"? This removes its fixtures, results and rivalries `
        + `for good - there's no undo.`)) return;
      try { await api(`/api/custom/seasons/${id}`, { method: "DELETE" }); }
      catch (e) { return toast(e.message, true); }
      toast("Season deleted"); populateSeasonPicker(); views.setup();
    };
  });
  if (el("exportBtn")) el("exportBtn").onclick = async () => {
    let data;
    try { data = await api("/api/custom/export"); }
    catch (e) { return toast(e.message, true); }
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `branksbowl-export-${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("Export downloaded");
  };
  if (el("importBtn")) el("importBtn").onclick = async () => {
    const file = el("importFile").files[0];
    if (!file) return toast("Choose an export file first", true);
    if (!confirm("This permanently replaces ALL current league data - every season, fixture, result and "
      + "rivalry - with the contents of this file. There's no undo. Continue?")) return;
    let parsed;
    try { parsed = JSON.parse(await file.text()); }
    catch { return toast("That file isn't valid JSON.", true); }
    let result;
    try { result = await api("/api/custom/import", { method: "POST", body: JSON.stringify(parsed) }); }
    catch (e) { return toast(e.message, true); }
    toast(`Import complete - ${result.seasons} season(s) restored`);
    refreshBadge(); populateSeasonPicker(); views.setup();
  };
};

// ============================ FIXTURES ====================================
views.fixtures = async function () {
  const token = renderToken;
  await renderFixtures();
  if (token !== renderToken) return;
  if (!isArchivedSelected() && await maybeRefresh()) {
    if (token !== renderToken) return;
    toast("Results updated"); renderFixtures();
  }
};

// Shared wherever a manager's name is shown - links out to their public FPL
// Draft history page (no login needed, confirmed working: /entry/{id}/history).
function mgrLink(m) {
  return `<a class="mgr-link" href="https://draft.premierleague.com/entry/${m.entry_id}/history" `
    + `target="_blank" rel="noopener noreferrer">${esc(m.name)}</a>`;
}
// Same, plus a small icon into this site's own manager profile page.
function mgrCell(m) {
  return `${mgrLink(m)} <button class="mini-link" title="View profile" `
    + `onclick="showManagerProfile(${m.entry_id})">📊</button>`;
}

// Shared by Fixtures' gameweek grid and League's "Live now" card.
function matchRowHtml(m) {
  const me = String(myEntryId || "");
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
}

async function renderFixtures() {
  const token = renderToken;
  const rulesPanel = `<div class="rules">
      <div class="rule"><div class="rule-ic">🏟️</div><div><b>Two divisions</b>
        <p>The league is split into two divisions with the same number of players in each, drafted separately on FPL Draft.</p></div></div>
      <div class="rule"><div class="rule-ic">📅</div><div><b>Regular season</b>
        <p>One match a week: each team in your division ×3, each in the other division ×2, plus 3 extra games (one derby + two random).</p></div></div>
      <div class="rule"><div class="rule-ic">⚖️</div><div><b>Head-to-head scoring</b>
        <p>Win 3, draw 1. Ranked on points, then total FPL points (PF). Only finished gameweeks count.</p></div></div>
      <div class="rule"><div class="rule-ic">🏆</div><div><b>Playoffs · GW36-38</b>
        <p>Top 2 from each division. Cross-division semis over GW36+37, then the final on GW38.</p></div></div>
    </div>`;
  const archived = isArchivedSelected();
  const header = `<h2>Fixtures ${archived ? '<span class="pill">📁 Archived season</span>' : ""}</h2>` + rulesPanel;
  app().innerHTML = header + loadingHtml("Loading fixtures…");
  let data;
  try { data = await api(withSeason("/api/custom/fixtures")); }
  catch (e) {
    const msg = await friendlyErrorHtml("Couldn't load fixtures - " + e.message);
    if (token === renderToken) app().innerHTML = header + msg;
    return;
  }
  if (token !== renderToken) return;
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
      <span><span class="dot" style="background:var(--muted-dot)"></span> Finished</span>
    </div>`;
  const tagText = { finished: "Finished", current: "Live", upcoming: "Upcoming" };
  app().innerHTML = header + lockbar + updated + legend + `<div class="gw-grid">${
    data.gameweeks.map((w) => `<div class="gw-card gw-${w.status}" id="gwc-${w.gameweek}">
      <h4>Gameweek ${w.gameweek} <span class="gw-tag ${w.status}">${tagText[w.status] || ""}</span></h4>
      ${w.deadline ? `<div class="gw-deadline">deadline ${fmt(w.deadline, false)}</div>` : ""}${
      w.matches.map(matchRowHtml).join("")
    }</div>`).join("")}</div>`;

  const cur = data.gameweeks.find((w) => w.status === "current");
  if (cur) { const n = el(`gwc-${cur.gameweek}`); if (n) n.scrollIntoView({ behavior: "smooth", block: "center" }); }
};

// ============================ RULES =======================================
views.rules = async function () {
  const token = renderToken;
  app().innerHTML = loadingHtml();
  const st = await api("/api/custom/status").catch(() => ({ has_season: false }));
  if (token !== renderToken) return;
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
       own fixture list, standings, playoffs, past-season archive and all-time records that FPL Draft
       itself doesn't offer.</p>`)}

    <div class="rules">
      <div class="rule"><div class="rule-ic">🏟️</div><div><b>${teams} managers, 2 divisions</b>
        <p>Division A and Division B, ${k} managers each - each is its own real FPL Draft mini-league, drafted separately.</p></div></div>
      <div class="rule"><div class="rule-ic">📅</div><div><b>${totalGws}-gameweek season</b>
        <p>A random schedule, generated once and locked for the rest of the season.</p></div></div>
      <div class="rule"><div class="rule-ic">⚖️</div><div><b>Head-to-head scoring</b>
        <p>Win = 3pts, draw = 1pt, loss = 0. Real FPL points decide who wins each match-up.</p></div></div>
      <div class="rule"><div class="rule-ic">🏆</div><div><b>Top-2-per-division playoff</b>
        <p>Each division's top 2 go into a cross-division knockout, GW36-38.</p></div></div>
      <div class="rule"><div class="rule-ic">🗂️</div><div><b>Past seasons</b>
        <p>Every finished season is archived and stays viewable, read-only, via the season picker in the nav.</p></div></div>
      <div class="rule"><div class="rule-ic">🥇</div><div><b>Hall of Fame</b>
        <p>Trophy cabinet of past champions, plus all-time records like highest gameweek score and longest streaks.</p></div></div>
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
        then <b>locked</b> for the season - the only way to get a fresh schedule is to start a new season
        on Setup, which archives this one rather than wiping it.</p>
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
        <li><b>PF only totals the ${totalGws} regular-season gameweeks (GW1-${totalGws})</b> - gameweeks 36-38 feed
          the playoffs instead (see below), so PF won't match the season-long total shown on the official FPL
          Draft site until the playoffs are done too. Example: a manager sitting on 1512 PF after GW35 who then
          scores 31, 40 and 34 in GW36-38 shows 1617 on FPL Draft's own site - the extra 105 points went into the
          playoff bracket, not the regular-season table.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>4. Playoffs - gameweeks 36 to 38</h3>
      <ul>
        <li>The <b>top 2 from each division</b> qualify - not the top 4 of the combined table - so one strong
          division can never shut the other out of the playoffs entirely.</li>
        <li><b>Semi-finals are cross-division:</b> a division's #1 faces the <b>other</b> division's #2 (A1 v B2,
          B1 v A2), aggregated over <b>GW36 + GW37</b> (both gameweeks' points are added together - it isn't
          decided on a single week). The two division winners can only meet each other in the final.</li>
        <li><b>Final:</b> the two semi-final winners meet on <b>GW38</b> to decide the champion.</li>
        <li><b>Tie-break:</b> if a tie is level after its gameweek(s), whichever of the two actually finished
          higher in the <b>combined</b> regular-season table goes through - not just whoever's the better seed
          within their own division.</li>
        <li>Points scored in GW36-38 only ever count towards the playoff bracket - they never get added back into
          the regular-season table's PF, even for the two managers who reach the final.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>5. Past seasons</h3>
      <ul>
        <li>Use the <b>season picker</b> in the top nav to browse any previous season - its table, fixtures and
          playoff bracket stay exactly as they finished, marked with a "📁 Archived season" badge.</li>
        <li>An archived season is permanently read-only: no more results sync, no editing - it's a historical
          record.</li>
        <li>The <b>Hall of Fame</b> tab (below) looks across every season, not just the current one.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>6. Hall of Fame</h3>
      <ul>
        <li><b>Trophy cabinet:</b> the champion and runner-up from every completed season's playoffs.</li>
        <li><b>Records:</b> highest single-gameweek score, biggest win margin, longest win streak and longest
          unbeaten run - all computed across every season this site has tracked.</li>
        <li><b>Most/fewest points in a season</b> only compares <b>archived</b> (fully completed) seasons, so an
          early hot streak in a season that's still in progress can't claim a record against a full season.</li>
      </ul>
    </div>

    <div class="card" style="margin-top:18px">
      <h3>7. Setup &amp; admin rules</h3>
      <ul>
        <li>The <b>League</b>, <b>Fixtures</b>, <b>Hall of Fame</b> and <b>Rules</b> tabs are open to everyone;
          <b>Setup</b> is admin-only, reached via the ⚙️ icon next to the theme toggle rather than a tab.</li>
        <li>Each division's FPL Draft team IDs are entered on Setup - both divisions must be the <b>same size</b>,
          and every ID is checked against FPL Draft before it can be saved.</li>
        <li><b>Generating fixtures is random and one-time only</b> - once generated, the schedule (and any
          rivalries) are locked for the season. Before it's generated, teams and rivalries can simply be
          re-saved to change them.</li>
        <li>Once a season's fixtures are locked, an admin can <b>start a new, named season</b>: the current one is
          archived (kept, read-only, forever) and a blank Setup opens for the next one. This is now the only way
          to redo a season's teams once fixtures are locked.</li>
        <li>Archived seasons can be <b>permanently deleted</b> from Setup if you want to clear out test data -
          this removes that season's fixtures, results and rivalries for good and can't be undone. The current
          season can't be deleted this way - it has to be archived first.</li>
        <li>Results refresh automatically for anyone viewing the site once they're more than 30 minutes old - or
          every 3 minutes while a gameweek is actually live. An admin can also force an immediate sync from Setup
          or League.</li>
        <li>Setup has a <b>Backup</b> section to download every season's teams, fixtures, results and rivalries
          as a JSON file - worth keeping a recent copy in case the site ever needs re-hosting. It can also
          <b>restore</b> from one of these files, but that replaces all current league data, so it's only
          meant for recovering from data loss, not everyday use.</li>
      </ul>
    </div>`;
};

// ============================ LEAGUE ======================================
views.league = async function () {
  const token = renderToken;
  app().innerHTML = loadingHtml();
  const isAdmin = await ensureAdmin();
  if (token !== renderToken) return;
  const archived = isArchivedSelected();
  const rulesPanel = `<div class="rules">
      <div class="rule"><div class="rule-ic">📊</div><div><b>Reading the table</b>
        <p>Head-to-head: win 3, draw 1. Ranked on points, then total FPL points (PF). Top 2 per division (highlighted) reach the playoffs.</p></div></div>
      <div class="rule"><div class="rule-ic">🏆</div><div><b>Playoffs · GW36-38</b>
        <p>Cross-division semis (A1 v B2, B1 v A2) aggregated over GW36+37, then the final on GW38.</p></div></div>
    </div>`;
  app().innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
      <h2 style="margin:0">League ${archived ? '<span class="pill">📁 Archived season</span>' : ""}</h2>
      ${isAdmin && !archived ? '<button class="btn small green" id="syncBtn2">Sync latest results</button>' : ""}
    </div>
    <div class="muted" id="lastUpd" style="margin:2px 0 10px"></div>` + rulesPanel + `
    <div id="liveNow"></div>
    <div id="tables">${loadingHtml("Loading table…")}</div>
    <h3 style="margin-top:26px">Playoffs (GW36-38)</h3>
    <div id="bracket">${loadingHtml("Loading playoffs…")}</div>`;
  if (el("syncBtn2")) el("syncBtn2").onclick = async () => {
    try { const r = await api("/api/custom/sync-points", { method: "POST" });
      toast(`Synced ${r.teams} teams`); } catch (e) { return toast(e.message, true); }
    renderTables(); renderBracket(); renderLiveNow();
  };
  await Promise.all([renderTables(), renderBracket(), renderLiveNow()]);
  if (token !== renderToken) return;
  if (!archived && await maybeRefresh()) {
    if (token !== renderToken) return;
    toast("Results updated"); renderTables(); renderBracket(); renderLiveNow();
  }
};

async function renderLiveNow() {
  const token = renderToken;
  const box = el("liveNow");
  if (!box) return;
  if (isArchivedSelected()) { box.innerHTML = ""; return; }
  let data;
  try { data = await api(withSeason("/api/custom/fixtures")); }
  catch { if (token === renderToken) box.innerHTML = ""; return; }  // not critical - fail quiet
  if (token !== renderToken) return;
  const live = data.gameweeks.find((w) => w.status === "current");
  box.innerHTML = !live ? "" : `<div class="card" style="margin-bottom:18px;border-color:var(--accent)">
      <h3 style="margin-top:0">⚡ Live - Gameweek ${live.gameweek}
        <span class="muted" style="font-weight:400;font-size:12px">(provisional, not final)</span></h3>
      ${live.matches.map(matchRowHtml).join("")}
    </div>`;
}

async function renderTables() {
  const token = renderToken;
  let data;
  try { data = await api(withSeason("/api/custom/table")); }
  catch (e) {
    const msg = await friendlyErrorHtml("Couldn't load the table - " + e.message);
    if (token === renderToken) el("tables").innerHTML = msg;
    return;
  }
  if (token !== renderToken) return;
  const lu = el("lastUpd");
  if (lu) lu.textContent = data.last_updated ? `Results updated ${timeAgo(data.last_updated)}` : "No results synced yet";
  if (!data.combined || !data.combined.length) {
    el("tables").innerHTML = '<p class="empty">No table yet - generate fixtures and sync results first.</p>';
    return;
  }
  // Top 2 from each division qualify for the playoffs, not the top 4 combined.
  const qualified = new Set([...data.division_a.slice(0, 2), ...data.division_b.slice(0, 2)]
    .map((r) => r.entry_id));
  const me = String(myEntryId || "");
  const tbl = (title, rows) => `<div class="card"><h3>${esc(title)}</h3>
    <table><thead><tr><th>#</th><th>Manager</th><th class="num">P</th><th class="num">W</th>
      <th class="num">D</th><th class="num">L</th><th class="num">PF</th><th class="num">Pts</th></tr></thead>
    <tbody>${rows.map((r) => `<tr class="${qualified.has(r.entry_id) ? "qualified" : ""}${String(r.entry_id) === me ? " mine" : ""}">
      <td>${r.rank}</td><td>${mgrCell({ entry_id: r.entry_id, name: r.manager })}${qualified.has(r.entry_id) ? '<span class="qtag">PO</span>' : ""}</td>
      <td class="num">${r.played}</td><td class="num">${r.won}</td><td class="num">${r.drawn}</td>
      <td class="num">${r.lost}</td><td class="num">${r.points_for}</td>
      <td class="num"><b>${r.h2h_points}</b></td></tr>`).join("")}</tbody></table></div>`;
  el("tables").innerHTML = `<div class="two-col">
    ${tbl("Division A", data.division_a)}${tbl("Division B", data.division_b)}</div>`;
}

async function renderBracket() {
  const token = renderToken;
  let po;
  try { po = await api(withSeason("/api/custom/playoffs")); }
  catch (e) {
    const msg = await friendlyErrorHtml("Couldn't load the playoff bracket - " + e.message);
    if (token === renderToken) el("bracket").innerHTML = msg;
    return;
  }
  if (token !== renderToken) return;
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
      ${line(sf.high_seed, sf.high_points, hiWin, sf.high_seed.seed_label)}
      ${line(sf.low_seed, sf.low_points, loWin, sf.low_seed.seed_label)}
    </div>${sf.status === "pending" ? '<div class="muted" style="font-size:12px;margin-top:4px">in progress</div>' : ""}</div>`;
  };

  const f = po.final;
  const fa = f.team_a, fb = f.team_b, w = f.winner;
  const finalHtml = `<div><div class="round-title">Final · GW38</div><div class="tie">
      ${line(fa, f.a_points, w && fa && w.entry_id === fa.entry_id, fa ? fa.seed_label : "")}
      ${line(fb, f.b_points, w && fb && w.entry_id === fb.entry_id, fb ? fb.seed_label : "")}
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

// ============================ HALL OF FAME ================================
views.records = async function () {
  const token = renderToken;
  app().innerHTML = "<h2>Hall of Fame</h2>" + loadingHtml();
  let trophies = [], recs = {}, hadError = false;
  try {
    [trophies, recs] = await Promise.all([api("/api/custom/trophies"), api("/api/custom/records")]);
  } catch { hadError = true; }
  if (token !== renderToken) return;
  if (hadError) {
    const msg = await friendlyErrorHtml("Couldn't load Hall of Fame data.");
    if (token === renderToken) app().innerHTML = "<h2>Hall of Fame</h2>" + msg;
    return;
  }

  const trophyHtml = !trophies.length
    ? '<p class="empty">No completed seasons yet - champions appear here once a season\'s playoffs finish.</p>'
    : `<table><thead><tr><th>Season</th><th>Champion</th><th>Runner-up</th></tr></thead>
       <tbody>${trophies.map((t) => `<tr>
           <td>${esc(t.season_name)}</td>
           <td>🏆 ${mgrCell(t.champion)}</td>
           <td>${t.runner_up ? mgrCell(t.runner_up) : "-"}</td>
         </tr>`).join("")}</tbody></table>`;

  const card = (icon, title, body) =>
    `<div class="rule"><div class="rule-ic">${icon}</div><div><b>${title}</b><p>${body}</p></div></div>`;
  const r = recs || {};
  const cards = [
    r.highest_gameweek && card("🚀", "Highest gameweek score",
      `${mgrCell(r.highest_gameweek)} - <b>${r.highest_gameweek.points}</b> pts `
      + `(GW${r.highest_gameweek.gameweek}, ${esc(r.highest_gameweek.season_name)})`),
    r.biggest_margin && card("💥", "Biggest win margin",
      `${mgrCell({ entry_id: r.biggest_margin.winner_id, name: r.biggest_margin.winner })} `
      + `${r.biggest_margin.winner_points}-${r.biggest_margin.loser_points} `
      + `${mgrCell({ entry_id: r.biggest_margin.loser_id, name: r.biggest_margin.loser })} `
      + `(GW${r.biggest_margin.gameweek}, ${esc(r.biggest_margin.season_name)})`),
    r.best_win_streak && card("🔥", "Longest win streak",
      `${mgrCell(r.best_win_streak)} - <b>${r.best_win_streak.length}</b> wins in a row`),
    r.best_unbeaten_streak && card("🛡️", "Longest unbeaten run",
      `${mgrCell(r.best_unbeaten_streak)} - <b>${r.best_unbeaten_streak.length}</b> games unbeaten`),
    r.most_points_season && card("📈", "Most points in a season",
      `${mgrCell(r.most_points_season)} - <b>${r.most_points_season.total}</b> pts (${esc(r.most_points_season.season_name)})`),
    r.fewest_points_season && card("📉", "Fewest points in a season",
      `${mgrCell(r.fewest_points_season)} - <b>${r.fewest_points_season.total}</b> pts (${esc(r.fewest_points_season.season_name)})`),
  ].filter(Boolean);

  app().innerHTML = help("Hall of Fame",
    "<p>Champions and record-breakers across every season this site has tracked.</p>") + `
    <h2>Hall of Fame</h2>
    <h3 style="margin-top:0">Trophy cabinet</h3>
    <div class="card">${trophyHtml}</div>
    <h3>Records</h3>
    ${cards.length ? `<div class="rules">${cards.join("")}</div>`
      : '<p class="empty">No finished gameweeks yet - records appear once results start coming in.</p>'}`;
};

// ============================ MANAGER PROFILE ==============================
// Not a nav tab - a "sub-page" opened via the 📊 icon next to a manager's name
// anywhere on the site. Bypasses setView() entirely (no tab should show
// "active"), so a refresh while viewing one just falls back to whichever real
// tab was open beforehand rather than trying to restore the exact manager.
function showManagerProfile(entryId) {
  const active = document.querySelector("#tabs button.active");
  previousViewBeforeProfile = active ? active.dataset.view : "league";
  managerProfileEntryId = entryId;
  renderToken++;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.remove("active"));
  renderManagerProfile();
}

async function renderManagerProfile() {
  const token = renderToken;
  const entryId = managerProfileEntryId;
  app().innerHTML = loadingHtml("Loading profile…");
  let profile;
  try { profile = await api(`/api/custom/manager/${entryId}`); }
  catch (e) {
    const msg = await friendlyErrorHtml("Couldn't load this manager's profile - " + e.message);
    if (token === renderToken) app().innerHTML = msg;
    return;
  }
  if (token !== renderToken) return;

  const formBadge = (g) => `<span class="form-badge form-${g.result.toLowerCase()}">${g.result}</span>`;
  const formStrip = (log) => {
    const last5 = log.slice(-5);
    return last5.length ? last5.map(formBadge).join("") : '<span class="muted">No results yet</span>';
  };
  const career = profile.career;
  const season = profile.season;
  app().innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap">
      <h2 style="margin:0">${esc(profile.name)}</h2>
      <button class="btn small" id="profileBack">← Back</button>
    </div>
    <div class="rules" style="margin-top:14px">
      <div class="rule"><div class="rule-ic">🎽</div><div><b>Career</b>
        <p>${career.seasons_played} season${career.seasons_played === 1 ? "" : "s"} played -
        ${career.wins}-${career.draws}-${career.losses} W-D-L, ${career.points_for} pts total</p></div></div>
      <div class="rule"><div class="rule-ic">📈</div><div><b>Current form</b>
        <p>${season ? formStrip(season.log) : '<span class="muted">Hasn\'t played yet</span>'}</p></div></div>
    </div>
    ${!season ? '<p class="empty">This manager hasn\'t played in any season yet.</p>' : `
    <div class="card" style="margin-top:18px">
      <h3 style="margin-top:0">${esc(season.season_name)}${season.division ? ` - Division ${season.division}` : ""}</h3>
      ${!season.log.length ? '<p class="empty">No finished gameweeks yet this season.</p>' : `
      <table><thead><tr><th class="num">GW</th><th>Opponent</th>
          <th class="num">For</th><th class="num">Against</th><th>Result</th></tr></thead>
        <tbody>${season.log.map((g) => `<tr>
            <td class="num">${g.gameweek}</td><td>${esc(g.opponent)}</td>
            <td class="num">${g.own_points}</td><td class="num">${g.opp_points}</td>
            <td>${formBadge(g)}</td>
          </tr>`).join("")}</tbody></table>`}
    </div>`}`;

  el("profileBack").onclick = () => setView(previousViewBeforeProfile);
}

// --- boot -----------------------------------------------------------------
initThemeToggle();
initSetupCog();
populateMePicker();
populateSeasonPicker();
refreshBadge();
// Return to whatever tab was open if this is a refresh, not a fresh visit.
const savedView = sessionStorage.getItem("activeView");
setView(views[savedView] ? savedView : "league");
