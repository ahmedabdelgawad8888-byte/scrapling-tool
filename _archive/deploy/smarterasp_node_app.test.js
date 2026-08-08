"use strict";

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");
const test = require("node:test");

const appRoot = path.resolve(__dirname, "..");

async function withApp(fetchImpl, run) {
  const { createNodeFallback } = require("../smarterasp_node_app");
  const app = createNodeFallback({ appRoot, fetchImpl });
  assert.equal(typeof app.handle, "function", "fallback must expose an HTTP handler");
  const server = http.createServer(app.handle);
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  try {
    await run(`http://127.0.0.1:${port}`, app);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    app.close?.();
  }
}

function htmlResponse(url) {
  return new Response(`<!doctype html><html><head>
    <title>Example Creator</title>
    <meta name="description" content="Creator biography">
    <meta property="og:image" content="https://cdn.example/avatar.jpg">
    </head><body>Contact creator@example.com</body></html>`, {
    status: 200,
    headers: { "content-type": "text/html; charset=utf-8" },
  });
}

async function unusedPort() {
  const server = net.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  await new Promise((resolve) => server.close(resolve));
  return port;
}

test("exports a Node fallback application factory", () => {
  let createNodeFallback;
  try {
    ({ createNodeFallback } = require("../smarterasp_node_app"));
  } catch {
    // The first TDD run deliberately reaches this branch: the fallback does
    // not exist yet.
  }

  assert.equal(typeof createNodeFallback, "function");
});

test("health advertises the honest HTTP-only Node runtime", async () => {
  await withApp(htmlResponse, async (base) => {
    const response = await fetch(`${base}/api/health`);
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.deepEqual(body.modes, ["http"]);
    assert.equal(body.status, "ok");
    assert.equal(body.runtime, "node");
    assert.equal(body.browser, false);
  });
});

test("serves the existing dashboard and its static assets", async () => {
  await withApp(htmlResponse, async (base) => {
    const page = await fetch(`${base}/`);
    const script = await fetch(`${base}/assets/app.js`);
    assert.equal(page.status, 200);
    assert.match(await page.text(), /Scrapling Tool/i);
    assert.equal(script.status, 200);
    assert.match(await script.text(), /EventSource/);
  });
});

test("capture fetches a public URL and extracts useful metadata", async () => {
  await withApp(htmlResponse, async (base) => {
    const response = await fetch(`${base}/api/capture`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: "https://example.com/creator", screenshot: true }),
    });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.status, 200);
    assert.equal(body.mode, "http");
    assert.equal(body.parsed.full_name, "Example Creator");
    assert.deepEqual(body.parsed.emails, ["creator@example.com"]);
    assert.equal(body.screenshot, "");
  });
});

test("scrape jobs stream progress and remain available for export", async () => {
  await withApp(htmlResponse, async (base) => {
    const start = await fetch(`${base}/api/jobs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        kind: "scrape",
        options: { mode: "stealth", concurrency: 2 },
        params: { urls: ["https://example.com/a", "https://example.com/b"] },
      }),
    });
    assert.equal(start.status, 200);
    const { job_id: jobId } = await start.json();

    const events = await fetch(`${base}/api/jobs/${jobId}/events`);
    assert.equal(events.status, 200);
    const stream = await events.text();
    assert.match(stream, /"type":"progress"/);
    assert.match(stream, /"type":"finished"/);

    const detail = await fetch(`${base}/api/jobs/${jobId}`);
    const job = await detail.json();
    assert.equal(job.status, "done");
    assert.equal(job.results.length, 2);

    const csv = await fetch(`${base}/api/jobs/${jobId}/export?format=csv`);
    assert.equal(csv.status, 200);
    assert.match(csv.headers.get("content-disposition"), /\.csv/);
    assert.match(await csv.text(), /creator@example\.com/);
  });
});

test("the IIS supervisor activates the Node app when Python is unavailable", async () => {
  const port = await unusedPort();
  const child = spawn(process.execPath, [path.join(appRoot, "smarterasp_server.js")], {
    cwd: appRoot,
    env: { ...process.env, PORT: String(port), PATH: "", PYTHON: "missing-python" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", chunk => { output += chunk; });
  child.stderr.on("data", chunk => { output += chunk; });
  try {
    let response;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        response = await fetch(`http://127.0.0.1:${port}/api/health`);
        if (response.status === 200) break;
      } catch {}
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    assert.ok(response, `supervisor did not listen; output: ${output}`);
    assert.equal(response.status, 200, `unexpected supervisor response; output: ${output}`);
    assert.equal((await response.json()).runtime, "node");
  } finally {
    child.kill();
    await new Promise(resolve => child.once("exit", resolve));
  }
});
