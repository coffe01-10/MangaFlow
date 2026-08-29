import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";

import {
  API_ORIGIN,
  WEB_URL,
  assertPortFree,
  assertSupervised,
  defaultPython,
  finalizeOwnedRun,
  json,
  spawnOwned,
  waitForOwnedHealth,
} from "./phase2_runner.mjs";

const ROOT = process.cwd();
const PYTHON = defaultPython(ROOT);
const owned = assertSupervised();
const RUN_ID = owned.runId;
const runtimeDir = owned.runtime;
const OUT_DIR = path.join(ROOT, "output", "playwright", "phase2", RUN_ID);

function run(command, args, extra = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: ROOT,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      shell: extra.shell ?? false,
      ...extra,
    });
    let output = "";
    child.stdout.on("data", (chunk) => {
      output += chunk;
      process.stdout.write(chunk);
    });
    child.stderr.on("data", (chunk) => {
      output += chunk;
      process.stderr.write(chunk);
    });
    child.on("error", reject);
    child.on("exit", (code) => resolve({ code: code ?? 1, output }));
  });
}

const children = [];
const summary = {
  sha: process.env.MANGAFLOW_SHA ?? "",
  node: process.version,
  run_id: RUN_ID,
  started_at: new Date().toISOString(),
  lighthouse: [],
  fps: [],
  errors: [],
};

async function writeSummary() {
  await mkdir(OUT_DIR, { recursive: true });
  await writeFile(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
}

async function cleanup() {
  // The controller appends the authoritative exit/cleanup result after this Node
  // process exits. Never delete a directory while an API or browser is alive.
  summary.cleanup_owner = "run_e2e_owned.py";
  summary.runtime_removed = false;
}

async function main() {
  await assertPortFree(8000);
  await assertPortFree(3000);
  await mkdir(OUT_DIR, { recursive: true });
  summary.runtime_dir = `mangaflow-e2e-${RUN_ID}`;

  const apiEnv = {
    ...process.env,
    MANGAFLOW_E2E_RUN_ID: RUN_ID,
    MANGAFLOW_E2E_RUNTIME: runtimeDir,
    MANGAFLOW_E2E_SEED: "1",
    MANGAFLOW_DISABLE_DOTENV: "1",
  };
  const api = spawnOwned(PYTHON, [path.join(ROOT, "scripts", "serve_e2e_api.py")], {
    cwd: ROOT,
    env: apiEnv,
  });
  children.push(api);
  await waitForOwnedHealth({
    url: `${API_ORIGIN}/health`,
    runId: RUN_ID,
    child: api,
  });

  assertSupervised(8000);
  const web = spawnOwned(process.execPath, [path.join(ROOT, "node_modules", "next", "dist", "bin", "next"), "start", path.join(ROOT, "apps", "web"), "--hostname", "127.0.0.1"], {
    cwd: ROOT,
    env: process.env,
    shell: false,
  });
  children.push(web);
  const webStarted = Date.now();
  let webReady = false;
  while (Date.now() - webStarted < 120_000) {
    if (web.spawnError) {
      throw new Error(`web process failed to spawn: ${web.spawnError}`);
    }
    if (web.exitCode !== null && web.exitCode !== undefined) {
      throw new Error(`web process exited ${web.exitCode}`);
    }
    try {
      const response = await fetch(WEB_URL, { signal: AbortSignal.timeout(2_000) });
      if (response.ok) {
        webReady = true;
        break;
      }
    } catch {
      // keep waiting while the owned process lives
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  if (!webReady) throw new Error("timed out waiting for owned web server");
  assertSupervised(3000);

  const projects = await json(`${API_ORIGIN}/projects`);
  const lighthouseProject = projects.find((item) => item.name === "e2e-lighthouse-workbench");
  if (!lighthouseProject) throw new Error("seeded lighthouse project missing");
  summary.project_id = lighthouseProject.id;
  summary.dataset = {
    lighthouse_project: lighthouseProject.name,
    pages: 1,
    candidates: 1,
    inspections: 5,
  };

  const lighthouseEnv = {
    ...process.env,
    MANGAFLOW_WEB_URL: WEB_URL,
    MANGAFLOW_PROJECT_ID: lighthouseProject.id,
    MANGAFLOW_LH_AUDIT_DIR: path.join(OUT_DIR, "lh"),
  };
  const fpsEnv = {
    ...process.env,
    MANGAFLOW_WEB_ORIGIN: WEB_URL,
    MANGAFLOW_API_ORIGIN: API_ORIGIN,
  };

  let failed = false;
  for (const round of [1, 2]) {
    console.log(`\n--- Lighthouse round ${round} ---`);
    const result = await run(process.execPath, [path.join(ROOT, "apps", "web", "scripts", "lighthouse-gate.mjs")], {
      env: lighthouseEnv,
      shell: false,
    });
    summary.lighthouse.push({ round, exit_code: result.code, output: result.output.trim() });
    if (result.code !== 0) failed = true;
  }
  for (const round of [1, 2]) {
    console.log(`\n--- Workflow FPS round ${round} ---`);
    const result = await run(process.execPath, [path.join(ROOT, "apps", "web", "scripts", "workflow-fps-gate.mjs")], {
      env: fpsEnv,
      shell: false,
    });
    summary.fps.push({ round, exit_code: result.code, output: result.output.trim() });
    if (result.code !== 0) failed = true;
  }
  summary.finished_at = new Date().toISOString();
  if (failed) summary.errors.push("lighthouse or fps gate failed");
}

try {
  await main();
} catch (error) {
  summary.errors.push(String(error));
  console.error(error);
} finally {
  process.exitCode = await finalizeOwnedRun({
    summary,
    cleanup,
    writeSummary,
  });
  console.log(`Wrote ${path.join(OUT_DIR, "summary.json")}`);
}
