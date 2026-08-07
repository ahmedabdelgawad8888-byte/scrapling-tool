/* ==========================================================================
   Scrapling Tool — dashboard client
   Talks to the FastAPI backend, streams job events over SSE, and renders
   results in three layouts. No build step and no external dependencies.
   ========================================================================== */
"use strict";

const $  = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const state = {
  view: "scrape",
  layout: "cards",
  mode: "http",
  health: null,
  jobId: null,
  source: null,     // EventSource for the running job
  results: [],
  runId: null,
  filter: "",
};

/* ------------------------------------------------------------------ utils */

/** Escape before interpolating — every field here came off a scraped page. */
function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/**
 * Return the URL only if it is safe to put in an href.
 *
 * These values come off scraped third-party pages. The backend canonicaliser
 * already forces an http(s) scheme, but link rendering shouldn't depend on
 * that holding for every field of every future parser.
 */
function safeUrl(value) {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  try {
    return /^https?:$/.test(new URL(raw, location.origin).protocol) ? raw : "";
  } catch {
    return "";
  }
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

function toast(message, kind = "") {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 5200);
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
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

/* ------------------------------------------------------------------- tabs */

// Views that own the shared results panel. Capture and Schedules render their
// own output, so leaving the last run's table under them reads as if it
// belonged to that screen.
const RESULT_VIEWS = new Set(["scrape", "discover", "lookalike", "posts", "mentions", "history"]);

function syncResultsVisibility() {
  const belongs = RESULT_VIEWS.has(state.view);
  $("#results").hidden = !belongs || !state.results.length;
  $("#empty").hidden = !belongs || state.results.length > 0 || state.view === "history";
}

$("#tabs").addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) return;
  state.view = tab.dataset.view;
  $$(".tab").forEach((t) => t.classList.toggle("is-active", t === tab));
  $$(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === state.view));
  syncResultsVisibility();
  if (state.view === "history") loadHistory();
  if (state.view === "schedules") loadSchedules();
});

/* ------------------------------------------------------------- fetch mode */

const MODE_HINTS = {
  http: "Fast impersonated HTTP via Scrapling's AsyncFetcher.",
  browser: "Real Chromium through Scrapling's DynamicFetcher.",
  stealth: "Anti-detection browser — slowest, beats most bot walls.",
};

$("#modeGroup").addEventListener("click", (event) => {
  const btn = event.target.closest(".seg");
  if (!btn || btn.disabled) return;
  state.mode = btn.dataset.mode;
  $$("#modeGroup .seg").forEach((s) => {
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

/* ----------------------------------------------------------------- health */

async function loadHealth() {
  try {
    state.health = await api("/api/health");
  } catch (err) {
    toast(`Cannot reach the backend: ${err.message}`, "error");
    return;
  }
  const h = state.health;

  $("#healthPills").innerHTML = [
    `<span class="pill ${h.scrapling ? "on" : "off"}">Scrapling</span>`,
    `<span class="pill ${h.browser ? "on" : "off"}">${h.browser ? "Chromium" : "No browser"}</span>`,
  ].join("");

  // Never offer a mode this host cannot actually run.
  $$("#modeGroup .seg").forEach((seg) => {
    const usable = h.modes.includes(seg.dataset.mode);
    seg.disabled = !usable;
    seg.title = usable ? "" : "Chromium is not available on this host";
  });
  if (!h.modes.includes(state.mode)) $("#modeGroup .seg").click();
  if (!h.browser) $("#autoEscalate").checked = false;

  buildChips("#discPlatforms", h.platforms, ["tiktok", "instagram"]);
  buildChips("#lookPlatforms", h.platforms, h.platforms);
  buildChips("#postPlatforms", h.post_platforms, ["tiktok", "instagram"]);
  buildChips("#menPlatforms", h.post_platforms, h.post_platforms);

  $("#discTarget").innerHTML = h.targets
    .map((t) => `<option value="${esc(t)}">${esc(t[0].toUpperCase() + t.slice(1))}</option>`)
    .join("");

  const s = h.store || {};
  $("#storeStat").textContent = `${s.runs || 0} runs · ${s.results || 0} rows saved`;
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

const chipValues = (selector) => $$(`${selector} .chip.on`).map((c) => c.dataset.value);

/* -------------------------------------------------------------- form data */

function paramsFor(kind) {
  switch (kind) {
    case "scrape":
      return {
        urls: $("#scrapeUrls").value.split("\n").map((s) => s.trim()).filter(Boolean),
      };
    case "discover":
      return {
        keywords: $("#discKeywords").value,
        target: $("#discTarget").value,
        platforms: chipValues("#discPlatforms"),
        per_platform: +$("#discPer").value,
        limit: +$("#discLimit").value,
        location: $("#discLocation").value,
        min_followers: +$("#discMinF").value,
        max_followers: +$("#discMaxF").value,
        bio_keyword: $("#discBio").value,
        verified_only: $("#discVerified").checked,
      };
    case "lookalike":
      return {
        seeds: $("#lookSeeds").value.split("\n").map((s) => s.trim()).filter(Boolean),
        platforms: chipValues("#lookPlatforms"),
        per_platform: +$("#lookPer").value,
        limit: +$("#lookLimit").value,
        min_score: +$("#lookScore").value,
        signals: $("#lookSignals").value,
      };
    case "posts":
      return {
        usernames: $("#postUsers").value,
        terms: $("#postTerms").value,
        platforms: chipValues("#postPlatforms"),
        per_platform: +$("#postPer").value,
        deep: $("#postDeep").checked,
      };
    case "mentions":
      return {
        seeds: $("#menSeeds").value,
        terms: $("#menTerms").value,
        platforms: chipValues("#menPlatforms"),
        per_platform: +$("#menPer").value,
        recency: $("#menRecency").value,
      };
    default:
      return {};
  }
}

/* --------------------------------------------------------------- URL file */

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
    // Covers .txt and .csv alike: pull anything that looks like a link.
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

/* ------------------------------------------------------------------- jobs */

$$("[data-run]").forEach((btn) => {
  btn.addEventListener("click", () => startJob(btn.dataset.run, btn));
});

async function startJob(kind, button) {
  if (state.source) { toast("A run is already in progress.", "warn"); return; }

  const params = paramsFor(kind);
  if (kind === "scrape" && !params.urls.length) { toast("Add at least one URL.", "warn"); return; }
  if (kind === "lookalike" && !params.seeds.length) { toast("Add at least one seed URL.", "warn"); return; }

  button && (button.disabled = true);
  state.results = [];
  state.filter = "";
  $("#filterBox").value = "";
  $("#empty").hidden = true;
  $("#results").hidden = false;
  $("#logBox").hidden = false;
  $("#logBox").innerHTML = "";
  $("#resultsTitle").textContent = kind[0].toUpperCase() + kind.slice(1) + " results";
  renderStats({ total: 0, done: 0, ok: 0, blocked: 0, errors: 0 });
  renderResults();

  let job;
  try {
    job = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ kind, options: options(), params }),
    });
  } catch (err) {
    toast(`Could not start: ${err.message}`, "error");
    button && (button.disabled = false);
    return;
  }

  state.jobId = job.job_id;
  $("#runBar").hidden = false;
  $("#progressFill").style.width = "0%";
  $("#runLabel").textContent = "Starting…";

  const source = new EventSource(`/api/jobs/${job.job_id}/events`);
  state.source = source;

  source.onmessage = (event) => {
    const data = JSON.parse(event.data);
    handleEvent(data);
    if (data.type === "finished") {
      source.close();
      state.source = null;
      button && (button.disabled = false);
      $("#runBar").hidden = true;
      const noun = data.status === "cancelled"
        ? `Cancelled after ${data.kept} of ${data.total}`
        : "Finished";
      toast(`${noun} — ${data.ok} ok, ${data.blocked} blocked, ${data.errors} errors.`,
            data.status === "error" ? "error" : "ok");
      loadHealth();
    }
  };

  source.onerror = () => {
    // The stream also ends normally when the job finishes; only complain if
    // we never reached a terminal event.
    if (state.source) {
      source.close();
      state.source = null;
      button && (button.disabled = false);
      $("#runBar").hidden = true;
      toast("Lost the event stream. The run may still be going — check History.", "warn");
    }
  };
}

function handleEvent(event) {
  switch (event.type) {
    case "started":
      state.runId = event.run_id || state.runId;
      $("#runLabel").textContent = event.total ? `0 / ${event.total}` : "Working…";
      if (event.discovered) {
        log(`Found ${Object.entries(event.discovered).map(([k, v]) => `${v} on ${k}`).join(", ")}`);
      }
      if (event.creators !== undefined) log(`${event.creators} creators, ${event.posts} posts`);
      break;

    case "progress": {
      if (event.result) state.results.push(event.result);
      const pct = event.total ? (event.done / event.total) * 100 : 0;
      $("#progressFill").style.width = `${pct}%`;
      $("#runLabel").textContent = `${event.done} / ${event.total}`;
      renderStats(liveStats());
      renderResults();
      break;
    }

    case "log":
      log(event.message, event.level);
      break;

    case "finished":
      state.runId = event.run_id || state.runId;
      renderStats(liveStats());
      renderResults();
      break;
  }
}

function liveStats() {
  const blocked = state.results.filter((r) => r.blocked).length;
  const ok = state.results.filter((r) => !r.blocked && !r.error && r.status === 200).length;
  return {
    total: state.results.length,
    ok,
    blocked,
    errors: state.results.length - ok - blocked,
    withData: state.results.filter((r) => r.followers).length,
  };
}

function log(message, level = "") {
  const box = $("#logBox");
  const line = document.createElement("div");
  line.className = level;
  line.textContent = message;
  box.append(line);
  box.scrollTop = box.scrollHeight;
}

$("#cancelBtn").addEventListener("click", async () => {
  if (!state.jobId) return;
  try {
    await api(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    toast("Stopping… partial results are kept.", "warn");
  } catch (err) {
    toast(err.message, "error");
  }
});

/* -------------------------------------------------------------- rendering */

function renderStats(s) {
  $("#stats").innerHTML = [
    ["Total", s.total, ""],
    ["OK", s.ok, "ok"],
    ["Blocked", s.blocked, "blocked"],
    ["Errors", s.errors, "err"],
  ].map(([k, v, cls]) =>
    `<div class="stat ${cls}"><div class="stat-v">${v}</div><div class="stat-k">${k}</div></div>`
  ).join("");
}

$("#viewGroup").addEventListener("click", (event) => {
  const btn = event.target.closest(".seg");
  if (!btn) return;
  state.layout = btn.dataset.layout;
  $$("#viewGroup .seg").forEach((s) => s.classList.toggle("is-active", s === btn));
  renderResults();
});

$("#filterBox").addEventListener("input", (event) => {
  state.filter = event.target.value.toLowerCase();
  renderResults();
});

function visibleResults() {
  if (!state.filter) return state.results;
  return state.results.filter((r) =>
    JSON.stringify(r).toLowerCase().includes(state.filter));
}

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

function renderResults() {
  const rows = visibleResults();
  const body = $("#resultsBody");
  if (!rows.length) {
    body.innerHTML = `<p class="muted">${state.results.length ? "Nothing matches that filter." : "Waiting for results…"}</p>`;
    return;
  }
  if (state.layout === "cards") body.innerHTML = `<div class="cards">${rows.map(card).join("")}</div>`;
  else if (state.layout === "table") body.innerHTML = table(rows);
  else body.innerHTML = `<div class="rows">${rows.map(listRow).join("")}</div>`;
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
    <div class="card-head">
      ${avatar(r)}
      <div class="card-id">
        <div class="card-name">${name}</div>
        ${r.username ? `<div class="card-handle">@${esc(r.username)}</div>` : ""}
      </div>
      ${r.platform ? `<span class="tagchip">${esc(r.platform)}</span>` : ""}
    </div>
    ${metrics ? `<div class="metrics">${metrics}</div>` : ""}
    ${r.biography ? `<p class="bio">${esc(r.biography)}</p>` : ""}
    ${r.emails?.length ? `<div class="card-handle">✉️ ${esc(r.emails.slice(0, 2).join(", "))}</div>` : ""}
    ${r.blocked || r.error ? `<div class="bio">${esc(r.error || r.blocked)}</div>` : ""}
    <div class="card-foot">
      <span class="state ${cls}">${label}</span>
      ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open ↗</a>` : ""}
    </div>
  </article>`;
}

const COLUMNS = [
  ["State",     (r) => `<span class="state ${stateOf(r)[0]}">${stateOf(r)[1]}</span>`],
  ["Platform",  (r) => esc(r.platform ?? "")],
  ["Username",  (r) => esc(r.username ?? "")],
  ["Name",      (r) => esc(r.full_name ?? r.title ?? "")],
  ["Followers", (r) => `<span class="num">${esc(fmtCount(r.followers))}</span>`],
  ["Following", (r) => `<span class="num">${esc(fmtCount(r.following))}</span>`],
  ["Likes",     (r) => `<span class="num">${esc(fmtCount(r.likes))}</span>`],
  ["Verified",  (r) => (r.is_verified ? "✓" : "")],
  ["Emails",    (r) => esc((r.emails || []).join(", "))],
  ["Bio",       (r) => `<span class="wide">${esc((r.biography || "").slice(0, 90))}</span>`],
  ["URL",       (r) => {
    const u = safeUrl(r.profile_url || r.url);
    return u ? `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">link</a>` : "";
  }],
  ["Time",      (r) => `<span class="num">${esc(r.response_time ?? "")}</span>`],
];

function table(rows) {
  return `<div class="tablewrap"><table>
    <thead><tr>${COLUMNS.map(([h]) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) =>
      `<tr>${COLUMNS.map(([, cell]) => `<td>${cell(r)}</td>`).join("")}</tr>`).join("")}
    </tbody></table></div>`;
}

function listRow(r) {
  const [cls, label] = stateOf(r);
  const url = safeUrl(r.profile_url || r.url);
  const bits = [
    r.username ? `@${r.username}` : "",
    r.followers ? `${fmtCount(r.followers)} followers` : "",
    (r.biography || "").slice(0, 70),
  ].filter(Boolean).join(" · ");
  return `
  <div class="row">
    ${avatar(r)}
    <div class="row-main">
      <div class="card-name">${esc(r.full_name || r.title || r.username || "(untitled)")}</div>
      <div class="row-sub">${esc(bits)}</div>
    </div>
    <span class="state ${cls}">${label}</span>
    ${r.platform ? `<span class="tagchip">${esc(r.platform)}</span>` : ""}
    ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">↗</a>` : ""}
  </div>`;
}

/* ----------------------------------------------------------------- export */

$("#exportBtn").addEventListener("click", () => {
  const fmt = $("#exportFmt").value;
  if (state.runId) { window.location = `/api/runs/${state.runId}/export?format=${fmt}`; return; }
  if (state.jobId) { window.location = `/api/jobs/${state.jobId}/export?format=${fmt}`; return; }
  toast("Nothing to export yet.", "warn");
});

/* ---------------------------------------------------------------- capture */

$("#capBtn").addEventListener("click", async () => {
  const url = $("#capUrl").value.trim();
  if (!url) { toast("Enter a URL to capture.", "warn"); return; }
  const btn = $("#capBtn");
  btn.disabled = true;
  btn.textContent = "Capturing…";
  $("#captureOut").innerHTML = "";
  try {
    const data = await api("/api/capture", {
      method: "POST",
      body: JSON.stringify({
        url,
        mode: $("#capMode").value,
        timeout: +$("#timeout").value || 40,
        full_page: $("#capFull").checked,
        screenshot: $("#capShot").checked,
        proxy: $("#proxy").value.trim(),
      }),
    });
    renderCapture(data);
  } catch (err) {
    toast(`Capture failed: ${err.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Capture";
  }
});

function renderCapture(data) {
  const p = data.parsed || {};
  const facts = [
    ["Status", data.status], ["Mode", data.mode],
    ["Elapsed", `${data.elapsed}s`], ["HTML", `${(data.html_bytes / 1024).toFixed(0)} KB`],
    ["Blocked", data.blocked || "no"],
  ].map(([k, v]) =>
    `<div class="stat"><div class="stat-v" style="font-size:15px">${esc(v)}</div><div class="stat-k">${k}</div></div>`
  ).join("");

  $("#captureOut").innerHTML = `
    <div class="stats">${facts}</div>
    ${data.blocked ? `<div class="toast warn" style="position:static;max-width:none">
        This page was served as a block (<code>${esc(data.blocked)}</code>) — the HTML below
        is the challenge page, not real content.</div>` : ""}
    ${data.screenshot ? `<div class="shot"><img alt="Screenshot of ${esc(data.url)}"
        src="data:image/png;base64,${data.screenshot}"></div>` : ""}
    <div>
      <h2 style="font-size:15px;margin-bottom:8px">Parsed fields</h2>
      <pre class="codebox">${esc(JSON.stringify(p, null, 2))}</pre>
    </div>
    <div>
      <h2 style="font-size:15px;margin-bottom:8px">Raw HTML</h2>
      <pre class="codebox">${esc(data.html.slice(0, 60000))}</pre>
    </div>`;
}

/* ---------------------------------------------------------------- history */

async function loadHistory() {
  const host = $("#historyOut");
  host.innerHTML = `<p class="muted">Loading…</p>`;
  try {
    const { runs } = await api("/api/runs");
    if (!runs.length) { host.innerHTML = `<p class="muted">No runs saved yet.</p>`; return; }
    host.innerHTML = runs.map((r) => `
      <div class="hrow">
        <div class="hrow-main">
          <div class="hrow-title">${esc(r.label || r.kind)} <span class="badge ${esc(r.status)}">${esc(r.status)}</span></div>
          <div class="hrow-sub">${fmtTime(r.started_at)} · ${esc(r.id)}</div>
        </div>
        <div class="hrow-counts">
          <span><b>${r.total}</b> rows</span>
          <span class="state ok">${r.ok} ok</span>
          <span class="state blocked">${r.blocked} blocked</span>
        </div>
        <div class="hrow-actions">
          <button class="btn btn-ghost btn-sm" data-open="${esc(r.id)}">Open</button>
          <button class="btn btn-ghost btn-sm" data-csv="${esc(r.id)}">CSV</button>
          <button class="btn btn-ghost btn-sm" data-del="${esc(r.id)}">Delete</button>
        </div>
      </div>`).join("");
  } catch (err) {
    host.innerHTML = `<p class="muted">Could not load history: ${esc(err.message)}</p>`;
  }
}

$("#historyOut").addEventListener("click", async (event) => {
  const btn = event.target.closest("button");
  if (!btn) return;
  const { open, csv, del } = btn.dataset;
  if (csv) { window.location = `/api/runs/${csv}/export?format=csv`; return; }
  if (del) {
    if (!confirm("Delete this run and its saved results?")) return;
    await api(`/api/runs/${del}`, { method: "DELETE" });
    loadHistory(); loadHealth();
    return;
  }
  if (open) {
    const run = await api(`/api/runs/${open}`);
    state.results = run.results || [];
    state.runId = run.id;
    state.jobId = null;
    state.filter = "";
    $("#filterBox").value = "";
    $("#resultsTitle").textContent = `${run.kind} · ${run.id}`;
    $("#empty").hidden = true;
    $("#results").hidden = false;
    $("#logBox").hidden = true;
    renderStats(liveStats());
    renderResults();
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  }
});

/* -------------------------------------------------------------- schedules */

$("#schedBtn").addEventListener("click", async () => {
  const name = $("#schedName").value.trim();
  const kind = $("#schedKind").value;
  if (!name) { toast("Give the schedule a name.", "warn"); return; }
  try {
    await api("/api/schedules", {
      method: "POST",
      body: JSON.stringify({
        name, kind,
        interval_min: +$("#schedEvery").value || 1440,
        options: options(),
        params: paramsFor(kind),
      }),
    });
    $("#schedName").value = "";
    toast("Schedule created.", "ok");
    loadSchedules();
  } catch (err) {
    toast(err.message, "error");
  }
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
          </div>
          <div class="hrow-sub">${esc(s.kind)} · every ${s.interval_min} min · next ${fmtTime(s.next_run_at)}</div>
        </div>
        <div class="hrow-actions">
          <button class="btn btn-ghost btn-sm" data-run-now="${esc(s.id)}">Run now</button>
          <button class="btn btn-ghost btn-sm" data-toggle="${esc(s.id)}" data-enabled="${s.enabled}">
            ${s.enabled ? "Pause" : "Resume"}</button>
          <button class="btn btn-ghost btn-sm" data-del-sched="${esc(s.id)}">Delete</button>
        </div>
      </div>`).join("");
  } catch (err) {
    host.innerHTML = `<p class="muted">Could not load schedules: ${esc(err.message)}</p>`;
  }
}

$("#schedulesOut").addEventListener("click", async (event) => {
  const btn = event.target.closest("button");
  if (!btn) return;
  const { runNow, toggle, enabled, delSched } = btn.dataset;
  try {
    if (runNow) { await api(`/api/schedules/${runNow}/run`, { method: "POST" }); toast("Started.", "ok"); }
    if (toggle) await api(`/api/schedules/${toggle}/toggle?enabled=${enabled !== "true"}`, { method: "POST" });
    if (delSched) {
      if (!confirm("Delete this schedule?")) return;
      await api(`/api/schedules/${delSched}`, { method: "DELETE" });
    }
    loadSchedules();
  } catch (err) {
    toast(err.message, "error");
  }
});

/* ------------------------------------------------------------------- boot */

loadHealth();
updateUrlCount();
