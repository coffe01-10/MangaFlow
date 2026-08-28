import { spawn } from "node:child_process";
import { mkdir, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
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
    shell: options.shell ?? false,
  });
  child.owned = true;
  child.spawnError = null;
  child.once("error", (error) => {
    child.spawnError = error;
  });
  return child;
}

export async function createOwnedRuntime(runId, {
  tmpdir = os.tmpdir(),
  mkdirImpl = mkdir,
} = {}) {
  const runtime = path.join(tmpdir, `mangaflow-e2e-${runId}`);
  await mkdirImpl(runtime, { recursive: true });
  return runtime;
}

export async function removeOwnedRuntime(runtimePath, runId, {
  rmImpl = rm,
  existsImpl = existsSync,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  retries = 16,
} = {}) {
  if (!runtimePath || !runId || !String(runtimePath).includes(`mangaflow-e2e-${runId}`)) {
    throw new Error("refusing to delete a path this run does not own");
  }
  let lastError = "";
  for (let attempt = 0; attempt < retries; attempt += 1) {
    try {
      await rmImpl(runtimePath, { recursive: true, force: true });
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    if (!existsImpl(runtimePath)) return;
    await sleep(250 * (attempt + 1));
  }
  throw new Error(`failed to remove owned runtime ${runtimePath}: ${lastError}`);
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

export async function stopPidTree(pid, {
  platform = process.platform,
  spawnImpl = spawn,
  killImpl = process.kill,
  waitForExit = defaultWaitForExit,
  timeoutMs = 15_000,
} = {}) {
  if (!Number.isInteger(pid) || pid <= 0 || pid === process.pid) {
    throw new Error("refusing to stop an invalid pid");
  }
  if (platform === "win32") {
    const killer = spawnImpl("taskkill", ["/pid", String(pid), "/t", "/f"], {
      stdio: ["ignore", "pipe", "pipe"],
      shell: false,
      windowsHide: true,
    });
    const killed = await waitForExit(killer, timeoutMs);
    if (killed.code !== 0 && killed.code !== 128) {
      throw new Error(`taskkill exited ${killed.code}: ${killed.stderr ?? ""}`.trim());
    }
    return;
  }
  try {
    killImpl(pid, "SIGTERM");
  } catch {
    // already gone
  }
}

export async function stopOwned(child, {
  platform = process.platform,
  spawnImpl = spawn,
  killImpl = process.kill,
  waitForExit = defaultWaitForExit,
  timeoutMs = 15_000,
} = {}) {
  if (!child?.owned) {
    throw new Error("refusing to stop a process that this run does not own");
  }
  if (child.spawnError) {
    throw new Error(`owned process failed to spawn: ${child.spawnError}`);
  }
  if (!child.pid) {
    throw new Error("owned process has no pid");
  }
  await stopPidTree(child.pid, { platform, spawnImpl, killImpl, waitForExit, timeoutMs });
  await waitForExit(child, timeoutMs);
}

function defaultWaitForExit(child, timeoutMs) {
  if (child.exitCode !== null && child.exitCode !== undefined) {
    return Promise.resolve({ code: child.exitCode, stderr: "" });
  }
  return new Promise((resolve, reject) => {
    let stderr = "";
    child.stderr?.on?.("data", (chunk) => {
      stderr += chunk;
    });
    const timer = setTimeout(
      () => reject(new Error(`owned pid ${child.pid ?? "unknown"} did not exit`)),
      timeoutMs,
    );
    child.once("exit", (code) => {
      clearTimeout(timer);
      resolve({ code: code ?? 1, stderr });
    });
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
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

export async function finalizeOwnedRun({ summary, cleanup, writeSummary }) {
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
