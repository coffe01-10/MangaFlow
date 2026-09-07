// V02-53B D5 browser-level verification: static export + runtime origin
// injection + direct loopback API + CORS.
//
// Simulates exactly what the Tauri shell does:
//   1. spawns the sidecar helper and runs the frozen handshake (READY line →
//      token/PID/journal verify → GO → loopback health);
//   2. serves the static export from dist/frontend;
//   3. injects the verified origin synchronously before any page script
//      (initialization script equivalent);
//   4. asserts the exported app calls the API origin DIRECTLY (no /api path
//      on the static server, no NEXT_PUBLIC dependency) and renders.
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { join, extname } from "node:path";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

const DESKTOP_ROOT = new URL("..", import.meta.url).pathname;
const REPO_ROOT = new URL("../../..", import.meta.url).pathname;
const FRONTEND = join(DESKTOP_ROOT, "dist/frontend");
const HELPER = join(DESKTOP_ROOT, "sidecar/mangaflow_desktop_helper.py");
const PYTHON = process.env.MANGAFLOW_DESKTOP_PYTHON ?? "python3";
const STATIC_PORT = 4173;
const WEB_ORIGIN = `http://127.0.0.1:${STATIC_PORT}`;
const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
  ".txt": "text/plain", ".ico": "image/x-icon", ".woff2": "font/woff2",
};

async function main() {
const token = (await import("node:crypto")).randomBytes(16).toString("hex");
const user_data = await (async () => {
  const { mkdtemp } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  return mkdtemp(join(tmpdir(), "mangaflow-desktop-d5-"));
})();
const runtime = join(user_data, `runtime/mangaflow-desktop-${token}`);
await (await import("node:fs/promises")).mkdir(runtime, { recursive: true });
const journal = join(runtime, "owner.json");

function fail(message) {
  console.error(`D5 FAIL: ${message}`);
  process.exitCode = 1;
}

// ---- 1. sidecar helper + frozen handshake --------------------------------
const helper = spawn(PYTHON, [HELPER, "app", "--api-root", join(REPO_ROOT, "apps/api"),
  "--user-data", user_data, "--fake-channel", "--web-origin", WEB_ORIGIN], {
  env: { ...process.env, MANGAFLOW_DESKTOP_TOKEN: token, MANGAFLOW_DESKTOP_JOURNAL: journal,
    MANGAFLOW_DISABLE_DOTENV: "1" },
  stdio: ["pipe", "pipe", "inherit"],
});
const readyLine = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => reject(new Error("helper readiness timeout")), 20000);
  helper.stdout.once("data", (chunk) => {
    clearTimeout(timer);
    resolve(chunk.toString().split("\n")[0]);
  });
});
if (!readyLine.startsWith("MANGAFLOW_READY ")) return fail(`bad ready line: ${readyLine}`);
const ready = JSON.parse(readyLine.slice("MANGAFLOW_READY ".length));
const record = JSON.parse((await readFile(journal)).toString());
if (ready.token !== token) return fail("token mismatch");
if (ready.pid !== helper.pid) return fail("pid mismatch");
if (record.state !== "ready" || record.api_origin !== ready.api_origin) return fail("journal mismatch");
if (!ready.api_origin.startsWith("http://127.0.0.1:")) return fail("origin not loopback");
// The pre-bound socket serves nothing until the shell verifies and sends GO.
helper.stdin.write(`MANGAFLOW_GO ${token}\n`);
let health_ok = false;
for (let attempt = 0; attempt < 50 && !health_ok; attempt += 1) {
  try {
    const probe = await fetch(`${ready.api_origin}/api/v1/health`);
    health_ok = probe.status === 200;
  } catch {
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
}
if (!health_ok) return fail("health not ready after GO");
console.log(`D5 handshake ok, api_origin=${ready.api_origin}`);

// ---- 2. static export server (no /api routes exist here) -----------------
const static_hits = [];
const server = createServer(async (req, res) => {
  static_hits.push(req.url);
  if (req.url.startsWith("/api/")) {
    res.writeHead(404, { "content-type": "application/json" });
    return res.end(JSON.stringify({ error: "static server has no /api routes (D5 contract)" }));
  }
  let path = req.url.split("?")[0];
  if (path.endsWith("/")) path += "index.html";
  try {
    let file = join(FRONTEND, decodeURIComponent(path));
    if ((await stat(file)).isDirectory()) file = join(file, "index.html");
    const body = await readFile(file);
    res.writeHead(200, { "content-type": MIME[extname(file)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    const body = await readFile(join(FRONTEND, "404.html")).catch(() => "404");
    res.writeHead(404, { "content-type": "text/html" });
    res.end(body);
  }
});
await new Promise((resolve) => server.listen(STATIC_PORT, "127.0.0.1", resolve));

// ---- 3+4. browser with shell-equivalent initialization script ------------
const browser = await chromium.launch({ args: ["--no-sandbox"] });
const context = await browser.newContext();
await context.addInitScript(`window.__MANGAFLOW_API_ORIGIN__ = '${ready.api_origin}';`);
const page = await context.newPage();
const api_requests = [];
const api_bad = [];
const page_errors = [];
page.on("request", (request) => {
  if (request.url().startsWith(ready.api_origin)) api_requests.push(request.url());
});
page.on("response", (response) => {
  if (response.url().startsWith(ready.api_origin) && response.status() >= 300) {
    api_bad.push(`${response.status()} ${response.url()}`);
  }
});
page.on("pageerror", (error) => page_errors.push(String(error)));

await page.goto(`${WEB_ORIGIN}/`, { waitUntil: "networkidle", timeout: 60000 });
await page.waitForTimeout(1500);

const body_text = await page.evaluate(() => document.body.innerText);
const evidence = {
  api_request_count: api_requests.length,
  api_bad,
  static_api_hits: static_hits.filter((url) => url?.startsWith("/api/")),
  page_errors,
  injected_origin: await page.evaluate(() => window.__MANGAFLOW_API_ORIGIN__ ?? null),
  api_origin_env_free: await page.evaluate(() =>
    Object.keys(window).filter((key) => key.startsWith("__MANGAFLOW_ORIGIN")).length === 0),
  rendered_marker: body_text.includes("新建项目") || body_text.includes("最近创作"),
};
let ok = true;
if (evidence.injected_origin !== ready.api_origin) { ok = false; fail("origin injection missing"); }
if (evidence.api_request_count === 0) { ok = false; fail("no direct API request observed"); }
if (evidence.api_bad.length > 0) { ok = false; fail(`API responses failed: ${evidence.api_bad.join(", ")}`); }
if (evidence.static_api_hits.length > 0) { ok = false; fail("app still calls the static server for /api"); }
if (!evidence.rendered_marker) { ok = false; fail("exported dashboard did not render its shell markers"); }
if (page_errors.length > 0) { ok = false; fail(`page errors: ${page_errors.join(" | ")}`); }

console.log("D5 evidence:", JSON.stringify(evidence, null, 2));
console.log("sample api requests:", api_requests.slice(0, 3));

await browser.close();
server.close();
helper.stdin.end();
const exit_code = await new Promise((resolve) => {
  const timer = setTimeout(() => {
    try { process.kill(-helper.pid, "SIGTERM"); } catch {}
    helper.once("exit", (_, signal) => resolve(signal));
  }, 15000);
  helper.once("exit", (code) => { clearTimeout(timer); resolve(code); });
});
if (exit_code !== 0) { ok = false; fail(`helper exit ${exit_code}`); }
console.log(ok ? "D5 PASS: static export + runtime origin injection + direct CORS-allowed API verified" : "D5 FAILED");
process.exitCode = ok ? 0 : 1;
}

main().catch((error) => {
  console.error("D5 FAIL:", error);
  process.exit(1);
});
