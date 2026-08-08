"use strict";

/* ==========================================================================
   Creators, Analytics, Providers and Proxies.

   Loaded after app.js and sharing its global helpers ($, esc, api, toast, go,
   fmtCount, fmtRel, PAGE_LOADERS). Kept in a separate file because app.js
   already owns the run pipeline, and one file holding both would be the
   largest thing in the project by a wide margin.
   ========================================================================== */

/* ---------------------------------------------------------------- helpers */

function debounce(fn, ms = 250) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function pct(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

/** A bar plus its number. Colour alone never carries the meaning. */
function meter(value, max = 100, tone = "") {
  const width = Math.max(0, Math.min(100, (Number(value) || 0) / (max || 1) * 100));
  return `<div class="meter-row">
    <div class="meter ${tone}"><span style="width:${width.toFixed(1)}%"></span></div>
    <span class="meter-val">${esc(String(value))}</span>
  </div>`;
}

function skeletonTable(rows = 8) {
  return `<div class="table-wrap" aria-busy="true" aria-label="Loading">
    ${Array.from({ length: rows }, () => `<div class="skel skel-row"></div>`).join("")}
  </div>`;
}

function emptyBlock({ art = "🗂️", title, body, action = "" }) {
  return `<div class="empty-state" role="status">
    <div class="art" aria-hidden="true">${art}</div>
    <h3>${esc(title)}</h3>
    <p>${esc(body)}</p>
    ${action}
  </div>`;
}

function errorBlock(message, retryId = "") {
  return `<div class="error-state" role="alert">
    <strong>Could not load this view</strong>
    ${esc(message)}
    ${retryId ? `<div style="margin-top:12px"><button class="btn btn-sm" id="${retryId}">Try again</button></div>` : ""}
  </div>`;
}

/** Initials fallback so a row never renders as a broken image. */
function avatarCell(record) {
  const url = safeUrl(record.avatar_url);
  const initials = (record.username || "?").slice(0, 2).toUpperCase();
  const inner = url
    ? `<img src="${esc(url)}" alt="" loading="lazy" referrerpolicy="no-referrer"
            onerror="this.replaceWith(document.createTextNode('${esc(initials)}'))">`
    : esc(initials);
  return `<span class="who-av" aria-hidden="true">${inner}</span>`;
}

/* ------------------------------------------------------------------ drawer */

let drawerClickHandler = null;
let drawerKeyHandler = null;

function openDrawer(title, subtitle, bodyHtml, footHtml = "") {
  const root = $("#drawerRoot");
  root.hidden = false;
  root.innerHTML = `
    <div class="drawer-scrim" data-close="1"></div>
    <aside class="drawer" role="dialog" aria-modal="true" aria-label="${esc(title)}">
      <header class="drawer-head">
        <div style="min-width:0">
          <h2>${esc(title)}</h2>
          ${subtitle ? `<p class="muted" style="font-size:13px">${esc(subtitle)}</p>` : ""}
        </div>
        <span class="spacer"></span>
        <button class="icon-btn" data-close="1" aria-label="Close">✕</button>
      </header>
      <div class="drawer-body">${bodyHtml}</div>
      ${footHtml ? `<footer class="drawer-foot">${footHtml}</footer>` : ""}
    </aside>`;

  if (drawerClickHandler) root.removeEventListener("click", drawerClickHandler);
  drawerClickHandler = (e) => {
    if (e.target.closest("[data-close]")) closeDrawer();
  };
  root.addEventListener("click", drawerClickHandler);

  // Focus moves into the panel, and Escape closes it — a dialog that traps a
  // keyboard user is worse than no dialog.
  const first = root.querySelector(".drawer-body button, .drawer-body input, .drawer [data-close]");
  if (first) setTimeout(() => first.focus(), 40);

  if (drawerKeyHandler) document.removeEventListener("keydown", drawerKeyHandler);
  drawerKeyHandler = (e) => { if (e.key === "Escape") closeDrawer(); };
  document.addEventListener("keydown", drawerKeyHandler);
}

function closeDrawer() {
  const root = $("#drawerRoot");
  root.hidden = true;
  root.innerHTML = "";
  if (drawerClickHandler) {
    root.removeEventListener("click", drawerClickHandler);
    drawerClickHandler = null;
  }
  if (drawerKeyHandler) {
    document.removeEventListener("keydown", drawerKeyHandler);
    drawerKeyHandler = null;
  }
}

/* ==========================================================================
   Creators
   ========================================================================== */

const creators = {
  items: [],
  total: 0,
  offset: 0,
  limit: 50,
  selected: new Set(),
  facets: { platforms: [], tags: [], lists: [] },
  sort: "last_seen",
  order: "desc",
  loading: false,
};

function creatorQuery() {
  const params = new URLSearchParams({
    q: $("#creSearch").value.trim(),
    platform: $("#crePlatform").value,
    tag: $("#creTag").value,
    list_id: $("#creList").value,
    sort: creators.sort,
    order: creators.order,
    limit: String(creators.limit),
    offset: String(creators.offset),
  });
  if ($("#creContact").checked) params.set("has_contact", "true");
  if ($("#creVerified").checked) params.set("verified_only", "true");
  return params;
}

async function loadCreators() {
  if (creators.loading) return;
  creators.loading = true;
  $("#creTable").innerHTML = skeletonTable();

  try {
    const [page, facets] = await Promise.all([
      api(`/api/creators?${creatorQuery()}`),
      api("/api/creators/facets"),
    ]);
    creators.items = page.items;
    creators.total = page.total;
    creators.facets = facets;
    renderCreatorFilters();
    renderCreatorTable();
    loadCreatorStats().catch(() => {});
  } catch (err) {
    $("#creTable").innerHTML = errorBlock(err.message, "creRetry");
    const retry = $("#creRetry");
    if (retry) retry.addEventListener("click", () => loadCreators());
  } finally {
    creators.loading = false;
  }
}

async function loadCreatorStats() {
  const data = await api("/api/analytics?days=14");
  const c = data.creators;
  const reach = c.total ? Math.round(c.reachable / c.total * 100) : 0;
  $("#creStats").innerHTML = [
    kpi("Creators tracked", fmtCount(c.total) || "0", "deduplicated across all runs"),
    kpi("Reachable", fmtCount(c.reachable) || "0", `${reach}% have an email or phone`, reach >= 40 ? "ok" : ""),
    kpi("Verified", fmtCount(c.verified) || "0", "platform-verified accounts"),
    kpi("Avg engagement", pct(c.avg_engagement), "views or likes per follower"),
    kpi("Avg quality", String(c.avg_quality), "reachability and completeness"),
  ].join("");
}

function renderCreatorFilters() {
  const fill = (sel, options, allLabel) => {
    const el = $(sel);
    const current = el.value;
    el.innerHTML = `<option value="">${allLabel}</option>` + options;
    el.value = current;
  };
  fill("#crePlatform", creators.facets.platforms
    .map((p) => `<option value="${esc(p.platform)}">${esc(p.platform)} (${p.n})</option>`).join(""),
    "All platforms");
  fill("#creTag", creators.facets.tags
    .map((t) => `<option value="${esc(t.tag)}">${esc(t.tag)} (${t.n})</option>`).join(""),
    "All tags");
  fill("#creList", creators.facets.lists
    .map((l) => `<option value="${esc(l.id)}">${esc(l.name)} (${l.count})</option>`).join(""),
    "All lists");
}

const CREATOR_COLUMNS = [
  { key: "username", label: "Creator", sortable: true },
  { key: "followers", label: "Followers", sortable: true, num: true },
  { key: "engagement_rate", label: "Engagement", sortable: true, num: true, small: true },
  { key: "quality", label: "Quality", sortable: true, num: true },
  { key: "contact", label: "Contact", small: true },
  { key: "tags", label: "Tags", small: true },
  { key: "seen_count", label: "Seen", sortable: true, num: true, small: true },
  { key: "last_seen", label: "Last seen", sortable: true, small: true },
];

function renderCreatorTable() {
  const host = $("#creTable");

  if (!creators.total) {
    const filtered = $("#creSearch").value || $("#crePlatform").value ||
      $("#creTag").value || $("#creList").value ||
      $("#creContact").checked || $("#creVerified").checked;
    host.innerHTML = emptyBlock(filtered
      ? {
        art: "🔍",
        title: "Nothing matches those filters",
        body: "Loosen a filter, or clear them all to see everything collected so far.",
        action: `<button class="btn btn-sm" id="creClearFilters">Clear filters</button>`,
      }
      : {
        art: "👥",
        title: "No creators yet",
        body: "Creators appear here automatically after a run finishes. Start a scrape or a discovery run and come back.",
        action: `<button class="btn btn-primary btn-sm" id="creGoRun">Start a run</button>`,
      });
    const clear = $("#creClearFilters");
    if (clear) clear.addEventListener("click", resetCreatorFilters);
    const goRun = $("#creGoRun");
    if (goRun) goRun.addEventListener("click", () => go("run"));
    $("#crePager").innerHTML = "";
    updateBulkBar();
    return;
  }

  const head = CREATOR_COLUMNS.map((col) => {
    const active = creators.sort === col.key;
    const sortAttr = active ? ` aria-sort="${creators.order === "asc" ? "ascending" : "descending"}"` : "";
    const cls = [col.sortable ? "sortable" : "", col.num ? "num" : "", col.small ? "hide-sm" : ""]
      .filter(Boolean).join(" ");
    return `<th class="${cls}"${sortAttr} ${col.sortable ? `data-sort="${col.key}"` : ""} scope="col">
      ${esc(col.label)}${col.sortable ? `<span class="sort-ind" aria-hidden="true">${active ? (creators.order === "asc" ? "▲" : "▼") : "↕"}</span>` : ""}
    </th>`;
  }).join("");

  const allSelected = creators.items.every((c) => creators.selected.has(c.id));

  host.innerHTML = `
    <div class="table-wrap">
      <table class="dt">
        <thead><tr>
          <th class="col-check" scope="col">
            <input type="checkbox" id="creSelAll" ${allSelected ? "checked" : ""}
                   aria-label="Select all rows on this page">
          </th>
          ${head}
        </tr></thead>
        <tbody>${creators.items.map(creatorRow).join("")}</tbody>
      </table>
    </div>`;

  host.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (creators.sort === key) {
        creators.order = creators.order === "asc" ? "desc" : "asc";
      } else {
        creators.sort = key;
        creators.order = "desc";
      }
      creators.offset = 0;
      loadCreators();
    });
  });

  $("#creSelAll").addEventListener("change", (e) => {
    creators.items.forEach((c) => {
      if (e.target.checked) creators.selected.add(c.id);
      else creators.selected.delete(c.id);
    });
    renderCreatorTable();
  });

  host.querySelectorAll("input[data-pick]").forEach((box) => {
    box.addEventListener("change", () => {
      if (box.checked) creators.selected.add(box.dataset.pick);
      else creators.selected.delete(box.dataset.pick);
      box.closest("tr").classList.toggle("is-selected", box.checked);
      updateBulkBar();
    });
  });

  host.querySelectorAll("[data-open]").forEach((cell) => {
    cell.addEventListener("click", () => openCreator(cell.dataset.open));
  });

  renderCreatorPager();
  updateBulkBar();
}

function creatorRow(c) {
  const picked = creators.selected.has(c.id);
  const contact = [
    c.emails.length ? `✉ ${c.emails.length}` : "",
    c.phones.length ? `☎ ${c.phones.length}` : "",
    (c.links || []).some((l) => l.kind === "aggregator") ? "🔗" : "",
  ].filter(Boolean).join(" ");

  const qualityTone = c.quality >= 70 ? "ok" : c.quality >= 40 ? "warn" : "err";

  return `<tr class="${picked ? "is-selected" : ""}">
    <td class="col-check">
      <input type="checkbox" data-pick="${esc(c.id)}" ${picked ? "checked" : ""}
             aria-label="Select ${esc(c.username)}">
    </td>
    <td>
      <button class="who" data-open="${esc(c.id)}" style="background:none;border:none;padding:0;cursor:pointer;color:inherit;text-align:left;width:100%">
        ${avatarCell(c)}
        <span class="who-txt">
          <span class="who-name">${esc(c.username)}${c.is_verified ? ' <span title="Verified" aria-label="Verified">✔</span>' : ""}</span>
          <span class="who-sub">${esc(c.platform)}${c.full_name ? ` · ${esc(c.full_name)}` : ""}</span>
        </span>
      </button>
    </td>
    <td class="num">${esc(fmtCount(c.followers) || "—")}</td>
    <td class="num hide-sm">${c.engagement_rate ? pct(c.engagement_rate) : "—"}</td>
    <td class="num"><div class="q-score"><b>${c.quality}</b>${meter(c.quality, 100, qualityTone).replace(/<span class="meter-val">.*?<\/span>/, "")}</div></td>
    <td class="hide-sm nowrap">${contact ? esc(contact) : '<span class="muted">—</span>'}</td>
    <td class="hide-sm">${(c.tags || []).slice(0, 3).map((t) => `<span class="tagchip">${esc(t)}</span>`).join(" ") || '<span class="muted">—</span>'}</td>
    <td class="num hide-sm">${c.seen_count}</td>
    <td class="hide-sm nowrap muted">${esc(fmtRel(c.last_seen))}</td>
  </tr>`;
}

function renderCreatorPager() {
  const from = creators.offset + 1;
  const to = Math.min(creators.offset + creators.limit, creators.total);
  $("#creCount").textContent = `${from}–${to} of ${fmtCount(creators.total) || creators.total}`;

  const prev = creators.offset > 0;
  const next = creators.offset + creators.limit < creators.total;
  $("#crePager").innerHTML = `
    <button class="btn btn-sm" id="crePrev" ${prev ? "" : "disabled"}>← Previous</button>
    <span class="muted" style="font-size:12px">Page ${Math.floor(creators.offset / creators.limit) + 1}</span>
    <button class="btn btn-sm" id="creNext" ${next ? "" : "disabled"}>Next →</button>`;

  if (prev) $("#crePrev").addEventListener("click", () => {
    creators.offset = Math.max(0, creators.offset - creators.limit);
    loadCreators();
  });
  if (next) $("#creNext").addEventListener("click", () => {
    creators.offset += creators.limit;
    loadCreators();
  });
}

function updateBulkBar() {
  const n = creators.selected.size;
  $("#creBulk").hidden = n === 0;
  $("#creToolbar").hidden = n > 0;
  $("#creSelCount").textContent = `${n} selected`;
}

function resetCreatorFilters() {
  $("#creSearch").value = "";
  $("#crePlatform").value = "";
  $("#creTag").value = "";
  $("#creList").value = "";
  $("#creContact").checked = false;
  $("#creVerified").checked = false;
  creators.offset = 0;
  loadCreators();
}

async function bulkAction(action) {
  const ids = [...creators.selected];
  if (!ids.length) return;

  let payload = { ids, action, tags: [], list_id: "" };

  if (action === "tag") {
    const tag = prompt(`Tag ${ids.length} creator(s) with:`);
    if (!tag || !tag.trim()) return;
    payload.tags = tag.split(",").map((t) => t.trim()).filter(Boolean);
  } else if (action === "list_add" || action === "list_remove") {
    const lists = creators.facets.lists;
    if (!lists.length) {
      toast("Create a list first.", "warn");
      return;
    }
    const choice = prompt(
      `Which list?\n\n${lists.map((l, i) => `${i + 1}. ${l.name}`).join("\n")}\n\nEnter a number:`
    );
    const index = parseInt(choice, 10) - 1;
    if (!(index >= 0 && index < lists.length)) return;
    payload.list_id = lists[index].id;
  } else if (action === "delete") {
    if (!confirm(`Delete ${ids.length} creator(s) from the database? Run history is not affected.`)) return;
  }

  try {
    const res = await api("/api/creators/bulk", { method: "POST", body: JSON.stringify(payload) });
    toast(`Done — ${res.affected} change(s).`, "ok");
    creators.selected.clear();
    loadCreators();
  } catch (err) {
    toast(err.message, "error");
  }
}

async function openCreator(id) {
  try {
    const c = await api(`/api/creators/${encodeURIComponent(id)}`);
    const growth = c.history.length > 1
      ? `${fmtCount(c.history[0].followers)} → ${fmtCount(c.history[c.history.length - 1].followers)} over ${c.history.length} observations`
      : "Only one observation so far — run this profile again to see movement.";

    const links = (c.links || []).map((l) =>
      `<li><a href="${esc(safeUrl(l.url))}" target="_blank" rel="noopener noreferrer">${esc(l.host)}</a> <span class="muted">${esc(l.kind)}</span></li>`
    ).join("") || "<li class='muted'>None found</li>";

    openDrawer(
      `@${c.username}`,
      `${c.platform}${c.full_name ? " · " + c.full_name : ""}`,
      `<dl class="kv">
        <dt>Followers</dt><dd>${esc(fmtCount(c.followers) || "—")}</dd>
        <dt>Following</dt><dd>${esc(fmtCount(c.following) || "—")}</dd>
        <dt>Likes</dt><dd>${esc(fmtCount(c.likes) || "—")}</dd>
        <dt>Engagement</dt><dd>${c.engagement_rate ? pct(c.engagement_rate) : "—"}</dd>
        <dt>Quality</dt><dd>${c.quality} / 100</dd>
        <dt>Verified</dt><dd>${c.is_verified ? "Yes" : "No"}</dd>
        <dt>Private</dt><dd>${c.is_private ? "Yes" : "No"}</dd>
        <dt>Location</dt><dd>${esc([c.city, c.country].filter(Boolean).join(", ") || "—")}</dd>
        <dt>Emails</dt><dd>${c.emails.map((e) => `<a href="mailto:${esc(e)}">${esc(e)}</a>`).join("<br>") || "—"}</dd>
        <dt>Phones</dt><dd>${c.phones.map(esc).join("<br>") || "—"}</dd>
        <dt>Links</dt><dd><ul style="margin:0;padding-left:16px">${links}</ul></dd>
        <dt>Tags</dt><dd>${(c.tags || []).map((t) => `<span class="tagchip">${esc(t)}</span>`).join(" ") || "—"}</dd>
        <dt>Lists</dt><dd>${(c.lists || []).map((l) => esc(l.name)).join(", ") || "—"}</dd>
        <dt>Seen</dt><dd>${c.seen_count} time(s), last ${esc(fmtRel(c.last_seen))}</dd>
        <dt>Growth</dt><dd>${esc(growth)}</dd>
      </dl>
      <h3 style="font-size:13px;margin:18px 0 6px">Biography</h3>
      <p style="font-size:13px;white-space:pre-wrap">${esc(c.biography) || '<span class="muted">Empty</span>'}</p>
      <h3 style="font-size:13px;margin:18px 0 6px">Notes</h3>
      <textarea id="creNotes" rows="4" placeholder="Anything worth remembering about this creator…">${esc(c.notes)}</textarea>`,
      `${c.profile_url ? `<a class="btn btn-sm" href="${esc(safeUrl(c.profile_url))}" target="_blank" rel="noopener noreferrer">Open profile ↗</a>` : ""}
       <button class="btn btn-primary btn-sm" id="creSaveNotes">Save notes</button>`
    );

    const save = $("#creSaveNotes");
    if (save) save.addEventListener("click", async () => {
      try {
        await api(`/api/creators/${encodeURIComponent(id)}`, {
          method: "PATCH",
          body: JSON.stringify({ notes: $("#creNotes").value }),
        });
        toast("Notes saved.", "ok");
        closeDrawer();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  } catch (err) {
    toast(err.message, "error");
  }
}

function wireCreators() {
  const reload = () => { creators.offset = 0; creators.selected.clear(); loadCreators(); };
  $("#creSearch").addEventListener("input", debounce(reload, 300));
  ["#crePlatform", "#creTag", "#creList", "#creContact", "#creVerified"]
    .forEach((sel) => $(sel).addEventListener("change", reload));
  $("#creSort").addEventListener("change", (e) => {
    creators.sort = e.target.value;
    creators.order = "desc";
    reload();
  });
  $("#creClearSel").addEventListener("click", () => {
    creators.selected.clear();
    renderCreatorTable();
  });
  $("#creBulk").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-bulk]");
    if (btn) bulkAction(btn.dataset.bulk);
  });
  $("#creExport").addEventListener("click", () => {
    const params = creatorQuery();
    params.delete("offset");
    params.set("limit", "5000");
    params.set("fmt", $("#creExportFmt")?.value || "csv");
    window.open(`/api/creators/export?${params}`, "_blank");
  });
  $("#creNewList").addEventListener("click", async () => {
    const name = prompt("Name this list:");
    if (!name || !name.trim()) return;
    try {
      await api("/api/lists", { method: "POST", body: JSON.stringify({ name: name.trim() }) });
      toast(`List "${name.trim()}" created.`, "ok");
      loadCreators();
    } catch (err) {
      toast(err.message, "error");
    }
  });
}

/* ==========================================================================
   Analytics
   ========================================================================== */

async function loadAnalytics() {
  const host = $("#anaOut");
  host.innerHTML = skeletonTable(6);

  try {
    const days = $("#anaDays").value;
    const data = await api(`/api/analytics?days=${days}`);
    host.innerHTML = renderAnalytics(data);
  } catch (err) {
    host.innerHTML = errorBlock(err.message, "anaRetry");
    const retry = $("#anaRetry");
    if (retry) retry.addEventListener("click", () => loadAnalytics());
  }
}

function renderAnalytics(data) {
  const c = data.creators;
  const runs = data.runs || [];
  const attempted = runs.reduce((sum, r) => sum + (r.total || 0), 0);
  const succeeded = runs.reduce((sum, r) => sum + (r.ok || 0), 0);

  const bands = [
    ["nano", "Under 1K"], ["micro", "1K – 10K"], ["mid", "10K – 100K"],
    ["macro", "100K – 1M"], ["mega", "Over 1M"],
  ];
  const bandMax = Math.max(1, ...Object.values(c.bands || {}));

  const modes = Object.entries(data.modes || {});
  const domains = data.domains || [];
  const providers = data.providers || [];

  return `
    <div class="stat-grid">
      ${kpi("Rows attempted", fmtCount(attempted) || "0", `${runs.length} runs in range`)}
      ${kpi("Succeeded", fmtCount(succeeded) || "0", attempted ? `${Math.round(succeeded / attempted * 100)}% of attempts` : "no attempts yet", "ok")}
      ${kpi("Creators tracked", fmtCount(c.total) || "0", "unique profiles")}
      ${kpi("Reachable", fmtCount(c.reachable) || "0", "with email or phone")}
    </div>

    <div class="grid-dash">
      <div class="panel">
        <h2 class="panel-title">Audience tiers</h2>
        ${bands.map(([key, label]) => `
          <div class="bar-track">
            <span class="bar-label">${esc(label)}</span>
            ${meter(c.bands?.[key] || 0, bandMax)}
          </div>`).join("")}
        <p class="side-hint">Tiers follow how influencer work is normally priced, so the split maps onto budget rather than being an arbitrary histogram.</p>
      </div>

      <div class="panel">
        <h2 class="panel-title">Fetch mode success</h2>
        ${modes.length ? modes.map(([mode, m]) => {
          const rate = m.attempts ? m.ok / m.attempts * 100 : 0;
          return `<div class="bar-track">
            <span class="bar-label">${esc(mode)}</span>
            ${meter(Math.round(rate), 100, rate >= 70 ? "ok" : rate >= 40 ? "warn" : "err")}
            <span class="muted nowrap" style="font-size:12px">${m.attempts} tries</span>
          </div>`;
        }).join("") : `<p class="muted">No fetches recorded yet in this range.</p>`}
        <p class="side-hint">This is the number that answers whether stealth mode is worth its cost on your targets.</p>
      </div>
    </div>

    <div class="section-head"><h2>Where blocks happen</h2></div>
    ${domains.length ? `
      <div class="table-wrap">
        <table class="dt">
          <thead><tr>
            <th scope="col">Domain</th>
            <th class="num" scope="col">Attempts</th>
            <th class="num" scope="col">OK</th>
            <th class="num" scope="col">Blocked</th>
            <th class="num" scope="col">Errors</th>
            <th scope="col">Block rate</th>
            <th class="num hide-sm" scope="col">Avg time</th>
          </tr></thead>
          <tbody>
            ${domains.map((d) => `<tr>
              <td class="mono">${esc(d.domain)}</td>
              <td class="num">${d.attempts}</td>
              <td class="num">${d.ok}</td>
              <td class="num">${d.blocked}</td>
              <td class="num">${d.errors}</td>
              <td>${meter(d.block_rate, 100, d.block_rate > 40 ? "err" : d.block_rate > 15 ? "warn" : "ok")}</td>
              <td class="num hide-sm">${d.avg_ms} ms</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>` : emptyBlock({
        art: "🛡️",
        title: "No fetch telemetry yet",
        body: "Block rates are recorded as runs execute. Start a run and this table fills in.",
      })}

    <div class="section-head"><h2>Provider cost and reliability</h2></div>
    ${providers.length ? `
      <div class="table-wrap">
        <table class="dt">
          <thead><tr>
            <th scope="col">Provider</th>
            <th class="num" scope="col">Calls</th>
            <th class="num" scope="col">OK</th>
            <th class="num" scope="col">Errors</th>
            <th scope="col">Success</th>
            <th class="num" scope="col">Avg latency</th>
          </tr></thead>
          <tbody>
            ${providers.map((p) => `<tr>
              <td>${esc(p.provider)}</td>
              <td class="num">${p.calls}</td>
              <td class="num">${p.ok}</td>
              <td class="num">${p.errors}</td>
              <td>${meter(p.success_rate, 100, p.success_rate >= 80 ? "ok" : p.success_rate >= 50 ? "warn" : "err")}</td>
              <td class="num">${p.avg_ms} ms</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>` : `<p class="muted">No provider calls recorded in this range.</p>`}

    <div class="section-head"><h2>Biggest audience movers</h2></div>
    ${c.movers?.length ? `
      <div class="table-wrap">
        <table class="dt">
          <thead><tr>
            <th scope="col">Creator</th>
            <th scope="col">Platform</th>
            <th class="num" scope="col">Followers now</th>
            <th class="num" scope="col">Change observed</th>
          </tr></thead>
          <tbody>
            ${c.movers.map((m) => `<tr>
              <td>@${esc(m.username)}</td>
              <td>${esc(m.platform)}</td>
              <td class="num">${esc(fmtCount(m.followers))}</td>
              <td class="num" style="color:var(--ok)">+${esc(fmtCount(m.gained))}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>` : `<p class="muted">Movement needs a profile scraped at least twice. Set up a schedule to track change over time.</p>`}
  `;
}

/* ==========================================================================
   Providers
   ========================================================================== */

async function loadProviders() {
  const host = $("#provOut");
  host.innerHTML = skeletonTable(8);

  try {
    const data = await api("/api/providers/detail");
    host.innerHTML = renderProviders(data.providers);
    wireProviderRows();
  } catch (err) {
    host.innerHTML = errorBlock(err.message, "provRetry");
    const retry = $("#provRetry");
    if (retry) retry.addEventListener("click", () => loadProviders());
  }
}

function renderProviders(rows) {
  const keyed = rows.filter((r) => r.needs_key);
  const free = rows.filter((r) => !r.needs_key);

  const keyRow = (p) => `<tr data-provider="${esc(p.name)}">
    <td>
      <div class="who-txt">
        <span class="who-name">${esc(p.name)}</span>
        <span class="who-sub">priority ${p.priority}${p.free ? " · free tier" : ""}</span>
      </div>
    </td>
    <td>
      <span class="state ${p.configured ? "ok" : "err"}">${p.configured ? "Configured" : "No key"}</span>
    </td>
    <td class="mono hide-sm">${esc(p.masked_key) || "—"}</td>
    <td class="hide-sm muted">${esc(p.key_source)}</td>
    <td style="min-width:280px">
      <div style="display:flex;gap:6px;align-items:center">
        <input type="password" class="prov-key" placeholder="Paste a new key to replace"
               aria-label="API key for ${esc(p.name)}" autocomplete="off">
        <button class="btn btn-sm prov-save">Save</button>
        <button class="btn btn-ghost btn-sm prov-test">Test</button>
        ${p.stored ? `<button class="btn btn-danger btn-sm prov-clear">Clear</button>` : ""}
      </div>
      <p class="prov-result muted" style="font-size:12px;margin-top:4px"></p>
    </td>
  </tr>`;

  return `
    <div class="panel" style="border-color:color-mix(in srgb, var(--info) 35%, transparent)">
      <p style="font-size:13px">
        Keys saved here are stored in the app database and take precedence over
        <code>.env</code>, applying immediately without a restart.
        The dashboard only ever receives a masked key back — the full value is never sent to the browser.
      </p>
    </div>

    <div class="section-head"><h2>Keyed providers</h2></div>
    <div class="table-wrap">
      <table class="dt">
        <thead><tr>
          <th scope="col">Provider</th>
          <th scope="col">Status</th>
          <th class="hide-sm" scope="col">Current key</th>
          <th class="hide-sm" scope="col">Source</th>
          <th scope="col">Manage</th>
        </tr></thead>
        <tbody>${keyed.map(keyRow).join("")}</tbody>
      </table>
    </div>

    <div class="section-head"><h2>Keyless providers</h2></div>
    <div class="table-wrap">
      <table class="dt">
        <thead><tr>
          <th scope="col">Provider</th>
          <th class="num" scope="col">Priority</th>
          <th scope="col">Available</th>
          <th scope="col"></th>
        </tr></thead>
        <tbody>
          ${free.map((p) => `<tr data-provider="${esc(p.name)}">
            <td>${esc(p.name)}</td>
            <td class="num">${p.priority}</td>
            <td><span class="state ${p.available ? "ok" : "err"}">${p.available ? "Ready" : "Unavailable"}</span></td>
            <td>
              <button class="btn btn-ghost btn-sm prov-test">Test</button>
              <span class="prov-result muted" style="font-size:12px;margin-left:8px"></span>
            </td>
          </tr>`).join("")}
        </tbody>
      </table>
    </div>`;
}

function wireProviderRows() {
  $("#provOut").addEventListener("click", async (e) => {
    const row = e.target.closest("tr[data-provider]");
    if (!row) return;
    const name = row.dataset.provider;
    const result = row.querySelector(".prov-result");

    if (e.target.classList.contains("prov-save")) {
      const input = row.querySelector(".prov-key");
      const value = input.value.trim();
      if (!value) { toast("Paste a key first.", "warn"); return; }
      try {
        await api(`/api/providers/${encodeURIComponent(name)}/key`, {
          method: "PUT",
          body: JSON.stringify({ api_key: value, enabled: true }),
        });
        input.value = "";
        toast(`${name} key saved.`, "ok");
        loadProviders();
      } catch (err) { toast(err.message, "error"); }
    }

    if (e.target.classList.contains("prov-clear")) {
      if (!confirm(`Remove the stored ${name} key? The environment value, if any, takes over again.`)) return;
      try {
        await api(`/api/providers/${encodeURIComponent(name)}/key`, { method: "DELETE" });
        toast(`${name} key cleared.`, "ok");
        loadProviders();
      } catch (err) { toast(err.message, "error"); }
    }

    if (e.target.classList.contains("prov-test")) {
      const button = e.target;
      button.disabled = true;
      result.textContent = "Testing…";
      result.style.color = "";
      try {
        const res = await api(`/api/providers/${encodeURIComponent(name)}/test`, { method: "POST" });
        result.textContent = res.ok
          ? `OK — ${res.ms} ms, ${fmtCount(res.bytes) || res.bytes} bytes`
          : `Failed — ${res.error}`;
        result.style.color = res.ok ? "var(--ok)" : "var(--err)";
      } catch (err) {
        result.textContent = err.message;
        result.style.color = "var(--err)";
      } finally {
        button.disabled = false;
      }
    }
  });
}

/* ==========================================================================
   Proxies and anti-block
   ========================================================================== */

async function loadProxies() {
  const host = $("#pxOut");
  host.innerHTML = skeletonTable(6);

  try {
    const [pool, block] = await Promise.all([
      api("/api/proxies"),
      api("/api/antiblock?days=7"),
    ]);
    host.innerHTML = renderProxies(pool, block);
    wireProxyRows();
  } catch (err) {
    host.innerHTML = errorBlock(err.message, "pxRetry");
    const retry = $("#pxRetry");
    if (retry) retry.addEventListener("click", () => loadProxies());
  }
}

function renderProxies(pool, block) {
  const proxies = pool.proxies || [];
  const combos = block.combos || [];
  const limiter = block.limiter || {};
  const domains = Object.entries(limiter.domains || {});

  const proxyTable = proxies.length ? `
    <div class="table-wrap">
      <table class="dt">
        <thead><tr>
          <th class="col-check" scope="col"><span class="muted" style="font-size:11px">On</span></th>
          <th scope="col">Proxy</th>
          <th class="hide-sm" scope="col">Label</th>
          <th scope="col">Status</th>
          <th class="num" scope="col">Latency</th>
          <th scope="col">Success</th>
          <th class="hide-sm" scope="col">Last checked</th>
          <th scope="col"></th>
        </tr></thead>
        <tbody>
          ${proxies.map((p) => `<tr data-proxy="${esc(p.id)}">
            <td class="col-check">
              <input type="checkbox" class="px-toggle" ${p.enabled ? "checked" : ""}
                     aria-label="Enable ${esc(p.url)}">
            </td>
            <td class="mono truncate" title="${esc(p.url)}">${esc(maskProxy(p.url))}</td>
            <td class="hide-sm">${esc(p.label) || '<span class="muted">—</span>'}</td>
            <td><span class="state ${p.status === "ok" ? "ok" : p.status === "down" ? "err" : "blocked"}">${esc(p.status)}</span></td>
            <td class="num">${p.latency_ms ? p.latency_ms + " ms" : "—"}</td>
            <td>${meter(p.success_rate, 100, p.success_rate >= 80 ? "ok" : p.success_rate >= 50 ? "warn" : "err")}</td>
            <td class="hide-sm muted nowrap">${p.last_checked ? esc(fmtRel(p.last_checked)) : "never"}</td>
            <td><button class="btn btn-danger btn-sm px-del">Remove</button></td>
          </tr>`).join("")}
        </tbody>
      </table>
    </div>` : emptyBlock({
      art: "🛡️",
      title: "No proxies in the pool",
      body: "Runs go out from this machine's own IP. Add a proxy above to rotate outbound traffic and reduce block rates on strict hosts.",
    });

  return `
    <div class="stat-grid">
      ${kpi("In pool", String(pool.proxies.length), "configured proxies")}
      ${kpi("Healthy", String(pool.active), "enabled and not benched", pool.active ? "ok" : "")}
      ${kpi("Rate limit", `${limiter.default_rate}/s`, `per domain, burst ${limiter.burst}`)}
      ${kpi("Browser engine", block.browser_available ? "Available" : "Not installed",
            block.browser_available ? "browser and stealth modes work" : "HTTP mode only",
            block.browser_available ? "ok" : "blocked")}
    </div>

    <div class="section-head"><h2>Proxy pool</h2></div>
    ${proxyTable}

    <div class="section-head"><h2>Block rates by host and mode</h2></div>
    ${combos.length ? `
      <div class="table-wrap">
        <table class="dt">
          <thead><tr>
            <th scope="col">Domain</th>
            <th scope="col">Mode</th>
            <th class="num" scope="col">Attempts</th>
            <th scope="col">Blocked</th>
            <th scope="col">Success</th>
            <th class="num hide-sm" scope="col">Avg time</th>
          </tr></thead>
          <tbody>
            ${combos.map((c) => `<tr>
              <td class="mono">${esc(c.domain)}</td>
              <td>${esc(c.mode)}</td>
              <td class="num">${c.attempts}</td>
              <td>${meter(c.block_rate, 100, c.block_rate > 40 ? "err" : c.block_rate > 15 ? "warn" : "ok")}</td>
              <td>${meter(c.success_rate, 100, c.success_rate >= 70 ? "ok" : "warn")}</td>
              <td class="num hide-sm">${c.avg_ms} ms</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>
      <p class="side-hint">A high block rate in <code>http</code> but not in <code>stealth</code> means that host is worth the browser cost. Both high means the proxy or the target needs attention.</p>
      ` : `<p class="muted">No fetch telemetry in the last 7 days. Run something and this fills in.</p>`}

    <div class="section-head"><h2>Rate limiter</h2></div>
    ${domains.length ? `
      <div class="table-wrap">
        <table class="dt">
          <thead><tr>
            <th scope="col">Domain</th>
            <th class="num" scope="col">Requests / second</th>
            <th class="num" scope="col">Tokens available</th>
          </tr></thead>
          <tbody>
            ${domains.map(([d, s]) => `<tr>
              <td class="mono">${esc(d)}</td>
              <td class="num">${s.rate}</td>
              <td class="num">${s.tokens}</td>
            </tr>`).join("")}
          </tbody>
        </table>
      </div>` : `<p class="muted">The limiter has not been exercised since startup. Buckets appear here once requests go out.</p>`}
  `;
}

/** Never render proxy credentials in full — the page may be over someone's shoulder. */
function maskProxy(url) {
  return String(url || "").replace(/\/\/([^:@/]+):([^@/]+)@/, "//$1:••••@");
}

function wireProxyRows() {
  $("#pxOut").addEventListener("click", async (e) => {
    const row = e.target.closest("tr[data-proxy]");
    if (!row) return;
    const id = row.dataset.proxy;

    if (e.target.classList.contains("px-del")) {
      if (!confirm("Remove this proxy from the pool?")) return;
      try {
        await api(`/api/proxies/${encodeURIComponent(id)}`, { method: "DELETE" });
        toast("Proxy removed.", "ok");
        loadProxies();
      } catch (err) { toast(err.message, "error"); }
    }
  });

  $("#pxOut").addEventListener("change", async (e) => {
    if (!e.target.classList.contains("px-toggle")) return;
    const id = e.target.closest("tr[data-proxy]").dataset.proxy;
    try {
      await api(`/api/proxies/${encodeURIComponent(id)}/toggle?enabled=${e.target.checked}`,
                { method: "POST" });
    } catch (err) {
      toast(err.message, "error");
      e.target.checked = !e.target.checked;
    }
  });
}

function wireProxyPage() {
  $("#pxAdd").addEventListener("click", async () => {
    const url = $("#pxUrl").value.trim();
    if (!url) { toast("Enter a proxy URL.", "warn"); return; }
    try {
      await api("/api/proxies", {
        method: "POST",
        body: JSON.stringify({ url, label: $("#pxLabel").value.trim() }),
      });
      $("#pxUrl").value = "";
      $("#pxLabel").value = "";
      toast("Proxy added.", "ok");
      loadProxies();
    } catch (err) { toast(err.message, "error"); }
  });

  $("#pxCheck").addEventListener("click", async () => {
    const button = $("#pxCheck");
    button.disabled = true;
    button.textContent = "Checking…";
    try {
      const res = await api("/api/proxies/check", { method: "POST" });
      const good = res.results.filter((r) => r.ok).length;
      toast(res.checked ? `${good} of ${res.checked} proxies responded.` : "No proxies to check.",
            good === res.checked && res.checked ? "ok" : "warn");
      loadProxies();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      button.disabled = false;
      button.textContent = "Check all";
    }
  });
}

/* ==========================================================================
   Registration
   ========================================================================== */

PAGE_LOADERS.creators = loadCreators;
PAGE_LOADERS.analytics = loadAnalytics;
PAGE_LOADERS.providers = loadProviders;
PAGE_LOADERS.proxies = loadProxies;

wireCreators();
wireProxyPage();
$("#anaDays").addEventListener("change", loadAnalytics);
$("#provRefresh").addEventListener("click", loadProviders);
