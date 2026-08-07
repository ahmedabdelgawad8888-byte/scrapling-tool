"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { randomUUID } = require("node:crypto");

const PLATFORMS = ["tiktok", "instagram", "youtube", "twitter", "facebook", "snapchat"];
const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webp": "image/webp",
};

function send(response, status, body, contentType = "application/json; charset=utf-8", headers = {}) {
  const payload = Buffer.isBuffer(body)
    ? body
    : contentType.startsWith("application/json")
      ? Buffer.from(JSON.stringify(body))
      : Buffer.from(String(body));
  response.writeHead(status, {
    "Content-Type": contentType,
    "Content-Length": payload.length,
    "Cache-Control": "no-store",
    ...headers,
  });
  response.end(payload);
}

async function readJson(request) {
  let size = 0;
  const chunks = [];
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 1_000_000) throw Object.assign(new Error("Request body is too large"), { status: 413 });
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    throw Object.assign(new Error("Invalid JSON body"), { status: 400 });
  }
}

function validPublicUrl(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch {
    return null;
  }
  if (!/^https?:$/.test(parsed.protocol) || parsed.username || parsed.password) return null;
  const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    host === "localhost" || host === "::1" || host === "0.0.0.0" ||
    /^127\./.test(host) || /^10\./.test(host) || /^192\.168\./.test(host) ||
    /^169\.254\./.test(host) || /^172\.(1[6-9]|2\d|3[01])\./.test(host)
  ) return null;
  return parsed.toString();
}

function decodeHtml(value) {
  return String(value || "")
    .replace(/&amp;/gi, "&").replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<").replace(/&gt;/gi, ">").replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)));
}

function metaValue(html, names) {
  for (const name of names) {
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const first = new RegExp(`<meta[^>]+(?:name|property)=["']${escaped}["'][^>]+content=["']([^"']*)`, "i").exec(html);
    if (first) return decodeHtml(first[1].trim());
    const reverse = new RegExp(`<meta[^>]+content=["']([^"']*)["'][^>]+(?:name|property)=["']${escaped}["']`, "i").exec(html);
    if (reverse) return decodeHtml(reverse[1].trim());
  }
  return "";
}

function textValue(html, tag) {
  const match = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\/${tag}>`, "i").exec(html);
  return match ? decodeHtml(match[1].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()) : "";
}

function platformFor(url) {
  const host = new URL(url).hostname.toLowerCase();
  if (host.includes("x.com") || host.includes("twitter.com")) return "twitter";
  return PLATFORMS.find((platform) => host.includes(platform)) || "web";
}

function parsePage(html, url, status, elapsed) {
  const title = metaValue(html, ["og:title", "twitter:title"]) || textValue(html, "title");
  const biography = metaValue(html, ["description", "og:description", "twitter:description"]);
  const image = metaValue(html, ["og:image", "twitter:image"]);
  const emails = [...new Set((html.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || []).map(x => x.toLowerCase()))];
  const parts = new URL(url).pathname.split("/").filter(Boolean);
  return {
    platform: platformFor(url),
    username: parts[0]?.replace(/^@/, "") || "",
    full_name: title,
    biography,
    emails,
    profile_image: image,
    profile_url: url,
    url,
    status,
    blocked: status === 401 || status === 403 || status === 429,
    error: status >= 400 ? `HTTP ${status}` : "",
    response_time: Number(elapsed.toFixed(2)),
  };
}

function csvEscape(value) {
  const text = Array.isArray(value) ? value.join(", ") : String(value ?? "");
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function exportRows(rows, format) {
  const fmt = String(format || "csv").toLowerCase();
  if (fmt === "json") return { body: JSON.stringify(rows, null, 2), type: "application/json", ext: "json" };
  if (fmt === "jsonl") return { body: rows.map(row => JSON.stringify(row)).join("\n"), type: "application/x-ndjson", ext: "jsonl" };
  const columns = ["platform", "username", "full_name", "followers", "following", "likes", "avg_views", "is_verified", "is_private", "biography", "emails", "country", "city", "url", "status", "blocked", "error", "response_time"];
  if (fmt === "csv") {
    const lines = [columns.join(","), ...rows.map(row => columns.map(key => csvEscape(row[key] ?? (key === "url" ? row.profile_url : ""))).join(","))];
    return { body: `\ufeff${lines.join("\r\n")}`, type: "text/csv; charset=utf-8", ext: "csv" };
  }
  if (fmt === "md") {
    const line = row => `| ${columns.map(key => String(row[key] ?? "").replaceAll("|", "\\|")).join(" | ")} |`;
    return { body: `${line(Object.fromEntries(columns.map(x => [x, x])))}\n| ${columns.map(() => "---").join(" | ")} |\n${rows.map(line).join("\n")}`, type: "text/markdown; charset=utf-8", ext: "md" };
  }
  if (fmt === "html") {
    const esc = value => String(value ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
    const table = `<table><thead><tr>${columns.map(x => `<th>${esc(x)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(key => `<td>${esc(row[key])}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    return { body: `<!doctype html><meta charset="utf-8"><title>Scrapling results</title>${table}`, type: "text/html; charset=utf-8", ext: "html" };
  }
  throw Object.assign(new Error(`Unsupported format: ${fmt}`), { status: 400 });
}

function createNodeFallback({ appRoot = __dirname, fetchImpl = globalThis.fetch } = {}) {
  const jobs = new Map();
  const runs = new Map();
  const schedules = new Map();
  const staticDir = path.join(appRoot, "webapp", "static");

  async function fetchOne(value, timeoutSeconds = 30) {
    const url = validPublicUrl(value);
    if (!url) throw Object.assign(new Error(`Invalid or private URL: ${value}`), { status: 400 });
    const started = Date.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), Math.max(1, timeoutSeconds) * 1000);
    try {
      const response = await fetchImpl(url, {
        redirect: "follow",
        signal: controller.signal,
        headers: { "User-Agent": "Mozilla/5.0 (compatible; ScraplingTool/2.0; +https://smarterasp.net)" },
      });
      const html = (await response.text()).slice(0, 400_000);
      return { url, html, status: response.status, elapsed: (Date.now() - started) / 1000, parsed: parsePage(html, url, response.status, (Date.now() - started) / 1000) };
    } finally {
      clearTimeout(timer);
    }
  }

  function emit(job, event) {
    job.events.push(event);
    for (const response of job.listeners) response.write(`data: ${JSON.stringify(event)}\n\n`);
    if (event.type === "finished") {
      for (const response of job.listeners) response.end();
      job.listeners.clear();
    }
  }

  function summary(job) {
    const blocked = job.results.filter(row => row.blocked).length;
    const errors = job.results.filter(row => row.error && !row.blocked).length;
    return { id: job.id, kind: job.kind, status: job.status, total: job.total, done: job.done, kept: job.results.length, ok: job.results.length - blocked - errors, blocked, errors, error: job.error, run_id: job.runId, created_at: job.createdAt, finished_at: job.finishedAt };
  }

  async function executeScrape(job, requestBody) {
    job.status = "running";
    const urls = Array.isArray(requestBody.params?.urls) ? requestBody.params.urls : [];
    const valid = urls.map(validPublicUrl).filter(Boolean);
    job.total = valid.length;
    emit(job, { type: "started", total: job.total, run_id: job.runId });
    if (!valid.length) throw new Error("No valid public URLs supplied.");
    const concurrency = Math.max(1, Math.min(10, Number(requestBody.options?.concurrency) || 4));
    let cursor = 0;
    const workers = Array.from({ length: Math.min(concurrency, valid.length) }, async () => {
      while (cursor < valid.length && !job.cancelled) {
        const index = cursor++;
        let result;
        try {
          result = (await fetchOne(valid[index], Number(requestBody.options?.timeout) || 30)).parsed;
        } catch (error) {
          result = { url: valid[index], profile_url: valid[index], status: 0, blocked: false, error: String(error.message || error), response_time: 0 };
        }
        job.results.push(result);
        job.done += 1;
        emit(job, { type: "progress", done: job.done, total: job.total, result });
      }
    });
    await Promise.all(workers);
  }

  function finishJob(job, status, error = "") {
    job.status = status;
    job.error = error;
    job.finishedAt = Date.now() / 1000;
    const run = { id: job.runId, kind: job.kind, label: job.label, status, params: job.params, results: job.results, error, created_at: job.createdAt, finished_at: job.finishedAt };
    runs.set(run.id, run);
    emit(job, { type: "finished", ...summary(job) });
  }

  async function startJob(body) {
    if (body.kind !== "scrape") throw Object.assign(new Error("This host supports URL scraping only; discovery and browser automation require a Python VPS."), { status: 400 });
    const id = randomUUID();
    const job = { id, runId: randomUUID(), kind: body.kind, label: body.label || "", params: body.params || {}, status: "queued", total: 0, done: 0, results: [], events: [], listeners: new Set(), cancelled: false, error: "", createdAt: Date.now() / 1000, finishedAt: 0 };
    jobs.set(id, job);
    queueMicrotask(async () => {
      try {
        await executeScrape(job, body);
        finishJob(job, job.cancelled ? "cancelled" : "done");
      } catch (error) {
        emit(job, { type: "log", level: "error", message: String(error.message || error) });
        finishJob(job, "error", String(error.message || error));
      }
    });
    return job;
  }

  function serveStatic(response, file) {
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) return false;
    const type = MIME[path.extname(file).toLowerCase()] || "application/octet-stream";
    send(response, 200, fs.readFileSync(file), type, { "Cache-Control": type.startsWith("text/html") ? "no-cache" : "public, max-age=3600" });
    return true;
  }

  async function handle(request, response) {
    try {
      const origin = `http://${request.headers.host || "localhost"}`;
      const url = new URL(request.url || "/", origin);
      const route = url.pathname;

      if (request.method === "GET" && (route === "/api/health" || route === "/healthz")) {
        return send(response, 200, route === "/healthz" ? { status: "ok" } : {
          status: "ok", version: "2.0.0-smarterasp", runtime: "node", scrapling: false,
          playwright: false, browser: false, modes: ["http"], platforms: PLATFORMS,
          post_platforms: [], targets: ["accounts", "videos", "posts", "stories", "hashtags"],
          limitations: ["HTTP-only mode", "Browser and stealth automation require a Python VPS"],
          store: { runs: runs.size, schedules: schedules.size },
        });
      }

      if (request.method === "POST" && route === "/api/capture") {
        const body = await readJson(request);
        const page = await fetchOne(body.url, Number(body.timeout) || 30);
        return send(response, 200, { url: page.url, mode: "http", status: page.status, blocked: page.parsed.blocked, elapsed: Number(page.elapsed.toFixed(2)), html: page.html, html_bytes: Buffer.byteLength(page.html), parsed: page.parsed, screenshot: "" });
      }

      if (request.method === "POST" && route === "/api/jobs") {
        const job = await startJob(await readJson(request));
        return send(response, 200, { job_id: job.id, kind: job.kind });
      }
      if (request.method === "GET" && route === "/api/jobs") return send(response, 200, { jobs: [...jobs.values()].map(summary) });

      let match = /^\/api\/jobs\/([^/]+)(?:\/(events|cancel|export))?$/.exec(route);
      if (match) {
        const job = jobs.get(match[1]);
        if (!job) return send(response, 404, { detail: "Unknown job" });
        if (request.method === "GET" && match[2] === "events") {
          response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache", Connection: "keep-alive" });
          for (const event of job.events) response.write(`data: ${JSON.stringify(event)}\n\n`);
          if (["done", "error", "cancelled"].includes(job.status)) return response.end();
          job.listeners.add(response);
          request.on("close", () => job.listeners.delete(response));
          return;
        }
        if (request.method === "POST" && match[2] === "cancel") {
          if (job.status !== "running" && job.status !== "queued") return send(response, 409, { detail: "Job is not running" });
          job.cancelled = true;
          return send(response, 200, { cancelled: true });
        }
        if (request.method === "GET" && match[2] === "export") {
          const exported = exportRows(job.results, url.searchParams.get("format"));
          return send(response, 200, exported.body, exported.type, { "Content-Disposition": `attachment; filename="${job.kind}_${job.id}.${exported.ext}"` });
        }
        if (request.method === "GET" && !match[2]) return send(response, 200, { ...summary(job), results: job.results });
      }

      if (request.method === "GET" && route === "/api/runs") return send(response, 200, { runs: [...runs.values()].sort((a, b) => b.created_at - a.created_at).slice(0, Number(url.searchParams.get("limit")) || 60) });
      match = /^\/api\/runs\/([^/]+)(?:\/(export))?$/.exec(route);
      if (match) {
        const run = runs.get(match[1]);
        if (!run) return send(response, 404, { detail: "Unknown run" });
        if (request.method === "DELETE" && !match[2]) { runs.delete(match[1]); return send(response, 200, { deleted: true }); }
        if (request.method === "GET" && match[2] === "export") {
          const exported = exportRows(run.results, url.searchParams.get("format"));
          return send(response, 200, exported.body, exported.type, { "Content-Disposition": `attachment; filename="${run.kind}_${run.id}.${exported.ext}"` });
        }
        if (request.method === "GET") return send(response, 200, run);
      }

      if (request.method === "GET" && route === "/api/schedules") return send(response, 200, { schedules: [...schedules.values()] });
      if (request.method === "POST" && route === "/api/schedules") {
        const body = await readJson(request);
        if (body.kind !== "scrape") throw Object.assign(new Error("Only URL scraping schedules are supported on this host."), { status: 400 });
        const schedule = { id: randomUUID(), name: body.name || "Schedule", kind: body.kind, interval_min: Math.max(5, Number(body.interval_min) || 60), enabled: true, params: { options: body.options || {}, ...(body.params || {}) }, created_at: Date.now() / 1000, last_run: 0 };
        schedules.set(schedule.id, schedule);
        return send(response, 200, schedule);
      }
      match = /^\/api\/schedules\/([^/]+)\/(toggle|run)$/.exec(route);
      if (match && request.method === "POST") {
        const schedule = schedules.get(match[1]);
        if (!schedule) return send(response, 404, { detail: "Unknown schedule" });
        if (match[2] === "toggle") { schedule.enabled = url.searchParams.get("enabled") !== "false"; return send(response, 200, schedule); }
        const params = { ...(schedule.params || {}) }; const options = params.options || {}; delete params.options;
        const job = await startJob({ kind: schedule.kind, params, options, label: schedule.name });
        schedule.last_run = Date.now() / 1000;
        return send(response, 200, { job_id: job.id });
      }
      match = /^\/api\/schedules\/([^/]+)$/.exec(route);
      if (match && request.method === "DELETE") { schedules.delete(match[1]); return send(response, 200, { deleted: true }); }

      if (request.method === "GET" && route.startsWith("/assets/")) {
        const relative = decodeURIComponent(route.slice("/assets/".length));
        const file = path.resolve(staticDir, relative);
        if (file.startsWith(`${path.resolve(staticDir)}${path.sep}`) && serveStatic(response, file)) return;
        return send(response, 404, { detail: "Not found" });
      }
      if (request.method === "GET" && !route.startsWith("/api/")) {
        if (serveStatic(response, path.join(staticDir, "index.html"))) return;
      }
      return send(response, 404, { detail: "Not found" });
    } catch (error) {
      return send(response, Number(error.status) || 500, { detail: String(error.message || error) });
    }
  }

  return { handle, close() { for (const job of jobs.values()) for (const response of job.listeners) response.end(); } };
}

module.exports = { createNodeFallback };
