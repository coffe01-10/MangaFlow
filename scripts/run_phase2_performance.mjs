import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";

const ROOT = process.cwd();
const PYTHON = process.env.MANGAFLOW_PYTHON
  ?? (process.platform === "win32"
    ? path.join(ROOT, ".venv", "Scripts", "python.exe")
    : path.join(ROOT, ".venv", "bin", "python"));
const WEB_URL = "http://127.0.0.1:3000";
const API_URL = "http://127.0.0.1:8000/api/v1";
const OUT_DIR = path.join(ROOT, "output", "playwright", "phase2");

function run(command, args, extra = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: ROOT,
      stdio: ["ignore", "pipe", "pipe"],
      shell: process.platform === "win32",
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
    child.on("exit", (code) => {
      resolve({ code: code ?? 1, output });
    });
  });
}

function start(command, args) {
  const child = spawn(command, args, {
    cwd: ROOT,
    stdio: "inherit",
    shell: process.platform === "win32",
    detached: process.platform !== "win32",
  });
  child.on("error", (error) => {
    console.error(error);
  });
  return child;
}

async function waitFor(url, timeoutMs = 120_000) {
  const startAt = Date.now();
  let lastError = "";
  while (Date.now() - startAt < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      lastError = `${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError}`);
}

async function json(url, init) {
  const response = await fetch(url, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${url}: ${response.status} ${await response.text()}`);
  }
  return response.status === 204 ? null : response.json();
}

function stop(child) {
  if (!child || child.killed) return;
  if (process.platform === "win32" && child.pid) {
    spawn("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

const children = [];
process.on("exit", () => children.forEach(stop));
process.on("SIGINT", () => process.exit(1));
process.on("SIGTERM", () => process.exit(1));

await mkdir(OUT_DIR, { recursive: true });
const api = start(PYTHON, [path.join(ROOT, "scripts", "serve_e2e_api.py")]);
children.push(api);
await waitFor(`${API_URL}/health`);
const web = start("npm", ["run", "serve:e2e:web"]);
children.push(web);
await waitFor(WEB_URL);

const project = await json(`${API_URL}/projects`, {
  method: "POST",
  body: JSON.stringify({ name: "Phase2 Lighthouse 项目" }),
});
await json(`${API_URL}/projects/${project.id}/sources/import`, {
  method: "POST",
  body: JSON.stringify({
    title: "第一章",
    text: "雨停之前，他把伞递给她。巷口的灯还亮着。",
    source_type: "PASTE",
  }),
});

const summary = {
  sha: process.env.MANGAFLOW_SHA ?? "",
  node: process.version,
  started_at: new Date().toISOString(),
  project_id: project.id,
  lighthouse: [],
  fps: [],
};

const lighthouseEnv = {
  ...process.env,
  MANGAFLOW_WEB_URL: WEB_URL,
  MANGAFLOW_PROJECT_ID: project.id,
};
const fpsEnv = {
  ...process.env,
  MANGAFLOW_WEB_ORIGIN: WEB_URL,
  MANGAFLOW_API_ORIGIN: API_URL,
};

let failed = false;
for (const round of [1, 2]) {
  console.log(`\n--- Lighthouse round ${round} ---`);
  const result = await run(
    "npm",
    ["run", "test:lighthouse", "--workspace", "@mangaflow/web"],
    { env: lighthouseEnv },
  );
  summary.lighthouse.push({ round, exit_code: result.code, output: result.output.trim() });
  if (result.code !== 0) failed = true;
}

for (const round of [1, 2]) {
  console.log(`\n--- Workflow FPS round ${round} ---`);
  const result = await run(
    "npm",
    ["run", "test:workflow-fps", "--workspace", "@mangaflow/web"],
    { env: fpsEnv },
  );
  summary.fps.push({ round, exit_code: result.code, output: result.output.trim() });
  if (result.code !== 0) failed = true;
}

summary.finished_at = new Date().toISOString();
await writeFile(path.join(OUT_DIR, "summary.json"), JSON.stringify(summary, null, 2));
children.forEach(stop);
console.log(`Wrote ${path.join(OUT_DIR, "summary.json")}`);
if (failed) process.exitCode = 1;
