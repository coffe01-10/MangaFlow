import { spawn } from "node:child_process";
import net from "node:net";
import process from "node:process";

export const WEB_URL = "http://127.0.0.1:3000";
export const API_ORIGIN = "http://127.0.0.1:8000/api/v1";

export function defaultPython(root, platform = process.platform) {
  return process.env.MANGAFLOW_PYTHON
    ?? (platform === "win32"
      ? `${root}\\.venv\\Scripts\\python.exe`
      : `${root}/.venv/bin/python`);
}

export async function assertPortFree(port, connect = net.connect) {
  const inUse = await new Promise((resolve) => {
    const socket = connect({ host: "127.0.0.1", port });
    socket.once("connect", () => {
      socket.end();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
  if (inUse) {
    throw new Error(`port ${port} is occupied; refusing to use an unknown instance`);
  }
}

export function spawnOwned(command, args, options = {}) {
  const child = spawn(command, args, {
    stdio: options.stdio ?? "inherit",
    cwd: options.cwd,
    env: options.env,
    windowsHide: true,
    shell: false,
  });
  child.owned = true;
  return child;
}

export async function waitForOwnedHealth({
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

export async function stopOwned(child, {
  platform = process.platform,
  spawnImpl = spawn,
  killImpl = process.kill,
  waitForExit = defaultWaitForExit,
  timeoutMs = 15_000,
} = {}) {
  if (!child?.owned || !child.pid) {
    throw new Error("refusing to stop a process that this run does not own");
  }
  if (platform === "win32") {
    spawnImpl("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore", shell: false });
  } else if (!child.killed) {
    try {
      killImpl(child.pid, "SIGTERM");
    } catch {
      // already gone
    }
  }
  await waitForExit(child, timeoutMs);
}

function defaultWaitForExit(child, timeoutMs) {
  if (child.exitCode !== null && child.exitCode !== undefined) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`owned pid ${child.pid} did not exit`)), timeoutMs);
    child.once("exit", () => {
      clearTimeout(timer);
      resolve();
    });
  });
}

export async function waitUntilPortFree(port, {
  timeoutMs = 15_000,
  connect = net.connect,
  now = Date.now,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
  const started = now();
  while (now() - started < timeoutMs) {
    try {
      await assertPortFree(port, connect);
      return;
    } catch {
      await sleep(100);
    }
  }
  throw new Error(`port ${port} still occupied after owned process stop`);
}

export async function json(url, init, fetchImpl = fetch) {
  const response = await fetchImpl(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok && response.status !== 204) {
    throw new Error(`${init?.method ?? "GET"} ${url}: ${response.status} ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}
