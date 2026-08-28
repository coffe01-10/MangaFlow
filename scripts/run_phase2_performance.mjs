import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";

import {
  API_ORIGIN,
  WEB_URL,
  assertPortFree,
  defaultPython,
  json,
  spawnOwned,
  stopOwned,
  waitForOwnedHealth,
  waitUntilPortFree,
} from "./phase2_runner.mjs";

const ROOT = process.cwd();
const PYTHON = defaultPython(ROOT);
const OUT_DIR = path.join(ROOT, "output", "playwright", "phase2");
const RUN_ID = process.env.MANGAFLOW_E2E_RUN_ID || crypto.randomUUID().replaceAll("-", "");

function run(command, args, extra = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: ROOT,
      stdio: ["ignore", "pipe", "pipe"],
      shell: false,
      windowsHide: true,
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

async function cleanup() {
  for (const child of [...children].reverse()) {
    try {
      await stopOwned(child);
    } catch (error) {
      summary.errors.push(String(error));
    }
  }
  children.length = 0;
  try {
    await waitUntilPortFree(8000);
    await waitUntilPortFree(3000);
  } catch (error) {
    summary.errors.push(String(error));
  }
}

async function main() {
  await assertPortFree(8000);
  await assertPortFree(3000);
  await mkdir(OUT_DIR, { recursive: true });

  const apiEnv = {
    ...process.env,
    MANGAFLOW_E2E_RUN_ID: RUN_ID,
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

  const web = spawnOwned(
    process.platform === "win32" ? "npm.cmd" : "npm",
    ["run", "serve:e2e:web"],
    { cwd: ROOT, env: process.env },
  );
  children.push(web);
  const webStarted = Date.now();
  let webReady = false;
  while (Date.now() - webStarted < 120_000) {
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
  };
  const fpsEnv = {
    ...process.env,
    MANGAFLOW_WEB_ORIGIN: WEB_URL,
    MANGAFLOW_API_ORIGIN: API_ORIGIN,
  };

  let failed = false;
  const npmBin = process.platform === "win32" ? "npm.cmd" : "npm";
  for (const round of [1, 2]) {
    console.log(`\n--- Lighthouse round ${round} ---`);
    const result = await run(npmBin, ["run", "test:lighthouse", "--workspace", "@mangaflow/web"], {
      env: lighthouseEnv,
    });
    summary.lighthouse.push({ round, exit_code: result.code, output: result.output.trim() });
    if (result.code !== 0) failed = true;
  }
  for (const round of [1, 2]) {
    console.log(`\n--- Workflow FPS round ${round} ---`);
    const result = await run(npmBin, ["run", "test:workflow-fps", "--workspace", "@mangaflow/web"], {
      env: fpsEnv,
    });
    summary.fps.push({ round, exit_code: result.code, output: result.output.trim() });
    if (result.code !== 0) failed = true;
  }
  summary.finished_at = new Date().toISOString();
  await writeFile(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
  console.log(`Wrote ${path.join(OUT_DIR, "summary.json")}`);
  if (failed || summary.errors.length) process.exitCode = 1;
}

try {
  await main();
} catch (error) {
  summary.errors.push(String(error));
  summary.finished_at = new Date().toISOString();
  await mkdir(OUT_DIR, { recursive: true });
  await writeFile(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
  console.error(error);
  process.exitCode = 1;
} finally {
  await cleanup();
}
