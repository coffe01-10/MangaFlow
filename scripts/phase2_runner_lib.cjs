"use strict";

// CommonJS twin of phase2_runner.mjs. Playwright loads playwright.config.ts and
// global-setup.ts through its CJS transpiler, so those entry points must import
// this file; requiring the ESM module there would break `import.meta`.
const { execFileSync, spawn } = require("node:child_process");
const net = require("node:net");
const path = require("node:path");

const WEB_URL = "http://127.0.0.1:3000";
const API_ORIGIN = "http://127.0.0.1:8000/api/v1";

function defaultPython(root, platform = process.platform) {
  return process.env.MANGAFLOW_PYTHON
    ?? (platform === "win32"
      ? `${root}\\.venv\\Scripts\\python.exe`
      : `${root}/.venv/bin/python`);
}

async function assertPortFree(port, connect = net.connect) {
  const inUse = await new Promise((resolve, reject) => {
    const socket = connect({ host: "127.0.0.1", port });
    socket.once("connect", () => {
      socket.end();
      resolve(true);
    });
    socket.once("error", (error) => {
      if (error.code === "ECONNREFUSED") resolve(false);
      else reject(error);
    });
    socket.setTimeout?.(2000, () => { socket.destroy(); reject(new Error("port probe timeout")); });
  });
  if (inUse) {
    throw new Error(`port ${port} is occupied; refusing to use an unknown instance`);
  }
}

function spawnOwned(command, args, options = {}) {
  assertSupervised();
  const child = spawn(command, args, {
    stdio: options.stdio ?? "inherit",
    cwd: options.cwd,
    env: options.env,
    windowsHide: true,
    shell: options.shell ?? false,
  });
  child.unref(); // The outer Job Object controller owns final shutdown.
  child.owned = true;
  child.spawnError = null;
  child.once("error", (error) => {
    child.spawnError = error;
  });
  return child;
}

// The Python verifier checks canonical path, owner, controller creation time
// and actual Windows Job Object membership. No raw PID or path deletion API.
function assertSupervised(port) {
  if (!/^[0-9a-f]{32}$/.test(process.env.MANGAFLOW_E2E_RUN_ID ?? "")) {
    throw new Error("Use scripts/run_e2e_owned.py: acceptance requires its controller");
  }
  const root = path.dirname(__dirname); // scripts/ lives directly under the repo root.
  const output = execFileSync(
    defaultPython(root),
    ["-I", "-B", path.join(root, "scripts", "run_e2e_owned.py"), "verify",
      ...(port === undefined ? [] : ["--port", String(port)])],
    { encoding: "utf8", windowsHide: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  return JSON.parse(output);
}

async function waitForOwnedHealth({
  url,
  runId,
  child,
  timeoutMs = 120_000,
  fetchImpl = fetch,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  const started = now();
  let lastError = "";
  while (now() - started < timeoutMs) {
    if (child.spawnError) {
      throw new Error(`owned process failed to spawn: ${child.spawnError}`);
    }
    if (child.exitCode !== null && child.exitCode !== undefined) {
      throw new Error(`owned process exited ${child.exitCode} before health: ${lastError}`);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2_000);
    try {
      const response = await fetchImpl(url, { signal: controller.signal });
      if (response.ok) {
        const body = await response.json();
        if (body.e2e_run_id !== runId) {
          throw new Error(`health identity mismatch: got ${body.e2e_run_id ?? "none"}`);
        }
        return body;
      }
      lastError = String(response.status);
    } catch (error) {
      if (String(error.message ?? error).includes("identity mismatch")) throw error;
      lastError = error instanceof Error ? error.message : String(error);
    } finally {
      clearTimeout(timer);
    }
    await sleep(250);
  }
  throw new Error(`timed out waiting for owned health at ${url}: ${lastError}`);
}

async function finalizeOwnedRun({ summary, cleanup, writeSummary }) {
  try {
    await cleanup();
  } catch (error) {
    summary.errors.push(String(error));
    summary.runtime_removed = false;
  }
  summary.finished_at = new Date().toISOString();
  await writeSummary(summary);
  return summary.errors.length ? 1 : 0;
}

async function json(url, init, fetchImpl = fetch) {
  const response = await fetchImpl(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok && response.status !== 204) {
    throw new Error(`${init?.method ?? "GET"} ${url}: ${response.status} ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}

module.exports = {
  API_ORIGIN,
  WEB_URL,
  assertPortFree,
  assertSupervised,
  defaultPython,
  finalizeOwnedRun,
  json,
  spawnOwned,
  waitForOwnedHealth,
};
