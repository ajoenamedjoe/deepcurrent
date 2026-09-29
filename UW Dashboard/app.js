/* UW Dashboard -- page logic. Served by server.py; never open index.html directly. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LS = {
  get(k, d) { try { const v = localStorage.getItem("uwdash." + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem("uwdash." + k, JSON.stringify(v)); } catch (e) { /* private window */ } },
};

async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? "POST" : "GET"), headers: {} };
  // every change goes out as JSON: the server refuses anything else (no cross-site form posts)
  if (init.method !== "GET") { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body === undefined ? {} : opts.body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch (e) { data = { error: "Bad response from the dashboard server." }; }
  if (!r.ok && !opts.allowError) { const err = new Error(data && data.error || ("HTTP " + r.status)); err.data = data; err.status = r.status; throw err; }
  if (opts.allowError) data.__status = r.status;
  return data;
}

const APP = { settings: null, desks: [], build: null, route: null, frames: {}, loadedPage: {}, pageTimer: null };

function toast(msg, ms = 2600) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), ms);
}

/* ---------------------------------------------------------------- formatting */
const fmtMoney = (v, sign = true, dec = 2) => {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const a = Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec });
  return (v < 0 ? "−$" : (sign && v > 0 ? "+$" : "$")) + a;
};
const fmtBig = (v) => {
  if (v === null || v === undefined || isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return "$" + (v / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(0) + "M";
  return "$" + Math.round(v).toLocaleString();
};
const fmtPct = (v, dec = 1) => (v === null || v === undefined || isNaN(v)) ? "—" : (Math.round(v * 1000) / 10).toFixed(dec) + "%";
const pnlCls = (v) => v > 0.005 ? "gain" : v < -0.005 ? "loss" : "";
const isoDay = (d) => d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };

/* ---------------------------------------------------------------- icons */
const ICONS = {
  events: '<path d="M8 2v4M16 2v4M3 10h18"/><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M8 14h2M14 14h2M8 18h2"/>',
  desk: '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M7 20h10M12 16v4M7 12l3-3 3 2 4-4"/>',
  portfolio: '<path d="M21 12a9 9 0 1 1-9-9v9z"/><path d="M15 3.5A9 9 0 0 1 20.5 9H15z"/>',
  pnl: '<path d="M3 3v18h18"/><path d="M7 15l4-5 3 3 5-7"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  morning: '<circle cx="12" cy="13" r="4"/><path d="M12 3v2M4.9 6.9l1.4 1.4M19.1 6.9l-1.4 1.4M2 13h2M20 13h2M3 20h18"/>',
  lookup: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
  trackrecord: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>',
  watchlist: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  changelog: '<path d="M12 8v4l3 2"/><circle cx="12" cy="12" r="9"/><path d="M3 4v4h4"/>',
  guide: '<path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5z"/><path d="M4 21.5A2.5 2.5 0 0 1 6.5 19H20v3H6.5"/><path d="M10 7.5a2 2 0 1 1 2.6 1.9c-.4.2-.6.5-.6.9V11M12 14h0"/>',
};
const icon = (k) => `<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[k] || ICONS.link}</svg>`;

/* ---------------------------------------------------------------- theme */
function applyTheme() {
  const pref = APP.settings ? APP.settings.theme : "system";
  const fixed = APP.theme && APP.theme.modes && APP.theme.modes.length === 1 ? APP.theme.modes[0] : null;
  const dark = fixed ? fixed === "dark" : pref === "dark" || (pref === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  document.documentElement.dataset.uwtheme = (APP.theme && APP.theme.id) || "paper";
}
/* Theme = which built-in look (Settings -> Appearance). The server builds the CSS; the page swaps it in,
   and every open desk frame is told to fetch its own version (theme.js in each desk listens). */
/* The name on everything the desks make: "<brand> · <desk name from Settings>". Unusual Whales is
   credited separately as the data source on every card and report. */
const DEFAULT_BRAND = "Deep Current";
function brandKey(s) { return JSON.stringify([s && s.brand, ((s && s.desks) || []).map((d) => d.name)]); }
async function refreshTheme(tell) {
  try { APP.theme = await api("/api/theme"); } catch (e) { return; }
  let st = document.getElementById("themeCss");
  if (!st) { st = document.createElement("style"); st.id = "themeCss"; document.head.appendChild(st); }
  st.textContent = APP.theme.css;
  applyTheme();
  if (tell) $$("#frames iframe").forEach((f) => { try { f.contentWindow.postMessage({ type: "uw-theme" }, "*"); } catch (e) { /* frame gone */ } });
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

/* ---------------------------------------------------------------- clock + market */
function nyParts(d) {
  const f = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, weekday: "short",
    hour: "2-digit", minute: "2-digit", year: "numeric", month: "2-digit", day: "2-digit" });
  const p = {}; f.formatToParts(d).forEach((x) => p[x.type] = x.value);
  return { wd: p.weekday, h: +p.hour % 24, m: +p.minute, ymd: `${p.year}-${p.month}-${p.day}` };
}
function marketStatus(now) {
  // Regular hours 9:30-16:00 ET, pre 4:00-9:30, after 16:00-20:00, weekdays. Holidays are not known here.
  const p = nyParts(now); const mins = p.h * 60 + p.m;
  const wkend = p.wd === "Sat" || p.wd === "Sun";
  const hm = (x) => { const h = Math.floor(x / 60), m = x % 60; return (h ? h + "h " : "") + m + "m"; };
  if (wkend) return { cls: "", text: "Market closed · opens Mon 9:30 ET" };
  if (mins >= 570 && mins < 960) return { cls: "run", text: "Market open · closes in " + hm(960 - mins) };
  if (mins >= 240 && mins < 570) return { cls: "warn", text: "Pre-market · opens in " + hm(570 - mins) };
  if (mins >= 960 && mins < 1200) return { cls: "warn", text: "After-hours · ends in " + hm(1200 - mins) };
  if (mins < 240) return { cls: "", text: "Market closed · opens 9:30 ET" };
  return { cls: "", text: "Market closed · opens " + (p.wd === "Fri" ? "Mon" : "tomorrow") + " 9:30 ET" };
}
function tickClock() {
  const now = new Date(); const s = APP.settings || {};
  const opts = { hour: "numeric", minute: "2-digit", hour12: !s.clock_24h };
  if (s.clock_seconds !== false) opts.second = "2-digit";
  let t = now.toLocaleTimeString([], opts);
  let ampm = "";
  const m = t.match(/\s?([AP]M)$/i); if (m) { ampm = m[1]; t = t.replace(/\s?[AP]M$/i, ""); }
  const tz = (now.toLocaleTimeString([], { timeZoneName: "short" }).split(" ").pop()) || "";
  $("#clkT").innerHTML = esc(t) + `<small>${esc(ampm)} ${esc(tz)}</small>`;
  $("#clkD").textContent = now.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" });
  const ms = marketStatus(now);
  $("#mkt").innerHTML = `<span class="dot ${ms.cls}"></span><span>${esc(ms.text)}</span>`;
}

/* ---------------------------------------------------------------- logo */
function renderLogo() {
  const s = APP.settings; const el = $("#logo");
  el.style.height = (s.logo_height || 96) + "px";
  if (s.logo) {
    el.className = "logo";
    el.innerHTML = `<img alt="" src="${esc(s.logo)}" style="object-fit:${s.logo_fit === "cover" ? "cover" : "contain"}">`;
    el.title = "";
    el.onclick = null;
  } else {
    el.className = "logo empty";
    el.innerHTML = `<div><b>${esc(s.brand || DEFAULT_BRAND)}</b>Click to add your logo</div>`;
    el.onclick = () => { location.hash = "#/settings"; };
  }
  document.title = s.brand || DEFAULT_BRAND;
}

/* ---------------------------------------------------------------- nav */
function deskById(id) { return APP.desks.find((d) => d.id === id); }
function deskCfg(id) { return (APP.settings.desks || []).find((d) => d.id === id) || { id, name: id }; }
function deskDot(d) {
  if (!d) return "";
  if (d.status === "running" && d.updated) return '<span class="dot warn" title="Code changed — restart to load it"></span>';
  if (d.status === "running") return '<span class="dot run" title="Running"></span>';
  if (d.status === "starting") return '<span class="dot warn pulse" title="Starting"></span>';
  if (d.status === "error" || d.status === "missing") return '<span class="dot err" title="' + esc(d.error || d.status) + '"></span>';
  return '<span class="dot" title="Stopped"></span>';
}
/* Growth Leaders (O'Neil-style) pieces shared by Lookup, the name drawer and Morning (r16). */
const GKEYS = ["C", "A", "N", "S", "L", "I", "M"];
const GNAMES = { C: "Current earnings", A: "Annual earnings", N: "New highs", S: "Supply & demand", L: "Leader", I: "Institutions", M: "Market direction" };
const GSTATE = { breakout: "Breaking out", in_range: "In the buy range", near_pivot: "Near its pivot", weak_breakout: "Above the pivot, light volume",
  extended: "Extended", in_base: "In a base", none: "No base", unknown: "–" };
function gDots(card) {
  return `<span class="gdots">${GKEYS.map((k) => { const x = (card.checks || []).find((c) => c.key === k) || {};
    const cls = !x.measured ? "u" : x.passed ? "p" : "f";
    return `<span class="gdot ${cls}" title="${esc(GNAMES[k] + ": " + (!x.measured ? "not measured. " : x.passed ? "passes. " : "does not pass. ") + (x.text || ""))}">${k}</span>`; }).join("")}</span>`;
}
function growthBlock(card, opts = {}) {
  const b = card.base || {};
  return `<div class="big">${card.score == null ? "—" : esc(card.score)} <span class="muted" style="font-size:14px">/ 100</span> ${card.leader ? '<span class="gchip">Leader</span>' : ""}</div>
    <div style="margin:4px 0 6px">${gDots(card)} <span class="small muted">RS ${esc(card.rs ?? "—")}</span></div>
    <p class="small" style="margin:0">${esc(GSTATE[b.state] || "")}${b.pivot ? ` · buy point ${usd2(b.pivot)}, range to ${usd2(b.buy_to)}, stop ${usd2(b.stop)}` : ""}</p>
    ${opts.full ? `<div class="small" style="margin-top:6px">${(card.checks || []).map((c) => `<div><b>${esc(c.key)}</b> ${esc(c.text)}</div>`).join("")}</div>` : ""}
    ${card.in_universe === false ? `<p class="small muted">Outside the desk's daily universe: ranked against it on request.</p>` : ""}`;
}
async function refreshDirection() {
  let d; try { d = await api("/api/direction"); } catch (e) { return; }
  APP.direction = d;
  const el = $("#mdir"); if (!el) return;
  if (!d || d.error || !d.state) { el.style.display = "none"; return; }
  const cls = d.state === "uptrend" ? "run" : d.state === "pressure" ? "warn" : "err";
  el.style.display = ""; el.innerHTML = `<span class="dot ${cls}"></span><span>${esc(d.label)}</span>`;
  el.title = "Market direction (O'Neil-style): " + (d.text || "");
}

/* Invest / Trade / All (r16). Pages and desks not listed here show in every mode. */
const DESK_SIDE = { valuation: "invest", confluence: "invest", institutional: "invest", flow: "trade", swing: "trade", growth: "trade" };
const PAGE_SIDE = { watchlist: "invest", portfolio: "invest", pnl: "trade" };
function curMode() { return (APP.settings && APP.settings.mode) || "all"; }
function inMode(side, mode = curMode()) { return !side || mode === "all" || side === mode; }
function modeFirst(ids, mode = curMode()) {   // this mode's desks first, then the rest, order otherwise kept
  return ids.filter((k) => inMode(DESK_SIDE[k], mode) && DESK_SIDE[k]).concat(ids.filter((k) => !(inMode(DESK_SIDE[k], mode) && DESK_SIDE[k])));
}
function renderMode() {
  $$("#modeSw button").forEach((b) => {
    b.classList.toggle("on", b.dataset.m === curMode());
    b.onclick = async () => { if (b.dataset.m === curMode()) return; await saveSettings({ mode: b.dataset.m }, true); if (/^#\/(morning|trackrecord)/.test(APP.route || "")) route(); };
  });
}
function renderNav() {
  const s = APP.settings; const cur = location.hash || "";
  const open = LS.get("groups", {});
  renderMode();
  let h = "";
  for (const it of s.menu) {
    if (it.type === "page" && !inMode(PAGE_SIDE[it.id]) && cur !== "#/" + it.id) continue;
    if (it.type === "page") {
      const badge = it.id === "changelog" && APP.state && APP.state.release && LS.get("seenRelease", "") !== APP.state.release
        ? '<span class="newdot" title="New changes since you last looked"></span>' : it.id === "portfolio" && APP.warnCount ? `<span class="nbadge" title="${APP.warnCount} warning${APP.warnCount === 1 ? "" : "s"} on your holdings">${APP.warnCount}</span>` : "";
      h += `<a class="item ${cur === "#/" + it.id ? "active" : ""}" href="#/${it.id}">${icon(it.id)}<span>${esc(it.label)}</span>${badge}</a>`;
    } else if (it.type === "group") {
      const closed = open[it.id] === false;
      h += `<div class="grp ${closed ? "closed" : ""}" data-grp="${esc(it.id)}"><span>${esc(it.label)}</span><span class="chev">▾</span></div><div class="kids ${closed ? "closed" : ""}">`;
      for (const id of it.children) {
        if (!inMode(DESK_SIDE[id]) && cur !== "#/desk/" + id) continue;
        const c = deskCfg(id);
        h += `<a class="item ${cur === "#/desk/" + id ? "active" : ""}" href="#/desk/${esc(id)}">${icon("desk")}<span>${esc(c.name)}</span>${deskDot(deskById(id))}</a>`;
      }
      h += "</div>";
    } else if (it.type === "link") {
      const href = it.mode === "tab" ? esc(it.url) : "#/link/" + esc(it.id);
      h += `<a class="item ${cur === "#/link/" + it.id ? "active" : ""}" href="${href}" ${it.mode === "tab" ? 'target="_blank" rel="noopener"' : ""}>${icon("link")}<span>${esc(it.label)}</span>${it.mode === "tab" ? '<span class="ext">↗</span>' : ""}</a>`;
    }
  }
  $("#nav").innerHTML = h;
  $("#navSettings").style.background = cur === "#/settings" ? "var(--surface-1)" : "";
  $$("#nav .grp").forEach((g) => g.onclick = () => {
    const o = LS.get("groups", {}); o[g.dataset.grp] = g.classList.contains("closed"); LS.set("groups", o); renderNav();
  });
  $$("#nav a.item").forEach((a) => a.addEventListener("click", () => $("#side").classList.remove("open")));
}

/* ---------------------------------------------------------------- router */
const PAGES = {};
function setTop(title, sub = "", actions = "") {
  $("#title").textContent = title; $("#subtitle").innerHTML = sub; $("#actions").innerHTML = actions;
}
function showFrame(key) {
  $$("#frames iframe").forEach((f) => f.classList.toggle("show", f.dataset.key === key));
  $("#frames").style.display = key ? "" : "none";
  $("#page").style.display = key ? "none" : "";
}
function route() {
  let h = location.hash || LS.get("route", "#/morning");
  if (!/^#\//.test(h)) h = "#/morning";
  if (/^#\/events\b/.test(h)) h = "#/morning";             // Today's Events is part of Today now (r17)
  if (location.hash !== h) { history.replaceState(null, "", h); }
  LS.set("route", h);
  APP.route = h;
  if (APP.onLeave) { try { APP.onLeave(); } catch (e) { /* never block navigation */ } APP.onLeave = null; }
  clearInterval(APP.pageTimer); APP.pageTimer = null; APP.onDesks = null;
  const parts = h.slice(2).split("/");
  renderNav();
  const page = $("#page"); page.innerHTML = ""; showFrame(null);
  const fn = PAGES[parts[0]];
  if (fn) fn(page, parts.slice(1)); else PAGES.morning(page, []);
}
window.addEventListener("hashchange", route);
$("#menuBtn").onclick = () => $("#side").classList.toggle("open");

/* ---------------------------------------------------------------- polling */
async function pollDesks() {
  try {
    const r = await api("/api/desks");
    const before = JSON.stringify(APP.desks.map((d) => [d.id, d.status, d.updated]));
    APP.desks = r.desks;
    if (APP.build && r.build.page_mtime && APP.build.page_mtime && r.build.page_mtime > APP.build.page_mtime) {
      toast("The dashboard was updated — reloading…"); setTimeout(() => location.reload(), 900); return;
    }
    if (APP.build && r.build.code_mtime > r.build.started + 1) {
      if (!$("#rsBtn")) {
        $("#stamp").innerHTML = `<span style="color:var(--warning)">Dashboard updated.</span> <button class="sm" id="rsBtn">Restart to load it</button>`;
        $("#rsBtn").onclick = restartDashboard;
      }
    }
    if (JSON.stringify(APP.desks.map((d) => [d.id, d.status, d.updated])) !== before) renderNav();
    if (APP.onDesks) APP.onDesks();
  } catch (e) { /* the server may be restarting */ }
}

async function boot() {
  const st = await api("/api/state");
  APP.settings = st.settings; APP.build = st.build; APP.state = st;
  await refreshTheme(false);
  applyTheme(); renderLogo(); tickClock();
  $("#stamp").textContent = "build " + st.build.built + " · port " + st.port;
  setInterval(tickClock, 1000);
  refreshDirection(); setInterval(refreshDirection, 10 * 60 * 1000);
  try { APP.desks = (await api("/api/desks")).desks; } catch (e) { APP.desks = []; }
  if (morningDue() && (!location.hash || location.hash === "#/")) { LS.set("morningShownOn", isoDay(new Date())); history.replaceState(null, "", "#/morning"); }
  route();
  // a tab left open overnight still lands on Morning once, before the bell -- unless you're typing
  setInterval(() => {
    const a = document.activeElement;
    if (morningDue() && document.visibilityState === "visible" && !(a && /TEXTAREA|INPUT/.test(a.tagName))) {
      LS.set("morningShownOn", isoDay(new Date())); location.hash = "#/morning";
    }
  }, 60000);
  setInterval(pollDesks, 4000);
  refreshWarnings(); setInterval(refreshWarnings, 15 * 60 * 1000);
}

function morningDue() {
  const ny = nyParts(new Date());
  return APP.settings.morning_autoopen !== false && LS.get("morningShownOn", "") !== isoDay(new Date())
    && !/^(Sat|Sun)$/.test(ny.wd) && ny.h * 60 + ny.m >= 240 && ny.h * 60 + ny.m < 570;
}

async function refreshWarnings() {
  try {
    const w = await api("/api/warnings");
    APP.warnings = w;
    APP.warnCount = (w.holdings || []).reduce((a, h) => a + h.warnings.length, 0);
    renderNav();
  } catch (e) { /* shown on the pages that use it */ }
}

async function restartDashboard() {
  toast("Restarting the dashboard\u2026 desks keep running", 6000);
  try { await api("/api/restart", { body: {} }); } catch (e) { /* it may drop the connection */ }
  const t0 = Date.now();
  const wait = async () => {
    try { const r = await fetch("/api/health", { cache: "no-store" }); if (r.ok && Date.now() - t0 > 1500) { location.reload(); return; } } catch (e) { /* not up yet */ }
    if (Date.now() - t0 < 30000) setTimeout(wait, 700); else toast("The dashboard didn't come back \u2014 run START_HERE.bat", 9000);
  };
  setTimeout(wait, 1200);
}

async function saveSettings(patch, quiet) {
  const before = brandKey(APP.settings);
  const r = await api("/api/settings", { body: Object.assign({}, APP.settings, patch) });
  APP.settings = r.settings;
  applyTheme(); renderLogo(); renderNav(); tickClock();
  if (brandKey(APP.settings) !== before) refreshTheme(true);   // open desks relabel their pages and share cards
  if (!quiet) { const s = $("#savedMark"); if (s) { s.classList.add("show"); clearTimeout(s._h); s._h = setTimeout(() => s.classList.remove("show"), 1400); } }
  return r.settings;
}

/* ================================================================ desks */
function ensureFrame(key, url) {
  let f = APP.frames[key];
  if (!f) {
    f = document.createElement("iframe");
    f.dataset.key = key;
    // the desks may use the clipboard (copy image / caption); other sites in the menu may not read it
    f.setAttribute("allow", key.startsWith("desk") ? "clipboard-read; clipboard-write; fullscreen" : "fullscreen");
    f.src = url;
    $("#frames").appendChild(f);
    APP.frames[key] = f;
  } else if (f.dataset.src !== url && url) {
    f.src = url;
  }
  f.dataset.src = url;
  return f;
}
function reloadFrame(key) {
  const f = APP.frames[key]; if (!f) return;
  f.src = f.dataset.src; // cross-origin: reassigning src is the reliable reload
}

PAGES.desk = function (page, args) {
  const id = args[0]; const key = "desk:" + id;
  let asked = false; let lastStatus = null;
  const draw = async () => {
    if (APP.route !== "#/desk/" + id) return;
    const d = deskById(id); const cfg = deskCfg(id);
    const name = (cfg.name || id) + (/desk/i.test(cfg.name || "") ? "" : " Desk");
    if (!d) { setTop(name); page.innerHTML = `<div class="center-msg"><h2>Unknown desk</h2></div>`; return; }
    const port = d.port ? "port " + d.port : "no port";
    const statusTxt = { running: "running", starting: "starting…", stopped: "stopped", error: "not running", missing: "not found", unknown: "checking…" }[d.status] || d.status;
    const upd = d.status === "running" && d.updated
      ? `<span class="chip hi" title="Its .py or .env files changed after it started. Python keeps the old model in memory until the desk restarts.">code changed on disk</span><button class="primary sm" id="dRestart2">Restart to load it</button>` : "";
    setTop(name, `${deskDot(d)} ${esc(statusTxt)} · ${esc(port)}`,
      upd + (d.status === "running" ? `<button class="sm" id="dReload" title="Reload the desk page">Reload</button>
      <a href="${esc(d.url)}" target="_blank" rel="noopener"><button class="sm">Open in new tab ↗</button></a>
      <button class="sm" id="dRestart">Restart</button><button class="sm danger" id="dStop">Stop</button>` : ""));
    const bind = (sel, fn) => { const b = $(sel); if (b) b.onclick = fn; };
    bind("#dReload", () => reloadFrame(key));
    const restart = async () => { toast("Restarting " + name + "…"); await api(`/api/desks/${id}/restart`, { body: {} }); await pollDesks(); };
    bind("#dRestart", restart); bind("#dRestart2", restart);
    bind("#dStop", async () => { if (!confirmInline("#dStop", "Stop?")) return; await api(`/api/desks/${id}/stop`, { body: {} }); await pollDesks(); });

    if (d.status === "running") {
      const f = ensureFrame(key, d.url);
      if (lastStatus && lastStatus !== "running") reloadFrame(key);            // came back after a restart
      if (APP.loadedPage[id] && d.page_mtime && d.page_mtime > APP.loadedPage[id]) { reloadFrame(key); toast(name + " page updated — reloaded"); }
      APP.loadedPage[id] = d.page_mtime || APP.loadedPage[id];
      showFrame(key); page.innerHTML = "";
      lastStatus = "running";
      return;
    }
    lastStatus = d.status;
    showFrame(null);
    if (d.status === "starting" || (!asked && ["stopped", "unknown", "error"].includes(d.status))) {
      page.innerHTML = `<div class="center-msg"><div class="spinner"></div><h2>Starting ${esc(name)}…</h2>
        <p>It was not running, so the dashboard started it. It usually answers within a few seconds.</p></div>`;
      if (!asked) { asked = true; try { await api(`/api/desks/${id}/ensure`, { body: {} }); } catch (e) { /* shown below */ } await pollDesks(); }
      return;
    }
    if (d.status === "stopped") {
      page.innerHTML = `<div class="center-msg"><h2>${esc(name)} is stopped</h2><p>Stopped from the dashboard. Start it again when you want it.</p>
        <button class="primary" id="dStart">Start ${esc(name)}</button></div>`;
      bind("#dStart", async () => { asked = true; await api(`/api/desks/${id}/start`, { body: {} }); await pollDesks(); });
      return;
    }
    // error / missing
    if (d.status === "missing") {
      page.innerHTML = `<div class="center-msg" style="max-width:640px"><h2>Can't find the ${esc(name)}</h2>
        <p>${esc(d.error || "")}</p><p>If it lives somewhere else on this PC, set its folder in Settings.</p>
        <p><a href="#/settings"><button class="primary">Desk settings</button></a> <button id="dRetry">Check again</button></p></div>`;
      bind("#dRetry", async () => { await api(`/api/desks/${id}/start`, { body: {} }); await pollDesks(); });
      return;
    }
    const log = d.log_tail ? `<pre class="log">${esc(d.log_tail)}</pre>` : "";
    page.innerHTML = `<div class="center-msg" style="max-width:720px"><h2>${esc(name)} is not running</h2>
      <p>${esc(d.error || "It did not start.")}</p>
      <p class="small muted">Folder: ${esc(d.folder || "—")}</p>
      <p><button class="primary" id="dRetry">Try again</button> <a href="#/settings"><button>Desk settings</button></a></p>${log}</div>`;
    bind("#dRetry", async () => { await api(`/api/desks/${id}/start`, { body: {} }); await pollDesks(); });
  };
  APP.onDesks = draw;
  draw();
};

function confirmInline(sel, label) {
  const b = $(sel); if (!b) return true;
  if (b.dataset.armed) return true;
  b.dataset.armed = "1"; const old = b.textContent; b.textContent = label;
  setTimeout(() => { if (b.isConnected) { b.textContent = old; delete b.dataset.armed; } }, 2500);
  return false;
}

/* ================================================================ custom links */
PAGES.link = async function (page, args) {
  APP.onDesks = null;
  const it = APP.settings.menu.find((m) => m.type === "link" && m.id === args[0]);
  if (!it) { setTop("Link"); page.innerHTML = `<div class="center-msg"><h2>That link was removed</h2></div>`; return; }
  const key = "link:" + it.id;
  setTop(it.label, esc(new URL(it.url).hostname), `<button class="sm" id="lReload">Reload</button>
    <a href="${esc(it.url)}" target="_blank" rel="noopener"><button class="sm">Open in new tab ↗</button></a>`);
  $("#lReload").onclick = () => reloadFrame(key);
  let chk = { frameable: null };
  try { chk = await api("/api/framecheck?url=" + encodeURIComponent(it.url)); } catch (e) { /* unknown */ }
  if (APP.route !== "#/link/" + it.id) return;
  if (chk.frameable === false) {
    showFrame(null);
    page.innerHTML = `<div class="center-msg"><h2>${esc(new URL(it.url).hostname)} won't open inside the dashboard</h2>
      <p>${esc(chk.reason || "")} Sites choose this themselves to stop other pages framing them.</p>
      <p><a href="${esc(it.url)}" target="_blank" rel="noopener"><button class="primary">Open in a new tab ↗</button></a></p>
      <p class="small muted">Tip: in Settings you can set this link to always open in a new tab.</p></div>`;
    return;
  }
  ensureFrame(key, it.url); showFrame(key);
};

/* ================================================================ Today (r17)
   Morning and Today's Events in one page. The date strip moves the calendar, earnings and journal to any day;
   the live parts (market direction, holdings, your names, desk highlights, market tide, systems) are today's only.
   Sections fold and remember it. */
const HIGH_IMPACT = /\b(CPI|PCE|FOMC|Fed(eral)? (Funds|Chair|Reserve Chair)|Powell|Nonfarm|Payrolls|Unemployment Rate|GDP|Retail Sales|ISM|PPI|Jobless Claims|JOLTS|Interest Rate Decision|Beige Book|Minutes)\b/i;
const EARN_PREVIEW = 12;       // other companies shown before "Show all"

function foldSec(key, title, sub, body, extra = "") {
  const shut = (LS.get("fold", {}) || {})[key] === false;
  return `<section class="panel fold ${shut ? "closed" : ""}" data-fold="${key}"><h2><button class="foldbtn" aria-expanded="${!shut}" title="Show or hide">${shut ? "▸" : "▾"}</button><span class="foldt">${title}</span>
    ${sub ? `<span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">${sub}</span>` : ""}<span class="spacer"></span>${extra}</h2><div class="fbody">${body}</div></section>`;
}
function wireFolds(root) {
  $$("[data-fold] > h2 .foldbtn, [data-fold] > h2 .foldt", root).forEach((el) => el.onclick = () => {
    const sec = el.closest("[data-fold]"); const open = sec.classList.toggle("closed") === false;
    const b = sec.querySelector(".foldbtn"); b.textContent = open ? "▾" : "▸"; b.setAttribute("aria-expanded", open);
    const f = LS.get("fold", {}) || {}; f[sec.dataset.fold] = open; LS.set("fold", f);
  });
}

PAGES.morning = async function (page) {
  const today = isoDay(new Date());
  let date = LS.get("todayDate", null);
  if (!date || LS.get("todayDateSetOn", "") !== today) date = today;   // a new day starts on today
  const isToday = date === today;
  if (isToday) LS.set("morningShownOn", today);
  const filt = { q: "", f: APP.settings.earnings_filter || "all", all: false };
  const d0 = parseDay(date);
  const ms = marketStatus(new Date());
  setTop("Today", esc(d0.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" })) + (isToday ? " · " + esc(ms.text) : ""),
    `<span class="datestrip"><button class="sm" id="evPrev" title="Previous market day">‹</button>
     <input type="date" id="evDate" class="sm" value="${date}" aria-label="Day">
     <button class="sm" id="evNext" title="Next market day">›</button>
     <button class="sm ${isToday ? "on" : ""}" id="evToday">Today</button></span>
     <button class="sm" id="mRefresh" title="Refresh">↻</button>`);
  const setDate = (d) => { LS.set("todayDate", d); LS.set("todayDateSetOn", today); route(); };
  const shift = (n) => { const d = parseDay(date); d.setDate(d.getDate() + n); if (d.getDay() === 6) d.setDate(d.getDate() + (n > 0 ? 2 : -1)); if (d.getDay() === 0) d.setDate(d.getDate() + (n > 0 ? 1 : -2)); setDate(isoDay(d)); };

  const small = (t) => `· ${t}`;
  const SEC = {
    cal: foldSec("cal", "Economic calendar", `<span id="calSub">${small("your local time")}</span>`, `<div id="cal"><p class="muted small">Loading…</p></div>`),
    names: `<section class="panel"><h2>Your names <span class="spacer"></span><a href="#/watchlist" class="small" style="text-transform:none;letter-spacing:0;font-weight:500">Watchlist →</a></h2><div id="mNames"><p class="muted small">Checking buy zones and theses…</p></div></section>`,
    holdings: `<section class="panel"><h2>Your holdings</h2><div id="mWarn">${warningsList(APP.warnings)}</div></section>`,
    market: `${marketStrip()}`,
    picks: `<section class="panel"><h2>Desk highlights <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· the top of each desk right now</span></h2><div id="mPicks"><p class="muted small">Asking the desks…</p></div></section>`,
    sys: `<section class="panel"><h2>Systems</h2><div id="mSys"></div></section>`,
    direction: `<div id="mDir"></div>`,
    journal: foldSec("journal", "Journal", `<span id="jDay"></span>`, `
      <textarea id="jText" class="jtext" spellcheck="true" placeholder="Thoughts for the day — plan, bias, levels, what you saw, what you'd do differently…"></textarea>
      <div class="row small" style="margin:6px 0 0"><button class="sm ghost" id="jTime" title="Insert the current time at the cursor">+ time</button><span class="muted" id="jCount"></span><span class="spacer"></span><span class="muted">Saves as you type</span></div>
      <details id="jPast" class="jpast"><summary>Past entries <span class="muted" id="jN"></span></summary>
        <input type="search" id="jQ" placeholder="Search your notes" style="width:100%;margin:8px 0">
        <div id="jList"></div></details>`, `<span class="jstat" id="jStat"></span>`),
    earn: foldSec("earn", "Earnings", `<span id="earnSub"></span>`, `
      <div class="row" style="margin-top:0">
        <div class="seg" id="earnFilt"><button data-f="all">All</button><button data-f="sp500">S&amp;P 500</button><button data-f="big">Over $10B</button><button data-f="opt">Optionable</button></div>
        <input type="text" id="earnQ" placeholder="Search ticker or name" style="flex:1;min-width:140px"></div>
      <div id="earn"><p class="muted small">Loading…</p></div>`),
  };
  const LAYOUT = {
    all: [["cal", "names", "holdings"], ["direction", "market", "picks", "sys"]],
    invest: [["cal", "names", "holdings"], ["market", "picks", "direction", "sys"]],
    trade: [["direction", "picks", "cal"], ["market", "holdings", "names", "sys"]],
  }[curMode()] || [];
  page.innerHTML = `<div class="pad"><div id="evBanner"></div>
    ${isToday ? `<div class="mgrid">${LAYOUT.map((col) => `<div>${col.map((k) => SEC[k]).join("\n")}</div>`).join("")}</div>`
      : `<div class="banner info" style="display:flex;align-items:center;gap:10px">Showing ${esc(d0.toLocaleDateString([], { weekday: "long", month: "long", day: "numeric", year: "numeric" }))}: the calendar, earnings and your note for that day. Warnings, desk picks and the market are on today.<span class="spacer"></span><button class="sm" id="tdBack">Back to today</button></div>`}
    <div class="cols2 tdday"><div class="evleft">${isToday ? "" : SEC.cal}${SEC.journal}</div><div class="evright">${SEC.earn}</div></div></div>`;
  wireFolds(page);
  $("#evPrev").onclick = () => shift(-1); $("#evNext").onclick = () => shift(1);
  $("#evToday").onclick = () => setDate(today);
  $("#evDate").onchange = (e) => e.target.value && /^\d{4}-\d{2}-\d{2}$/.test(e.target.value) && setDate(e.target.value);
  if ($("#tdBack")) $("#tdBack").onclick = () => setDate(today);
  $("#mRefresh").onclick = () => { loadDay(); if (isToday) loadLive(); };
  $$("#earnFilt button").forEach((b) => b.onclick = () => { filt.f = b.dataset.f; saveSettings({ earnings_filter: filt.f }, true); drawEarn(); });
  $("#earnQ").oninput = (e) => { filt.q = e.target.value.trim().toUpperCase(); drawEarn(); };
  let data = null;

  /* ---- the day: calendar + earnings (any date) */
  async function loadDay() {
    try { data = await api("/api/events?date=" + date); }
    catch (e) { $("#evBanner").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    if (APP.route !== "#/morning") return;
    const errs = [data.calendar.error, data.earnings.error].filter(Boolean);
    const wkend = d0.getDay() === 0 || d0.getDay() === 6;
    $("#evBanner").innerHTML = (!APP.state.token ? `<div class="banner err">No Unusual Whales token found. Put <b>UW_API_TOKEN=…</b> in the dashboard's .env, or keep a desk's .env next door (Valuation, Confluence or Swing Desk).</div>` : "")
      + errs.map((e) => `<div class="banner ${(data.calendar.rows.length || data.earnings.rows.length) ? "warn" : "err"}">${esc(e)}${data.calendar.stale || data.earnings.stale ? " — showing the last good copy." : ""}</div>`).join("")
      + (wkend ? `<div class="banner info">${d0.toLocaleDateString([], { weekday: "long" })}: US markets are closed. <button class="sm" id="evMon">Jump to Monday</button></div>` : "");
    const mon = $("#evMon"); if (mon) mon.onclick = () => shift(1);
    drawCal(); drawEarn();
  }

  function drawCal() {
    const rows = data.calendar.rows.filter((r) => r.time && isoDay(new Date(r.time)) === date);
    const now = Date.now();
    const nextIdx = rows.findIndex((r) => new Date(r.time).getTime() > now);
    const hi = rows.filter((r) => HIGH_IMPACT.test(r.event)).length;
    $("#calSub").textContent = rows.length ? `· ${rows.length} release${rows.length === 1 ? "" : "s"}${hi ? ", " + hi + " high impact" : ""} · your local time` : "· your local time";
    if (!rows.length) { $("#cal").innerHTML = `<p class="muted small">${data.calendar.error ? "Unavailable." : "No economic releases listed for this day."}</p>`; return; }
    $("#cal").innerHTML = `<div class="tblwrap"><table><thead><tr><th>Time</th><th>Event</th><th class="n">Actual</th><th class="n">Fcst</th><th class="n">Prior</th></tr></thead><tbody>${
      rows.map((r, i) => {
        const t = new Date(r.time);
        const cls = t.getTime() < now ? "past" : i === nextIdx ? "next" : "";
        const h = HIGH_IMPACT.test(r.event);
        return `<tr class="${cls}"><td class="n" style="text-align:left;white-space:nowrap">${t.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</td>
          <td>${h ? "<b>" : ""}${esc(r.event)}${h ? '</b> <span class="chip hi">high impact</span>' : ""}${r.period ? ` <span class="muted small">${esc(r.period)}</span>` : ""}${i === nextIdx ? ' <span class="chip">next</span>' : ""}</td>
          <td class="n">${r.actual ? `<b>${esc(r.actual)}</b>` : "—"}</td><td class="n">${esc(r.forecast ?? "—")}</td><td class="n">${esc(r.prev ?? "—")}</td></tr>`;
      }).join("")}</tbody></table></div>`;
  }

  function drawEarn() {
    $$("#earnFilt button").forEach((b) => b.classList.toggle("on", b.dataset.f === filt.f));
    if (!data) return;
    const all = data.earnings.rows, mine = data.mine || {};
    if (!all.length) { $("#earnSub").textContent = ""; $("#earn").innerHTML = `<p class="muted small">${data.earnings.error ? "Unavailable." : "No earnings reports listed for this day."}</p>`; return; }
    const norm = (s) => { s = (s || "").toLowerCase(); return /pre/.test(s) ? "premarket" : /post|after/.test(s) ? "afterhours" : "other"; };
    const when = { premarket: "before the open", afterhours: "after the close", other: "time not announced" };
    const yours = all.filter((r) => mine[r.symbol]);
    let rows = all.filter((r) => !mine[r.symbol]).filter((r) => filt.f === "all" || (filt.f === "sp500" && r.sp500) || (filt.f === "big" && (r.marketcap || 0) >= 1e10) || (filt.f === "opt" && r.has_options));
    if (filt.q) rows = rows.filter((r) => r.symbol.includes(filt.q) || (r.name || "").toUpperCase().includes(filt.q));
    const showAll = filt.all || !!filt.q;
    const shown = showAll ? rows : rows.slice().sort((a, b) => (b.marketcap || 0) - (a.marketcap || 0)).slice(0, EARN_PREVIEW);
    const keep = new Set(shown);
    $("#earnSub").textContent = `· ${all.length} reporting${yours.length ? ", " + yours.length + " of yours" : ""}`;
    const rowHtml = (r) => {
      const sur = r.surprise;
      return `<tr><td style="white-space:nowrap">${/^https:\/\//i.test(r.logo || "") ? `<img class="lg" loading="lazy" src="${esc(r.logo)}" alt="" onerror="this.style.visibility='hidden'"> ` : ""}<a class="tick" href="#/lookup/${esc(r.symbol)}" title="${esc(r.name || "")}">${esc(r.symbol)}</a>
        <span class="muted small nm" title="${esc(r.name || "")}">${esc(r.name || "")}</span>${mine[r.symbol] ? ` <span class="chip mine">${mine[r.symbol] === "held" ? "you hold" : "watching"}</span>` : ""}${r.sp500 ? ' <span class="chip">S&amp;P</span>' : ""}</td>
        <td class="n">${fmtBig(r.marketcap)}</td>
        <td class="n" title="${r.move !== null ? "±$" + r.move.toFixed(2) + " per share" : ""}">${r.move_pct !== null ? "±" + fmtPct(r.move_pct) : "—"}</td>
        <td class="n">${r.avg_abs_reaction !== null ? "±" + fmtPct(r.avg_abs_reaction) : "—"}</td>
        <td class="n">${r.eps_est !== null ? r.eps_est.toFixed(2) : "—"}</td>
        <td class="n">${r.eps_act !== null ? `<b>${r.eps_act.toFixed(2)}</b> ${sur !== null ? `<span class="${pnlCls(sur)} small">${sur > 0 ? "+" : ""}${fmtPct(sur, 0)}</span>` : ""}` : '<span class="muted">pending</span>'}</td></tr>`;
    };
    const head = `<tr><th>Company</th><th class="n">Mkt cap</th><th class="n" title="Options-implied move into the report (hover a value for dollars)">Implied</th><th class="n" title="Average absolute 1-day move after the last 4 reports">Past avg</th><th class="n">EPS est</th><th class="n">Actual</th></tr>`;
    let h = `<div class="tblwrap"><table><thead>${head}</thead><tbody>`;
    if (yours.length) h += `<tr><td colspan="6" class="sess" style="border:0">Your names</td></tr>` + yours.map((r) => rowHtml(r).replace("</span></td>", `</span><div class="muted small">${when[norm(r.session)]}</div></td>`)).join("");
    for (const k of ["premarket", "afterhours", "other"]) {
      const g = rows.filter((r) => norm(r.session) === k && keep.has(r));
      const n = rows.filter((r) => norm(r.session) === k).length;
      if (!n && k === "other") continue;
      h += `<tr><td colspan="6" class="sess" style="border:0">${when[k][0].toUpperCase() + when[k].slice(1)} <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">${showAll ? n : g.length + " of " + n}</span></td></tr>`;
      h += g.length ? g.map(rowHtml).join("") : `<tr><td colspan="6" class="muted small">${n ? "Smaller companies: use Show all." : "None" + (filt.f !== "all" || filt.q ? " matching the filter" : "") + "."}</td></tr>`;
    }
    h += "</tbody></table></div>";
    if (!filt.q && rows.length > EARN_PREVIEW) h += `<div class="row" style="margin-top:8px"><button class="sm" id="earnAll">${filt.all ? "Show the largest " + EARN_PREVIEW : "Show all " + rows.length}</button></div>`;
    $("#earn").innerHTML = h + `<p class="small muted" style="margin:8px 0 0">Unusual Whales · updated ${new Date(data.earnings.fetched_at * 1000 || Date.now()).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</p>`;
    const btn = $("#earnAll"); if (btn) btn.onclick = () => { filt.all = !filt.all; drawEarn(); };
  }

  /* ---- today only: warnings, names, direction, desk highlights, systems */
  async function loadLive() {
    fillMarketStrip();
    let d;
    try { d = await api("/api/morning"); } catch (e) { return; }
    if (APP.route !== "#/morning") return;
    APP.warnings = d.warnings; APP.warnCount = (d.warnings.holdings || []).reduce((a, h) => a + h.warnings.length, 0); renderNav();
    $("#mWarn").innerHTML = warningsList(d.warnings, { details: true });
    const nm = d.names || {};
    $("#mNames").innerHTML = nm.error ? `<p class="small">${esc(nm.error)}</p>` : !nm.count ? `<p class="small muted">No names yet. Add some on the <a href="#/watchlist">Watchlist</a>; your holdings appear there by themselves.</p>` : `
      ${nm.in_zone.length ? nm.in_zone.map((i) => `<div class="mn-row"><a href="#" data-open="${esc(i.ticker)}"><b>${esc(i.ticker)}</b></a> ${zoneChip(i)} <span class="small muted">${usd2(i.price)} vs buy below ${usd2(i.buy)}</span></div>`).join("") : `<p class="small muted" style="margin:0 0 6px">Nothing in its buy zone today.</p>`}
      ${nm.breaks.map((i) => `<div class="mn-row"><a href="#" data-open="${esc(i.ticker)}"><b>${esc(i.ticker)}</b></a> <span class="tbreak">▲ thesis</span> <span class="small">${esc(i.checks.filter((c) => c.status === "break").map((c) => c.detail).join("; "))}</span></div>`).join("")}
      ${nm.events.map((e) => `<div class="mn-row small"><a href="#" data-open="${esc(e.ticker)}"><b>${esc(e.ticker)}</b></a> ${esc(e.text.replace(/^\S+\s/, ""))}</div>`).join("")}`;
    $$("#mNames [data-open]").forEach((a) => a.onclick = (e) => { e.preventDefault(); openName(a.dataset.open); });
    const dr = d.direction || {};
    $("#mDir").innerHTML = dr.state ? `<section class="panel mdirbox ${esc(dr.state)}"><h2>Market direction <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· O'Neil-style, SPY and QQQ</span><span class="spacer"></span><a class="small" style="text-transform:none;letter-spacing:0;font-weight:500" href="#/desk/growth">Growth Leaders →</a></h2>
      <div class="big" style="font-size:20px">${esc(dr.label)}</div><p class="small" style="margin:4px 0 0">${esc(dr.text || "")}</p>
      ${!dr.ok ? `<p class="small" style="margin:6px 0 0"><b>New growth buys wait for a confirmed uptrend.</b></p>` : ""}</section>` : "";
    const pk = d.picks || {};
    const names = {}; modeFirst(["confluence", "flow", "institutional", "valuation", "swing", "growth"].filter((k) => k in pk || k !== "growth"))
      .forEach((k) => { names[k] = deskCfg(k).name || k; });
    $("#mPicks").innerHTML = pk.error ? `<p class="small">${esc(pk.error)}</p>` : Object.keys(names).map((k) => {
      const b = pk[k] || {}; const items = (b.items || []).slice(0, 3);
      const body = b.error ? `<span class="muted small">${b.error === "not running" ? "Desk is off." : esc(b.error)}</span> <a class="small" href="#/desk/${k}">${b.error === "not running" ? "Start it" : "Open"}</a>`
        : items.length ? items.map((x) => `<a class="pick" href="#/lookup/${esc(x.ticker)}"><b>${esc(x.ticker)}</b> <span class="muted">${x.score !== null && x.score !== undefined ? Math.round(x.score * 10) / 10 : ""}</span>${x.direction === "short" ? ' <span class="chip">bearish</span>' : ""}</a>`).join("")
        : `<span class="muted small">Nothing on the board.</span>`;
      return `<div class="prow"><a class="pdesk" href="#/desk/${k}">${esc(names[k])}</a><div>${body}</div></div>`;
    }).join("");
    const dot = (s) => s === "running" ? "run" : s === "starting" ? "warn" : s === "error" || s === "missing" ? "err" : "";
    const b = d.backup || {};
    $("#mSys").innerHTML = `<div class="sysrow">${d.desks.map((x) => `<span class="chip"><span class="dot ${dot(x.status)}"></span>${esc(x.name)}${x.updated ? " · update waiting" : ""}</span>`).join(" ")}</div>
      <p class="small" style="margin:8px 0 0">${b.ok ? `<span class="wico ok">✓</span> Backed up ${new Date(b.at * 1000).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}` : b.error ? `<span class="wico serious">▲</span> Last backup failed: ${esc(b.error)}` : "No backup yet — the first runs within the hour."} · <a href="#/settings">System health</a></p>`;
  }

  const journal = makeJournal((d) => setDate(d));
  loadDay();
  if (isToday) loadLive();
  journal.open(date);
  APP.pageTimer = setInterval(() => { loadDay(); if (isToday) loadLive(); }, 5 * 60 * 1000);
  APP.onLeave = () => journal.flush();
};
/* Today's Events was folded into Today (r17): old links and bookmarks land there. */
PAGES.events = function () { location.replace("#/morning"); };

/* ================================================================ Portfolio */
PAGES.portfolio = async function (page) {
  const s = APP.settings;
  let view = ["visual", "table", "sheet"].includes(s.portfolio_view) ? s.portfolio_view : "visual";
  let urls = { html: null, csv: null };
  try { urls = await api("/api/portfolio/urls"); } catch (e) { /* shown below */ }
  const src = s.portfolio_url || "";
  const openUrl = /^https:\/\//i.test(src) ? (src.replace(/\/pub\?.*$/, "/pubhtml").replace(/output=csv/, "") || src) : "";
  const draw = () => {
    setTop("Portfolio", esc(view === "sheet" ? "the published Google Sheet" : "from your Google Sheet"),
      `<div class="seg"><button data-v="visual" class="${view === "visual" ? "on" : ""}">Visual</button><button data-v="sheet" class="${view === "sheet" ? "on" : ""}">Sheet</button><button data-v="table" class="${view === "table" ? "on" : ""}">Table</button></div>
       <button class="sm" id="pRefresh">↻ Refresh</button>
       ${openUrl ? `<a href="${esc(openUrl)}" target="_blank" rel="noopener"><button class="sm">Open ↗</button></a>` : ""}`);
    $$("#actions .seg button").forEach((b) => b.onclick = () => { view = b.dataset.v; saveSettings({ portfolio_view: view }, true); draw(); });
    $("#pRefresh").onclick = () => view === "sheet" ? reloadFrame("portfolio") : view === "visual" ? visual(true) : table(true);
    if (!src) {
      showFrame(null);
      page.innerHTML = `<div class="center-msg"><h2>No sheet linked yet</h2><p>Paste your published Google Sheet link in Settings.</p><a href="#/settings"><button class="primary">Settings</button></a></div>`;
      return;
    }
    if (view === "sheet" && urls.html) { page.innerHTML = ""; ensureFrame("portfolio", urls.html); showFrame("portfolio"); return; }
    showFrame(null); if (view === "visual") visual(false); else table(false);
  };
  async function visual(force) {
    if (!page.querySelector(".pf")) page.innerHTML = `<div class="pad"><p class="muted">Loading the sheet\u2026</p></div>`;
    let d;
    try { d = await api("/api/portfolio" + (force ? "?force=1" : "")); } catch (e) { d = { error: e.message, model: { ok: false } }; }
    if (APP.route !== "#/portfolio" || view !== "visual") return;
    renderPortfolioVisual(page, d);
  }
  let sortCol = LS.get("pfSort", null), sortDir = LS.get("pfDir", -1);
  async function table(force) {
    page.innerHTML = `<div class="pad"><p class="muted">Loading the sheet…</p></div>`;
    let d;
    try { d = await api("/api/portfolio" + (force ? "?force=1" : "")); } catch (e) { d = { error: e.message, columns: [], rows: [] }; }
    if (APP.route !== "#/portfolio") return;
    const render = () => {
      let rows = d.rows.slice();
      const types = d.types || [];
      if (sortCol !== null && sortCol < d.columns.length) {
        const t = types[sortCol] || {};
        rows.sort((a, b) => {
          const va = t.kind === "num" ? pnum(a[sortCol]) : (a[sortCol] || "").toLowerCase();
          const vb = t.kind === "num" ? pnum(b[sortCol]) : (b[sortCol] || "").toLowerCase();
          if (va === null) return 1; if (vb === null) return -1;
          return (va > vb ? 1 : va < vb ? -1 : 0) * sortDir;
        });
      }
      page.innerHTML = `<div class="pad">${d.error ? `<div class="banner ${d.rows.length ? "warn" : "err"}">${esc(d.error)}</div>` : ""}
        <section class="panel">${d.columns.length ? `<div class="tblwrap"><table><thead><tr>${d.columns.map((c, i) => `<th class="${(types[i] || {}).kind === "num" ? "n" : ""}" data-i="${i}" style="cursor:pointer">${esc(c)}${sortCol === i ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr></thead>
        <tbody>${rows.map((r) => `<tr>${r.map((c, i) => { const t = types[i] || {}; const v = t.signed ? pnum(c) : null;
          return `<td class="${t.kind === "num" ? "n" : ""} ${v !== null ? pnlCls(v) : ""}">${esc(c)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>` : `<p class="muted">The sheet is empty.</p>`}
        <p class="small muted" style="margin:8px 0 0">Google refreshes a published sheet about every 5 minutes. ${d.fetched_at ? "Read " + new Date(d.fetched_at * 1000).toLocaleTimeString() : ""}</p></section></div>`;
      $$("th[data-i]", page).forEach((th) => th.onclick = () => { const i = +th.dataset.i; if (sortCol === i) sortDir = -sortDir; else { sortCol = i; sortDir = -1; } LS.set("pfSort", sortCol); LS.set("pfDir", sortDir); render(); });
    };
    render();
  }
  draw();
  APP.pageTimer = setInterval(() => { if (view === "table") table(false); if (view === "visual") visual(false); }, 120000);
};
/* ---------------------------------------------------------------- journal */
// One note per calendar day, tied to the date the Today page is showing.
// Saves 700 ms after typing stops, on blur, on date change and when leaving the page.
// Until the server confirms a save, the text is also kept in this browser, so a dropped
// connection or a closed tab never loses a note.
function makeJournal(goTo) {
  const ta = $("#jText"); let day = null, saved = "", timer = null, inflight = null;
  const draftKey = (d) => "jdraft." + d;
  const status = (t, cls = "") => { const e = $("#jStat"); if (e) { e.textContent = t; e.className = "jstat " + cls; } };
  const count = () => { const w = ta.value.trim() ? ta.value.trim().split(/\s+/).length : 0; $("#jCount").textContent = w ? w + " word" + (w === 1 ? "" : "s") : ""; };
  const grow = () => { ta.style.height = "auto"; ta.style.height = Math.min(Math.max(ta.scrollHeight + 2, 170), window.innerHeight * 0.6) + "px"; };

  async function save(d, text) {
    if (text === saved && d === day) { status(saved ? "Saved" : ""); return; }
    status("Saving\u2026");
    try {
      const r = await api("/api/journal", { body: { date: d, body: text } });
      LS.set(draftKey(d), null);
      if (d === day) { saved = r.body || ""; status(r.updated_at ? "Saved " + new Date(r.updated_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "", "ok"); }
      list();
    } catch (e) {
      status("Not saved \u2014 kept in this browser, will retry", "bad");
    }
  }
  function flush() {
    clearTimeout(timer); timer = null;
    if (day !== null && ta.isConnected && ta.value !== saved) inflight = save(day, ta.value);
    return inflight;
  }
  async function open(d) {
    if (d === day) return;
    flush();
    day = d; saved = ""; ta.value = ""; ta.disabled = true; status("");
    $("#jDay").textContent = "\u00b7 " + parseDay(d).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
    let r = { body: "" };
    try { r = await api("/api/journal?date=" + d); } catch (e) { status("Couldn't load this day's note", "bad"); }
    if (day !== d) return;                                   // the date moved on while loading
    saved = r.body || "";
    const draft = LS.get(draftKey(d), null);
    ta.value = draft !== null && draft !== saved ? draft : saved;
    ta.disabled = false; count(); grow();
    if (ta.value !== saved) save(d, ta.value);               // an unsaved draft from last time
    else status(r.updated_at ? "Saved " + new Date(r.updated_at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
    list();
  }
  ta.addEventListener("input", () => {
    LS.set(draftKey(day), ta.value); count(); grow();
    status("Editing\u2026"); clearTimeout(timer); timer = setTimeout(() => save(day, ta.value), 700);
  });
  ta.addEventListener("blur", flush);
  ta.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); flush(); } });
  $("#jTime").onclick = () => {
    const stamp = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const pos = ta.selectionStart ?? ta.value.length;
    const before = ta.value.slice(0, pos), lead = before && !before.endsWith("\n") ? "\n" : "";
    const ins = lead + "[" + stamp + "] ";
    ta.setRangeText(ins, pos, ta.selectionEnd ?? pos, "end"); ta.focus(); ta.dispatchEvent(new Event("input"));
  };
  window.addEventListener("beforeunload", () => {
    if (day !== null && ta.isConnected && ta.value !== saved) {
      try { fetch("/api/journal", { method: "POST", keepalive: true, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ date: day, body: ta.value }) }); } catch (e) { /* the draft is in localStorage */ }
    }
  });

  let q = "", lastList = 0;
  async function list() {
    const mine = ++lastList;
    let r;
    try { r = await api("/api/journal/list?q=" + encodeURIComponent(q)); } catch (e) { return; }
    if (mine !== lastList || !$("#jList")) return;
    $("#jN").textContent = q ? "\u00b7 " + r.entries.length + " matching" : "\u00b7 " + r.entries.length;
    const hl = (t) => { const e = esc(t); if (!q) return e; const i = e.toLowerCase().indexOf(esc(q).toLowerCase()); return i < 0 ? e : e.slice(0, i) + "<mark>" + e.slice(i, i + esc(q).length) + "</mark>" + e.slice(i + esc(q).length); };
    $("#jList").innerHTML = r.entries.length ? r.entries.map((x) => `<button class="jentry ${x.day === day ? "cur" : ""}" data-d="${x.day}">
        <span class="jd">${parseDay(x.day).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric", year: "numeric" })}<span class="muted"> \u00b7 ${x.words} word${x.words === 1 ? "" : "s"}</span></span>
        <span class="jp">${hl(x.preview)}</span></button>`).join("")
      : `<p class="muted small">${q ? "No notes mention that." : "No notes yet \u2014 today's will be the first."}</p>`;
    $$("#jList .jentry").forEach((b) => b.onclick = () => goTo(b.dataset.d));
  }
  let qt = null;
  $("#jQ").oninput = (e) => { clearTimeout(qt); qt = setTimeout(() => { q = e.target.value.trim(); list(); }, 250); };
  const past = $("#jPast");
  past.open = LS.get("jPastOpen", false);
  past.addEventListener("toggle", () => LS.set("jPastOpen", past.open));
  return { open, flush };
}

/* ---------------------------------------------------------------- portfolio visual */
// Portfolio widget palette. Colour follows the position (ordered by the day it was opened),
// so a position keeps its colour when its weight changes.
const PF_PALETTE = ["#FF3CAC", "#7B2FF7", "#00E5FF", "#FFB800", "#FF6B35", "#05FFA1", "#F9C80E", "#FF3864", "#2DE2E6", "#A32CC4", "#43BCCD", "#FF9F1C", "#E900FF"];
const pfColor = (i) => PF_PALETTE[i % PF_PALETTE.length];
const pfSigned = (v, d = 2) => v === null || v === undefined || isNaN(v) ? "\u2014" : (v >= 0 ? "+" : "\u2212") + Math.abs(v * 100).toFixed(d) + "%";
const pfNum = (v, d = 2) => v === null || v === undefined || isNaN(v) ? "\u2014" : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
function pfLum(hex) { const h = hex.replace("#", ""); return 0.299 * parseInt(h.substr(0, 2), 16) + 0.587 * parseInt(h.substr(2, 2), 16) + 0.114 * parseInt(h.substr(4, 2), 16); }
function pfPolar(cx, cy, r, deg) { const a = deg * Math.PI / 180; return [cx + r * Math.sin(a), cy - r * Math.cos(a)]; }
function pfArc(cx, cy, ro, ri, a0, a1) {
  if (a1 - a0 >= 359.99) a1 = a0 + 359.99;                  // a single position is a full ring
  const L = a1 - a0 > 180 ? 1 : 0;
  const [x0, y0] = pfPolar(cx, cy, ro, a0), [x1, y1] = pfPolar(cx, cy, ro, a1);
  const [x2, y2] = pfPolar(cx, cy, ri, a1), [x3, y3] = pfPolar(cx, cy, ri, a0);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${ro} ${ro} 0 ${L} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}L${x2.toFixed(2)} ${y2.toFixed(2)}A${ri} ${ri} 0 ${L} 0 ${x3.toFixed(2)} ${y3.toFixed(2)}Z`;
}

function renderPortfolioVisual(page, d) {
  const s = APP.settings; const m = d.model || {};
  const title = s.portfolio_title || "Stock Portfolio";
  const today = new Date().toLocaleDateString([], { year: "numeric", month: "long", day: "numeric" });
  const banners = (d.error ? `<div class="banner ${m.ok && m.open.length ? "warn" : "err"}">${esc(d.error)}${d.stale ? " \u2014 showing the last good copy." : ""}</div>` : "")
    + (m.ok === false && m.missing ? `<div class="banner err">The sheet has no ${m.missing.map(esc).join(" / ")} column. Its columns are: ${(m.columns || []).map(esc).join(", ") || "none"}.</div>` : "")
    + (m.skipped && m.skipped.length ? `<div class="banner warn">Left out: ${m.skipped.map(esc).join("; ")}.</div>` : "");
  if (!m.ok) { page.innerHTML = `<div class="pad"><div class="pf"><div class="pf-title">${esc(title)}</div><div class="pf-sub">${today}</div>${banners}</div></div>`; return; }
  const opens = m.open, sum = m.summary;
  const img = s.portfolio_image || "/portfolio-image";
  // donut
  const CX = 180, CY = 180, RO = 170, RI = 96, IR = 90;
  const bySize = opens.slice().sort((a, b) => b.mv - a.mv);
  let cum = 0, slices = "", labels = "";
  bySize.forEach((p) => {
    const a0 = cum * 360; cum += p.share; const a1 = cum * 360;
    const c = pfColor(p.color);
    slices += `<path class="pf-slice" data-t="${esc(p.ticker)}" d="${pfArc(CX, CY, RO, RI, a0, a1)}" fill="${c}"/>`;
    if (p.share >= 0.06) {
      const [x, y] = pfPolar(CX, CY, (RO + RI) / 2, (a0 + a1) / 2);
      labels += `<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="middle" dominant-baseline="central" class="pf-lab" fill="${pfLum(c) < 150 ? "#fff" : "#1a2332"}">${esc(p.ticker)}</text>`;
    }
  });
  const donut = `<svg viewBox="0 0 360 360" class="pf-donut" role="img" aria-label="Portfolio weights">
    <defs><clipPath id="pfClip"><circle cx="${CX}" cy="${CY}" r="${IR}"/></clipPath></defs>
    ${opens.length ? slices : `<circle cx="${CX}" cy="${CY}" r="${(RO + RI) / 2}" fill="none" stroke="var(--grid)" stroke-width="${RO - RI}"/>`}
    <circle cx="${CX}" cy="${CY}" r="${IR + 3}" class="pf-ring"/>
    <image id="pfImg" href="${esc(img)}" x="${CX - IR}" y="${CY - IR}" width="${IR * 2}" height="${IR * 2}" clip-path="url(#pfClip)" preserveAspectRatio="xMidYMid slice"/>
    ${labels}</svg>`;
  const rows = opens.map((p) => `<tr class="pf-row" data-t="${esc(p.ticker)}">
      <td><span class="pf-tk"><span class="pf-sw" style="background:${pfColor(p.color)}"></span>${esc(p.ticker)}</span></td>
      <td>${esc(p.date || "\u2014")}</td><td class="n">${pfNum(p.entry)}</td><td class="n">${pfNum(p.mark)}</td>
      <td class="n">${p.weight === null ? "\u2014" : (p.weight * 100).toFixed(1) + "%"}</td>
      <td class="n pf-b ${pnlCls(p.pct)}">${pfSigned(p.pct)}</td></tr>`).join("");
  const hist = m.closed.map((c) => `<tr><td class="pf-b">${esc(c.ticker)}</td><td>${esc(c.date || "\u2014")}</td><td>${esc(c.close_date || "\u2014")}</td>
      <td class="n pf-b ${pnlCls(c.pct)}">${pfSigned(c.pct)}</td><td class="pf-notes">${esc(c.notes || "")}</td></tr>`).join("");
  const sep = `<span class="pf-sep">|</span>`;
  page.innerHTML = `<div class="pad"><div class="pf">
    <div class="pf-title">${esc(title)}</div><div class="pf-sub">${today}</div>${banners}
    <div class="pf-body"><div class="pf-chart">${donut}</div>
      <div class="pf-tablewrap"><table class="pf-table"><thead><tr><th>Ticker</th><th>Date</th><th class="n">Entry</th><th class="n">Mark</th><th class="n">Weight</th><th class="n">Unreal</th></tr></thead>
      <tbody>${rows || `<tr><td colspan="6" class="muted">No open positions \u2014 all cash.</td></tr>`}</tbody></table>
      <div id="pfDetail"></div></div></div>
    <div class="pf-foot">Total Positions: ${sum.positions} ${sep} Deployed: ${sum.deployed === null ? "\u2014" : (sum.deployed * 100).toFixed(1) + "%"} ${sep}
      Cash: ${sum.cash === null ? "\u2014" : (sum.cash * 100).toFixed(1) + "%"} ${sep} Weighted Avg Unrealized: <span class="${pnlCls(sum.unrealized)}">${pfSigned(sum.unrealized)}</span>
      ${sep} Weighted Avg Realized: <span class="${pnlCls(sum.realized)}">${pfSigned(sum.realized)}</span></div>
    <div class="pf-h2">Warnings on your holdings</div><div id="pfWarn">${warningsList(APP.warnings)}</div>
    ${m.closed.length ? `<div class="pf-h2">Trade History (Closed)</div><div class="pf-tablewrap"><table class="pf-table"><thead><tr><th>Ticker</th><th>Opened</th><th>Closed</th><th class="n">Result</th><th>Notes</th></tr></thead><tbody>${hist}</tbody></table></div>` : ""}
    <p class="small muted" style="margin-top:14px">Weights are against $${Number(m.capital).toLocaleString()} starting capital (change it in Settings). The ring splits the money that is deployed. ${d.fetched_at ? "Sheet read " + new Date(d.fetched_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) + "." : ""}</p>
  </div></div>`;
  refreshWarnings().then(() => { if ($("#pfWarn")) $("#pfWarn").innerHTML = warningsList(APP.warnings, { details: true }); });
  const im = $("#pfImg");
  im.addEventListener("error", () => { if (s.logo && im.getAttribute("href") !== s.logo) im.setAttribute("href", s.logo); else im.remove(); });
  let active = null;
  const select = (t) => {
    active = active === t ? null : t;
    $$(".pf-slice", page).forEach((el) => el.classList.toggle("dim", !!active && el.dataset.t !== active));
    $$(".pf-row", page).forEach((el) => el.classList.toggle("act", el.dataset.t === active));
    const p = opens.find((x) => x.ticker === active); const box = $("#pfDetail");
    if (!p) { box.innerHTML = ""; return; }
    const kv = (k, v, cls = "") => `<div><span>${k}</span><b class="${cls}">${v}</b></div>`;
    box.innerHTML = `<div class="pf-card"><div class="pf-cardh"><span class="pf-sw" style="background:${pfColor(p.color)}"></span>${esc(p.ticker)}<span class="spacer"></span><button class="sm ghost" id="pfClose" aria-label="Close">\u2715</button></div>
      <div class="pf-kv">${kv("Opened", esc(p.date || "\u2014"))}${kv("Shares", p.amt === null ? "\u2014" : p.amt.toLocaleString())}${kv("Entry", pfNum(p.entry))}${kv("Mark", pfNum(p.mark))}
      ${kv("Market value", fmtMoney(p.mv, false))}${kv("Weight", (p.weight * 100).toFixed(1) + "% of capital \u00b7 " + (p.share * 100).toFixed(1) + "% of deployed")}
      ${kv("Unrealized", fmtMoney(p.usd) + " (" + pfSigned(p.pct) + ")", pnlCls(p.pct))}</div>
      ${p.notes ? `<div class="pf-notes" style="margin-top:8px">${esc(p.notes)}</div>` : ""}
      <div style="margin-top:10px"><button class="sm" id="pfName">Thesis, sell rules &amp; checks</button></div></div>`;
    $("#pfClose").onclick = () => select(p.ticker);
    $("#pfName").onclick = () => openName(p.ticker);
  };
  $$(".pf-slice, .pf-row", page).forEach((el) => el.addEventListener("click", () => select(el.dataset.t)));
  const tip = $("#tip");
  $$(".pf-slice", page).forEach((el) => {
    el.addEventListener("mousemove", (e) => {
      const p = opens.find((x) => x.ticker === el.dataset.t);
      tip.style.display = "block"; tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY - 10) + "px";
      tip.innerHTML = `<b>${esc(p.ticker)}</b><br>${(p.weight * 100).toFixed(1)}% of capital<br>Unreal <span class="${pnlCls(p.pct)}">${pfSigned(p.pct)}</span>`;
    });
    el.addEventListener("mouseleave", () => { tip.style.display = "none"; });
  });
}

function pnum(s) {
  let t = String(s || "").trim(); if (!t) return null;
  const neg = /^\(.*\)$/.test(t) || /^-|^−/.test(t) || /^\$?-/.test(t.replace(/^\(/, ""));
  const pct = /%\)?$/.test(t);
  t = t.replace(/[^\d.]/g, ""); if (!t || t === ".") return null;
  let v = parseFloat(t); if (isNaN(v)) return null; if (neg) v = -v; return pct ? v / 100 : v;
}

/* ================================================================ Interactive P&L */
PAGES.pnl = function (page) {
  let data = null;
  let account = LS.get("pnlAccount", "");
  let scope = LS.get("pnlScope", "month");
  let month = LS.get("pnlMonth", null);        // "YYYY-MM"
  let selDay = null;

  setTop("Interactive P&L", "", `<select id="pAcct" class="sm" title="Account"></select>
    <div class="seg" id="pScope"><button data-s="month">Month</button><button data-s="ytd">YTD</button><button data-s="all">All</button></div>
    <button class="sm primary" id="pUp">Upload CSV</button><input type="file" id="pFile" accept=".csv,.txt,text/csv" multiple hidden>`);
  page.innerHTML = `<div class="pad" id="pRoot"></div>`;
  const root = $("#pRoot");
  $("#pUp").onclick = () => $("#pFile").click();
  $("#pFile").onchange = (e) => uploadFiles(Array.from(e.target.files)); 
  $$("#pScope button").forEach((b) => b.onclick = () => { scope = b.dataset.s; LS.set("pnlScope", scope); draw(); });
  $("#pAcct").onchange = (e) => { account = e.target.value; LS.set("pnlAccount", account); load(); };
  // drop anywhere on the page
  const view = $("#view");
  view.ondragover = (e) => { if (APP.route === "#/pnl") { e.preventDefault(); const dz = $("#drop"); if (dz) dz.classList.add("over"); } };
  view.ondragleave = () => { const dz = $("#drop"); if (dz) dz.classList.remove("over"); };
  view.ondrop = (e) => { if (APP.route !== "#/pnl") return; e.preventDefault(); uploadFiles(Array.from(e.dataTransfer.files)); };

  async function load() {
    try { data = await api("/api/pnl?account=" + encodeURIComponent(account)); }
    catch (e) { root.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    if (account && !data.accounts.includes(account)) { account = ""; LS.set("pnlAccount", ""); return load(); }
    $("#pAcct").innerHTML = `<option value="">All accounts</option>` + data.accounts.map((a) => `<option ${a === account ? "selected" : ""}>${esc(a)}</option>`).join("");
    $("#pAcct").style.display = data.accounts.length > 1 ? "" : "none";
    if (!month && data.last_fill) month = data.last_fill.slice(0, 7);
    if (!month) month = isoDay(new Date()).slice(0, 7);
    draw();
  }

  function inScope(d) {
    if (scope === "all") return true;
    if (scope === "ytd") return d.slice(0, 4) === month.slice(0, 4);
    return d.slice(0, 7) === month;
  }

  function computeStats(trades, days) {
    const wins = trades.filter((t) => t.net > 0).map((t) => t.net), losses = trades.filter((t) => t.net < 0).map((t) => t.net);
    const gw = wins.reduce((a, b) => a + b, 0), gl = -losses.reduce((a, b) => a + b, 0);
    const traded = days.filter((d) => d.trades);
    return { net: trades.reduce((a, t) => a + t.net, 0), fees: trades.reduce((a, t) => a + t.fees, 0), n: trades.length,
      win: wins.length + losses.length ? wins.length / (wins.length + losses.length) : null,
      pf: gl ? gw / gl : (gw ? Infinity : null), avgW: wins.length ? gw / wins.length : null, avgL: losses.length ? -gl / losses.length : null,
      green: traded.filter((d) => d.net > 0).length, red: traded.filter((d) => d.net < 0).length,
      best: traded.length ? traded.reduce((a, b) => b.net > a.net ? b : a) : null, worst: traded.length ? traded.reduce((a, b) => b.net < a.net ? b : a) : null };
  }

  function draw() {
    $$("#pScope button").forEach((b) => b.classList.toggle("on", b.dataset.s === scope));
    if (!data) return;
    if (!data.fills) {
      root.innerHTML = `<div class="drop" id="drop" style="padding:48px 20px;margin-top:8vh">
        <h2 style="margin-bottom:6px">Drop an account statement here</h2>
        <p>CSV exports from thinkorswim, Schwab, Fidelity, Robinhood, Webull, Interactive Brokers, tastytrade or Tradovate (Orders, Fills or Performance) are read automatically.
        Anything else: you match its columns once.</p>
        <div class="row" style="justify-content:center"><input type="text" id="pAcctLbl" placeholder="Account name (optional)" style="max-width:240px">
        <button class="primary" onclick="document.getElementById('pFile').click()">Choose file…</button></div>
        <p class="small muted">Uploads are saved in the dashboard, so they are still here after a restart. Re-uploading an overlapping statement never double-counts a trade.</p></div><div id="mapForm"></div>`;
      return;
    }
    const days = data.days.filter((d) => inScope(d.date));
    const trades = data.closed.filter((t) => inScope(t.date));
    const st = computeStats(trades, days);
    const scopeLbl = scope === "month" ? parseDay(month + "-01").toLocaleDateString([], { month: "long", year: "numeric" }) : scope === "ytd" ? month.slice(0, 4) + " to date" : "all uploaded history";
    $("#subtitle").textContent = scopeLbl + (account ? " · " + account : "");
    const kpi = (l, v, s, cls = "") => `<div class="kpi"><div class="l">${l}</div><div class="v ${cls}">${v}</div><div class="s">${s || "&nbsp;"}</div></div>`;
    root.innerHTML = `<div id="upMsg"></div><div id="mapForm"></div>
      <div class="kpis">
        ${kpi("Net P&L", fmtMoney(st.net), `${st.n} closed trade${st.n === 1 ? "" : "s"} · fees ${fmtMoney(st.fees, false)}`, pnlCls(st.net))}
        ${kpi("Win rate", st.win === null ? "—" : fmtPct(st.win, 0), `${trades.filter((t) => t.net > 0).length} won · ${trades.filter((t) => t.net < 0).length} lost`)}
        ${kpi("Profit factor", st.pf === null ? "—" : st.pf === Infinity ? "∞" : st.pf.toFixed(2), "gross won ÷ gross lost")}
        ${kpi("Avg win / loss", `<span class="gain">${st.avgW === null ? "—" : fmtMoney(st.avgW, false, 0)}</span> <span class="muted" style="font-weight:400">/</span> <span class="loss">${st.avgL === null ? "—" : fmtMoney(st.avgL, true, 0)}</span>`, st.avgW && st.avgL ? "ratio " + (st.avgW / -st.avgL).toFixed(2) : "")}
        ${kpi("Green / red days", `<span class="gain">${st.green}</span> <span class="muted" style="font-weight:400">/</span> <span class="loss">${st.red}</span>`, st.best ? `best ${fmtMoney(st.best.net, true, 0)} · worst ${fmtMoney(st.worst.net, true, 0)}` : "")}
      </div>
      <div class="pnlgrid">
        <div>
          <section class="panel"><h2><button class="sm ghost" id="mPrev" aria-label="Previous month">◀</button>
            <span id="mLbl" style="color:var(--ink-1);text-transform:none;letter-spacing:0;font-size:15px;min-width:130px;text-align:center"></span>
            <button class="sm ghost" id="mNext" aria-label="Next month">▶</button><span class="spacer"></span><span id="mTot" class="n" style="text-transform:none;letter-spacing:0;font-size:14px"></span></h2>
            <div class="cal" id="calGrid"></div>
            <p class="small muted" style="margin:8px 0 0">Realised P&amp;L by the day each position was closed (FIFO, after fees). Click a day to see its trades. <span style="color:var(--warning);font-weight:700">!</span> marks a day with a close whose opening trade is not in the upload.</p>
          </section>
          <section class="panel"><h2>Cumulative P&amp;L <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· ${esc(scopeLbl)}</span></h2><svg id="eq"></svg></section>
        </div>
        <div>
          <section class="panel" id="dayPanel"></section>
          <section class="panel" id="openPanel"></section>
          <section class="panel" id="impPanel"></section>
        </div>
      </div>`;
    $("#mPrev").onclick = () => { month = shiftMonth(month, -1); LS.set("pnlMonth", month); draw(); };
    $("#mNext").onclick = () => { month = shiftMonth(month, 1); LS.set("pnlMonth", month); draw(); };
    drawCal(); drawEq(days); drawDay(); drawOpen(); drawImports();
  }

  function shiftMonth(m, n) { const d = parseDay(m + "-01"); d.setMonth(d.getMonth() + n); return isoDay(d).slice(0, 7); }

  function drawCal() {
    const first = parseDay(month + "-01");
    $("#mLbl").textContent = first.toLocaleDateString([], { month: "long", year: "numeric" });
    const byDay = {}; data.days.forEach((d) => byDay[d.date] = d);
    const monthDays = data.days.filter((d) => d.date.slice(0, 7) === month);
    const tot = monthDays.reduce((a, d) => a + d.net, 0);
    $("#mTot").innerHTML = monthDays.length ? `month <b class="${pnlCls(tot)}">${fmtMoney(tot)}</b>` : "";
    const maxAbs = Math.max(1, ...monthDays.map((d) => Math.abs(d.net)));
    const start = new Date(first); start.setDate(1 - first.getDay());
    const todayS = isoDay(new Date());
    let h = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d) => `<div class="h">${d}</div>`).join("") + `<div class="h wkh">Week</div>`;
    const cur = new Date(start);
    for (let w = 0; w < 6; w++) {
      if (w > 3 && cur.getMonth() !== first.getMonth()) break;
      let wk = 0, wkN = 0;
      for (let i = 0; i < 7; i++) {
        const ds = isoDay(cur); const d = byDay[ds]; const inM = cur.getMonth() === first.getMonth();
        const wkend = i === 0 || i === 6;
        let style = "";
        if (d && d.trades && Math.abs(d.net) >= 0.005) {
          const a = Math.round(10 + 40 * Math.min(1, Math.abs(d.net) / maxAbs));
          style = `background:color-mix(in srgb, var(${d.net > 0 ? "--gain" : "--loss"}) ${a}%, var(--surface-1));border-color:color-mix(in srgb, var(${d.net > 0 ? "--gain" : "--loss"}) 45%, transparent)`;
        }
        if (d && inM) { wk += d.net; wkN += d.trades; }
        h += `<div class="day ${inM ? "" : "out"} ${wkend ? "wkend" : ""} ${d && d.trades + d.unmatched ? "has" : ""} ${ds === todayS ? "today" : ""} ${ds === selDay ? "sel" : ""}" data-d="${ds}" style="${style}"
          ${d ? `title="${esc(ds)}: ${d.trades} closed, net ${fmtMoney(d.net)}${d.unmatched ? `, ${d.unmatched} close(s) without an opening trade` : ""}"` : ""}>
          <span class="dn">${cur.getDate()}</span>${d && d.unmatched ? '<span class="um">!</span>' : ""}
          ${d && d.trades ? `<span class="pv ${pnlCls(d.net)}" title="${fmtMoney(d.net)}">${cellMoney(d.net)}</span><span class="tc">${d.trades} trade${d.trades === 1 ? "" : "s"}</span>` : ""}</div>`;
        cur.setDate(cur.getDate() + 1);
      }
      h += `<div class="wk"><span class="l">Week</span><span class="v ${wkN ? pnlCls(wk) : "muted"}">${wkN ? fmtMoney(wk, true, 0) : "—"}</span></div>`;
    }
    $("#calGrid").innerHTML = h;
    $$("#calGrid .day.has").forEach((el) => el.onclick = () => { selDay = el.dataset.d; LS.set("pnlSel", selDay); $$("#calGrid .day").forEach((x) => x.classList.toggle("sel", x.dataset.d === selDay)); drawDay(); });
  }

  function drawDay() {
    const p = $("#dayPanel");
    if (!selDay) {
      const last = data.days.filter((d) => d.date.slice(0, 7) === month && d.trades).slice(-1)[0];
      selDay = last ? last.date : null;
      if (selDay) $$("#calGrid .day").forEach((x) => x.classList.toggle("sel", x.dataset.d === selDay));
    }
    if (!selDay) { p.innerHTML = `<h2>Day detail</h2><p class="muted">No closed trades in this month. Pick another month, or upload a statement that covers it.</p>`; return; }
    const trades = data.closed.filter((t) => t.date === selDay);
    const um = data.unmatched.filter((u) => u.date === selDay);
    const net = trades.reduce((a, t) => a + t.net, 0), fees = trades.reduce((a, t) => a + t.fees, 0);
    const groups = {};
    trades.forEach((t) => (groups[t.underlying] = groups[t.underlying] || []).push(t));
    const kindTag = (k) => ({ expire: "expired", expired_inferred: "expired worthless (inferred)", assign: "assigned", exercise: "exercised" }[k]);
    let h = `<h2>${parseDay(selDay).toLocaleDateString([], { weekday: "long", month: "short", day: "numeric", year: "numeric" })}
      <span class="spacer"></span><span class="${pnlCls(net)}" style="font-size:16px;text-transform:none;letter-spacing:0">${fmtMoney(net)}</span></h2>
      <p class="small muted" style="margin:-4px 0 6px">${trades.length} closed · fees ${fmtMoney(fees, false)}</p>`;
    for (const [u, ts] of Object.entries(groups).sort((a, b) => Math.abs(b[1].reduce((x, t) => x + t.net, 0)) - Math.abs(a[1].reduce((x, t) => x + t.net, 0)))) {
      const gnet = ts.reduce((a, t) => a + t.net, 0);
      h += `<div class="sess" style="padding-left:2px"><span class="tick" style="color:var(--ink-1)">${esc(u)}</span><span class="spacer"></span><span class="${pnlCls(gnet)} n" style="text-transform:none">${fmtMoney(gnet)}</span></div>`;
      for (const t of ts) {
        const label = t.asset === "option" ? t.key.replace(/^\S+\s/, "") : t.asset === "future" ? t.key : "shares";
        const unit = t.asset === "stock" ? "sh" : t.asset === "future" ? "ct" : "ct";
        const held = holdTime(t.open_ts, t.ts);
        h += `<div class="trade"><div class="r1"><span>${t.dir === "short" ? '<span class="chip">short</span> ' : ""}${esc(label)}${kindTag(t.kind) ? ` <span class="chip">${kindTag(t.kind)}</span>` : ""}</span>
          <span class="pv ${pnlCls(t.net)}">${fmtMoney(t.net)}</span></div>
          <div class="r2">${fmtQty(t.qty)} ${unit} · ${t.dir === "short" ? "sold" : "bought"} ${fmtPx(t.open_price)} → ${t.dir === "short" ? "bought back" : "sold"} ${fmtPx(t.close_price)} · held ${held}${t.fees ? " · fees " + fmtMoney(t.fees, false) : ""}${t.ts.slice(11, 16) !== "00:00" ? " · closed " + fmtClock(t.ts) : ""}</div></div>`;
      }
    }
    if (um.length) {
      h += `<div class="banner warn" style="margin-top:10px;display:block"><b>${um.length} close${um.length === 1 ? "" : "s"} not counted</b> — the opening trade is older than this upload, so the cost is unknown:<br>` +
        um.map((u) => `${esc(u.desc)} · ${u.side} ${fmtQty(u.qty)} @ ${fmtPx(u.price)}`).join("<br>") + `<br><span class="small">Upload an older statement to include ${um.length === 1 ? "it" : "them"}.</span></div>`;
    }
    p.innerHTML = h;
  }

  function drawOpen() {
    const p = $("#openPanel"); const o = data.open;
    if (!o.length) { p.innerHTML = `<h2>Still open</h2><p class="muted small">Every position in the upload is closed.</p>`; return; }
    p.innerHTML = `<h2>Still open <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· from the upload, not live</span></h2>
      <table><thead><tr><th>Position</th><th class="n">Qty</th><th class="n">Avg</th><th>Since</th></tr></thead><tbody>${
      o.map((x) => `<tr><td>${esc(x.key)}${data.accounts.length > 1 && !account ? ` <span class="muted small">${esc(x.account)}</span>` : ""}</td><td class="n">${fmtQty(x.qty)}</td><td class="n">${fmtPx(x.avg_price)}</td><td class="small">${esc(x.since.slice(0, 10))}</td></tr>`).join("")}</tbody></table>`;
  }

  function drawImports() {
    const p = $("#impPanel");
    p.innerHTML = `<h2>Uploads<span class="spacer"></span><input type="text" id="pAcctLbl" placeholder="Account name for next upload" style="text-transform:none;letter-spacing:0;font-weight:400;font-size:12.5px;padding:3px 8px;width:210px"></h2>
      <div class="drop" id="drop" style="padding:10px;margin-bottom:10px">Drop CSV files here or <a href="#" onclick="document.getElementById('pFile').click();return false">choose files</a></div>
      <table><tbody>${data.imports.map((i) => `<tr><td><b>${esc(i.filename)}</b><br><span class="small muted">${esc(BROKER_LABEL[i.broker] || i.broker)} · ${esc(i.account)} · ${new Date(i.uploaded_at * 1000).toLocaleDateString()}</span>
        ${i.notes ? `<br><span class="small" style="color:var(--warning)">${esc(i.notes)}</span>` : ""}</td>
        <td class="n small">${i.fills_kept} fills${i.fills_found > i.fills_new ? `<br><span class="muted">${i.fills_found - i.fills_new} already had</span>` : ""}</td>
        <td class="n"><button class="sm ghost danger" data-del="${i.id}" title="Remove this upload and its trades">Remove</button></td></tr>`).join("")}</tbody></table>
      <p class="small muted" style="margin:8px 0 0">P&amp;L uses FIFO lots per account. Brokers using average cost or specific lots can show different per-trade numbers; a full round trip totals the same.</p>`;
    $$("[data-del]", p).forEach((b) => b.onclick = async () => {
      if (!confirmInline(`[data-del="${b.dataset.del}"]`, "Really remove?")) return;
      await api(`/api/pnl/import/${b.dataset.del}/delete`, { body: {} }); toast("Upload removed"); selDay = null; load();
    });
  }

  function drawEq(days) {
    const svg = $("#eq"); const W = svg.clientWidth || 600, H = 220, padL = 64, padR = 14, padT = 12, padB = 26;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const pts = []; let c = 0;
    days.filter((d) => d.trades).forEach((d) => { c += d.net; pts.push({ date: d.date, net: d.net, cum: c }); });
    if (!pts.length) { svg.innerHTML = `<text x="${W / 2}" y="${H / 2}" text-anchor="middle" fill="var(--ink-3)" font-size="13">No closed trades in this range</text>`; return; }
    const ys = pts.map((p) => p.cum).concat([0]);
    let lo = Math.min(...ys), hi = Math.max(...ys); if (hi === lo) { hi += 1; lo -= 1; }
    const span = hi - lo; lo -= span * 0.08; hi += span * 0.08;
    const x = (i) => padL + (pts.length === 1 ? (W - padL - padR) / 2 : i * (W - padL - padR) / (pts.length - 1));
    const y = (v) => padT + (hi - v) * (H - padT - padB) / (hi - lo);
    const ticks = niceTicks(lo, hi, 4);
    let g = ticks.map((t) => `<line x1="${padL}" x2="${W - padR}" y1="${y(t)}" y2="${y(t)}" stroke="var(--grid)" stroke-width="1"/>
      <text x="${padL - 8}" y="${y(t) + 4}" text-anchor="end" font-size="11" fill="var(--ink-3)">${fmtMoney(t, false, 0)}</text>`).join("");
    g += `<line x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}" stroke="var(--axis)" stroke-width="1.2"/>`;
    const labIdx = [0, Math.floor((pts.length - 1) / 2), pts.length - 1].filter((v, i, a) => a.indexOf(v) === i);
    g += labIdx.map((i) => `<text x="${x(i)}" y="${H - 6}" text-anchor="${i === 0 && pts.length > 1 ? "start" : i === pts.length - 1 && pts.length > 1 ? "end" : "middle"}" font-size="11" fill="var(--ink-3)">${parseDay(pts[i].date).toLocaleDateString([], { month: "short", day: "numeric" })}</text>`).join("");
    const path = pts.map((p, i) => (i ? "L" : "M") + x(i).toFixed(1) + "," + y(p.cum).toFixed(1)).join("");
    const last = pts[pts.length - 1];
    g += `<path d="${path}" fill="none" stroke="var(--s1)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`;
    g += pts.length <= 60 ? pts.map((p, i) => `<circle cx="${x(i)}" cy="${y(p.cum)}" r="${pts.length > 25 ? 2 : 3}" fill="var(--s1)"/>`).join("") : "";
    g += `<line id="eqX" y1="${padT}" y2="${H - padB}" stroke="var(--ink-3)" stroke-dasharray="3 3" visibility="hidden"/><circle id="eqDot" r="5" fill="var(--s1)" stroke="var(--surface-1)" stroke-width="2" visibility="hidden"/>`;
    g += `<rect x="${padL}" y="0" width="${W - padL - padR}" height="${H}" fill="transparent" id="eqHit"/>`;
    svg.innerHTML = g;
    const tip = $("#tip");
    $("#eqHit").onmousemove = (e) => {
      const r = svg.getBoundingClientRect(); const mx = (e.clientX - r.left) * W / r.width;
      let i = Math.round((mx - padL) / ((W - padL - padR) / Math.max(1, pts.length - 1))); i = Math.max(0, Math.min(pts.length - 1, i));
      const p = pts[i];
      $("#eqX").setAttribute("x1", x(i)); $("#eqX").setAttribute("x2", x(i)); $("#eqX").setAttribute("visibility", "visible");
      $("#eqDot").setAttribute("cx", x(i)); $("#eqDot").setAttribute("cy", y(p.cum)); $("#eqDot").setAttribute("visibility", "visible");
      tip.style.display = "block"; tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY - 10) + "px";
      tip.innerHTML = `<b>${parseDay(p.date).toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })}</b><br>Day <span class="${pnlCls(p.net)}">${fmtMoney(p.net)}</span><br>Total <b class="${pnlCls(p.cum)}">${fmtMoney(p.cum)}</b>`;
    };
    $("#eqHit").onmouseleave = () => { tip.style.display = "none"; $("#eqX").setAttribute("visibility", "hidden"); $("#eqDot").setAttribute("visibility", "hidden"); };
    $("#eqHit").onclick = (e) => {
      const r = svg.getBoundingClientRect(); const mx = (e.clientX - r.left) * W / r.width;
      let i = Math.round((mx - padL) / ((W - padL - padR) / Math.max(1, pts.length - 1))); i = Math.max(0, Math.min(pts.length - 1, i));
      selDay = pts[i].date; month = selDay.slice(0, 7); LS.set("pnlMonth", month); draw();
    };
  }

  async function uploadFiles(files, mapping) {
    if (!files.length) return;
    const lblEl = $("#pAcctLbl"); const acct = lblEl ? lblEl.value.trim() : "";
    const msgs = [];
    for (const f of files) {
      const text = await f.text();
      const r = await api("/api/pnl/upload", { body: { filename: f.name, text, account: acct, mapping }, allowError: true });
      if (r.__status === 200) {
        msgs.push(`<b>${esc(f.name)}</b>: read as ${esc(r.broker)}, ${r.fills} fills (${r.new} new${r.duplicates ? `, ${r.duplicates} already uploaded` : ""}), ${esc(r.range[0])} → ${esc(r.range[1])}.` + (r.notes && r.notes.length ? `<br><span class="small">${r.notes.map(esc).join("<br>")}</span>` : ""));
        month = r.range[1].slice(0, 7); LS.set("pnlMonth", month); selDay = null;
      } else if (r.columns && r.columns.length) {
        showMapping(f, r); return;
      } else {
        msgs.push(`<b>${esc(f.name)}</b>: ${esc(r.error || "could not be read")}`);
      }
    }
    await load();
    const m = $("#upMsg"); if (m) m.innerHTML = `<div class="banner info" style="display:block">${msgs.join("<br>")}</div>`;
    $("#pFile").value = "";
  }

  function showMapping(file, r) {
    const box = $("#mapForm"); if (!box) return;
    const fields = [["date", "Date / time", true], ["symbol", "Symbol", true], ["side", "Buy / sell", false], ["qty", "Quantity", true], ["price", "Price", true], ["fees", "Fees", false], ["mult", "Multiplier", false]];
    box.innerHTML = `<section class="panel"><h2>Match the columns of ${esc(file.name)}</h2><p class="small">${esc(r.error)}</p>
      ${fields.map(([k, l, req]) => `<div class="row"><label>${l}${req ? " *" : ""}</label><select data-k="${k}"><option value="">— none —</option>${r.columns.map((c) => `<option ${r.guess && r.guess[k] === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>`).join("")}
      <p class="small muted">If there is no buy/sell column, negative quantities are read as sells. Options need an OCC-style symbol (e.g. SPY260927C00570000).</p>
      <div class="row"><button class="primary" id="mapGo">Read the file</button><button id="mapCancel">Cancel</button></div></section>`;
    $("#mapCancel").onclick = () => box.innerHTML = "";
    $("#mapGo").onclick = () => {
      const m = {}; $$("select[data-k]", box).forEach((s) => { if (s.value) m[s.dataset.k] = s.value; });
      if (!m.date || !m.symbol || !m.qty || !m.price) { toast("Date, symbol, quantity and price are needed"); return; }
      box.innerHTML = ""; uploadFiles([file], m);
    };
    box.scrollIntoView({ behavior: "smooth" });
  }

  selDay = LS.get("pnlSel", null);
  load();
};
const BROKER_LABEL = { tos: "thinkorswim", schwab: "Schwab", fidelity: "Fidelity", robinhood: "Robinhood", webull: "Webull", ibkr: "Interactive Brokers", ibkr_flex: "Interactive Brokers", tastytrade: "tastytrade", tradovate: "Tradovate", generic: "CSV" };
function cellMoney(v) {
  // calendar cells are narrow: exact value is in the tooltip and the day panel
  const a = Math.abs(v); const sg = v < 0 ? "\u2212" : "+";
  const narrow = window.matchMedia("(max-width: 820px)").matches;
  if (a >= 1000) return (narrow ? sg : sg + "$") + (a / 1000).toFixed(a >= 9950 || narrow ? 0 : 1) + "k";
  if (a >= 10) return sg + "$" + Math.round(a);
  return sg + "$" + a.toFixed(2);
}
function fmtQty(q) { return Math.abs(q) % 1 ? Math.abs(q).toFixed(3).replace(/0+$/, "") : Math.abs(q).toLocaleString(); }
function fmtPx(p) { return p === null || p === undefined ? "—" : "$" + (Math.abs(p) >= 100 ? p.toFixed(2) : p < 1 ? p.toFixed(3).replace(/0$/, "") : p.toFixed(2)); }
function fmtClock(ts) { const d = new Date(ts.replace(" ", "T")); return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); }
function holdTime(a, b) {
  const ms = new Date(b) - new Date(a); if (!(ms >= 0)) return "—";
  const sameClock = a.slice(11) === "00:00:00" || b.slice(11) === "00:00:00";
  const days = Math.round(ms / 864e5);
  if (sameClock) return days <= 0 ? "same day" : days + " day" + (days === 1 ? "" : "s");
  const m = Math.round(ms / 6e4);
  if (m < 60) return m + " min"; if (m < 60 * 24) return (m / 60).toFixed(1).replace(/\.0$/, "") + " h";
  return days + " day" + (days === 1 ? "" : "s");
}
function niceTicks(lo, hi, n) {
  const span = hi - lo; const step0 = span / n; const mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) || 10 * mag;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
  return out;
}

/* ================================================================ Settings */
PAGES.settings = function (page) {
  const s = () => APP.settings;
  setTop("Settings", "Saved automatically — kept in the dashboard, so they survive restarts.", `<span class="saved" id="savedMark">✓ Saved</span>`);
  page.innerHTML = `<div class="pad">
    <section class="panel"><h2>System health <span class="spacer"></span><button class="sm ghost" id="hRefresh" style="text-transform:none;letter-spacing:0">↻</button></h2><div id="healthBox"><p class="muted small">Checking…</p></div></section>
    <section class="panel"><h2>Appearance</h2>
      <p class="small muted" style="margin-top:-4px">One click changes the dashboard, every desk and the share cards. Paper is the original look.</p>
      <div class="th-grid" id="thGrid"></div>
      <div class="row" style="margin-top:12px"><label>Mode</label><div class="seg" id="theme"><button data-v="system">Match Windows</button><button data-v="light">Light</button><button data-v="dark">Dark</button></div>
        <span class="small muted" id="thModeNote"></span></div>
      <div class="row" id="thMotionRow" hidden><label>Motion</label><label style="min-width:0"><input type="checkbox" id="thMotion"> Slow neon glow and a moving grid</label>
        <span class="small muted">Turns itself off if Windows asks for reduced motion.</span></div>
    </section>
    <div class="set-grid">
    <section class="panel"><h2>Menu</h2>
      <p class="small muted" style="margin-top:-4px">Drag the handle (or use the arrows) to reorder. Rename anything. Settings always stays at the bottom.</p>
      <ul class="menu-ed" id="menuEd"></ul>
      <h2 style="margin-top:16px">Add a link</h2>
      <div class="row"><label>Label</label><input type="text" id="lnkLabel" placeholder="e.g. TradingView"></div>
      <div class="row"><label>Address</label><input type="url" id="lnkUrl" placeholder="https://…"></div>
      <div class="row"><label>Opens</label><select id="lnkMode"><option value="frame">Inside the dashboard</option><option value="tab">In a new browser tab</option></select></div>
      <div class="row"><label></label><button class="primary" id="lnkAdd">Add to menu</button><span class="small" id="lnkMsg"></span></div>
    </section>

    <section class="panel"><h2>Logo &amp; name</h2>
      <div class="row"><label>Image</label><input type="file" id="logoFile" accept="image/*" style="flex:1">
        <button class="sm" id="logoClear">Remove</button></div>
      <div class="row"><label>Fit</label><div class="seg" id="logoFit"><button data-v="contain">Whole image</button><button data-v="cover">Fill the box</button></div></div>
      <div class="row"><label>Height</label><input type="range" id="logoH" min="48" max="220" step="4" style="flex:1"><span class="small n" id="logoHv"></span></div>
      <div class="row"><label>Name</label><input type="text" id="brand" maxlength="40"></div>
      <p class="small muted">Shown at the top of the menu. The name appears when there is no image, and in the browser tab.</p>
      <h2 style="margin-top:16px">Display</h2>
      <div class="row"><label>Clock</label><label style="min-width:0"><input type="checkbox" id="c24"> 24-hour</label><label style="min-width:0"><input type="checkbox" id="cSec"> seconds</label></div>
    </section>

    <section class="panel"><h2>Desks</h2>
      <div id="deskCfg"></div>
      <div class="row"><label style="min-width:0"><input type="checkbox" id="keepAlive"> Keep every desk running — restart any desk that stops or crashes</label></div>
      <div class="row"><label style="min-width:0"><input type="checkbox" id="autoRestart"> Restart a desk by itself when its code changes on disk</label></div>
      <p class="small muted">A desk opens the moment you click it; if it isn't running the dashboard starts it. Desks keep running when the dashboard closes — stop them from the desk's page.</p>
    </section>

    <section class="panel"><h2>Portfolio sheet</h2>
      <div class="row"><label>Published link</label><input type="url" id="pfUrl" placeholder="https://docs.google.com/spreadsheets/d/e/…/pub?…"></div>
      <p class="small muted">In Google Sheets: File → Share → Publish to web. Any published link works (web page or CSV).</p>
      <div class="row"><label>Title</label><input type="text" id="pfTitle" maxlength="60"></div>
      <div class="row"><label>Starting capital</label><input type="text" id="pfCap" style="max-width:160px" inputmode="decimal"><span class="small muted">weights and cash are measured against this</span></div>
      <div class="row"><label>Centre image</label><input type="file" id="pfImgFile" accept="image/*" style="flex:1"><button class="sm" id="pfImgClear">Use default</button></div>
      <h2 style="margin-top:16px">Startup</h2>
      <div class="row"><label style="min-width:0"><input type="checkbox" id="obStart"> Open this page when I run START_HERE</label></div>
      <div class="row"><label style="min-width:0"><input type="checkbox" id="obBoot"> Open this page when Windows starts</label></div>
      <div class="row"><label style="min-width:0"><input type="checkbox" id="mAuto"> Open Today first thing on weekdays (before 9:30 ET)</label></div>
      <p class="small muted">Run <b>INSTALL_AUTOSTART.bat</b> once so the dashboard (and any desks set to start with it) come back after a reboot. <b>REMOVE_AUTOSTART.bat</b> undoes it.</p>
      <h2 style="margin-top:16px">Backups</h2>
      <p class="small">Saving to: <b id="bkWhere">…</b> <span class="muted" id="bkDest"></span></p>
      <div class="row"><label>Backup folder</label><input type="text" id="bkDir" style="flex:1" placeholder="Default: OneDrive \u2192 UW Dashboard Backups">
        <button class="sm" id="bkPick">Browse…</button><button class="sm" id="bkSave">Save</button><button class="sm ghost" id="bkDefault">Use default</button></div>
      <div class="row"><label style="min-width:0"><input type="checkbox" id="bkCopy" checked> Copy my existing backups to the new folder</label></div>
      <div class="row"><label>Keep the last</label><input type="number" id="bkKeep" min="3" max="365" style="max-width:90px"><span class="small muted">nightly backups (older ones are deleted from that folder)</span></div>
      <div class="row"><button class="sm" id="bkOpen">Open backup folder</button><button class="sm" id="bkNow">Back up now</button><span class="small muted" id="bkMsg"></span></div>
      <p class="small muted">Pick a folder on another drive, a USB drive or a synced folder (OneDrive, Google Drive, Dropbox) so a dead disk doesn't take the only copy. Browse… opens a folder picker on this PC; it can appear behind the browser.</p>
      <h2 style="margin-top:16px">Diagnostics</h2>
      <div class="row"><button id="diagBtn">Check every data source</button><button class="danger" id="resetBtn">Reset settings</button></div>
      <div id="diagOut"></div>
      <p class="small muted">Dashboard folder: ${esc(APP.state.folder)} · token: ${APP.state.token ? "found (" + esc(APP.state.token_source) + ")" : "<b>not found</b>"}</p>
    </section>
  </div></div>`;

  const save = (patch) => saveSettings(patch).then(() => { if (APP.route === "#/settings") fill(); });
  let dragIdx = null;

  function menuEditor() {
    const menu = s().menu; const ul = $("#menuEd");
    ul.innerHTML = menu.map((it, i) => {
      const tag = it.type === "group" ? "group" : it.type === "link" ? (it.mode === "tab" ? "link ↗" : "link") : "page";
      let li = `<li draggable="true" data-i="${i}"><span class="grip" title="Drag to reorder">⠇</span><span class="tag">${tag}</span>
        <input type="text" value="${esc(it.label)}" data-lab="${i}">
        <button class="sm ghost" data-up="${i}" ${i === 0 ? "disabled" : ""} title="Move up">↑</button>
        <button class="sm ghost" data-dn="${i}" ${i === menu.length - 1 ? "disabled" : ""} title="Move down">↓</button>
        ${it.type === "link" ? `<button class="sm ghost" data-mode="${i}" title="Switch where it opens">${it.mode === "tab" ? "new tab" : "inside"}</button><button class="sm ghost danger" data-rm="${i}" title="Remove">✕</button>` : ""}</li>`;
      if (it.type === "group") {
        li += `<div class="kidsed">` + it.children.map((c, j) => `<li style="margin-bottom:4px"><span class="tag">desk</span><span style="flex:1">${esc(deskCfg(c).name)}</span>
          <button class="sm ghost" data-kup="${i}:${j}" ${j === 0 ? "disabled" : ""}>↑</button><button class="sm ghost" data-kdn="${i}:${j}" ${j === it.children.length - 1 ? "disabled" : ""}>↓</button></li>`).join("") + `</div>`;
      }
      return li;
    }).join("");
    const move = (from, to) => { const m = menu.slice(); const [x] = m.splice(from, 1); m.splice(to, 0, x); save({ menu: m }); };
    $$("[data-up]", ul).forEach((b) => b.onclick = () => move(+b.dataset.up, +b.dataset.up - 1));
    $$("[data-dn]", ul).forEach((b) => b.onclick = () => move(+b.dataset.dn, +b.dataset.dn + 1));
    $$("[data-rm]", ul).forEach((b) => b.onclick = () => { if (!confirmInline(`[data-rm="${b.dataset.rm}"]`, "Remove?")) return; const m = menu.slice(); m.splice(+b.dataset.rm, 1); save({ menu: m }); });
    $$("[data-mode]", ul).forEach((b) => b.onclick = () => { const m = JSON.parse(JSON.stringify(menu)); const it = m[+b.dataset.mode]; it.mode = it.mode === "tab" ? "frame" : "tab"; save({ menu: m }); });
    $$("[data-lab]", ul).forEach((inp) => inp.onchange = () => { const m = JSON.parse(JSON.stringify(menu)); m[+inp.dataset.lab].label = inp.value.trim() || m[+inp.dataset.lab].label; save({ menu: m }); });
    const kmove = (spec, d) => { const [i, j] = spec.split(":").map(Number); const m = JSON.parse(JSON.stringify(menu)); const k = m[i].children; const [x] = k.splice(j, 1); k.splice(j + d, 0, x); save({ menu: m }); };
    $$("[data-kup]", ul).forEach((b) => b.onclick = () => kmove(b.dataset.kup, -1));
    $$("[data-kdn]", ul).forEach((b) => b.onclick = () => kmove(b.dataset.kdn, 1));
    $$("li[draggable]", ul).forEach((li) => {
      li.ondragstart = (e) => { dragIdx = +li.dataset.i; li.classList.add("drag"); e.dataTransfer.effectAllowed = "move"; };
      li.ondragend = () => { li.classList.remove("drag"); $$("li", ul).forEach((x) => x.classList.remove("over")); };
      li.ondragover = (e) => { e.preventDefault(); li.classList.add("over"); };
      li.ondragleave = () => li.classList.remove("over");
      li.ondrop = (e) => { e.preventDefault(); const to = +li.dataset.i; if (dragIdx !== null && dragIdx !== to) move(dragIdx, to); dragIdx = null; };
    });
  }

  function deskEditor() {
    const box = $("#deskCfg");
    box.innerHTML = s().desks.map((d) => {
      const live = deskById(d.id) || {};
      return `<div class="deskcfg"><div class="hd">${deskDot(live)} <input type="text" value="${esc(d.name)}" data-dname="${d.id}" style="flex:1;font-weight:600">
        <span class="small muted">${esc(live.status || "")}${live.port ? " · port " + live.port : ""}</span></div>
        <div class="row"><label>Folder</label><input type="text" value="${esc(d.folder)}" data-dfold="${d.id}" style="flex:1"></div>
        <div class="row"><label>Port</label><input type="text" value="${esc(d.port_override || "")}" placeholder="auto${live.port ? " (" + live.port + ")" : ""}" data-dport="${d.id}" style="max-width:110px">
          <label style="min-width:0"><input type="checkbox" data-dauto="${d.id}" ${d.autostart ? "checked" : ""}> start with the dashboard</label></div>
        ${live.error && live.status !== "running" ? `<p class="small" style="color:var(--critical);margin:4px 0 0">${esc(live.error)}</p>` : ""}</div>`;
    }).join("");
    const upd = (id, patch) => { const ds = JSON.parse(JSON.stringify(s().desks)); Object.assign(ds.find((x) => x.id === id), patch); save({ desks: ds }); };
    $$("[data-dname]", box).forEach((i) => i.onchange = () => upd(i.dataset.dname, { name: i.value.trim() }));
    $$("[data-dfold]", box).forEach((i) => i.onchange = () => upd(i.dataset.dfold, { folder: i.value.trim() }));
    $$("[data-dport]", box).forEach((i) => i.onchange = () => upd(i.dataset.dport, { port_override: i.value.trim() || null }));
    $$("[data-dauto]", box).forEach((i) => i.onchange = () => upd(i.dataset.dauto, { autostart: i.checked }));
  }

  function fill() {
    const st = s();
    menuEditor(); deskEditor();
    $$("#logoFit button").forEach((b) => b.classList.toggle("on", b.dataset.v === (st.logo_fit || "contain")));
    $$("#theme button").forEach((b) => b.classList.toggle("on", b.dataset.v === st.theme));
    themeGallery();
    $("#logoH").value = st.logo_height || 96; $("#logoHv").textContent = (st.logo_height || 96) + "px";
    $("#brand").value = st.brand || ""; $("#c24").checked = !!st.clock_24h; $("#cSec").checked = st.clock_seconds !== false;
    $("#keepAlive").checked = !!st.keep_alive; $("#autoRestart").checked = !!st.auto_restart_on_update;
    $("#pfUrl").value = st.portfolio_url || "";
    $("#pfTitle").value = st.portfolio_title || ""; $("#pfCap").value = st.portfolio_capital || 100000; $("#obStart").checked = !!st.open_browser_on_start; $("#obBoot").checked = !!st.open_browser_on_boot;
    $("#bkDir").value = st.backup_dir || ""; $("#bkKeep").value = st.backup_keep || 30;
    fillBackup();
  }

  async function fillBackup() {
    try {
      const b = (await api("/api/healthz")).backup;
      $("#bkWhere").textContent = b.where; $("#bkDest").textContent = b.dest + " \u00b7 " + b.files.length + " backup" + (b.files.length === 1 ? "" : "s") + " there";
    } catch (e) { $("#bkWhere").textContent = "unknown"; }
  }
  const bkMsg = (t) => { $("#bkMsg").textContent = t; };
  async function setBackupFolder(path) {
    bkMsg("Saving…");
    const r = await api("/api/backup/folder", { body: { path, copy_existing: $("#bkCopy").checked }, allowError: true });
    if (r.error) { bkMsg(""); toast(r.error, 6000); return; }
    APP.settings = r.settings; fill(); renderHealth($("#healthBox"));
    bkMsg(""); toast("Backups now save to " + r.backup.dest + (r.copied ? " (" + r.copied + " old backups copied)" : ""), 5000);
  }

  $("#lnkAdd").onclick = async () => {
    let url = $("#lnkUrl").value.trim(); const label = $("#lnkLabel").value.trim();
    if (url && !/^https?:\/\//i.test(url)) url = "https://" + url;
    try { new URL(url); } catch (e) { $("#lnkMsg").textContent = "That address doesn't look right."; return; }
    let mode = $("#lnkMode").value;
    $("#lnkMsg").textContent = "Checking whether the site allows it…";
    let note = "";
    if (mode === "frame") {
      try { const c = await api("/api/framecheck?url=" + encodeURIComponent(url)); if (c.frameable === false) { mode = "tab"; note = " It refuses to open inside other pages, so it will open in a new tab."; } } catch (e) { /* keep frame */ }
    }
    const id = "link-" + Date.now().toString(36);
    await save({ menu: s().menu.concat([{ id, type: "link", label: label || new URL(url).hostname, url, mode }]) });
    $("#lnkUrl").value = $("#lnkLabel").value = ""; $("#lnkMsg").textContent = "Added." + note;
  };
  $("#logoFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { await save({ logo: await shrinkImage(f, 640) }); toast("Logo updated"); } catch (err) { toast("Could not read that image"); }
  };
  $("#logoClear").onclick = () => save({ logo: null });
  $$("#logoFit button").forEach((b) => b.onclick = () => save({ logo_fit: b.dataset.v }));
  $("#logoH").oninput = (e) => { $("#logoHv").textContent = e.target.value + "px"; $("#logo").style.height = e.target.value + "px"; };
  $("#logoH").onchange = (e) => save({ logo_height: +e.target.value });
  $("#brand").onchange = (e) => save({ brand: e.target.value.trim() || DEFAULT_BRAND });
  $$("#theme button").forEach((b) => b.onclick = () => save({ theme: b.dataset.v }).then(() => refreshTheme(true)));
  $("#thMotion").onchange = (e) => save({ theme_motion: e.target.checked }).then(() => refreshTheme(true));
  function themeGallery() {
    const T = APP.theme; if (!T) return;
    const cur = T.themes.find((t) => t.id === T.id) || T.themes[0];
    // bundled fonts for the previews (only Cyberpunk has any)
    if (!document.getElementById("thFaces")) { const f = document.createElement("style"); f.id = "thFaces";
      f.textContent = T.themes.map((t) => t.fontface || "").join(""); document.head.appendChild(f); }
    const wantDark = document.documentElement.getAttribute("data-theme") === "dark";
    $("#thGrid").innerHTML = T.themes.map((t) => {
      const m = t.modes.length === 1 ? t.modes[0] : (wantDark ? "dark" : "light");
      const k = t.tokens[m], f = t.fonts, r = t.square ? "0" : "7px";
      const glow = t.glow ? `text-shadow:0 0 6px ${t.glow}99,0 0 14px ${t.glow}55;` : "";
      const head = t.bar ? `background:${t.bar};color:#fff;` : `background:${k["side"]};color:${k["ink-1"]};`;
      const scan = t.effects.includes("scanlines") ? `<i class="th-scan"></i>` : "";
      const bars = ["s1", "s2", "s3", "s4", "s5"].map((c, i) => `<i style="background:${k[c]};height:${[70, 45, 88, 34, 58][i]}%;border-radius:${t.square ? 0 : "3px 3px 0 0"}"></i>`).join("");
      return `<button class="th-card ${t.id === T.id ? "sel" : ""}" data-id="${esc(t.id)}" aria-pressed="${t.id === T.id}" title="${esc(t.desc)}">
        <div class="th-prev" style="background:${k.page};border-radius:${r};font-family:${esc(f.body)}">
          <div class="th-top" style="${head}font-family:${esc(f.head)};${glow}">${t.effects.includes("caps") ? esc(t.name.toUpperCase()) : esc(t.name)}</div>
          <div class="th-body">
            <div class="th-side" style="background:${k.side};border-right:1px solid ${k.border}"><i style="background:${k["ink-3"]}"></i><i style="background:${k.s1}"></i><i style="background:${k["ink-3"]}"></i></div>
            <div class="th-panel" style="background:${k["surface-1"]};border:1px solid ${k.border};border-radius:${r};box-shadow:${k.shadow === "none" ? "none" : "0 1px 3px rgba(0,0,0,.15)"}">
              <div style="color:${k["ink-3"]};font-size:9px;letter-spacing:.06em;font-family:${esc(f.head)}">TODAY</div>
              <div style="font-family:${esc(f.num)};font-size:15px;color:${k["ink-1"]};${glow}">4,812.30</div>
              <div style="font-family:${esc(f.num)};font-size:11px"><span style="color:${k.gain}">+1.8%</span> <span style="color:${k.loss}">−0.6%</span></div>
              <div class="th-bars" style="border-bottom:1px solid ${k.axis}">${bars}</div>
            </div>
          </div>${scan}
        </div>
        <div class="th-name"><b>${esc(t.name)}</b><span class="small muted">${t.modes.length === 2 ? "light + dark" : t.modes[0] + " only"}${t.motion ? " · motion" : ""}</span></div>
      </button>`; }).join("");
    $$("#thGrid .th-card").forEach((b) => b.onclick = async () => {
      $$("#thGrid .th-card").forEach((x) => x.classList.toggle("sel", x === b));
      await saveSettings({ theme_id: b.dataset.id }); await refreshTheme(true); fill();
    });
    const single = cur.modes.length === 1;
    $$("#theme button").forEach((b) => { b.disabled = single; });
    $("#thModeNote").textContent = single ? `${cur.name} is ${cur.modes[0]} only.` : "";
    $("#thMotionRow").hidden = !cur.motion;
    $("#thMotion").checked = !!APP.settings.theme_motion;
  }
  $("#c24").onchange = (e) => save({ clock_24h: e.target.checked });
  $("#cSec").onchange = (e) => save({ clock_seconds: e.target.checked });
  $("#keepAlive").onchange = (e) => save({ keep_alive: e.target.checked });
  $("#autoRestart").onchange = (e) => save({ auto_restart_on_update: e.target.checked });
  $("#pfUrl").onchange = (e) => { save({ portfolio_url: e.target.value.trim() }); if (APP.frames.portfolio) { APP.frames.portfolio.remove(); delete APP.frames.portfolio; } };
  $("#pfTitle").onchange = (e) => save({ portfolio_title: e.target.value.trim() || "Stock Portfolio" });
  $("#pfCap").onchange = (e) => { const v = parseFloat(e.target.value.replace(/[$,\s]/g, "")); if (v > 0) save({ portfolio_capital: Math.round(v) }); else { toast("Starting capital must be a positive number"); fill(); } };
  $("#pfImgFile").onchange = async (e) => { const f = e.target.files[0]; if (!f) return; try { await save({ portfolio_image: await shrinkImage(f, 400) }); toast("Portfolio image updated"); } catch (err) { toast("Could not read that image"); } };
  $("#pfImgClear").onclick = () => save({ portfolio_image: null });
  $("#obStart").onchange = (e) => save({ open_browser_on_start: e.target.checked });
  $("#obBoot").onchange = (e) => save({ open_browser_on_boot: e.target.checked });
  $("#mAuto").onchange = (e) => save({ morning_autoopen: e.target.checked });
  $("#bkSave").onclick = () => setBackupFolder($("#bkDir").value.trim());
  $("#bkDir").onkeydown = (e) => { if (e.key === "Enter") setBackupFolder(e.target.value.trim()); };
  $("#bkDefault").onclick = () => setBackupFolder("");
  $("#bkPick").onclick = async () => {
    $("#bkPick").disabled = true; bkMsg("A folder picker opened on this PC \u2014 check behind the browser if you don't see it.");
    try {
      const r = await api("/api/backup/pick", { body: {} });
      if (r.error) { bkMsg(""); toast(r.error, 6000); }
      else if (r.cancelled) bkMsg("");
      else { $("#bkDir").value = r.path; await setBackupFolder(r.path); }
    } finally { $("#bkPick").disabled = false; }
  };
  $("#bkKeep").onchange = (e) => { const v = parseInt(e.target.value, 10); if (v >= 3 && v <= 365) save({ backup_keep: v }).then(() => renderHealth($("#healthBox"))); else { toast("Keep between 3 and 365 backups"); fill(); } };
  $("#bkOpen").onclick = async () => { const r = await api("/api/backup/open", { body: {}, allowError: true }); if (r.error) toast(r.error, 5000); };
  $("#bkNow").onclick = async () => { $("#bkNow").disabled = true; bkMsg("Backing up…"); const r = await api("/api/backup", { body: {} }); $("#bkNow").disabled = false; bkMsg(""); toast(r.ok ? "Backed up to " + r.file : "Backup failed: " + r.error, 5000); fillBackup(); renderHealth($("#healthBox")); };
  renderHealth($("#healthBox")); $("#hRefresh").onclick = () => renderHealth($("#healthBox"));
  $("#resetBtn").onclick = async () => { if (!confirmInline("#resetBtn", "Reset menu, logo & desks?")) return; const r = await api("/api/settings/reset", { body: {} }); APP.settings = r.settings; applyTheme(); renderLogo(); renderNav(); fill(); toast("Settings reset"); };
  $("#diagBtn").onclick = async () => {
    const out = $("#diagOut"); out.innerHTML = `<p class="muted small">Checking…</p>`;
    try {
      const d = await api("/api/diag");
      out.innerHTML = `<table class="small"><tbody>${d.checks.map((c) => `<tr><td>${c.ok ? "✅" : "❌"} ${esc(c.path)}</td><td>${c.ok ? `${c.rows} rows · keys: <span class="muted">${esc((c.first_keys || []).join(", "))}</span>` : esc(c.error)}</td></tr>`).join("")}
        <tr><td>${d.portfolio.error ? "❌" : "✅"} Portfolio sheet</td><td>${d.portfolio.error ? esc(d.portfolio.error) : (d.portfolio.columns || []).length + " columns"}</td></tr>
        ${d.desks.map((k) => `<tr><td>${k.status === "running" ? "✅" : "⚪"} ${esc(k.name)}</td><td>${esc(k.status)}${k.port ? " · port " + k.port : ""}${k.error ? " · " + esc(k.error) : ""}</td></tr>`).join("")}</tbody></table>`;
    } catch (e) { out.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; }
  };
  APP.onDesks = () => { if (APP.route === "#/settings" && !document.activeElement.closest("#deskCfg")) deskEditor(); };
  fill();
};

function shrinkImage(file, max) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = reject;
    r.onload = () => {
      if (/svg/.test(file.type)) { resolve(r.result); return; }
      const img = new Image();
      img.onerror = reject;
      img.onload = () => {
        const k = Math.min(1, max / Math.max(img.width, img.height));
        if (k === 1 && r.result.length < 1_500_000) { resolve(r.result); return; }
        const c = document.createElement("canvas"); c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL(/png|gif|webp/.test(file.type) ? "image/png" : "image/jpeg", 0.9));
      };
      img.src = r.result;
    };
    r.readAsDataURL(file);
  });
}

/* ================================================================ shared: market strip, warnings */
function sectorHeatmap(rows) {
  if (!rows || !rows.length) return `<p class="muted small">Sector data unavailable.</p>`;
  const tile = (r) => {
    const c = r.chg;
    const a = c === null ? 0 : Math.round(8 + 42 * Math.min(1, Math.abs(c) / 0.02));
    const bg = c === null ? "var(--surface-2)" : `color-mix(in srgb, var(${c >= 0 ? "--gain" : "--loss"}) ${a}%, var(--surface-1))`;
    const lean = r.lean === null ? "" : `<span class="hm-lean" title="Options premium leaning ${r.lean >= 0 ? "bullish" : "bearish"} today">${r.lean >= 0 ? "▲" : "▼"} flow ${Math.abs(Math.round(r.lean * 100))}%</span>`;
    return `<div class="hm-tile" style="background:${bg}" title="${esc(r.name || "")}: ${r.last !== null ? "$" + r.last.toFixed(2) : ""}">
      <span class="hm-t">${esc(r.ticker)}</span><span class="hm-n">${esc((r.name || "").replace("S&P 500 Index", "S&P 500"))}</span>
      <span class="hm-c ${pnlCls(c)}">${c === null ? "—" : (c >= 0 ? "+" : "−") + Math.abs(c * 100).toFixed(2) + "%"}</span>${lean}</div>`;
  };
  return `<div class="hm">${rows.map(tile).join("")}</div>`;
}

function tideChart(el, rows) {
  if (!rows || rows.length < 2) { el.innerHTML = `<p class="muted small">Market tide appears once the session starts.</p>`; return; }
  const W = el.clientWidth || 520, H = 170, pl = 58, pr = 64, pt = 10, pb = 22;
  const xs = rows.map((r) => new Date(r.t).getTime());
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const vals = rows.flatMap((r) => [r.call, r.put]).filter((v) => v !== null).concat([0]);
  let lo = Math.min(...vals), hi = Math.max(...vals); const span = (hi - lo) || 1; lo -= span * 0.08; hi += span * 0.08;
  const X = (t) => pl + (x1 === x0 ? 0 : (t - x0) / (x1 - x0)) * (W - pl - pr);
  const Y = (v) => pt + (hi - v) / (hi - lo) * (H - pt - pb);
  const short = (v) => { const a = Math.abs(v); const s = a >= 1e9 ? (v / 1e9).toFixed(1) + "B" : a >= 1e6 ? (v / 1e6).toFixed(0) + "M" : (v / 1e3).toFixed(0) + "k"; return (v < 0 ? "−$" : "$") + s.replace("-", ""); };
  const path = (k) => rows.map((r, i) => r[k] === null ? "" : (i ? "L" : "M") + X(xs[i]).toFixed(1) + "," + Y(r[k]).toFixed(1)).join("");
  const ticks = niceTicks(lo, hi, 3);
  const last = rows[rows.length - 1];
  const tl = (d) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  let g = ticks.map((t) => `<line x1="${pl}" x2="${W - pr}" y1="${Y(t)}" y2="${Y(t)}" stroke="var(--grid)"/><text x="${pl - 6}" y="${Y(t) + 4}" text-anchor="end" font-size="10.5" fill="var(--ink-3)">${short(t)}</text>`).join("");
  g += `<line x1="${pl}" x2="${W - pr}" y1="${Y(0)}" y2="${Y(0)}" stroke="var(--axis)"/>`;
  g += `<text x="${pl}" y="${H - 5}" font-size="10.5" fill="var(--ink-3)">${tl(new Date(x0))}</text><text x="${W - pr}" y="${H - 5}" text-anchor="end" font-size="10.5" fill="var(--ink-3)">${tl(new Date(x1))}</text>`;
  g += `<path d="${path("call")}" fill="none" stroke="var(--gain)" stroke-width="2" stroke-linejoin="round"/><path d="${path("put")}" fill="none" stroke="var(--loss)" stroke-width="2" stroke-linejoin="round"/>`;
  // direct labels at the line ends, nudged apart if they would collide
  let yc = Y(last.call ?? 0), yp = Y(last.put ?? 0);
  if (Math.abs(yc - yp) < 13) { const m = (yc + yp) / 2; if (yc <= yp) { yc = m - 7; yp = m + 7; } else { yc = m + 7; yp = m - 7; } }
  g += `<text x="${W - pr + 6}" y="${yc + 4}" font-size="11" font-weight="600" fill="var(--ink-2)">Calls</text><text x="${W - pr + 6}" y="${yp + 4}" font-size="11" font-weight="600" fill="var(--ink-2)">Puts</text>`;
  g += `<line id="tdX" y1="${pt}" y2="${H - pb}" stroke="var(--ink-3)" stroke-dasharray="3 3" visibility="hidden"/><rect x="${pl}" y="0" width="${W - pl - pr}" height="${H}" fill="transparent" class="tdHit"/>`;
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="tide" role="img" aria-label="Market tide">${g}</svg>
    <div class="legend small"><span><i style="background:var(--gain)"></i>Net call premium</span><span><i style="background:var(--loss)"></i>Net put premium</span></div>`;
  const svg = el.querySelector("svg"), tip = $("#tip");
  svg.querySelector(".tdHit").onmousemove = (e) => {
    const r = svg.getBoundingClientRect(); const mx = (e.clientX - r.left) * W / r.width;
    let i = 0, best = 1e18; xs.forEach((t, j) => { const d = Math.abs(X(t) - mx); if (d < best) { best = d; i = j; } });
    const ln = svg.querySelector("#tdX"); ln.setAttribute("x1", X(xs[i])); ln.setAttribute("x2", X(xs[i])); ln.setAttribute("visibility", "visible");
    tip.style.display = "block"; tip.style.left = (e.clientX + 14) + "px"; tip.style.top = (e.clientY - 10) + "px";
    tip.innerHTML = `<b>${tl(new Date(xs[i]))}</b><br>Calls ${short(rows[i].call)}<br>Puts ${short(rows[i].put)}`;
  };
  svg.querySelector(".tdHit").onmouseleave = () => { tip.style.display = "none"; svg.querySelector("#tdX").setAttribute("visibility", "hidden"); };
}

function marketStrip() {
  return `<section class="panel"><h2>Market <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· SPY and the sector ETFs today · options market tide</span></h2>
    <div class="mstrip"><div id="hmBox"><p class="muted small">Loading…</p></div><div id="tideBox"></div></div></section>`;
}
async function fillMarketStrip() {
  let m = null;
  try { m = await api("/api/market"); } catch (e) { m = { sectors: [], tide: [], sectors_error: e.message }; }
  if (!$("#hmBox")) return;
  $("#hmBox").innerHTML = sectorHeatmap(m.sectors) + (m.sectors_error ? `<p class="small" style="color:var(--critical)">${esc(m.sectors_error)}</p>` : "");
  tideChart($("#tideBox"), m.tide);
}

function warningsList(w, opts = {}) {
  if (!w) return `<p class="muted small">Checking your holdings…</p>`;
  if (!w.ok) return `<p class="muted small">${esc(w.error || "No portfolio to check.")}</p>`;
  const hs = w.holdings || [];
  if (!hs.length) return `<p class="muted small">No open positions in the sheet.</p>`;
  const flagged = hs.filter((h) => h.warnings.length), clean = hs.filter((h) => !h.warnings.length);
  const icon = (lvl) => lvl === "serious" ? `<span class="wico serious" aria-label="serious">▲</span>` : `<span class="wico warn" aria-label="heads up">●</span>`;
  let h = flagged.map((x) => `<div class="wrow"><div class="wtk"><span class="pf-sw" style="background:${pfColor(x.color || 0)}"></span><a href="#/lookup/${esc(x.ticker)}">${esc(x.ticker)}</a></div>
      <div>${x.warnings.map((y) => `<div class="wi">${icon(y.level)} <b>${esc(y.title)}</b><div class="small muted">${esc(y.detail || "")}</div></div>`).join("")}</div></div>`).join("");
  if (clean.length) h += `<p class="small muted" style="margin:8px 0 0">Nothing to flag: ${clean.map((x) => `<a href="#/lookup/${esc(x.ticker)}">${esc(x.ticker)}</a>`).join(", ")}.</p>`;
  if (!flagged.length) h = `<p class="small" style="margin:0 0 4px"><span class="wico ok">✓</span> No warnings on your ${hs.length} holding${hs.length === 1 ? "" : "s"}.</p>` + h;
  if (opts.details) {
    h += `<details class="small" style="margin-top:8px"><summary class="muted">What was measured</summary><table class="small"><thead><tr><th>Ticker</th><th>Next earnings</th><th class="n">Insider sales (non-plan, ${w.thresholds.insider_days}d)</th><th class="n">Puts / calls bought (${w.thresholds.flow_days} sessions)</th></tr></thead><tbody>${
      hs.map((x) => `<tr><td>${esc(x.ticker)}</td><td>${x.earnings ? esc(x.earnings.date) + " (" + x.earnings.days + "d)" : "—"}</td><td class="n">${fmtBig(x.insider.nonplan_usd)}</td><td class="n">${fmtBig(x.flow.put_ask)} / ${fmtBig(x.flow.call_ask)}${x.flow.unmeasured ? ` <span class="muted">(${x.flow.unmeasured} unmeasured)</span>` : ""}</td></tr>`).join("")}</tbody></table>
      <p class="muted">Warns when: earnings within ${w.thresholds.earnings_days} days; insiders sell over ${fmtBig(w.thresholds.insider_usd)} outside 10b5-1 plans; puts bought on the ask over ${fmtBig(w.thresholds.put_usd)} and ${w.thresholds.put_call}× the calls.</p></details>`;
  }
  if (w.errors && w.errors.length) h += `<p class="small" style="color:var(--critical)">${w.errors.map(esc).join("<br>")}</p>`;
  return h;
}

/* ================================================================ Morning */

/* ================================================================ Ticker Lookup + position sizer */
PAGES.lookup = function (page, args) {
  let t = (args[0] || LS.get("lookupLast", "") || "").toUpperCase();
  setTop("Ticker Lookup", "every desk, your history and a position size, for one ticker",
    `<form id="lkForm" class="lkform"><input type="text" id="lkIn" placeholder="Ticker" autocomplete="off" spellcheck="false" value="${esc(t)}"><button class="primary sm" type="submit">Look up</button></form>`);
  page.innerHTML = `<div class="pad"><div id="lkRecent" class="small muted" style="margin:-4px 0 12px"></div><div id="lkBody"></div></div>`;
  const recent = () => { const r = LS.get("lookupRecent", []); $("#lkRecent").innerHTML = r.length ? "Recent: " + r.map((x) => `<a href="#/lookup/${esc(x)}">${esc(x)}</a>`).join(" · ") : ""; };
  recent();
  $("#lkForm").onsubmit = (e) => { e.preventDefault(); const v = $("#lkIn").value.trim().toUpperCase().replace(/[^A-Z0-9.\-]/g, ""); if (v) location.hash = "#/lookup/" + v; };
  if (!t) { $("#lkBody").innerHTML = `<div class="center-msg" style="margin-top:6vh"><h2>Type a ticker</h2><p>See what the Valuation, Confluence, Flow and Institutional desks say about it, your own trades and notes on it, and size a position at the live price.</p></div>`; setTimeout(() => $("#lkIn").focus(), 50); return; }
  LS.set("lookupLast", t);
  LS.set("lookupRecent", [t].concat(LS.get("lookupRecent", []).filter((x) => x !== t)).slice(0, 8)); recent();
  $("#lkBody").innerHTML = `<p class="muted">Looking up ${esc(t)}…</p>`;
  api("/api/lookup?t=" + encodeURIComponent(t)).then((d) => { if (APP.route.toUpperCase() === ("#/lookup/" + t).toUpperCase() || APP.route === "#/lookup") render(d); })
    .catch((e) => { $("#lkBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; });

  function render(d) {
    const q = d.quote || {};
    const price = q.price;
    const pos = d.position;
    const er = (d.holding || {}).earnings;
    const warn = (d.holding || {}).warnings || [];
    const deskCard = (id, title, inner) => {
      const x = d.desks[id] || {};
      const off = x.error === "not running";
      const body = off ? `<p class="muted small">The desk is off.</p><button class="sm" data-start="${id}">Start it</button>`
        : x.error ? `<p class="small" style="color:var(--critical)">${esc(x.error)}</p>` : inner(x);
      return `<section class="panel dcard"><h2>${title}<span class="spacer"></span><a class="small" style="text-transform:none;letter-spacing:0;font-weight:500" href="#/desk/${id}">Open desk →</a></h2>${body}</section>`;
    };
    const val = deskCard("valuation", "Valuation", (x) => {
      if (!x.found) return `<p class="muted small">Not valued yet.</p><p class="small">Run it in the Valuation Desk to get an intrinsic value.</p>`;
      const r = x.run; const up = r.base_value && r.price ? r.base_value / r.price - 1 : null;
      return `<div class="big">${esc(r.verdict || "—")} <span class="muted" style="font-size:14px">${r.score !== null ? Number(r.score).toFixed(1) + " / 10" : ""}</span></div>
        <p class="small">Intrinsic value <b>$${Number(r.base_value || 0).toFixed(2)}</b> vs $${Number(r.price || 0).toFixed(2)} when run ${up !== null ? `<span class="${pnlCls(up)}">(${up >= 0 ? "+" : "−"}${Math.abs(up * 100).toFixed(0)}%)</span>` : ""}</p>
        <p class="small muted">Run ${esc(String(r.run_at || "").slice(0, 16))}${x.runs > 1 ? " · " + x.runs + " runs" : ""}</p>`;
    });
    const con = deskCard("confluence", "Confluence", (x) => {
      if (x.pending) return `<p class="muted small">First scan still running.</p>`;
      if (!x.found) return `<p class="muted small">Not on today's board (no qualifying insider buying).</p>`;
      const c = x.card;
      return `<div class="big">${Number(c.score).toFixed(1)} <span class="muted" style="font-size:14px">/ 100${c.band ? " · " + esc(c.band) : ""}</span></div>
        <p class="small">${c.carried_by && c.carried_by.length ? "Carried by " + c.carried_by.map(esc).join(" + ") : ""}</p>`;
    });
    const flw = deskCard("flow", "Unusual Options Flow", (x) => {
      if (!x.found) return `<p class="muted small">Not on today's flow board.</p>`;
      const c = x.card;
      const hasTape = (c.alerts || []).length || (c.contracts || []).length;
      return `<div class="big">${Number(c.score).toFixed(1)} <span class="muted" style="font-size:14px">/ 100</span> ${c.direction ? `<span class="chip">${esc(c.direction)}</span>` : ""}</div>
        <p class="small">Lane: ${esc(c.lane || "—")}${c.ask_premium ? " · " + fmtBig(c.ask_premium) + " bought on the ask" : ""}</p>
        <div class="row" style="gap:8px">${hasTape ? `<button class="sm" id="lkTapeBtn">Show tape</button>` : ""}<a class="small" href="${esc(uwFlowLink(d.ticker))}" target="_blank" rel="noopener">Open on Unusual Whales ↗</a></div>`;
    });
    const ins = deskCard("institutional", "Institutional (13F)", (x) => {
      if (!x.found) return `<p class="muted small">No small-fund buying in the ${esc(x.report_date || "latest")} 13F wave.</p>`;
      const c = x.cluster;
      return `<div class="big">${Number(c.score).toFixed(1)} <span class="muted" style="font-size:14px">/ 100</span></div>
        <p class="small">${Number(c.n_buyers)} fund${c.n_buyers === 1 ? "" : "s"} buying${c.n_new ? " (" + Number(c.n_new) + " new)" : ""}${c.n_sellers ? ", " + Number(c.n_sellers) + " selling" : ""}</p>
        <p class="small muted">${(x.top_funds || []).filter(Boolean).map(esc).join(" · ")}</p>`;
    });
    const gro = deskCard("growth", "Growth Leaders", (x) => {
      if (!x.found) return `<p class="muted small">${esc(x.error || "No reading.")}</p>`;
      return growthBlock(x.card);
    });
    const mt = d.my_trades || {};
    const hist = `<section class="panel"><h2>Your history with ${esc(d.ticker)}</h2>
      ${pos ? `<p><span class="chip" style="border-color:var(--s1);color:var(--s1)">In your portfolio</span> ${pos.amt} shares from $${pfNum(pos.entry)} · <span class="${pnlCls(pos.pct)}">${pfSigned(pos.pct)}</span> · ${(pos.weight * 100).toFixed(1)}% of capital${pos.notes ? `<br><span class="small muted">${esc(pos.notes)}</span>` : ""}</p>` : ""}
      ${d.closed_in_sheet.length ? `<p class="small">Closed in the sheet: ${d.closed_in_sheet.map((c) => `${esc(c.date || "")}→${esc(c.close_date || "")} <span class="${pnlCls(c.pct)}">${pfSigned(c.pct)}</span>`).join(" · ")}</p>` : ""}
      <p class="small">${mt.count ? `P&L uploads: <b>${mt.count}</b> closed trade${mt.count === 1 ? "" : "s"}, <b class="${pnlCls(mt.net)}">${fmtMoney(mt.net)}</b> net, ${mt.wins} won.` : "No closed trades in your P&L uploads."}</p>
      ${mt.recent && mt.recent.length ? `<table class="small"><tbody>${mt.recent.map((x) => `<tr><td>${esc(x.date)}</td><td>${esc(x.asset === "stock" ? "shares" : x.key.replace(/^\S+\s/, ""))}</td><td class="n ${pnlCls(x.net)}">${fmtMoney(x.net)}</td></tr>`).join("")}</tbody></table>` : ""}
      <h2 style="margin-top:12px">Journal mentions</h2>
      ${d.journal.length ? d.journal.map((j) => `<button class="jentry" data-day="${j.day}"><span class="jd">${parseDay(j.day).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" })}</span><span class="jp">${esc(j.snippet)}</span></button>`).join("") : `<p class="muted small">You haven't written about ${esc(d.ticker)}.</p>`}
    </section>`;
    $("#lkBody").innerHTML = `
      <section class="panel lkhead"><div><div class="lkt">${esc(d.ticker)} <span class="muted" style="font-size:15px;font-weight:500">${esc(q.name || "")}</span></div>
        <div class="small muted">${q.error ? `<span style="color:var(--critical)">${esc(q.error)}</span>` : esc(q.source || "")}</div></div>
        <div class="lkp">${price ? "$" + pfNum(price) : "—"}</div>
        <div class="small">${er ? `Next earnings <b>${esc(er.date)}</b> (${er.days === 0 ? "today" : er.days + " days"})` : ""}</div>
        <div><button class="sm" id="lkName">Thesis &amp; watchlist</button></div></section>
      ${warn.length ? `<div class="banner warn" style="display:block">${warn.map((w) => `<b>${esc(w.title)}</b> <span class="small">${esc(w.detail || "")}</span>`).join("<br>")}</div>` : ""}
      <div class="dgrid drow">${val}${con}${flw}${ins}${d.desks.growth ? gro : ""}</div>
      <div class="lkgrid"><div>${openFlowPanel(d)}</div><div>${sizerPanel(d)}</div></div>
      <div id="lkTape"></div>${hist}`;
    $$("[data-start]", page).forEach((b) => b.onclick = async () => { b.disabled = true; b.textContent = "Starting…"; await api(`/api/desks/${b.dataset.start}/ensure`, { body: {} }); setTimeout(() => route(), 4000); });
    $$(".jentry[data-day]", page).forEach((b) => b.onclick = () => { LS.set("todayDate", b.dataset.day); LS.set("todayDateSetOn", isoDay(new Date())); location.hash = "#/morning"; });
    wireSizer(d);
    $("#lkName").onclick = () => openName(d.ticker);
    $$("#ofTbl tr[data-osi]", page).forEach((tr) => tr.onclick = () => {
      const of = d.opening_flow || {};
      flowTapePanel($("#lkTape"), { alerts: [], session: of.session,
        contracts: (of.contracts || []).map((c) => ({ option_symbol: c.contract, premium: c.premium })) }, d.ticker);
      const pick = $$("#lkTape [data-key]").find((b) => b.dataset.key === tr.dataset.osi); if (pick) pick.click();
      $("#lkTape").scrollIntoView({ behavior: "smooth", block: "start" });
    });
    const tb = $("#lkTapeBtn");
    if (tb) tb.onclick = () => flowTapePanel($("#lkTape"), ((d.desks || {}).flow || {}).card, d.ticker);
  }
};

/* ---------------------------------------------------------------- flow tape (Ticker Lookup)
   The individual option trades behind the Flow Desk's card, fetched through the desk
   (/api/flowtape -> flow desk /api/tape -> UW). "This alert" = the prints UW grouped into one
   flow alert; "Contract today" = the contract's last 50 prints. */
function uwFlowLink(t) { return "https://unusualwhales.com/live-options-flow?ticker_symbol=" + encodeURIComponent(t || ""); }

/* Single-leg, likely-opening flow for the looked-up ticker today (openflow.py). */
function openFlowPanel(d) {
  const of = d.opening_flow || {};
  const head = `<h2>Single-leg opening flow <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· ${esc(of.session || "today")}</span><span class="spacer"></span><a class="small" style="text-transform:none;letter-spacing:0;font-weight:500" href="${esc(uwFlowLink(d.ticker))}" target="_blank" rel="noopener">Unusual Whales ↗</a></h2>`;
  if (of.error) return `<section class="panel ofpanel">${head}<p class="small" style="color:var(--critical)">${esc(of.error)}</p></section>`;
  const cs = of.contracts || [], t = of.totals || {}, dr = of.dropped || {};
  const leanTxt = of.lean === null || of.lean === undefined ? "" : of.lean > 0.2 ? "leans bullish" : of.lean < -0.2 ? "leans bearish" : "is balanced";
  const chip = (c) => c.lean === "bullish" ? '<span class="chip good">bullish</span>' : c.lean === "bearish" ? '<span class="chip bad">bearish</span>' : '<span class="chip">neutral</span>';
  const left = (dr.multi_leg || 0) + (dr.not_opening || 0);
  const body = !cs.length ? `<p class="muted small">No single-leg opening flow alerts on ${esc(d.ticker)} ${of.session ? "for " + esc(of.session) : "today"}.${of.alerts_read ? ` ${of.alerts_read} alert${of.alerts_read === 1 ? "" : "s"} read, none qualified.` : ""}</p>`
    : `<p class="small" style="margin:0 0 8px"><b>${fmtBig(of.premium)}</b> across ${cs.length} contract${cs.length === 1 ? "" : "s"}${leanTxt ? `, which ${leanTxt}` : ""}: <span class="${pnlCls(1)}">${fmtBig(t.bullish || 0)} bullish</span> · <span class="${pnlCls(-1)}">${fmtBig(t.bearish || 0)} bearish</span>${t.neutral ? ` · ${fmtBig(t.neutral)} mixed` : ""}.</p>
      <div class="tblwrap" style="max-height:420px"><table id="ofTbl"><thead><tr><th>Contract</th><th>Side</th><th class="n">Premium</th></tr></thead><tbody>
      ${cs.slice(0, 40).map((c) => `<tr data-osi="${esc(c.contract)}" style="cursor:pointer" title="Show the prints (needs the Flow desk running)">
        <td><b style="white-space:nowrap">${esc(osiText(c.contract))}</b>${c.sweep ? ' <span class="chip">sweep</span>' : ""}${c.floor ? ' <span class="chip">floor</span>' : ""}
          <div class="small muted">${c.dte === null || c.dte === undefined ? "" : c.dte + " DTE · "}${Number(c.size).toLocaleString()} contracts · vol ${Number(c.volume).toLocaleString()} / OI ${c.oi === null ? "—" : Number(c.oi).toLocaleString()} · ${esc((c.reasons || []).join(", "))}</div></td>
        <td style="white-space:nowrap">${chip(c)}<div class="small muted">${esc(c.side)}${c.ask_share !== null && c.ask_share !== undefined ? " · " + Math.round(c.ask_share * 100) + "% ask" : ""}</div></td>
        <td class="n">${fmtBig(c.premium)}</td></tr>`).join("")}</tbody></table></div>`;
  return `<section class="panel ofpanel">${head}${body}
    <p class="small muted" style="margin:8px 0 0">From today's Unusual Whales flow alerts. Single leg only (no spreads or rolls). "Opening" is likely, not confirmed: the alert was bigger than yesterday's open interest, or the contract traded more than its open interest, or UW marked every print opening. Tomorrow's open interest confirms it.${left ? ` Left out: ${dr.multi_leg || 0} multi-leg, ${dr.not_opening || 0} possibly closing.` : ""}${of.truncated ? " Very busy ticker: only the latest 1,000 alerts were read." : ""} Click a row for its prints.</p></section>`;
}

function osiText(sym) {
  const m = /^([A-Z.]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/.exec(sym || "");
  if (!m) return sym || "";
  const k = parseInt(m[6], 10) / 1000;
  return `${m[1]} $${k % 1 ? k.toFixed(2) : k} ${m[5] === "C" ? "call" : "put"} ${m[3]}/${m[4]}/${m[2]}`;
}

function flowTapePanel(box, card, ticker) {
  if (!box || !card) return;
  const st = { tab: (card.alerts || []).length ? "alert" : "contract", key: null };
  const items = () => st.tab === "alert"
    ? (card.alerts || []).map((a) => ({ key: a.id, label: `${osiText(a.option_chain)} · ${a.alert_rule || "alert"} · ${fmtBig(Number(a.total_premium))}` }))
    : (card.contracts || []).map((k) => ({ key: k.option_symbol, label: `${osiText(k.option_symbol)} · ${fmtBig(Number(k.premium))}` }));
  const draw = () => {
    const list = items();
    if (!list.some((i) => i.key === st.key)) st.key = list.length ? list[0].key : null;
    box.innerHTML = `<section class="panel" style="margin-top:14px"><h2>${esc(ticker)} tape <span class="spacer"></span><button class="sm ghost" id="tpClose">Close</button></h2>
      <div class="row" style="gap:6px;flex-wrap:wrap">
        <button class="sm ${st.tab === "alert" ? "primary" : ""}" data-tab="alert" ${(card.alerts || []).length ? "" : "disabled"}>This alert</button>
        <button class="sm ${st.tab === "contract" ? "primary" : ""}" data-tab="contract" ${(card.contracts || []).length ? "" : "disabled"}>Contract today</button>
        <span class="spacer"></span><a class="small" href="${esc(uwFlowLink(ticker))}" target="_blank" rel="noopener">Open on Unusual Whales ↗</a></div>
      ${list.length > 1 ? `<div class="row" style="gap:6px;flex-wrap:wrap;margin-top:8px">${list.map((i) => `<button class="sm ${i.key === st.key ? "primary" : "ghost"}" data-key="${esc(i.key)}">${esc(i.label)}</button>`).join("")}</div>` : `<p class="small muted" style="margin:8px 0 0">${esc((list[0] || {}).label || "")}</p>`}
      <div id="tpBody" style="margin-top:10px"><p class="muted small">Loading the tape…</p></div>
      <p class="small muted" style="margin:8px 0 0">Times are US Eastern. Side is where the print hit the quote: at the ask means a buyer paid up. Canceled prints are struck through and not counted.</p></section>`;
    $("#tpClose", box).onclick = () => { box.innerHTML = ""; };
    $$("[data-tab]", box).forEach((b) => b.onclick = () => { st.tab = b.dataset.tab; st.key = null; draw(); });
    $$("[data-key]", box).forEach((b) => b.onclick = () => { st.key = b.dataset.key; draw(); });
    load();
  };
  const load = async () => {
    const out = $("#tpBody", box);
    if (!st.key) { out.innerHTML = `<p class="muted small">Nothing to show.</p>`; return; }
    const want = st.tab + ":" + st.key;
    const qs = st.tab === "alert" ? `alert=${encodeURIComponent(st.key)}` : `contract=${encodeURIComponent(st.key)}${card.session ? "&date=" + encodeURIComponent(card.session) : ""}`;
    let d;
    try { d = await api("/api/flowtape?" + qs, { allowError: true }); } catch (e) { d = { error: e.message }; }
    if (st.tab + ":" + st.key !== want || !box.contains(out)) return;
    if (d.error) { out.innerHTML = `<div class="banner warn" style="display:block">${esc(d.error)}</div>`; return; }
    const rows = d.trades || [], s = d.summary || {};
    if (!rows.length) { out.innerHTML = `<p class="muted small">${esc(d.note || "No trades returned.")}</p>`; return; }
    const multi = (s.options || []).length > 1;
    const side = { ask: `<b style="color:var(--s1)">Ask</b>`, bid: "Bid", mid: "Mid", none: `<span class="muted">No side</span>` };
    out.innerHTML = `<p class="small">${Number(s.trades)} print${s.trades === 1 ? "" : "s"}${s.canceled ? ` (+${Number(s.canceled)} canceled)` : ""} · ${Number(s.contracts).toLocaleString()} contracts · ${fmtBig(s.premium)} · ${s.ask_share == null ? "—" : Math.round(s.ask_share * 100) + "%"} at the ask · avg fill ${s.vwap == null ? "—" : "$" + s.vwap.toFixed(2)} · ${Number(s.sweeps)} sweep${s.sweeps === 1 ? "" : "s"}</p>
      <div class="tblwrap"><table class="small" style="white-space:nowrap"><thead><tr><th>Time ET</th>${multi ? "<th>Contract</th>" : ""}<th class="n">Contracts</th><th class="n">Price</th><th class="n">Bid × Ask</th><th>Side</th><th class="n">Premium</th><th>Exch</th><th>Flags</th><th class="n">Stock</th><th class="n">IV</th><th class="n">Vol / OI</th></tr></thead>
      <tbody>${rows.map((t) => `<tr${t.canceled ? ' style="text-decoration:line-through;opacity:.6" title="canceled print"' : ""}><td>${esc(t.time_et || "")}</td>${multi ? `<td>${esc(osiText(t.option))}</td>` : ""}<td class="n">${Number(t.contracts).toLocaleString()}</td><td class="n">${t.price == null ? "—" : t.price.toFixed(2)}</td><td class="n">${t.bid && t.ask ? t.bid.toFixed(2) + " × " + t.ask.toFixed(2) : "—"}</td><td>${side[t.side] || esc(t.side)}</td><td class="n">${fmtBig(t.premium)}</td><td>${esc(t.exchange || "")}</td><td>${(t.flags || []).map((f) => `<span class="chip">${esc(f)}</span>`).join(" ")}</td><td class="n">${t.underlying == null ? "—" : t.underlying.toFixed(2)}</td><td class="n">${t.iv == null ? "—" : Math.round(t.iv * 100) + "%"}</td><td class="n">${t.volume == null ? "—" : Number(t.volume).toLocaleString()} / ${t.oi == null ? "—" : Number(t.oi).toLocaleString()}</td></tr>`).join("")}</tbody></table></div>`;
  };
  draw();
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function sizerPanel(d) {
  const s = APP.settings; const price = (d.quote || {}).price;
  return `<section class="panel sizer"><h2>Position size</h2>
    <div class="sz-side seg"><button type="button" data-side="long">Long</button><button type="button" data-side="short">Short</button></div>
    <div class="szf"><label>Entry</label><div class="szin"><span>$</span><input id="szEntry" inputmode="decimal" value="${price ? price.toFixed(2) : ""}"><button class="sm ghost" id="szLive" type="button" title="Use the live price again">↺ live</button></div></div>
    <div class="szf"><label>Risk</label><div class="szin"><input id="szRisk" inputmode="decimal" value="${s.sizer_risk_pct}"><span>% of account</span></div></div>
    <div class="szf"><label>Max position</label><div class="szin"><input id="szMax" inputmode="decimal" value="${s.sizer_max_pos_pct}"><span>% of account</span></div></div>
    <div class="szf"><label>Stop loss</label><div class="szin"><input id="szStop" inputmode="decimal" value="${s.sizer_stop_pct}"><span>% from entry</span></div></div>
    <p class="small muted" style="margin:6px 0 10px">Account: <b>$${Number(s.portfolio_capital).toLocaleString()}</b> starting capital (Settings → Portfolio)</p>
    <div id="szOut"></div>
    <label class="small" style="display:flex;gap:6px;align-items:center;margin-top:10px"><input type="checkbox" id="szRemember"> Remember risk, size, stop and side as my defaults</label>
  </section>`;
}

function wireSizer(d) {
  const s = APP.settings; let side = s.sizer_side === "short" ? "short" : "long"; let timer = null;
  const setSide = (v) => { side = v; $$(".sz-side button").forEach((b) => b.classList.toggle("on", b.dataset.side === side)); calc(); };
  $$(".sz-side button").forEach((b) => b.onclick = () => setSide(b.dataset.side));
  $("#szLive").onclick = () => { if (d.quote && d.quote.price) { $("#szEntry").value = d.quote.price.toFixed(2); calc(); } };
  ["#szEntry", "#szRisk", "#szMax", "#szStop"].forEach((id) => $(id).addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(calc, 150); }));
  $("#szRemember").onchange = (e) => { if (e.target.checked) calc(true); };
  const n = (id) => parseFloat(String($(id).value).replace(/[$,%\s]/g, ""));
  async function calc(remember) {
    const body = { entry: n("#szEntry"), risk_pct: n("#szRisk"), max_pos_pct: n("#szMax"), stop_pct: n("#szStop"), side, remember: !!remember };
    let r;
    try { r = await api("/api/size", { body }); } catch (e) { r = { error: e.message }; }
    if (!$("#szOut")) return;
    if (remember && !r.error) { APP.settings = Object.assign(APP.settings, { sizer_risk_pct: body.risk_pct, sizer_max_pos_pct: body.max_pos_pct, sizer_stop_pct: body.stop_pct, sizer_side: side }); toast("Saved as your defaults"); $("#szRemember").checked = false; }
    if (r.error) { $("#szOut").innerHTML = `<div class="banner err">${esc(r.error)}</div>`; return; }
    const usd = (v) => "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    $("#szOut").innerHTML = `<div class="szres"><div class="szsh"><span class="n">${r.shares.toLocaleString()}</span><span class="small muted">shares to ${r.side === "short" ? "short" : "buy"}</span></div>
      <div class="szkv"><div><span>Stop price</span><b>${usd(r.stop)}</b></div>
        <div><span>Position</span><b>${usd(r.position_usd)} <span class="muted">(${(r.position_pct * 100).toFixed(1)}%)</span></b></div>
        <div><span>At risk</span><b class="loss">${usd(r.at_risk_usd)} <span class="muted">(${(r.at_risk_pct * 100).toFixed(2)}%)</span></b></div>
        <div><span>Risk per share</span><b>${usd(r.per_share)}</b></div>
        ${r.targets.map((t) => `<div><span>${t.r}R target</span><b>${usd(t.price)} <span class="muted gain">+${usd(t.gain_usd)}</span></b></div>`).join("")}</div>
      ${r.notes.map((x) => `<p class="small ${/cash|Zero|not capped/.test(x) ? "szwarn" : "muted"}">${esc(x)}</p>`).join("")}</div>`;
  }
  setSide(side);
}

/* ================================================================ Track record */
PAGES.trackrecord = async function (page) {
  setTop("Track Record", "did each desk's picks go the right way?", `<button class="sm" id="trRec" title="Record the desks' current top names now">Record now</button>`);
  page.innerHTML = `<div class="pad"><div id="trBody"><p class="muted">Loading…</p></div></div>`;
  $("#trRec").onclick = async () => { const r = await api("/api/trackrecord/record", { body: {} }); toast(r.added ? r.added + " new pick" + (r.added === 1 ? "" : "s") + " recorded" : "No new picks — desks are off or already recorded"); load(); };
  async function load() {
    let d;
    try { d = await api("/api/trackrecord"); } catch (e) { $("#trBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    if (APP.route !== "#/trackrecord") return;
    const H = d.horizons;
    const pct = (v, dec = 1) => v === null || v === undefined ? "—" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(dec) + "%";
    const intro = `<p class="small muted" style="margin:0 0 12px;max-width:900px">While the market is open the dashboard records each running desk's top names (once per name per month; per quarter for 13F), then checks the close ${H.join(", ")} sessions later. Entry is the close on the day it was flagged. <b>Bearish flow picks win when the stock falls.</b> "vs SPY" is the same return minus SPY's over the same sessions. Pending results are shown as pending, never zero.</p>`;
    if (!d.total) {
      $("#trBody").innerHTML = intro + `<div class="center-msg" style="margin-top:4vh"><h2>Nothing recorded yet</h2><p>Recording starts at 9:45 ET on the next market day, for any desk that is running. First 5-session results arrive a week after that.</p></div>`;
      return;
    }
    const hdr = H.map((n) => `<th colspan="3" style="text-align:center;border-left:1px solid var(--grid)">${n} sessions</th>`).join("");
    const sub = H.map(() => `<th class="n" style="border-left:1px solid var(--grid)">Hit</th><th class="n">Avg</th><th class="n">vs SPY</th>`).join("");
    const order = modeFirst(d.summary.map((s) => s.desk));
    d.summary.sort((a, b) => order.indexOf(a.desk) - order.indexOf(b.desk));
    const rows = d.summary.map((s) => `<tr style="${curMode() !== "all" && !inMode(DESK_SIDE[s.desk]) ? "opacity:.5" : ""}"><td><b>${esc(s.name)}</b><div class="small muted">${s.picks} pick${s.picks === 1 ? "" : "s"} on ${s.days} day${s.days === 1 ? "" : "s"}</div></td>${
      H.map((n) => { const h = s.horizons[String(n)]; return h.n ? `<td class="n" style="border-left:1px solid var(--grid)">${Math.round(h.hit * 100)}%<div class="small muted">of ${h.n}</div></td><td class="n ${pnlCls(h.avg)}">${pct(h.avg)}</td><td class="n ${pnlCls(h.excess)}">${pct(h.excess)}</td>`
        : `<td class="muted" colspan="3" style="text-align:center;border-left:1px solid var(--grid)">${h.pending} pending</td>`; }).join("")}</tr>`).join("");
    $("#trBody").innerHTML = intro + `<section class="panel"><h2>By desk</h2><div class="tblwrap" style="max-height:none"><table><thead><tr><th>Desk</th>${hdr}</tr><tr><th></th>${sub}</tr></thead><tbody>${rows}</tbody></table></div>
      <p class="small muted" style="margin:8px 0 0">A few dozen picks over a few weeks is an anecdote, not evidence — read the counts before the percentages.</p></section>
      <section class="panel"><h2>Picks <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· ${d.total} recorded since ${esc(d.first_day || "")}</span></h2>
        <div class="row tr-filters">
          <select id="trDesk" aria-label="Desk"><option value="">All desks</option>${[...new Set(d.recent.map((r) => r.desk))].map((k) => `<option value="${esc(k)}">${esc(DESK_SHORT[k] || k)}</option>`).join("")}</select>
          <input id="trTicker" placeholder="Ticker" aria-label="Ticker" autocomplete="off" spellcheck="false" style="width:110px;text-transform:uppercase">
          <span class="seg" id="trDir"><button data-v="" class="on">All</button><button data-v="long">Bullish</button><button data-v="short">Bearish</button></span>
          <span class="spacer"></span><span class="small muted" id="trCount"></span><button class="sm ghost" id="trReset">Reset</button>
        </div>
        <div class="tblwrap"><table class="tr-tbl"><thead><tr id="trHead"></tr></thead><tbody id="trRows"></tbody></table></div></section>`;
    recentTable(d.recent, H, pct);
  }
  load();
};

const DESK_SHORT = { confluence: "Confluence", flow: "Flow", institutional: "13F", valuation: "Valuation", swing: "Swing", growth: "Growth" };
/* Track Record picks table: click a header to sort (again to reverse), filter by desk, ticker and
   direction. The choice is remembered on this browser. Empty results always sort last. */
function recentTable(rows, H, pct) {
  const st = Object.assign({ key: "day", dir: -1, desk: "", q: "", side: "" }, LS.get("trTable", {}));
  const cols = [["day", "Flagged", ""], ["desk", "Desk", ""], ["ticker", "Ticker", ""], ["score", "Score", "n"], ["why", "Why", ""]]
    .concat(H.map((n) => ["r" + n, n + "d", "n"]));
  const val = (r, k) => k === "day" ? r.picked_day + String(r.picked_at || 0).padStart(14, "0")
    : k === "desk" ? (DESK_SHORT[r.desk] || r.desk) : k === "why" ? (r.why || "") : r[k];
  const save = () => LS.set("trTable", st);
  $("#trDesk").value = st.desk; $("#trTicker").value = st.q;
  $$("#trDir button").forEach((b) => b.classList.toggle("on", b.dataset.v === st.side));
  function draw() {
    const q = st.q.trim().toUpperCase();
    const list = rows.filter((r) => (!st.desk || r.desk === st.desk) && (!q || r.ticker.includes(q)) && (!st.side || r.direction === st.side));
    list.sort((a, b) => {
      const x = val(a, st.key), y = val(b, st.key);
      const nx = x === null || x === undefined || x === "", ny = y === null || y === undefined || y === "";
      if (nx || ny) return nx === ny ? 0 : nx ? 1 : -1;               // blanks and pending last, either way
      return (typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y))) * st.dir;
    });
    $("#trHead").innerHTML = cols.map(([k, l, c]) => `<th class="${c} sortable${st.key === k ? " sorted" : ""}" data-k="${k}" aria-sort="${st.key === k ? (st.dir > 0 ? "ascending" : "descending") : "none"}" title="Sort by ${esc(l)}">${esc(l)}<span class="arr">${st.key === k ? (st.dir > 0 ? "▲" : "▼") : "↕"}</span></th>`).join("");
    const shown = list.slice(0, 1000);
    $("#trRows").innerHTML = shown.length ? shown.map((r) => `<tr><td>${esc(r.picked_day)}</td><td>${esc(DESK_SHORT[r.desk] || r.desk)}</td>
      <td><a href="#/lookup/${esc(r.ticker)}"><b>${esc(r.ticker)}</b></a>${r.direction === "short" ? ' <span class="chip">bearish</span>' : ""}</td><td class="n">${r.score !== null ? Math.round(r.score * 10) / 10 : ""}</td><td class="small muted">${esc(r.why || "")}</td>
      ${H.map((n) => { const v = r["r" + n]; return `<td class="n ${pnlCls(v)}">${v === null ? '<span class="muted small">pending</span>' : pct(v)}</td>`; }).join("")}</tr>`).join("")
      : `<tr><td colspan="${cols.length}" class="muted" style="text-align:center;padding:18px">No picks match these filters.</td></tr>`;
    $("#trCount").textContent = `${list.length} of ${rows.length} pick${rows.length === 1 ? "" : "s"}` + (list.length > shown.length ? ` (first ${shown.length} shown)` : "");
    $$("#trHead th").forEach((th) => th.onclick = () => {
      const k = th.dataset.k;
      if (st.key === k) st.dir = -st.dir; else { st.key = k; st.dir = ["desk", "ticker", "why"].includes(k) ? 1 : -1; }
      save(); draw();
    });
  }
  $("#trDesk").onchange = (e) => { st.desk = e.target.value; save(); draw(); };
  $("#trTicker").oninput = (e) => { st.q = e.target.value; save(); draw(); };
  $$("#trDir button").forEach((b) => b.onclick = () => { st.side = b.dataset.v; $$("#trDir button").forEach((x) => x.classList.toggle("on", x === b)); save(); draw(); });
  $("#trReset").onclick = () => { Object.assign(st, { key: "day", dir: -1, desk: "", q: "", side: "" }); save(); recentTable(rows, H, pct); };
  draw();
}

/* ================================================================ What's new (CHANGELOG.md) */
PAGES.changelog = async function (page) {
  setTop("What's new", "every change to the dashboard and the desks, newest first");
  page.innerHTML = `<div class="pad"><div id="clBody"><p class="muted">Loading…</p></div></div>`;
  let d;
  try { d = await api("/api/changelog"); } catch (e) { $("#clBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
  if (APP.route !== "#/changelog") return;
  const lastSeen = LS.get("seenRelease", "");
  LS.set("seenRelease", d.release); renderNav();                 // the dot clears once you've looked
  const ids = d.releases.map((r) => r.id), seenAt = ids.indexOf(lastSeen);
  const isNew = (r) => lastSeen && seenAt > 0 && ids.indexOf(r.id) < seenAt;
  const used = d.tags.filter((t) => d.releases.some((r) => r.tags.includes(t)));
  let tag = LS.get("clTag", "");
  if (tag && !used.includes(tag)) tag = "";
  const md = (s) => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>");
  function draw() {
    const list = d.releases.filter((r) => !tag || r.tags.includes(tag));
    $("#clBody").innerHTML = `${d.error ? `<div class="banner warn">${esc(d.error)}</div>` : ""}
      <div class="row cl-filter"><span class="seg" id="clTags"><button data-t="" class="${tag ? "" : "on"}">All</button>${used.map((t) => `<button data-t="${esc(t)}" class="${t === tag ? "on" : ""}">${esc(t)}</button>`).join("")}</span>
        <span class="spacer"></span><span class="small muted">${list.length} release${list.length === 1 ? "" : "s"} · running ${esc(d.release)}</span></div>
      ${list.map((r) => `<section class="panel cl-rel${isNew(r) ? " cl-new" : ""}">
        <div class="cl-head"><h3>${esc(r.title)}</h3>${isNew(r) ? '<span class="chip hi">new</span>' : ""}<span class="spacer"></span>
          <span class="small muted">${esc(r.date)} · ${esc(r.id)}</span></div>
        <div class="cl-tags">${r.tags.map((t) => `<span class="chip">${esc(t)}</span>`).join("")}</div>
        ${r.do ? `<div class="banner info small" style="margin:10px 0 4px"><b>After updating:</b>&nbsp;${md(r.do)}</div>` : ""}
        <ul class="cl-items">${r.items.map((it) => it.details
          ? `<li><details><summary>${md(it.headline)}</summary><p>${md(it.details)}</p></details></li>`
          : `<li><div class="cl-plain">${md(it.headline)}</div></li>`).join("")}</ul>
      </section>`).join("") || `<p class="muted">Nothing tagged ${esc(tag)} yet.</p>`}`;
    $$("#clTags button").forEach((b) => b.onclick = () => { tag = b.dataset.t; LS.set("clTag", tag); draw(); });
  }
  draw();
};

/* ================================================================ Watchlist, buy zones, theses */
const ZONE = { in: ["In buy zone", "zin"], near: ["Near buy zone", "znear"], above: ["Above buy price", "zabove"] };
const usd2 = (v) => v === null || v === undefined ? "—" : "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pctS = (v, dec = 0) => v === null || v === undefined ? "" : (v >= 0 ? "+" : "−") + Math.abs(v * 100).toFixed(dec) + "%";
const wico = (s) => s === "break" ? '<span class="wico serious" title="broken">▲</span>' : s === "ok" ? '<span class="wico ok" title="holds">✓</span>' : '<span class="muted" title="not measured">–</span>';
function zoneChip(it) {
  if (!it.zone) return `<span class="muted small">${it.valuation || it.buy ? "no price" : "not valued"}</span>`;
  const z = ZONE[it.zone];
  return `<span class="zchip ${z[1]}">${z[0]}</span>${it.zone !== "in" && it.to_buy !== null ? ` <span class="small muted">${pctS(it.to_buy)}</span>` : ""}`;
}
function thesisCell(it, sig) {
  const cs = (it.checks || []).concat(sig && sig.check ? [sig.check] : []);
  const br = cs.filter((c) => c.status === "break").length;
  if (!it.thesis && !it.sell_rules) return br ? `<span class="wico serious">▲</span> <span class="small">${br} warning${br > 1 ? "s" : ""}</span>` : `<span class="small muted">write one</span>`;
  return br ? `<span class="tbreak">▲ ${br} broken</span>` : `<span class="tok">✓ holds</span>${sig ? "" : ' <span class="small muted">…</span>'}`;
}
function lightsCell(sig) {
  if (!sig) return `<span class="small muted">…</span>`;
  if (!sig.lights.length) return `<span class="small muted">quiet</span>`;
  return sig.lights.map((l) => `<span class="lchip ${l.tone}" title="${esc(l.text)}">${esc({ insider: "Insiders", funds: "13F", confluence: "Confluence", cluster: "Funds", flow: "Flow", swing: "Swing" }[l.kind] || l.kind)}</span>`).join("");
}
APP.wlSig = APP.wlSig || {};
async function nameSignals(t, force) {
  const hit = APP.wlSig[t];
  if (hit && !force && Date.now() - hit.at < 15 * 60e3) return hit.d;
  const d = await api("/api/watchlist/signals?t=" + encodeURIComponent(t));
  APP.wlSig[t] = { at: Date.now(), d };
  return d;
}

PAGES.watchlist = async function (page) {
  setTop("Watchlist", "your holdings, and the names you'd buy at the right price",
    `<form id="wlAdd" class="lkform"><input type="text" id="wlIn" placeholder="Add a ticker" autocomplete="off" spellcheck="false"><button class="primary sm" type="submit">Add</button></form>`);
  page.innerHTML = `<div class="pad"><div id="wlBody"><p class="muted">Loading your names…</p></div></div>`;
  $("#wlAdd").onsubmit = async (e) => {
    e.preventDefault(); const v = $("#wlIn").value.trim().toUpperCase(); if (!v) return;
    try { await api("/api/watchlist/add", { body: { ticker: v } }); $("#wlIn").value = ""; toast(v + " added to the watchlist"); load(); openName(v); }
    catch (err) { toast(err.message, 4000); }
  };
  let data = null;
  async function load() {
    try { data = await api("/api/watchlist"); } catch (e) { $("#wlBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    if (APP.route !== "#/watchlist") return;
    draw(); fill();
  }
  function draw() {
    const d = data, items = d.items;
    const ev = d.events.filter((e) => ["revalued", "stale", "error"].includes(e.kind)).slice(0, 5);
    const nz = items.filter((i) => i.zone === "in").length, nb = items.filter((i) => (i.checks || []).some((c) => c.status === "break")).length;
    $("#wlBody").innerHTML = `
      <p class="small muted" style="margin:0 0 12px;max-width:980px">Buy price = the Valuation desk's intrinsic value less a ${Math.round(d.mos * 100)}% margin of safety, unless you set your own. Holdings from your sheet are always here. Click a name for its thesis, sell rules and what smart money is doing in it.</p>
      <div class="kpis">
        <div class="kpi"><div class="l">Names</div><div class="v">${items.length}</div><div class="s">${items.filter((i) => i.holding).length} held · ${items.filter((i) => !i.holding).length} watching</div></div>
        <div class="kpi"><div class="l">In buy zone</div><div class="v ${nz ? "gain" : ""}">${nz}</div><div class="s">${items.filter((i) => i.zone === "near").length} within ${Math.round(d.rules.near_zone * 100)}%</div></div>
        <div class="kpi"><div class="l">Thesis warnings</div><div class="v ${nb ? "loss" : ""}">${nb}</div><div class="s">price, business, smart money</div></div>
        <div class="kpi"><div class="l">Re-valued this fortnight</div><div class="v">${d.events.filter((e) => e.kind === "revalued").length}</div><div class="s">after earnings or by hand</div></div>
      </div>
      ${ev.length ? `<section class="panel"><h2>Recent changes</h2>${ev.map((e) => `<div class="wl-ev ${e.kind}"><a href="#" data-open="${esc(e.ticker)}"><b>${esc(e.ticker)}</b></a> <span class="small">${esc(e.text.startsWith(e.ticker + " ") ? e.text.slice(e.ticker.length + 1) : e.text)}</span> <span class="small muted">${new Date(e.at * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}</span></div>`).join("")}</section>` : ""}
      <section class="panel"><h2>Your names</h2>
      ${items.length ? `<div class="tblwrap" style="max-height:none"><table class="wl-tbl"><thead><tr><th>Name</th><th class="n">Price</th><th class="n">Buy below</th><th>Zone</th><th class="n">Intrinsic value</th><th>Verdict</th><th>Smart money</th><th>Thesis</th><th>Earnings</th></tr></thead><tbody>
        ${items.map((it) => { const v = it.valuation || {}; const sig = (APP.wlSig[it.ticker] || {}).d;
          return `<tr class="wl-row" data-t="${esc(it.ticker)}" tabindex="0">
          <td><b>${esc(it.ticker)}</b> ${it.holding ? '<span class="chip">held</span>' : ""}</td>
          <td class="n">${usd2(it.price)}</td>
          <td class="n">${usd2(it.buy)}${it.buy ? `<div class="small muted">${it.buy_is_override ? "your price" : "from Valuation"}</div>` : ""}</td>
          <td>${zoneChip(it)}</td>
          <td class="n">${usd2(v.base_value)}${v.run_at ? `<div class="small muted">${esc(String(v.run_at).slice(0, 10))}</div>` : ""}</td>
          <td>${v.verdict ? esc(v.verdict) + (v.quality !== null && v.quality !== undefined ? ` <span class="small muted">q ${Number(v.quality).toFixed(1)}</span>` : "") : '<span class="small muted">run it</span>'}</td>
          <td class="wl-sig">${lightsCell(sig)}</td>
          <td class="wl-th">${thesisCell(it, sig)}</td>
          <td class="small">${it.next_er ? esc(it.next_er) : ""}</td></tr>`; }).join("")}
        </tbody></table></div>` : `<div class="center-msg" style="margin:4vh auto"><h2>No names yet</h2><p>Add a ticker above. Holdings from your portfolio sheet appear here by themselves.</p></div>`}
      </section>`;
    $$(".wl-row", page).forEach((r) => { r.onclick = () => openName(r.dataset.t); r.onkeydown = (e) => { if (e.key === "Enter") openName(r.dataset.t); }; });
    $$("[data-open]", page).forEach((a) => a.onclick = (e) => { e.preventDefault(); openName(a.dataset.open); });
  }
  async function fill() {
    for (const it of data.items) {
      if (APP.route !== "#/watchlist") return;
      let sig; try { sig = await nameSignals(it.ticker); } catch (e) { continue; }
      const row = page.querySelector(`.wl-row[data-t="${CSS.escape(it.ticker)}"]`); if (!row) continue;
      row.querySelector(".wl-sig").innerHTML = lightsCell(sig);
      row.querySelector(".wl-th").innerHTML = thesisCell(it, sig);
      if (sig.earnings && sig.earnings.date && !it.next_er) row.lastElementChild.textContent = sig.earnings.date;
    }
  }
  APP.onNameSaved = () => { if (APP.route === "#/watchlist") load(); };
  load();
};

/* One name: thesis, sell rules, buy price, checks, smart money, history. Opens over any page. */
function closeName() { const d = $("#nmDrawer"); if (d) d.classList.remove("open"); }
async function openName(t) {
  t = String(t || "").toUpperCase();
  let dr = $("#nmDrawer");
  if (!dr) {
    dr = document.createElement("div"); dr.id = "nmDrawer"; dr.className = "nm-over"; document.body.appendChild(dr);
    dr.addEventListener("click", (e) => { if (e.target === dr) closeName(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && dr.classList.contains("open")) closeName(); });
  }
  dr.innerHTML = `<div class="nm-panel" role="dialog" aria-modal="true" aria-label="${esc(t)}"><p class="muted">Loading ${esc(t)}…</p></div>`;
  dr.classList.add("open");
  let it;
  try { it = await api("/api/watchlist/item?t=" + encodeURIComponent(t)); } catch (e) { dr.firstElementChild.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
  const P = dr.firstElementChild;
  const v = it.valuation || null, b = it.baseline;
  const num = (x) => x === null || x === undefined ? "" : String(x);
  P.innerHTML = `
    <div class="nm-head"><div><div class="nm-t">${esc(it.ticker)} ${it.holding ? '<span class="chip">held</span>' : it.on_list ? '<span class="chip">watching</span>' : ""}</div>
      <div class="small muted">${usd2(it.price)} ${it.zone ? "· " + zoneChip(it) : ""}</div></div><span class="spacer"></span>
      <button class="sm ghost" id="nmClose" aria-label="Close">✕</button></div>
    <div class="row" style="gap:6px;margin:8px 0 2px">
      <button class="sm" id="nmRerun" title="Run the Valuation desk on it now (about a minute)">Re-value now</button>
      <a class="small" href="#/lookup/${esc(it.ticker)}" id="nmLookup">Ticker Lookup →</a>
      <span class="spacer"></span>
      ${it.holding ? "" : it.on_list ? `<button class="sm ghost danger" id="nmRemove">Remove from watchlist</button>` : `<button class="sm" id="nmAdd">+ Watchlist</button>`}</div>
    <section class="nm-sec"><h4>Valuation</h4>${v ? `
      <div class="nm-kv"><div><span>Intrinsic value</span><b>${usd2(v.base_value)}</b></div><div><span>Range</span><b>${usd2(v.bear)} – ${usd2(v.bull)}</b></div>
      <div><span>Verdict</span><b>${esc(v.verdict || "—")} ${v.score !== null ? `<span class="muted">${Number(v.score).toFixed(1)}/10</span>` : ""}</b></div>
      <div><span>Quality</span><b>${v.quality !== null && v.quality !== undefined ? Number(v.quality).toFixed(1) + " / 10" : "unknown"}</b></div></div>
      ${v.synopsis ? `<p class="small nm-syn">${esc(v.synopsis)}</p>` : ""}
      <p class="small muted">Run ${esc(String(v.run_at || "").slice(0, 16))}${v.period_end ? " · financials to " + esc(v.period_end) : ""}${it.next_er ? " · re-values itself after the " + esc(it.next_er) + " report" : ""}</p>`
      : `<p class="small muted">Not valued yet. <b>Re-value now</b> runs the Valuation desk on it (it starts the desk if needed).</p>`}</section>
    <section class="nm-sec"><h4>Buy zone</h4>
      <p class="small">${it.buy ? `Buy below <b>${usd2(it.buy)}</b> ${it.buy_is_override ? "(your price" + (it.buy_auto ? ", Valuation says " + usd2(it.buy_auto) : "") + ")" : `(intrinsic value less ${Math.round(it.mos * 100)}%)`}${it.to_buy !== null ? ` · price is ${pctS(it.to_buy, 1)} from it` : ""}` : "No buy price yet: run a valuation or set your own below."}</p></section>
    <section class="nm-sec"><h4>Thesis &amp; sell rules</h4>
      <label class="nm-l">Why I own it / want to own it</label><textarea id="nmThesis" rows="4" placeholder="The business, why it's mispriced, what you expect over 3-5 years">${esc(it.thesis)}</textarea>
      <label class="nm-l">What would make me sell</label><textarea id="nmRules" rows="3" placeholder="e.g. price well above value, the moat breaks, management starts diluting">${esc(it.sell_rules)}</textarea>
      <div class="nm-nums">
        <label>Buy below $<input id="nmBuy" inputmode="decimal" value="${num(it.buy_is_override ? it.buy : "")}" placeholder="${it.buy_auto ? Number(it.buy_auto).toFixed(2) : "auto"}"></label>
        <label>Sell target $<input id="nmSell" inputmode="decimal" value="${num(it.sell_target)}" placeholder="optional"></label>
        <label>Stop $<input id="nmStop" inputmode="decimal" value="${num(it.stop)}" placeholder="optional"></label></div>
      <div class="row" style="gap:8px"><button class="primary sm" id="nmSave">Save</button>${b ? `<button class="sm" id="nmRebase" title="Use today's valuation as the new baseline for the 'business got worse' check">Re-baseline</button>` : ""}<span class="small muted" id="nmMsg"></span></div>
      ${b ? `<p class="small muted">Baseline (${new Date(it.thesis_at * 1000).toLocaleDateString()}): ${esc(b.verdict || "—")}, quality ${b.quality !== null && b.quality !== undefined ? Number(b.quality).toFixed(1) : "unknown"}, value ${usd2(b.value)}.</p>` : `<p class="small muted">Saving sets today's valuation as the baseline the checks compare against.</p>`}</section>
    <section class="nm-sec"><h4>Checks</h4><div id="nmChecks">${it.checks.map((c) => `<div class="nm-chk">${wico(c.status)} <b>${esc(c.label)}</b> <span class="small">${esc(c.detail)}</span></div>`).join("")}<div class="nm-chk muted small" id="nmSmartChk">Smart money leaving: checking…</div></div></section>
    <section class="nm-sec"><h4>Growth checks (O'Neil-style)</h4><div id="nmGrowth"><p class="small muted">Asking the Growth Leaders desk…</p></div></section>
    <section class="nm-sec"><h4>Smart money in ${esc(it.ticker)}</h4><div id="nmSig"><p class="small muted">Asking insiders, 13F filings and the desks…</p></div></section>
    ${it.events && it.events.length ? `<section class="nm-sec"><h4>History</h4>${it.events.slice(0, 12).map((e) => `<div class="small nm-ev"><span class="muted">${new Date(e.at * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}</span> ${esc(e.text)}</div>`).join("")}</section>` : ""}`;
  $("#nmClose").onclick = closeName;
  $("#nmLookup").onclick = closeName;
  const after = () => { APP.onNameSaved && APP.onNameSaved(); openName(it.ticker); };
  const post = async (path, body, msg) => { try { await api(path, { body: Object.assign({ ticker: it.ticker }, body) }); toast(msg); after(); } catch (e) { toast(e.message, 4000); } };
  $("#nmSave").onclick = () => post("/api/watchlist/thesis", { thesis: $("#nmThesis").value, sell_rules: $("#nmRules").value,
    buy_override: $("#nmBuy").value.replace(/[$,\s]/g, ""), sell_target: $("#nmSell").value.replace(/[$,\s]/g, ""), stop: $("#nmStop").value.replace(/[$,\s]/g, "") }, "Thesis saved");
  if ($("#nmRebase")) $("#nmRebase").onclick = () => post("/api/watchlist/thesis", { rebaseline: true }, "Baseline updated");
  if ($("#nmAdd")) $("#nmAdd").onclick = () => post("/api/watchlist/add", {}, it.ticker + " added");
  if ($("#nmRemove")) $("#nmRemove").onclick = () => { if (confirmInline("#nmRemove", "Remove?")) post("/api/watchlist/remove", {}, it.ticker + " removed"); };
  $("#nmRerun").onclick = async () => {
    const btn = $("#nmRerun"); btn.disabled = true; btn.textContent = "Re-valuing… (about a minute)";
    try { await api("/api/watchlist/rerun", { body: { ticker: it.ticker } }); toast(it.ticker + " re-valued"); delete APP.wlSig[it.ticker]; after(); }
    catch (e) { btn.disabled = false; btn.textContent = "Re-value now"; toast(e.message, 5000); }
  };
  api("/api/growth?t=" + encodeURIComponent(it.ticker)).then((g) => {
    if (!$("#nmGrowth")) return;
    $("#nmGrowth").innerHTML = g.found ? growthBlock(g.card, { full: true })
      : `<p class="small muted">${g.error === "not running" ? "The Growth Leaders desk is off." : esc(g.error || "No reading.")} <a href="#/desk/growth">Open it</a></p>`;
  }).catch(() => {});
  let sig; try { sig = await nameSignals(it.ticker); } catch (e) { $("#nmSig").innerHTML = `<p class="small" style="color:var(--critical)">${esc(e.message)}</p>`; return; }
  if (!$("#nmSig")) return;
  const c = sig.check; $("#nmSmartChk").outerHTML = `<div class="nm-chk">${wico(c.status)} <b>${esc(c.label)}</b> <span class="small">${esc(c.detail)}</span></div>`;
  const ib = sig.insider_buy || {}, is = sig.insider_sell || {}, f = sig.funds || {}, dk = sig.desks || {};
  const line = (k, v) => `<div class="nm-sig"><span>${k}</span><span>${v}</span></div>`;
  $("#nmSig").innerHTML = `${sig.lights.length ? `<div style="margin:0 0 8px">${sig.lights.map((l) => `<span class="lchip ${l.tone}">${esc(l.text)}</span>`).join(" ")}</div>` : `<p class="small muted">Nothing lighting up right now.</p>`}
    ${line("Insider buying", ib.people !== undefined ? `${fmtBig(ib.usd)} by ${ib.people} ${ib.people === 1 ? "person" : "people"}, ${ib.days} days` : "—")}
    ${line("Insider selling (non-plan)", is.nonplan_usd !== undefined ? `${fmtBig(is.nonplan_usd)} in ${is.days} days` : "—")}
    ${line("13F, " + esc(f.report_date || "latest quarter"), f.holders ? `${f.adds} added, ${f.cuts} cut (${f.exits} exited), net ${pctS(f.net, 1)} of ${f.holders} holders` : "—")}
    ${line("Confluence", dk.confluence ? `${Number(dk.confluence.score).toFixed(0)} (${esc(dk.confluence.band || "")})` : "not on the board")}
    ${line("Options flow", dk.flow ? `${esc(dk.flow.direction || "undirected")}, score ${Number(dk.flow.score || 0).toFixed(0)}` : "not on today's board")}
    ${line("13F cluster", dk.institutional ? `${dk.institutional.n_buyers} small funds buying` : "none")}
    ${line("Swing", dk.swing ? `${dk.swing.direction === "BULL" ? "bullish" : "bearish"} ${Math.abs(dk.swing.score || 0).toFixed(0)}` : "no setup")}
    ${sig.errors && sig.errors.length ? `<p class="small muted">${sig.errors.map(esc).join(" · ")}</p>` : ""}
    <p class="small muted" style="margin-top:6px">Desk rows need that desk running; the insider and 13F rows come straight from Unusual Whales.</p>`;
}

/* ================================================================ How it works */
PAGES.guide = async function (page) {
  setTop("How it works", "what each desk and page does, and how it scores", `<button class="sm" id="gdRefresh" title="Read the desks' numbers again">Refresh</button>`);
  page.innerHTML = `<div class="pad"><div id="gdBody"><p class="muted">Loading…</p></div></div>`;
  $("#gdRefresh").onclick = () => load();
  const src = (x) => x.source === "live"
    ? `<span class="chip gd-live" title="${x.kind === "desk" ? "Read from the running desk just now" : "Read from the dashboard's own settings"}">● live</span>`
    : `<span class="chip" title="${esc(x.source_note || "")}">written</span>`;
  const bars = (parts) => {
    const top = parts.filter((p) => !/^\s/.test(p[0]));
    const max = Math.max(...top.map((p) => p[1]), 1);
    const total = top.reduce((a, p) => a + p[1], 0);
    return `<div class="gd-bars">${parts.map(([label, w]) => { const sub = /^\s/.test(label);
      return `<div class="gd-bar ${sub ? "sub" : ""}"><span class="gd-lbl">${esc(label.trim())}</span><span class="gd-track"><span style="width:${Math.max(2, (w / max) * 100).toFixed(1)}%"></span></span><span class="gd-w">${Math.round(w * 10) / 10}</span></div>`; }).join("")}
      ${top.length > 1 ? `<div class="gd-bar gd-total"><span class="gd-lbl">Total</span><span></span><span class="gd-w">${Math.round(total * 10) / 10}</span></div>` : ""}</div>`;
  };
  const rules = (rs) => rs && rs.length ? `<table class="gd-rules"><tbody>${rs.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("")}</tbody></table>` : "";
  const card = (x) => {
    const sc = x.score || {};
    const open = x.kind === "desk" ? `<a class="small" href="#/desk/${esc(x.id)}">Open desk →</a>` : x.id === "sizer" ? `<a class="small" href="#/lookup">Open Ticker Lookup →</a>` : x.id === "discord" ? "" : `<a class="small" href="#/${esc(x.id)}">Open page →</a>`;
    return `<section class="panel gd-card" id="gd-${esc(x.id)}">
      <div class="gd-head"><h3>${esc(x.name)}</h3>${src(x)}<span class="spacer"></span>${open}</div>
      <p class="gd-q">${esc(x.question)}</p>
      ${x.source_note ? `<p class="small muted gd-note">${esc(x.source_note)}</p>` : ""}${x.note_live ? `<p class="small muted gd-note">${esc(x.note_live)}</p>` : ""}
      <ul class="gd-how">${x.how.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>
      ${sc.title ? `<h4>${esc(sc.title)}</h4><p class="small gd-formula">${esc(sc.formula)}</p>` : ""}
      ${sc.bands && sc.bands.length ? `<div class="gd-bands">${sc.bands.map(([r, l]) => `<span class="chip"><b>${esc(l)}</b>&nbsp;${esc(r)}</span>`).join("")}</div>` : ""}
      ${x.parts ? `<h4>${esc(x.parts_title || "Weights")}</h4>${bars(x.parts)}` : ""}
      ${x.formula ? `<h4>Formula</h4><p class="small gd-formula">${esc(x.formula)}</p>` : ""}
      ${x.rules && x.rules.length ? `<h4>${x.kind === "desk" ? "Gates and alerts" : "Rules"}</h4>${rules(x.rules)}` : ""}
    </section>`;
  };
  async function load() {
    let d;
    try { d = await api("/api/guide"); } catch (e) { $("#gdBody").innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
    if (APP.route !== "#/guide") return;
    const all = d.desks.concat(d.pages);
    const live = d.desks.filter((x) => x.source === "live").length;
    $("#gdBody").innerHTML = `<p class="small muted" style="margin:0 0 10px;max-width:900px">Numbers marked <b>live</b> were read from the running desk just now, so they follow any change in its settings. <b>Written</b> means the desk is off or doesn't publish its numbers, and the values shown are the ones it ships with. ${live} of ${d.desks.length} desks live.</p>
      <nav class="gd-jump">${all.map((x) => `<a href="#" data-j="gd-${esc(x.id)}">${esc(x.name.replace(/ Desk$/, "").replace(/ \(.*\)$/, ""))}</a>`).join("")}</nav>
      <h2 class="gd-sec">Desks</h2><div class="gd-grid">${d.desks.map(card).join("")}</div>
      <h2 class="gd-sec">Dashboard pages</h2><div class="gd-grid">${d.pages.map(card).join("")}</div>
      <p class="small muted" style="margin-top:16px">Scores are heuristics built on public market data, with weights that were reasoned, not fitted. The Track Record page is where you check whether they work. Not financial advice.</p>`;
    $$("#gdBody .gd-jump a").forEach((a) => a.onclick = (e) => { e.preventDefault(); const el = document.getElementById(a.dataset.j); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); });
  }
  load();
};

/* ================================================================ health (Settings) */
async function renderHealth(box) {
  let h;
  try { h = await api("/api/healthz"); } catch (e) { box.innerHTML = `<div class="banner err">${esc(e.message)}</div>`; return; }
  const since = (t) => { if (!t) return "—"; const m = Math.round((Date.now() / 1000 - t) / 60); return m < 60 ? m + " min" : m < 1440 ? (m / 60).toFixed(1) + " h" : Math.round(m / 1440) + " d"; };
  const dot = (s) => s === "running" ? "run" : s === "starting" ? "warn" : s === "error" || s === "missing" ? "err" : "";
  const b = h.backup, last = b.last || {};
  box.innerHTML = `<table class="small"><thead><tr><th>Desk</th><th>Status</th><th>Up for</th><th>What the desk says</th><th>Last log line</th></tr></thead><tbody>${h.desks.map((x) => {
      const hh = x.health || {}; const info = hh.info || {};
      const said = x.status === "missing" || x.status === "error" ? `<span style="color:var(--critical)">${esc(x.error || x.status)}</span>` : hh.error ? esc(hh.error) : hh.problems && hh.problems.length ? `<span style="color:var(--critical)">${hh.problems.map(esc).join("; ")}</span>` : "OK" + Object.entries(info).slice(0, 3).map(([k, v]) => ` · ${esc(k.replace(/_/g, " "))} ${esc(String(v).slice(0, 25))}`).join("");
      const tail = (x.log_tail || "").split("\n").filter(Boolean).pop() || "";
      return `<tr><td><b>${esc(x.name)}</b><div class="muted">port ${Number(x.port) || "—"}</div></td><td><span class="dot ${dot(x.status)}"></span> ${esc(x.status)}${x.updated ? '<div style="color:var(--warning)">update waiting</div>' : ""}</td>
        <td>${x.status === "running" && x.started_at ? since(x.started_at) : "—"}</td><td>${said}</td>
        <td class="muted" style="max-width:320px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(tail)}">${tail ? esc(tail) : x.status === "running" && !x.started_by_us ? "started outside the dashboard (log in its own window)" : "\u2014"}</td></tr>`; }).join("")}</tbody></table>
    <div class="row" style="margin-top:12px"><b>Dashboard</b><span class="small muted">up ${since(h.dashboard.started)} · ${h.dashboard.uw_calls ?? 0} UW calls since start · build ${esc(h.dashboard.build.built)}</span>
      ${h.dashboard.restart_needed ? '<span class="chip hi">update waiting</span>' : ""}<span class="spacer"></span><button id="hRestart">Restart dashboard</button></div>
    <div class="row"><b>Backups</b><span class="small">${last.ok ? `<span class="wico ok">✓</span> ${new Date(last.at * 1000).toLocaleString()}` : last.error ? `<span class="wico serious">▲</span> failed: ${esc(last.error)}` : "none yet"}
      · <span class="muted">${esc(b.where)}: ${esc(b.dest)} · ${b.files.length} of ${b.keep} kept</span></span><span class="spacer"></span><button id="hBackup">Back up now</button></div>
    <p class="small muted">To restore: close the dashboard, copy a backup over <code>dashboard.db</code> in the dashboard folder, run START_HERE.</p>`;
  $("#hRestart").onclick = restartDashboard;
  $("#hBackup").onclick = async () => { $("#hBackup").disabled = true; const r = await api("/api/backup", { body: {} }); toast(r.ok ? "Backed up to " + r.where : "Backup failed: " + r.error, 5000); renderHealth(box); };
}

boot().catch((e) => {
  document.getElementById("page").innerHTML = `<div class="center-msg"><h2>The dashboard server isn't answering</h2><p>${esc(e.message)}</p><p>Double-click <b>START_HERE.bat</b> in the UW Dashboard folder, then reload this page.</p></div>`;
});
