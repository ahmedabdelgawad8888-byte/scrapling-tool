"use strict";

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
  health: null,
  page: "dashboard",
  kind: "scrape",
  mode: "http",
  layout: "cards",
  filter: "",
  mon: { source: null, jobId: null, runId: null, results: [], live: false, terminal: false, kind: "" },
  aud: { timer: null, runId: null },
  notifTimer: null,
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function safeUrl(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  try {
    return /^https?:$/.test(new URL(raw, location.origin).protocol) ? raw : "";
  } catch { return ""; }
}

function fmtCount(value) {
  if (value === null || value === undefined || value === "") return "";
  const s = String(value).replace(/,/g, "").trim();
  const m = /^([\d.]+)\s*([KMBkmb]?)$/.exec(s);
  let n;
  if (m) {
    n = parseFloat(m[1]) * ({ "": 1, k: 1e3, m: 1e6, b: 1e9 }[m[2].toLowerCase()] ?? 1);
  } else {
    n = parseInt(s.replace(/[^\d]/g, ""), 10);
  }
  if (!Number.isFinite(n) || n === 0) return String(value);
  if (n >= 1e9) return (n / 1e9).toFixed(1) + "B";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

function fmtTime(seconds) {
  if (!seconds) return "—";
  return new Date(seconds * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

function fmtRel(seconds) {
  const d = (Date.now() / 1000) - (Number(seconds) || 0);
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 5400);
}

async function api(path, options = {}) {
  const ctrl = new AbortController();
  const timeout = options.timeout || 15000;
  delete options.timeout;
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, signal: ctrl.signal, ...options });
    clearTimeout(timer);
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail ?? detail; } catch { }
      throw new Error(detail);
    }
    if (res.status === 204) return null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res;
  } catch (err) {
    clearTimeout(timer);
    if (err.name === "AbortError") throw new Error(`Request timed out after ${Math.round(timeout / 1000)}s`);
    throw err;
  }
}

/* ------------------------------------------------------------------ theme */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("sc-theme", theme);
  $("#themeBtn").textContent = theme === "dark" ? "◐" : "◑";
}
$("#themeBtn").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
applyTheme(localStorage.getItem("sc-theme") || "dark");

/* ----------------------------------------------------------------- i18n */

const I18N_AR = {
  brandSub: "ذكاء صناع القرار", searchPh: "ابحث عن صناع، عمليات، إجراءات…",
  grpOverview: "نظرة عامة", grpCollection: "التجميع", grpData: "البيانات", grpSystem: "النظام",
  dashTitle: "لوحة القيادة", dashSub: "صورة حية مبنية من عمليات حقيقية.",
  runTitle: "تشغيل جديد", runSub: "اختر وضعًا واملأ النموذج وشاهد النتائج.",
  runStart: "ابدأ",
  setTitle: "الإعدادات", setSub: "الخلفية والمزودين والسلوك.",
  healthTitle: "الحالة", healthSub: "حالة المحرك والإمكانيات وسجل التدقيق.",
  jobsTitle: "الوظائف المحفوظة", jobsSub: "تكوينات قابلة لإعادة الاستخدام.",
  schedTitle: "الجداول", schedSub: "أعد تشغيل تكوين على فاصل زمني.",
  histTitle: "السجل", histSub: "كل عملية محفوظة. أعد الفتح أو التصدير أو المحاولة.",
  capTitle: "التقاط الصفحة", capSub: "لقطة وHTML وحقول محللة لرابط واحد.",
  scrapeUrlsLabel: "روابط الملفات — سطر لكل رابط",
  platformsLabel: "المنصات", perPlatformLabel: "لكل منصة", maxResultsLabel: "أقصى نتائج",
  modeLabel: "الوضع", timeoutLabel: "مهلة (ث)", retriesLabel: "إعادة", proxyLabel: "بروكسي",
  escalate: "تصعيد تلقائي", cache: "استخدام الكاش",
  monResultsTitle: "النتائج", auditTitle: "سجل التدقيق",
};

function applyLang(lang) {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  $("#langBtn").textContent = lang === "ar" ? "EN" : "ع";
  $$("[data-i18n]").forEach((el) => {
    const key = el.dataset.i18n;
    if (lang === "ar" && I18N_AR[key]) el.textContent = I18N_AR[key];
    else if (!el.dataset.i18nEn) el.dataset.i18nEn = el.textContent;
    else if (lang === "en") el.textContent = el.dataset.i18nEn;
  });
  localStorage.setItem("sc-lang", lang);
}
$("#langBtn").addEventListener("click", () => {
  applyLang(document.documentElement.lang === "ar" ? "en" : "ar");
});
applyLang(localStorage.getItem("sc-lang") || "en");

/* ---------------------------------------------------------------- router */

// Pages defined in pages.js register their loader here rather than being
// hard-coded into go(), so adding a page never means editing the router.
const PAGE_LOADERS = {
  dashboard: () => loadDashboard(),
  history: () => loadHistory(),
  jobs: () => loadJobs(),
  schedules: () => loadSchedules(),
  settings: () => loadSettings(),
  health: () => loadHealthPage(),
};

function go(page) {
  if (state.mon.live && page !== "monitor") {
    if (!confirm("A run is still streaming. Leave it running and switch view?")) return;
  }
  state.page = page;
  $$(".nav-item").forEach((n) => n.classList.toggle("is-active", n.dataset.page === page));
  $$("#mobileNav button").forEach((n) => n.classList.toggle("is-active", n.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("is-active", p.id === "page-" + page));
  hideMonitor();
  window.scrollTo(0, 0);

  // Deep-linkable: reload, or a shared URL, lands on the same page.
  if (location.hash.slice(1) !== page) history.replaceState(null, "", `#${page}`);

  const load = PAGE_LOADERS[page];
  if (load) Promise.resolve(load()).catch((err) => toast(err.message, "error"));
}
$("#appNav").addEventListener("click", (e) => {
  const item = e.target.closest(".nav-item");
  if (item) go(item.dataset.page);
});

// The mobile bar is generated from the sidebar so the two cannot drift.
function buildMobileNav() {
  const host = $("#mobileNav");
  if (!host) return;
  host.innerHTML = $$(".nav-item").map((item) => `
    <button type="button" data-page="${esc(item.dataset.page)}"
            class="${item.classList.contains("is-active") ? "is-active" : ""}">
      ${esc(item.textContent.trim())}
    </button>`).join("");
  host.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-page]");
    if (btn) go(btn.dataset.page);
  });
}

/* ---------------------------------------------------------------- health */

async function loadHealth() {
  try {
    state.health = await api("/api/health");
  } catch (err) {
    toast(`Cannot reach the backend: ${err.message}`, "error");
    state.health = null;
    return null;
  }
  const h = state.health;
  if (!h) return null;
  $("#healthPills").innerHTML = [
    `<span class="pill ${h.scrapling ? "on" : "off"}">Scrapling</span>`,
    `<span class="pill ${h.browser ? "on" : "off"}">${h.browser ? "Chromium" : "No browser"}</span>`,
  ].join("");
  $$("#optMode .seg").forEach((seg) => {
    const usable = h.modes.includes(seg.dataset.mode);
    seg.disabled = !usable;
    seg.title = usable ? "" : "Chromium is not available on this host";
  });
  if (!h.modes.includes(state.mode)) {
    const usable = $("#optMode .seg:not(:disabled)");
    if (usable) usable.click();
  }
  if (!h.browser) $("#autoEscalate").checked = false;
  buildChips("#discPlatforms", h.platforms, ["tiktok", "instagram"]);
  buildChips("#lookPlatforms", h.platforms, h.platforms);
  buildChips("#postPlatforms", h.post_platforms, ["tiktok", "instagram"]);
  buildChips("#menPlatforms", h.post_platforms, h.post_platforms);
  $("#discTarget").innerHTML = h.targets
    .map((t) => `<option value="${esc(t)}">${esc(t[0].toUpperCase() + t.slice(1))}</option>`).join("");
  const s = h.store || {};
  $("#storeStat").textContent = `${s.runs || 0} runs · ${s.results || 0} rows`;
  return h;
}

function buildChips(selector, items, selected) {
  const host = $(selector);
  if (!host) return;
  host.innerHTML = items
    .map((p) => `<button type="button" class="chip ${selected.includes(p) ? "on" : ""}" data-value="${esc(p)}">${esc(p)}</button>`)
    .join("");
  host.onclick = (event) => {
    const chip = event.target.closest(".chip");
    if (chip) chip.classList.toggle("on");
  };
}
const chipValues = (sel) => $$(`${sel} .chip.on`).map((c) => c.dataset.value);

/* ----------------------------------------------------------- run options */

const MODE_HINTS = {
  http: "Fast impersonated HTTP via Scrapling's AsyncFetcher.",
  browser: "Real Chromium through Scrapling's DynamicFetcher.",
  stealth: "Anti-detection browser — slowest, beats most bot walls.",
};
$("#optMode").addEventListener("click", (event) => {
  const btn = event.target.closest(".seg");
  if (!btn || btn.disabled) return;
  state.mode = btn.dataset.mode;
  $$("#optMode .seg").forEach((s) => {
    const on = s === btn;
    s.classList.toggle("is-active", on);
    s.setAttribute("aria-checked", String(on));
  });
  $("#modeHint").textContent = MODE_HINTS[state.mode];
});
$("#concurrency").addEventListener("input", (e) => { $("#concOut").value = e.target.value; });

function options() {
  return {
    mode: state.mode,
    timeout: +$("#timeout").value || 30,
    concurrency: +$("#concurrency").value || 10,
    retries: +$("#retries").value || 0,
    auto_escalate: $("#autoEscalate").checked,
    use_cache: $("#useCache").checked,
    proxy: $("#proxy").value.trim(),
  };
}

/* ----------------------------------------------------------- run forms */

$("#runTabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".seg");
  if (!tab) return;
  state.kind = tab.dataset.kind;
  $$("#runTabs .seg").forEach((s) => s.classList.toggle("is-active", s === tab));
  $$(".run-pane").forEach((p) => p.classList.toggle("is-active", p.dataset.pane === state.kind));
  $("#runBtn").dataset.run = state.kind;
});

function paramsFor(kind) {
  switch (kind) {
    case "scrape":
      return { urls: $("#scrapeUrls").value.split("\n").map((s) => s.trim()).filter(Boolean) };
    case "discover":
      return {
        keywords: $("#discKeywords").value, target: $("#discTarget").value,
        platforms: chipValues("#discPlatforms"), per_platform: +$("#discPer").value,
        limit: +$("#discLimit").value, location: $("#discLocation").value,
        min_followers: +$("#discMinF").value, max_followers: +$("#discMaxF").value,
        bio_keyword: $("#discBio").value, verified_only: $("#discVerified").checked,
      };
    case "lookalike":
      return {
        seeds: $("#lookSeeds").value.split("\n").map((s) => s.trim()).filter(Boolean),
        platforms: chipValues("#lookPlatforms"), per_platform: +$("#lookPer").value,
        limit: +$("#lookLimit").value, min_score: +$("#lookScore").value, signals: $("#lookSignals").value,
      };
    case "posts":
      return {
        usernames: $("#postUsers").value, terms: $("#postTerms").value,
        platforms: chipValues("#postPlatforms"), per_platform: +$("#postPer").value,
        deep: $("#postDeep").checked,
      };
    case "mentions":
      return {
        seeds: $("#menSeeds").value, terms: $("#menTerms").value,
        platforms: chipValues("#menPlatforms"), per_platform: +$("#menPer").value,
        recency: $("#menRecency").value,
      };
    default: return {};
  }
}

$("#scrapeUrls").addEventListener("input", updateUrlCount);
function updateUrlCount() {
  const n = $("#scrapeUrls").value.split("\n").filter((s) => s.trim()).length;
  $("#scrapeCount").textContent = `${n} URL${n === 1 ? "" : "s"}`;
}
$("#scrapeFile").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  const text = await file.text();
  let urls = [];
  if (file.name.endsWith(".json")) {
    try {
      const data = JSON.parse(text);
      urls = (Array.isArray(data) ? data : [data]).map((item) =>
        typeof item === "string" ? item : item.url || item.profile_url || item.link || "");
    } catch { toast("That JSON could not be parsed.", "error"); }
  } else {
    urls = text.split(/[\n,;]/).map((s) => s.trim().replace(/^["']|["']$/g, ""));
  }
  urls = [...new Set(urls.filter((u) => /^https?:\/\//i.test(u)))];
  if (!urls.length) { toast("No URLs found in that file.", "warn"); return; }
  const box = $("#scrapeUrls");
  box.value = (box.value.trim() ? box.value.trim() + "\n" : "") + urls.join("\n");
  updateUrlCount();
  toast(`Imported ${urls.length} URL${urls.length === 1 ? "" : "s"}.`, "ok");
  event.target.value = "";
});

/* ------------------------------------------------------------- start run */

$("#runBtn").addEventListener("click", () => startRun(state.kind, $("#runBtn")));

async function startRun(kind, button) {
  const params = paramsFor(kind);
  if (kind === "scrape" && !params.urls.length) { toast("Add at least one URL.", "warn"); return; }
  if (kind === "lookalike" && !params.seeds.length) { toast("Add at least one seed URL.", "warn"); return; }
  button.disabled = true;
  let job;
  try {
    job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ kind, options: options(), params }) });
  } catch (err) {
    toast(`Could not start: ${err.message}`, "error");
    button.disabled = false;
    return;
  }
  button.disabled = false;
  openMonitorLive(job.job_id, kind, kind[0].toUpperCase() + kind.slice(1));
}

/* ----------------------------------------------------------- monitor */

function openMonitorLive(jobId, kind, title) {
  const m = state.mon;
  m.live = true; m.terminal = false; m.jobId = jobId; m.runId = null;
  m.results = []; m.kind = kind; m.filter = "";
  $("#monFilter").value = "";
  showMonitor(title, "Streaming live…", true);
  renderMonStats();
  renderMonResults();
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  m.source = source;
  $("#monLog").hidden = false;
  $("#monLog").innerHTML = "";
  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleMonEvent(data);
    if (data.type === "finished") {
      source.close(); m.source = null; m.terminal = true;
      $("#monSpinner").style.display = "none";
      const noun = data.status === "cancelled" ? `Cancelled — ${data.kept} of ${data.total}` : "Finished";
      $("#monSub").textContent = `${noun} · ${data.ok} ok, ${data.blocked} blocked, ${data.errors} errors`;
      setMonBadge(data.status);
      toast(`${noun} — ${data.ok} ok, ${data.blocked} blocked, ${data.errors} errors.`,
        data.status === "error" ? "error" : "ok");
      loadHealth();
    }
  };
  source.onerror = () => {
    if (m.source) {
      source.close(); m.source = null;
      if (!m.terminal) {
        $("#monSpinner").style.display = "none";
        $("#monSub").textContent = "Lost the event stream — the run may still be going. Check History.";
      }
    }
  };
}

function handleMonEvent(event) {
  const m = state.mon;
  switch (event.type) {
    case "started":
      m.runId = event.run_id || m.runId;
      $("#monRunLabel").textContent = event.total ? `0 / ${event.total}` : "Working…";
      if (event.discovered) monLog(`Found ${Object.entries(event.discovered).map(([k, v]) => `${v} on ${k}`).join(", ")}`);
      if (event.creators !== undefined) monLog(`${event.creators} creators, ${event.posts} posts`);
      break;
    case "progress":
      if (event.result) m.results.push(event.result);
      $("#monProgressFill").style.width = `${event.total ? (event.done / event.total) * 100 : 0}%`;
      $("#monRunLabel").textContent = `${event.done} / ${event.total}`;
      renderMonStats();
      renderMonResults();
      break;
    case "log": monLog(event.message, event.level); break;
    case "finished":
      m.runId = event.run_id || m.runId;
      $("#monProgressFill").style.width = "100%";
      renderMonStats();
      renderMonResults();
      break;
  }
}

function monLog(message, level = "") {
  const box = $("#monLog");
  const line = document.createElement("div");
  if (level) line.className = level;
  line.textContent = message;
  box.append(line);
  box.scrollTop = box.scrollHeight;
}

function showMonitor(title, sub, live) {
  $("#monTitle").textContent = title;
  $("#monSub").textContent = sub;
  $("#monSpinner").style.display = live ? "block" : "none";
  $("#monCancelBtn").style.display = live ? "inline-flex" : "none";
  $("#monProgressFill").style.width = "0%";
  $("#monRunLabel").textContent = live ? "Starting…" : "—";
  setMonBadge("");
  $("#monitor").hidden = false;
}
function hideMonitor() {
  const m = state.mon;
  if (m.source) { m.source.close(); m.source = null; }
  m.live = false;
  $("#monitor").hidden = true;
}
$("#monBackBtn").addEventListener("click", hideMonitor);
$("#monCancelBtn").addEventListener("click", async () => {
  if (!state.mon.jobId) return;
  try { await api(`/api/jobs/${state.mon.jobId}/cancel`, { method: "POST" }); toast("Stopping… partial results are kept.", "warn"); }
  catch (err) { toast(err.message, "error"); }
});

function setMonBadge(status) {
  const b = $("#monBadge");
  if (!status) { b.textContent = ""; b.className = "badge"; return; }
  b.textContent = status;
  b.className = `badge ${status}`;
}

function monStats() {
  const r = state.mon.results;
  const blocked = r.filter((x) => x.blocked).length;
  const ok = r.filter((x) => !x.blocked && !x.error && x.status === 200).length;
  return { total: r.length, ok, blocked, errors: r.length - ok - blocked };
}

function renderMonStats() {
  const s = monStats();
  $("#monStats").innerHTML = [
    ["Rows", s.total, ""], ["OK", s.ok, "ok"], ["Blocked", s.blocked, "blocked"], ["Errors", s.errors, "err"],
  ].map(([k, v, cls]) =>
    `<div class="stat ${cls}"><div class="stat-v">${v}</div><div class="stat-k">${k}</div></div>`).join("");
}

$("#monFilter").addEventListener("input", (e) => { state.filter = e.target.value.toLowerCase(); renderMonResults(); });
$("#monViewGroup").addEventListener("click", (e) => {
  const btn = e.target.closest(".seg");
  if (!btn) return;
  state.layout = btn.dataset.layout;
  $$("#monViewGroup .seg").forEach((s) => s.classList.toggle("is-active", s === btn));
  renderMonResults();
});

function visibleMon() {
  const r = state.mon.results;
  if (!state.filter) return r;
  return r.filter((x) => JSON.stringify(x).toLowerCase().includes(state.filter));
}

function renderMonResults() {
  const rows = visibleMon();
  const body = $("#monResultsBody");
  if (!rows.length) {
    body.innerHTML = `<p class="muted">${state.mon.results.length ? "Nothing matches that filter." : "Waiting for results…"}</p>`;
    return;
  }
  if (state.layout === "cards") body.innerHTML = `<div class="cards">${rows.map(card).join("")}</div>`;
  else if (state.layout === "table") body.innerHTML = tableHtml(rows);
  else body.innerHTML = `<div class="rows">${rows.map(listRow).join("")}</div>`;
}

$("#monExportBtn").addEventListener("click", () => {
  const fmt = $("#monExportFmt").value;
  const m = state.mon;
  if (m.runId) { window.location = `/api/runs/${m.runId}/export?format=${fmt}`; return; }
  if (m.jobId) { window.location = `/api/jobs/${m.jobId}/export?format=${fmt}`; return; }
  toast("Nothing to export yet.", "warn");
});

function openMonitorReplay(run) {
  const m = state.mon;
  m.live = false; m.terminal = true; m.jobId = null; m.runId = run.id;
  m.results = run.results || []; m.kind = run.kind; m.filter = "";
  $("#monFilter").value = "";
  showMonitor(`${run.kind} · ${run.id}`, `${run.label || ""} · ${fmtTime(run.started_at)}`, false);
  setMonBadge(run.status);
  $("#monProgressFill").style.width = "100%";
  $("#monRunLabel").textContent = `${run.ok || 0} ok · ${run.blocked || 0} blocked · ${run.errors || 0} err`;
  $("#monLog").hidden = true;
  renderMonStats();
  renderMonResults();
}

/* ----------------------------------------------------------- result cards */

function stateOf(r) {
  if (r.blocked) return ["blocked", "Blocked"];
  if (r.error) return ["err", "Error"];
  if (r.status === 200) return ["ok", "OK"];
  return ["", `HTTP ${r.status ?? "?"}`];
}

function avatar(r) {
  const url = r.avatar_url || r.profile_pic_hd || r.profile_pic || "";
  const name = r.username || r.full_name || r.title || "?";
  const initial = esc(String(name).charAt(0).toUpperCase() || "?");
  const style = url ? ` style="background-image:url('${esc(url)}')"` : "";
  return `<div class="avatar"${style}>${url ? "" : initial}</div>`;
}

function card(r) {
  const [cls, label] = stateOf(r);
  const url = safeUrl(r.profile_url || r.url);
  const metrics = [
    ["Followers", r.followers], ["Following", r.following],
    ["Likes", r.likes], ["Avg views", r.avg_views],
  ].filter(([, v]) => v).map(([k, v]) =>
    `<span class="metric"><b>${k}</b> ${esc(fmtCount(v))}</span>`).join("");
  let name = esc(r.full_name || r.title || r.username || "(untitled)");
  if (r.is_verified) name += " ✓";
  if (r.is_private) name += " 🔒";
  return `
  <article class="card ${r.blocked ? "is-blocked" : ""} ${r.error && !r.blocked ? "is-error" : ""}">
    <div class="card-head">${avatar(r)}
      <div class="card-id">
        <div class="card-name">${name}</div>
        ${r.username ? `<div class="card-handle">@${esc(r.username)}</div>` : ""}
      </div>
      ${r.platform ? `<span class="tagchip">${esc(r.platform)}</span>` : ""}
    </div>
    ${metrics ? `<div class="metrics">${metrics}</div>` : ""}
    ${r.biography ? `<p class="bio">${esc(r.biography)}</p>` : ""}
    ${r.emails?.length ? `<div class="card-handle">✉️ ${esc(r.emails.slice(0, 2).join(", "))}</div>` : ""}
    ${(r.blocked || r.error) ? `<div class="bio">${esc(r.error || r.blocked)}</div>` : ""}
    <div class="card-foot"><span class="state ${cls}">${label}</span>
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open ↗</a>` : ""}
    </div>
  </article>`;
}

const COLUMNS = [
  ["State", (r) => `<span class="state ${stateOf(r)[0]}">${stateOf(r)[1]}</span>`],
  ["Platform", (r) => esc(r.platform ?? "")],
  ["Username", (r) => esc(r.username ?? "")],
  ["Name", (r) => esc(r.full_name ?? r.title ?? "")],
  ["Followers", (r) => `<span class="num">${esc(fmtCount(r.followers))}</span>`],
  ["Following", (r) => `<span class="num">${esc(fmtCount(r.following))}</span>`],
  ["Likes", (r) => `<span class="num">${esc(fmtCount(r.likes))}</span>`],
  ["Verified", (r) => (r.is_verified ? "✓" : "")],
  ["Emails", (r) => esc((r.emails || []).join(", "))],
  ["Bio", (r) => `<span class="wide">${esc((r.biography || "").slice(0, 90))}</span>`],
  ["URL", (r) => { const u = safeUrl(r.profile_url || r.url); return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">link</a>` : ""; }],
  ["Time", (r) => `<span class="num">${esc(r.response_time ?? "")}</span>`],
];

function tableHtml(rows) {
  return `<div class="tablewrap"><table>
    <thead><tr>${COLUMNS.map(([h]) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${COLUMNS.map(([, c]) => `<td>${c(r)}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div>`;
}

function listRow(r) {
  const [cls, label] = stateOf(r);
  const url = safeUrl(r.profile_url || r.url);
  const bits = [
    r.username ? `@${r.username}` : "",
    r.followers ? `${fmtCount(r.followers)} followers` : "",
    (r.biography || "").slice(0, 70),
  ].filter(Boolean).join(" · ");
  return `<div class="row">${avatar(r)}
    <div class="row-main"><div class="card-name">${esc(r.full_name || r.title || r.username || "(untitled)")}</div>
      <div class="row-sub">${esc(bits)}</div></div>
    <span class="state ${cls}">${label}</span>
    ${r.platform ? `<span class="tagchip">${esc(r.platform)}</span>` : ""}
    ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">↗</a>` : ""}
  </div>`;
}

/* --------------------------------------------------------- save-as-job */

$$("[data-save-from]").forEach((btn) => {
  btn.addEventListener("click", () => openSaveModal(btn.dataset.saveFrom));
});

function openSaveModal(kind, preset) {
  const p = preset || paramsFor(kind);
  openModal("Save job", `
    <label class="field"><span class="field-label">Name</span>
      <input type="text" id="sjName" placeholder="Daily competitor check"></label>
    <label class="field"><span class="field-label">Description</span>
      <input type="text" id="sjDesc" placeholder="optional"></label>
    <label class="field"><span class="field-label">Tags</span>
      <div class="taginput" id="sjTags"><input type="text" id="sjTagInput" placeholder="add tag + Enter"></div></label>
    <label class="check"><input type="checkbox" id="sjPin"><span>Pin to top</span></label>
    <div class="modal-actions">
      <button class="btn btn-ghost" id="sjCancel">Cancel</button>
      <button class="btn btn-primary" id="sjSave">Save</button>
    </div>
  `);
  const tags = [];
  const renderTags = () => {
    $$("#sjTags .tag").forEach((t) => t.remove());
    tags.forEach((t, i) => {
      const el = document.createElement("span");
      el.className = "tag";
      el.innerHTML = `${esc(t)} <button type="button" data-i="${i}">×</button>`;
      $("#sjTags").prepend(el);
    });
  };
  $("#sjTagInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && e.target.value.trim()) { e.preventDefault(); tags.push(e.target.value.trim()); e.target.value = ""; renderTags(); }
  });
  $("#sjTags").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-i]");
    if (b) { tags.splice(+b.dataset.i, 1); renderTags(); }
  });
  $("#sjCancel").addEventListener("click", closeModal);
  $("#sjSave").addEventListener("click", async () => {
    const name = $("#sjName").value.trim();
    if (!name) { toast("Give the job a name.", "warn"); return; }
    try {
      await api("/api/jobs/saved", { method: "POST", body: JSON.stringify({
        name, kind, params: p, options: options(),
        description: $("#sjDesc").value.trim(), tags, pinned: $("#sjPin").checked,
      }) });
      toast("Job saved.", "ok");
      closeModal();
    } catch (err) { toast(err.message, "error"); }
  });
  setTimeout(() => $("#sjName").focus(), 50);
}

/* ------------------------------------------------------------- jobs */

async function loadJobs() {
  const host = $("#jobsList");
  host.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const { jobs } = await api("/api/jobs/saved?include_archived=true");
    if (!jobs.length) { host.innerHTML = emptyState("🕸️", "No saved jobs yet. Save one from the New Run page."); return; }
    host.innerHTML = jobs.map((j) => `
      <div class="hrow">
        <div class="hrow-main">
          <div class="hrow-title">${j.pinned ? "📌 " : ""}${esc(j.name)}
            ${j.archived ? `<span class="badge archived">archived</span>` : ""}
            <span class="tagchip">${esc(j.kind)}</span>
            ${(j.tags || []).map((t) => `<span class="tagchip">${esc(t)}</span>`).join("")}
          </div>
          <div class="hrow-sub">${esc(j.description || "")} · ${j.run_count || 0} runs
            ${j.last_run_status ? `· last <span class="badge ${j.last_run_status}">${esc(j.last_run_status)}</span>` : ""}
            ${j.last_run_at ? `· ${fmtRel(j.last_run_at)}` : ""}</div>
        </div>
        <div class="hrow-actions">
          <button class="btn btn-primary btn-sm" data-jrun="${esc(j.id)}">Run</button>
          <button class="btn btn-ghost btn-sm" data-jdup="${esc(j.id)}">Duplicate</button>
          <button class="btn btn-ghost btn-sm" data-jpin="${esc(j.id)}" data-pinned="${j.pinned}">${j.pinned ? "Unpin" : "Pin"}</button>
          <button class="btn btn-ghost btn-sm" data-jarch="${esc(j.id)}" data-arch="${j.archived}">${j.archived ? "Restore" : "Archive"}</button>
          <button class="btn btn-danger btn-sm" data-jdel="${esc(j.id)}">Delete</button>
        </div>
      </div>`).join("");
  } catch (err) { host.innerHTML = `<p class="muted">Could not load jobs: ${esc(err.message)}</p>`; }
}
$("#jobsList").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const { jrun, jdup, jpin, jarch, jdel } = btn.dataset;
  try {
    if (jrun) { const r = await api(`/api/jobs/saved/${jrun}/run`, { method: "POST" }); openMonitorLive(r.job_id, "", "Saved job"); }
    if (jdup) { await api(`/api/jobs/saved/${jdup}/duplicate`, { method: "POST" }); toast("Duplicated.", "ok"); loadJobs(); }
    if (jpin) { await api(`/api/jobs/saved/${jpin}`, { method: "PATCH", body: JSON.stringify({ pinned: btn.dataset.pinned !== "true" }) }); loadJobs(); }
    if (jarch) { await api(`/api/jobs/saved/${jarch}`, { method: "PATCH", body: JSON.stringify({ archived: btn.dataset.arch !== "true" }) }); loadJobs(); }
    if (jdel) { if (!confirm("Delete this saved job?")) return; await api(`/api/jobs/saved/${jdel}`, { method: "DELETE" }); toast("Deleted.", "ok"); loadJobs(); }
  } catch (err) { toast(err.message, "error"); }
});
$("#jobsCreateBtn").addEventListener("click", () => openSaveModal(state.kind));

/* ---------------------------------------------------------- schedules */

$("#schedBtn").addEventListener("click", async () => {
  const name = $("#schedName").value.trim();
  const kind = $("#schedKind").value;
  if (!name) { toast("Give the schedule a name.", "warn"); return; }
  try {
    await api("/api/schedules", { method: "POST", body: JSON.stringify({
      name, kind, interval_min: +$("#schedEvery").value || 1440,
      options: options(), params: paramsFor(kind),
    }) });
    $("#schedName").value = "";
    toast("Schedule created.", "ok");
    loadSchedules();
  } catch (err) { toast(err.message, "error"); }
});

async function loadSchedules() {
  const host = $("#schedulesOut");
  host.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const { schedules } = await api("/api/schedules");
    if (!schedules.length) { host.innerHTML = `<p class="muted">No schedules yet.</p>`; return; }
    host.innerHTML = schedules.map((s) => `
      <div class="hrow">
        <div class="hrow-main">
          <div class="hrow-title">${esc(s.name)}
            <span class="badge ${s.enabled ? "running" : ""}">${s.enabled ? "active" : "paused"}</span>
            <span class="tagchip">${esc(s.kind)}</span>
          </div>
          <div class="hrow-sub">every ${s.interval_min} min · next ${fmtTime(s.next_run_at)}
            ${s.last_run_at ? `· last ${fmtRel(s.last_run_at)}` : ""}</div>
        </div>
        <div class="hrow-actions">
          <button class="btn btn-ghost btn-sm" data-srun="${esc(s.id)}">Run now</button>
          <button class="btn btn-ghost btn-sm" data-stog="${esc(s.id)}" data-en="${s.enabled}">${s.enabled ? "Pause" : "Resume"}</button>
          <button class="btn btn-danger btn-sm" data-sdel="${esc(s.id)}">Delete</button>
        </div>
      </div>`).join("");
  } catch (err) { host.innerHTML = `<p class="muted">Could not load schedules: ${esc(err.message)}</p>`; }
}
$("#schedulesOut").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const { srun, stog, sdel } = btn.dataset;
  try {
    if (srun) { const r = await api(`/api/schedules/${srun}/run`, { method: "POST" }); openMonitorLive(r.job_id, "", "Schedule run"); }
    if (stog) { await api(`/api/schedules/${stog}/toggle?enabled=${btn.dataset.en !== "true"}`, { method: "POST" }); loadSchedules(); }
    if (sdel) { if (!confirm("Delete this schedule?")) return; await api(`/api/schedules/${sdel}`, { method: "DELETE" }); loadSchedules(); }
  } catch (err) { toast(err.message, "error"); }
});

/* ------------------------------------------------------------- history */

let histCache = [];
async function loadHistory() {
  const host = $("#historyOut");
  host.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const { runs } = await api("/api/runs?limit=200");
    histCache = runs;
    renderHistory();
  } catch (err) { host.innerHTML = `<p class="muted">Could not load history: ${esc(err.message)}</p>`; }
}
function renderHistory() {
  const host = $("#historyOut");
  const kind = $("#histKind").value, status = $("#histStatus").value, q = $("#histSearch").value.toLowerCase();
  let runs = histCache;
  if (kind) runs = runs.filter((r) => r.kind === kind);
  if (status) runs = runs.filter((r) => r.status === status);
  if (q) runs = runs.filter((r) => JSON.stringify(r).toLowerCase().includes(q));
  $("#histCount").textContent = `${runs.length} run${runs.length === 1 ? "" : "s"}`;
  if (!runs.length) { host.innerHTML = emptyState("🕸️", "No runs match."); return; }
  host.innerHTML = runs.map((r) => `
    <div class="hrow">
      <div class="hrow-main">
        <div class="hrow-title">${esc(r.label || r.kind)} <span class="badge ${esc(r.status)}">${esc(r.status)}</span>
          <span class="tagchip">${esc(r.kind)}</span></div>
        <div class="hrow-sub">${fmtTime(r.started_at)} · ${esc(r.id)}</div>
      </div>
      <div class="hrow-counts">
        <span><b>${r.total || 0}</b> rows</span>
        <span class="state ok">${r.ok || 0} ok</span>
        <span class="state blocked">${r.blocked || 0} blocked</span>
        <span class="state err">${r.errors || 0} err</span>
      </div>
      <div class="hrow-actions">
        <button class="btn btn-ghost btn-sm" data-hopen="${esc(r.id)}">Open</button>
        <button class="btn btn-ghost btn-sm" data-hretry="${esc(r.id)}">Retry</button>
        <button class="btn btn-ghost btn-sm" data-hcsv="${esc(r.id)}">CSV</button>
        <button class="btn btn-danger btn-sm" data-hdel="${esc(r.id)}">Delete</button>
      </div>
    </div>`).join("");
}
["#histKind", "#histStatus", "#histSearch"].forEach((s) => $(s).addEventListener("input", renderHistory));
$("#histClear").addEventListener("click", () => { $("#histKind").value = ""; $("#histStatus").value = ""; $("#histSearch").value = ""; renderHistory(); });
$("#historyOut").addEventListener("click", async (e) => {
  const btn = e.target.closest("button");
  if (!btn) return;
  const { hopen, hretry, hcsv, hdel } = btn.dataset;
  try {
    if (hopen) { const run = await api(`/api/runs/${hopen}`); openMonitorReplay(run); }
    if (hretry) {
      const r = await api(`/api/runs/${hretry}/retry`, { method: "POST" });
      openMonitorLive(r.job_id, r.kind, `Retry of ${hretry.slice(0, 8)}`);
      loadHistory();
    }
    if (hcsv) window.location = `/api/runs/${hcsv}/export?format=csv`;
    if (hdel) { if (!confirm("Delete this run and its results?")) return; await api(`/api/runs/${hdel}`, { method: "DELETE" }); toast("Deleted.", "ok"); loadHistory(); loadHealth(); }
  } catch (err) { toast(err.message, "error"); }
});

/* ------------------------------------------------------------- capture */

$("#capBtn").addEventListener("click", async () => {
  const url = $("#capUrl").value.trim();
  if (!url) { toast("Enter a URL to capture.", "warn"); return; }
  const btn = $("#capBtn");
  btn.disabled = true; btn.textContent = "Capturing…";
  $("#captureOut").innerHTML = "";
  try {
    // Browser modes for TikTok/Instagram may retry several times to beat the
    // unhydrated shell; give the request a long leash (timeout + retries).
    const timeout = (+$("#timeout").value || 40) * 1000 * 8;
    const data = await api("/api/capture", {
      method: "POST", timeout,
      body: JSON.stringify({
        url, mode: $("#capMode").value, timeout: +$("#timeout").value || 40,
        full_page: $("#capFull").checked, screenshot: $("#capShot").checked, proxy: $("#proxy").value.trim(),
      }),
    });
    renderCapture(data);
  } catch (err) { toast(`Capture failed: ${err.message}`, "error"); }
  finally { btn.disabled = false; btn.textContent = "Capture"; }
});

function renderCapture(data) {
  const p = data.parsed || {};
  const facts = [
    ["Status", data.status], ["Mode", data.mode], ["Elapsed", `${data.elapsed}s`],
    ["HTML", `${(data.html_bytes / 1024).toFixed(0)} KB`],
    ["Attempts", data.attempts || 1], ["Blocked", data.blocked || "no"],
  ].map(([k, v]) => `<div class="stat"><div class="stat-v" style="font-size:15px">${esc(v)}</div><div class="stat-k">${k}</div></div>`).join("");
  $("#captureOut").innerHTML = `
    <div class="stats">${facts}</div>
    ${data.blocked ? `<div class="toast warn" style="position:static;max-width:none">This page was served as a block (<code>${esc(data.blocked)}</code>) — the HTML below is the challenge page, not real content.</div>` : ""}
    ${data.screenshot ? `<div class="shot"><img alt="Screenshot of ${esc(data.url)}" src="data:image/png;base64,${data.screenshot}"></div>` : ""}
    <div class="panel"><h2 class="panel-title">Parsed fields</h2><pre class="codebox">${esc(JSON.stringify(p, null, 2))}</pre></div>
    <div class="panel"><h2 class="panel-title">Raw HTML</h2><pre class="codebox">${esc(data.html.slice(0, 60000))}</pre></div>`;
}

/* ------------------------------------------------------------- audience */

function audStopButton() {
  $("#audStart").textContent = "Start extraction";
  $("#audStart").disabled = false;
  if (state.aud.timer) { clearInterval(state.aud.timer); state.aud.timer = null; }
}

$("#audStart").addEventListener("click", async () => {
  const btn = $("#audStart");
  if (state.aud.timer && btn.textContent.startsWith("Cancel")) {
    try {
      await api(`/api/audience/cancel/${state.aud.runId}`, { method: "POST" });
      toast("Cancelling…", "warn");
    } catch (err) { toast(err.message, "error"); }
    audStopButton();
    return;
  }
  const url = $("#audUrl").value.trim();
  if (!url) { toast("Enter a profile URL.", "warn"); return; }
  btn.disabled = true;
  try {
    const r = await api("/api/audience/start", { method: "POST", body: JSON.stringify({ url, limit: +$("#audLimit").value || 0 }) });
    if (!r.started) { toast("Could not start — check the URL.", "warn"); btn.disabled = false; return; }
    state.aud.runId = r.run_id;
    toast("Audience extraction started.", "ok");
    btn.textContent = "Cancel";
    btn.disabled = false;
    pollAudience();
    state.aud.timer = setInterval(pollAudience, 3000);
  } catch (err) { toast(err.message, "error"); btn.disabled = false; }
});

async function pollAudience() {
  if (!state.aud.runId) return;
  try {
    const snap = await api(`/api/audience/status/${state.aud.runId}?limit=200`);
    renderAudience(snap);
    const st = snap.status || (snap.run || {}).status;
    if (st === "done" || st === "error" || st === "cancelled") audStopButton();
  } catch { audStopButton(); }
}

function renderAudience(snap) {
  const run = snap.run || snap;
  const followers = snap.followers || [];
  const status = run.status || "?";
  const prog = run.progress || 0;
  const exp = run.expected_total || 0, col = run.collected || 0;
  $("#audOut").innerHTML = `
    <div class="stats">
      <div class="stat"><div class="stat-v">${esc(status)}</div><div class="stat-k">Status</div></div>
      <div class="stat"><div class="stat-v">${col}${exp ? ` / ${exp}` : ""}</div><div class="stat-k">Collected</div></div>
      <div class="stat"><div class="stat-v">${snap.result_total || followers.length}</div><div class="stat-k">Shown</div></div>
    </div>
    <div class="progress"><div class="progress-fill" style="width:${prog}%"></div></div>
    <p class="muted">${prog}% ${run.error ? "· " + esc(run.error) : ""}</p>
    ${followers.length ? `<div class="panel"><div class="results-head"><h2>Followers</h2>
      <button class="btn btn-ghost btn-sm" data-audexp="csv">CSV</button>
      <button class="btn btn-ghost btn-sm" data-audexp="xlsx">Excel</button></div>
      <div class="tablewrap"><table><thead><tr><th>Username</th><th>Name</th><th>Followers</th><th>Following</th><th>Likes</th><th>Verified</th><th>URL</th></tr></thead>
      <tbody>${followers.map((f) => `<tr>
        <td>${esc(f.username || "")}</td><td>${esc(f.full_name || "")}</td>
        <td class="num">${esc(fmtCount(f.followers))}</td><td class="num">${esc(fmtCount(f.following))}</td>
        <td class="num">${esc(fmtCount(f.likes))}</td><td>${f.is_verified ? "✓" : ""}</td>
        <td>${safeUrl(f.profile_url) ? `<a href="${esc(f.profile_url)}" target="_blank" rel="noopener noreferrer">link</a>` : ""}</td>
      </tr>`).join("")}</tbody></table></div></div>` : ""}`;
}

$("#audOut").addEventListener("click", (e) => {
  const b = e.target.closest("[data-audexp]");
  if (b && state.aud.runId) window.location = `/api/audience/export/${state.aud.runId}?format=${b.dataset.audexp}`;
});

/* ------------------------------------------------------------- dashboard */

let dashTimer = null;
async function loadDashboard() {
  let data;
  try { data = await api("/api/dashboard"); }
  catch (err) { $("#dashKpis").innerHTML = `<p class="muted">Could not load dashboard: ${esc(err.message)}</p>`; return; }
  if (!data || !data.stats) return;
  const s = data.stats;
  $("#dashKpis").innerHTML = [
    kpi("Total runs", s.total_runs, `${s.runs_7d || 0} in 7d · ${s.runs_today || 0} today`, "brand"),
    kpi("Rows scraped", s.total_rows, `${s.ok_rows || 0} ok · ${s.blocked_rows || 0} blocked`, "ok"),
    kpi("Blocked rate", s.total_rows ? `${Math.round((s.blocked_rows / s.total_rows) * 100)}%` : "—", `${s.error_rows || 0} errored rows`, "blocked"),
    kpi("Success rate", s.success_rate == null ? "—" : `${s.success_rate}%`, `${s.error_runs || 0} failed runs`, "ok"),
    kpi("Saved jobs", s.saved_jobs, "", "brand"),
    kpi("Active schedules", s.active_schedules, "", "brand"),
  ].join("");
  renderChart(data.timeseries || []);
  renderDonut(s.ok_rows || 0, s.blocked_rows || 0, s.error_rows || 0);
  renderKinds(data.kinds || []);
  renderAttention(data.attention || []);
  renderActivity(data.activity || []);
  if (!dashTimer) dashTimer = setInterval(loadDashboard, 30000);
}
$("#dashRefresh").addEventListener("click", loadDashboard);

function kpi(label, value, sub, cls) {
  return `<div class="kpi"><div class="kpi-v ${cls || ""}">${esc(value)}</div><div class="kpi-k">${esc(label)}</div><div class="kpi-s">${esc(sub)}</div></div>`;
}

function renderChart(series) {
  const host = $("#dashChart");
  if (!series.length) { host.innerHTML = `<p class="muted">No data yet.</p>`; return; }
  const w = 600, h = 180, pad = 28;
  const rows = series.map((d) => +d.rows || 0);
  const max = Math.max(1, ...rows);
  const stepX = (w - pad * 2) / Math.max(1, series.length - 1);
  const pts = series.map((d, i) => [pad + i * stepX, h - pad - ((+d.rows || 0) / max) * (h - pad * 2)]);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const area = `M${pad},${h - pad} ${pts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ")} L${w - pad},${h - pad} Z`;
  const labels = series.map((d, i) => (i % 3 === 0 || i === series.length - 1) ? d.label?.slice(5) : "").filter(Boolean);
  host.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <defs><linearGradient id="gradFill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="var(--brand1)" stop-opacity="0.35"/><stop offset="100%" stop-color="var(--brand1)" stop-opacity="0"/>
    </linearGradient></defs>
    <path d="${area}" class="area"/>
    <path d="${line}" class="line"/>
    ${pts.map((p) => `<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="2.5" class="dot"/>`).join("")}
    ${series.map((d, i) => (i % 3 === 0 || i === series.length - 1) ?
      `<text x="${(pad + i * stepX).toFixed(1)}" y="${h - 8}" text-anchor="middle" class="axis-txt">${esc((d.label || "").slice(5))}</text>` : "").join("")}
  </svg>`;
}

function renderDonut(ok, blocked, err) {
  const total = ok + blocked + err;
  const host = $("#dashDonut"), legend = $("#dashLegend");
  if (!total) { host.style.background = "var(--bg2)"; host.innerHTML = `<div class="donut-center muted">0</div>`; legend.innerHTML = `<span class="muted">No rows yet</span>`; return; }
  const okP = (ok / total) * 100, blP = (blocked / total) * 100;
  host.style.background = `conic-gradient(var(--ok) ${okP}%, var(--blocked) ${okP + blP}%, var(--err) 0 ${okP + blP + (err / total) * 100}%)`;
  host.innerHTML = `<div class="donut-center">${fmtCount(total)}</div>`;
  legend.innerHTML = [
    legRow("ok", "OK", ok), legRow("blocked", "Blocked", blocked), legRow("err", "Errors", err),
  ].join("");
}
function legRow(cls, label, val) {
  return `<div class="legend-item"><span class="legend-swatch" style="background:var(--${cls})"></span>${esc(label)}<b>${val}</b></div>`;
}

function renderKinds(kinds) {
  const host = $("#dashKinds");
  if (!kinds.length) { host.innerHTML = `<p class="muted">No runs yet.</p>`; return; }
  const max = Math.max(1, ...kinds.map((k) => k.runs || 0));
  host.innerHTML = kinds.map((k) => `
    <div class="bar-row"><span class="b-name">${esc(k.kind)}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${((k.runs || 0) / max) * 100}%"></div></div>
      <span class="b-val">${k.runs || 0}</span></div>`).join("");
}

function renderAttention(items) {
  const host = $("#dashAttention");
  if (!items.length) { host.innerHTML = `<p class="muted">All clear.</p>`; return; }
  host.innerHTML = items.map((a) => `
    <div class="attention-item"><span class="state err">!</span>
      <span class="a-msg">${esc(a.label || a.id)} — ${esc(a.error || a.status || "")}</span>
      <button class="btn btn-ghost btn-sm" data-att="${esc(a.id)}" data-kind="${esc(a.kind)}">Open</button>
    </div>`).join("");
}
$("#dashAttention").addEventListener("click", (e) => {
  const b = e.target.closest("[data-att]");
  if (!b) return;
  if (b.dataset.kind === "schedule") go("schedules");
  else { go("history"); setTimeout(() => { const row = $(`[data-hopen="${b.dataset.att}"]`); if (row) row.click(); }, 400); }
});

function renderActivity(items) {
  const host = $("#dashActivity");
  if (!items.length) { host.innerHTML = `<p class="muted">No activity yet.</p>`; return; }
  host.innerHTML = items.map((a) => {
    const icon = a.kind === "schedule" ? "⏰" : { done: "✅", error: "⚠️", cancelled: "⏹️" }[a.status] || "•";
    return `<div class="activity-item"><span class="act-icon">${icon}</span>
      <div class="act-main"><div class="act-title">${esc(a.label || a.kind)} <span class="badge ${esc(a.status)}">${esc(a.status || "")}</span></div>
        <div class="act-sub">${esc(a.detail || fmtRel(a.ts))}</div></div></div>`;
  }).join("");
}

/* ------------------------------------------------------------- settings */

async function loadSettings() {
  const host = $("#settingsOut");
  host.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const [h, sets, prov] = await Promise.all([api("/api/health"), api("/api/settings"), api("/api/providers")]);
    const s = sets.settings;
    const st = h.store || {};
    const providers = prov.search_providers || {};
    const keys = prov.api_keys || {};
    host.innerHTML = `
      <div class="panel"><h2 class="panel-title">Backend</h2>
        <div class="grid-3">
          ${setKV("Store backend", st.backend || "?")}${setKV("Runs stored", st.runs || 0)}${setKV("Rows saved", st.results || 0)}
          ${setKV("Scrapling", h.scrapling ? "available" : "missing")}${setKV("Browser", h.browser ? h.browser_engine : "http-only")}${setKV("Version", h.version || "?")}
        </div>
      </div>
      <div class="panel"><h2 class="panel-title">Providers</h2>
        <div class="grid-2">
          <div>${Object.entries(providers).map(([n, en]) => `<div class="set-group"><div class="set-key">${esc(n)}</div><div class="set-value">${en ? "enabled" : "disabled"}</div></div>`).join("")}</div>
          <div>${Object.entries(keys).map(([n, on]) => `<div class="set-group"><div class="set-key">${esc(n)} API key</div><div class="set-value">${on ? "set ✓" : "not set"}</div></div>`).join("")}</div>
        </div>
      </div>
      <div class="panel"><h2 class="panel-title">Behaviour</h2>
        <label class="field"><span class="field-label">Webhook URLs (one per line)</span>
          <textarea id="setWebhooks" rows="3" spellcheck="false">${esc((s.webhooks || []).join("\n"))}</textarea></label>
        <div class="grid-3">
          <label class="field"><span class="field-label">Default export</span>
            <select id="setExport"><option value="csv">CSV</option><option value="json">JSON</option><option value="jsonl">JSON Lines</option><option value="xlsx">Excel</option><option value="md">Markdown</option><option value="html">HTML</option></select></label>
          <label class="field"><span class="field-label">Default mode</span>
            <select id="setMode"><option value="http">HTTP</option><option value="browser">Browser</option><option value="stealth">Stealth</option></select></label>
          <label class="field"><span class="field-label">Default concurrency</span>
            <input type="number" id="setConc" min="1" max="40" value="${s.default_concurrency || 10}"></label>
        </div>
        <div class="grid-3">
          <label class="field"><span class="field-label">Default timeout (s)</span>
            <input type="number" id="setTime" min="5" max="180" value="${s.default_timeout || 30}"></label>
          <label class="field"><span class="field-label">Default retries</span>
            <input type="number" id="setRet" min="0" max="5" value="${s.default_retries || 0}"></label>
          <label class="field"><span class="field-label">Retention days (0 = forever)</span>
            <input type="number" id="setRetn" min="0" value="${s.retention_days || 0}"></label>
        </div>
        <label class="check"><input type="checkbox" id="setNotif" ${s.notifications ? "checked" : ""}><span>Desktop notifications</span></label>
        <div class="panel-foot"><span class="spacer"></span>
          <button class="btn btn-primary" id="setSave">Save settings</button></div>
      </div>`;
    $("#setExport").value = s.default_export || "csv";
    $("#setMode").value = s.default_mode || "http";
    $("#setSave").addEventListener("click", async () => {
      try {
        await api("/api/settings", { method: "PATCH", body: JSON.stringify({
          webhooks: $("#setWebhooks").value.split("\n").map((x) => x.trim()).filter(Boolean),
          default_export: $("#setExport").value, default_mode: $("#setMode").value,
          default_concurrency: +$("#setConc").value, default_timeout: +$("#setTime").value,
          default_retries: +$("#setRet").value, retention_days: +$("#setRetn").value,
          notifications: $("#setNotif").checked,
        }) });
        toast("Settings saved.", "ok");
      } catch (err) { toast(err.message, "error"); }
    });
  } catch (err) { host.innerHTML = `<p class="muted">Could not load settings: ${esc(err.message)}</p>`; }
}
function setKV(k, v) { return `<div class="set-group"><div class="set-key">${esc(k)}</div><div class="set-value">${esc(v)}</div></div>`; }

/* ------------------------------------------------------------- health page */

async function loadHealthPage() {
  const host = $("#healthOut");
  const audit = $("#auditOut");
  host.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const [h, a] = await Promise.all([api("/api/health"), api("/api/audit?limit=100")]);
    host.innerHTML = `<div class="panel"><h2 class="panel-title">Engine</h2>
      <div class="grid-3">${setKV("Status", h.status)}${setKV("Version", h.version || "?")}${setKV("Scrapling", h.scrapling ? "available" : "missing")}
      ${setKV("Browser", h.browser ? h.browser_engine : "http-only")}${setKV("Modes", (h.modes || []).join(", "))}${setKV("Platforms", (h.platforms || []).join(", "))}
      ${setKV("Store backend", (h.store || {}).backend || "?")}${setKV("System", JSON.stringify(h.system || {}))}</div></div>
      <div class="panel"><h2 class="panel-title">Full payload</h2><pre class="codebox">${esc(JSON.stringify(h, null, 2))}</pre></div>`;
    const entries = a.entries || [];
    audit.innerHTML = entries.length ? `<div class="tablewrap"><table><thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Detail</th><th>Actor</th></tr></thead>
      <tbody>${entries.map((e) => `<tr><td class="num">${fmtTime(e.created_at)}</td><td>${esc(e.action || "")}</td>
        <td>${esc(e.target_type || "")} ${esc(e.target_id || "")}</td><td>${esc(e.detail || "")}</td><td>${esc(e.actor || "")}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="muted">No audit entries yet.</p>`;
  } catch (err) { host.innerHTML = `<p class="muted">Could not load health: ${esc(err.message)}</p>`; }
}
$("#healthRefresh").addEventListener("click", loadHealthPage);

/* ------------------------------------------------------------- notifications */

async function loadNotifications() {
  try {
    const data = await api("/api/notifications?limit=20");
    const unread = data.unread || 0;
    $("#bellDot").hidden = unread === 0;
    $("#bellDot").textContent = unread > 9 ? "9+" : "";
    const list = data.notifications || [];
    $("#notifDrop").innerHTML = list.length ? list.map((n) => `
      <div class="notif-item ${n.read ? "" : "unread"}" data-nid="${esc(n.id)}">
        <div><div>${esc(n.message)}</div><div class="time">${fmtRel(n.created_at)}</div></div>
      </div>`).join("") : `<p class="muted" style="padding:12px">No notifications.</p>`;
  } catch { }
}
$("#bellBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const drop = $("#notifDrop");
  drop.hidden = !drop.hidden;
  if (!drop.hidden) loadNotifications();
});
document.addEventListener("click", (e) => { if (!e.target.closest(".bell-wrap")) $("#notifDrop").hidden = true; });
$("#notifDrop").addEventListener("click", async (e) => {
  const item = e.target.closest(".notif-item");
  if (!item) return;
  await api("/api/notifications/read", { method: "POST", body: JSON.stringify({ ids: [item.dataset.nid] }) });
  loadNotifications();
});

/* ------------------------------------------------------------- command palette */

function buildPalette() {
  const actions = [
    { ico: "📊", label: "Go to Dashboard", page: "dashboard", hint: "Overview" },
    { ico: "🚀", label: "New Run", page: "run", hint: "Start a scrape" },
    { ico: "💾", label: "Saved Jobs", page: "jobs", hint: "Reusable configs" },
    { ico: "⏰", label: "Schedules", page: "schedules", hint: "Intervals" },
    { ico: "🕘", label: "History", page: "history", hint: "Past runs" },
    { ico: "👥", label: "Creators", page: "creators", hint: "Profile database" },
    { ico: "📈", label: "Analytics", page: "analytics", hint: "Cross-run insights" },
    { ico: "📸", label: "Capture", page: "capture", hint: "Single URL" },
    { ico: "🔌", label: "Providers", page: "providers", hint: "API keys & tests" },
    { ico: "🛡️", label: "Proxies", page: "proxies", hint: "Pool & block rates" },
    { ico: "⚙️", label: "Settings", page: "settings", hint: "Config" },
    { ico: "❤️", label: "Health", page: "health", hint: "Status" },
  ];
  const input = $("#paletteInput");
  const render = (q) => {
    const list = actions.filter((a) => a.label.toLowerCase().includes(q.toLowerCase()));
    $("#paletteResults").innerHTML = list.length ? list.map((a, i) => `
      <div class="palette-item ${i === 0 ? "on" : ""}" data-page="${a.page}">
        <span class="p-ico">${a.ico}</span><span>${esc(a.label)}</span><span class="p-hint">${esc(a.hint)}</span>
      </div>`).join("") : `<p class="muted" style="padding:12px">No matches.</p>`;
  };
  input.addEventListener("input", () => render(input.value));
  $("#paletteResults").addEventListener("click", (e) => {
    const it = e.target.closest(".palette-item");
    if (it) { go(it.dataset.page); closePalette(); }
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const items = $$(".palette-item");
      let i = items.findIndex((x) => x.classList.contains("on"));
      if (i === -1) return;
      items[i].classList.remove("on");
      i = (i + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[i].classList.add("on"); items[i].scrollIntoView({ block: "nearest" });
    }
    if (e.key === "Enter") { const it = $(".palette-item.on"); if (it) { go(it.dataset.page); closePalette(); } }
  });
  return render;
}
let paletteRender = null;
function openPalette() {
  $("#palette").hidden = false;
  $("#paletteInput").value = "";
  paletteRender("");
  setTimeout(() => $("#paletteInput").focus(), 30);
}
function closePalette() { $("#palette").hidden = true; }
$("#searchPill").addEventListener("click", openPalette);
$("#palette").addEventListener("click", (e) => { if (e.target.id === "palette") closePalette(); });
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#palette").hidden ? openPalette() : closePalette(); }
  if (e.key === "Escape") { closePalette(); closeModal(); }
});

/* ----------------------------------------------------------------- modal */

function openModal(title, body) {
  $("#modalTitle").textContent = title;
  $("#modalBody").innerHTML = body;
  $("#modalOverlay").hidden = false;
}
function closeModal() { $("#modalOverlay").hidden = true; $("#modalBody").innerHTML = ""; }
$("#modalOverlay").addEventListener("click", (e) => { if (e.target.id === "modalOverlay") closeModal(); });

/* ----------------------------------------------------------------- misc */

function emptyState(art, msg) {
  return `<div class="empty"><div class="empty-art">${art}</div><p>${esc(msg)}</p></div>`;
}

/* ----------------------------------------------------------------- boot */

(async function boot() {
  paletteRender = buildPalette();
  buildMobileNav();
  loadHealth().catch(() => {});
  loadNotifications().catch(() => {});
  state.notifTimer = setInterval(() => { loadNotifications().catch(() => {}); }, 30000);
  updateUrlCount();

  // Deferred to DOMContentLoaded because pages.js loads after this file and
  // registers the remaining PAGE_LOADERS entries as it runs — routing before
  // that would land on a page whose loader does not exist yet.
  const start = () => {
    const wanted = location.hash.slice(1);
    go(document.getElementById("page-" + wanted) ? wanted : "dashboard");
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();

window.addEventListener("hashchange", () => {
  const page = location.hash.slice(1);
  if (page && page !== state.page && document.getElementById("page-" + page)) go(page);
});