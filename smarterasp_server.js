"use strict";

// SmarterASP's GitHub builder runs on Linux, but the resulting files are
// published to Windows IIS. This dependency-free Node process is the IIS
// entry point: it prepares Windows Python packages, starts FastAPI on a private
// loopback port, and proxies public traffic to it.

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const appRoot = __dirname;
const publicPort = Number.parseInt(process.env.PORT || "3000", 10);
const backendPort = publicPort >= 65534 ? publicPort - 1 : publicPort + 1;
const dependencyDir = path.join(appRoot, ".smarterasp-python");
const dataDir = path.join(appRoot, "data");
const requirementsFile = path.join(appRoot, "requirements-smarterasp.txt");

let stage = "Starting the application";
let detail = "";
let backendReady = false;
let backendProcess = null;

function safeDetail(value) {
  return String(value || "")
    .replaceAll(appRoot, "[app]")
    .replace(/[\r\n]+/g, " ")
    .slice(0, 600);
}

function pythonEnvironment() {
  const sourceDir = path.join(appRoot, "src");
  const existing = process.env.PYTHONPATH || "";
  return {
    ...process.env,
    PYTHONPATH: [dependencyDir, sourceDir, appRoot, existing]
      .filter(Boolean)
      .join(path.delimiter),
    PYTHONUNBUFFERED: "1",
    PYTHONDONTWRITEBYTECODE: "1",
    SCRAPLING_DATA_DIR: process.env.SCRAPLING_DATA_DIR || dataDir,
  };
}

function findPython() {
  const candidates = [
    process.env.PYTHON && { command: process.env.PYTHON, prefix: [] },
    { command: "python", prefix: [] },
    { command: "python3", prefix: [] },
    { command: "py", prefix: ["-3"] },
  ].filter(Boolean);

  for (const candidate of candidates) {
    const result = spawnSync(
      candidate.command,
      [...candidate.prefix, "--version"],
      { encoding: "utf8", timeout: 10000, windowsHide: true },
    );
    if (!result.error && result.status === 0) {
      return candidate;
    }
  }
  return null;
}

function runPython(python, args, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(python.command, [...python.prefix, ...args], {
      cwd: appRoot,
      env,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    child.stdout.on("data", (chunk) => {
      output = (output + chunk.toString()).slice(-8000);
    });
    child.stderr.on("data", (chunk) => {
      output = (output + chunk.toString()).slice(-8000);
    });
    child.on("error", reject);
    child.on("exit", (code) => {
      if (code === 0) resolve(output);
      else reject(new Error(`Python exited with code ${code}: ${output}`));
    });
  });
}

function proxyRequest(request, response) {
  if (!backendReady) {
    response.statusCode = 503;
    response.setHeader("Content-Type", "application/json; charset=utf-8");
    response.setHeader("Retry-After", "5");
    response.end(JSON.stringify({ status: "starting", stage, detail }));
    return;
  }

  const headers = { ...request.headers, host: `127.0.0.1:${backendPort}` };
  const upstream = http.request(
    {
      hostname: "127.0.0.1",
      port: backendPort,
      path: request.url,
      method: request.method,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      upstreamResponse.pipe(response);
    },
  );
  upstream.on("error", (error) => {
    backendReady = false;
    stage = "FastAPI proxy connection failed";
    detail = safeDetail(error.message);
    if (!response.headersSent) {
      response.statusCode = 502;
      response.setHeader("Content-Type", "application/json; charset=utf-8");
    }
    response.end(JSON.stringify({ status: "error", stage, detail }));
  });
  request.pipe(upstream);
}

const server = http.createServer(proxyRequest);
server.listen(publicPort, "0.0.0.0", () => {
  console.log(`SmarterASP supervisor listening on ${publicPort}`);
});

function waitForBackend() {
  const probe = () => {
    const request = http.get(
      { hostname: "127.0.0.1", port: backendPort, path: "/healthz", timeout: 3000 },
      (response) => {
        response.resume();
        if (response.statusCode === 200) {
          backendReady = true;
          stage = "Ready";
          detail = "";
          console.log("FastAPI backend is ready");
        } else {
          setTimeout(probe, 1000);
        }
      },
    );
    request.on("error", () => setTimeout(probe, 1000));
    request.on("timeout", () => request.destroy());
  };
  probe();
}

async function bootstrap() {
  try {
    fs.mkdirSync(dependencyDir, { recursive: true });
    fs.mkdirSync(dataDir, { recursive: true });

    const python = findPython();
    if (!python) {
      throw new Error("Python 3 is not available in this hosting plan.");
    }
    const env = pythonEnvironment();

    stage = "Checking Python dependencies";
    const importCheck = [
      "-c",
      "import fastapi, uvicorn, scrapling, pandas, openpyxl; print('ok')",
    ];
    try {
      await runPython(python, importCheck, env);
    } catch {
      stage = "Installing Windows Python dependencies";
      await runPython(
        python,
        [
          "-m", "pip", "install",
          "--disable-pip-version-check",
          "--no-input",
          "--target", dependencyDir,
          "-r", requirementsFile,
        ],
        env,
      );
      await runPython(python, importCheck, env);
    }

    stage = "Starting FastAPI";
    backendProcess = spawn(
      python.command,
      [
        ...python.prefix,
        "-m", "uvicorn",
        "webapp.server:create_app",
        "--factory",
        "--host", "127.0.0.1",
        "--port", String(backendPort),
        "--workers", "1",
      ],
      { cwd: appRoot, env, windowsHide: true, stdio: "inherit" },
    );
    backendProcess.on("error", (error) => {
      backendReady = false;
      stage = "Could not start FastAPI";
      detail = safeDetail(error.message);
    });
    backendProcess.on("exit", (code) => {
      backendReady = false;
      stage = "FastAPI stopped";
      detail = `Exit code ${code}`;
    });
    waitForBackend();
  } catch (error) {
    stage = "Startup failed";
    detail = safeDetail(error.message || error);
    console.error(stage, detail);
  }
}

function shutdown() {
  if (backendProcess && !backendProcess.killed) backendProcess.kill();
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 5000).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
bootstrap();
