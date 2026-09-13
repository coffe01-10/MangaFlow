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
import { lstat, readFile } from "node:fs/promises";
import { join, extname, resolve, sep } from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { connect } from "node:net";
import { chromium } from "playwright";
// fileURLToPath, not `.pathname`: on Windows `.pathname` yields `/C:/...`,
// which join/resolve/spawn all mishandle (recorded as a residual in the N2
// audit §11 before this fix). POSIX output is unchanged.
import { fileURLToPath } from "node:url";

const DESKTOP_ROOT = fileURLToPath(new URL("..", import.meta.url));
const REPO_ROOT = fileURLToPath(new URL("../../..", import.meta.url));
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

// Module-scope handle on the spawned helper so the main().catch teardown can
// reach it. The readiness timer and fail() already kill the helper's tree
// (process group on POSIX, taskkill /T on Windows — killHelperTree below)
// on their paths; but any OTHER throw after spawn that bypasses fail()
// (malformed READY JSON, an unreadable journal, a browser-phase exception)
// used to fall into main().catch → process.exit(1) with the setsid'd helper
// still alive on its loopback port — the same orphan class as the readiness
// timeout (#346).
let helper;

// Single choke-point for EVERY teardown kill of the helper (#588). The old
// per-path `process.kill(-helper.pid, "SIGKILL")` is POSIX process-group
// semantics: on Windows libuv rejects pid<=0 with EINVAL without attempting
// anything, every call site's try/catch swallowed that as "already gone",
// and the helper stayed alive holding its loopback port.
// - win32: `taskkill /PID <pid> /T /F` tree-kills by pid. The helper is a
//   plain spawn() (no Job Object exists to close), so /T is the approved
//   minimal fix; taskkill's /F means the SIGTERM/SIGKILL distinction
//   collapses to the same forced kill on that platform.
// - POSIX: keep the negative-pid group kill (the helper is setsid'd).
// Outcome reporting: ESRCH — and taskkill exit 128 ("process not found")
// after a real attempt — means already gone and stays silent; any other
// failure (EINVAL/EPERM/spawn error/non-zero taskkill exit) prints a
// one-line stderr diagnostic naming the pid, so the log stops misreporting
// a kill that never happened as "already gone".
function killHelperTree(child, signal = "SIGKILL") {
  if (!child?.pid) return;
  // Reaped-child short-circuit (pid-reuse hazard): node sets exitCode once
  // the child has been reaped; from that moment the OS may recycle the pid,
  // and a `taskkill /PID <pid> /T /F` (or the POSIX group kill) aimed at
  // the stale pid would tree-kill an innocent recycled process. There is
  // also nothing left to kill, so return BEFORE either platform branch.
  if (child.exitCode !== null) return;
  if (process.platform === "win32") {
    const kill = spawnSync(
      "taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    if (kill.error) {
      console.error(`D5: helper tree kill for pid ${child.pid} could not run: ${kill.error.message}`);
    } else if (kill.status !== 0 && kill.status !== 128) {
      console.error(`D5: helper tree kill for pid ${child.pid} failed: taskkill exited ${kill.status}`);
    }
    return;
  }
  try {
    process.kill(-child.pid, signal);
  } catch (error) {
    if (error?.code === "ESRCH") return; // group already gone: silent ok
    console.error(`D5: helper tree kill for pid ${child.pid} failed: ${error?.code ?? error}`);
  }
}

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

let ok = true;

function fail(message) {
  // Every failure — including the request-handler fence breaches that fire
  // before the ok flag's declaration point in the flow — must clear ok:
  // otherwise the final `process.exitCode = ok ? 0 : 1` turns a breached
  // fence into "D5 PASS" + exit 0 (E2 review, HIGH).
  ok = false;
  console.error(`D5 FAIL: ${message}`);
  // The helper is setsid'd (own process group), so the negative-pid kill
  // reaches it and anything it spawned. Without this, a helper hung before
  // READY (stdin-EOF watch never armed, nothing reads its stdin) outlives
  // the script while holding its loopback port (E-review L1a). #588: the
  // kill routes through killHelperTree so Windows does not silently skip
  // it (libuv rejects pid<=0 with EINVAL there).
  killHelperTree(helper);
  process.exitCode = 1;
}

// ---- 1. sidecar helper + frozen handshake --------------------------------
helper = spawn(PYTHON, [HELPER, "app", "--api-root", join(REPO_ROOT, "apps/api"),
  "--user-data", user_data, "--fake-channel", "--web-origin", WEB_ORIGIN], {
  env: { ...process.env, MANGAFLOW_DESKTOP_TOKEN: token, MANGAFLOW_DESKTOP_JOURNAL: journal,
    MANGAFLOW_DISABLE_DOTENV: "1" },
  stdio: ["pipe", "pipe", "inherit"],
});
const readyLine = await new Promise((resolve, reject) => {
  const timer = setTimeout(() => {
    // A helper hung before READY has no stdin-EOF watch armed (that is
    // armed after GO), so nothing would terminate it: kill the process
    // group before failing, or it outlives the script holding its
    // loopback port (E2 review of #353 — the headline fix bypassed
    // fail() on exactly this path). #588: via killHelperTree so the kill
    // also happens on Windows (taskkill tree-kill).
    killHelperTree(helper);
    reject(new Error("helper readiness timeout"));
  }, 20000);
  // Accumulate until the first newline: stdout is a pipe, so the READY
  // line is not guaranteed to arrive in one chunk — a partial first chunk
  // resolved here would fail the startsWith check below and report a
  // phantom "bad ready line".
  let buffer = "";
  const onData = (chunk) => {
    buffer += chunk.toString();
    // The helper's stdout is protocol-only before READY; megabytes of
    // newline-free output mean a rogue import looping on print — bound the
    // buffer and kill the group like the timeout path does.
    if (buffer.length > 1048576) {
      killHelperTree(helper);
      cleanup();
      reject(new Error("helper stdout exceeded 1 MiB before READY"));
      return;
    }
    const newline = buffer.indexOf("\n");
    if (newline === -1) return;
    cleanup();
    resolve(buffer.slice(0, newline));
  };
  // A helper that dies before READY (an import error, for example) must
  // fail NOW with its exit status — waiting for the timer would spend the
  // full 20s and misreport a crash as a "readiness timeout".
  const onExit = (code, signal) => {
    cleanup();
    reject(new Error(`helper exited before READY (code ${code} signal ${signal})`));
  };
  // A spawn failure (no python3 binary) emits 'error', not 'exit' — an
  // unhandled 'error' event would crash the script with a raw stack
  // instead of this diagnosis.
  const onError = (error) => {
    cleanup();
    reject(new Error(`helper could not be spawned: ${error.message}`));
  };
  const cleanup = () => {
    clearTimeout(timer);
    helper.stdout.off("data", onData);
    helper.off("exit", onExit);
    helper.off("error", onError);
  };
  helper.stdout.on("data", onData);
  helper.once("exit", onExit);
  helper.once("error", onError);
});
// Track the helper's exit from the earliest possible moment: fail() may
// SIGKILL the process group at ANY later point (including while the script
// is inside the browser phase), and an exit listener registered only in the
// teardown block would miss that event and hang on the give-up timer.
let helper_exit_resolve;
const helper_exit = new Promise((resolve) => { helper_exit_resolve = resolve; });
helper.once("exit", (code, signal) => helper_exit_resolve({ code, signal }));
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
    // Per-attempt timeout: a connection that is accepted but never
    // responded would otherwise hang the loop past the 50-attempt bound
    // (E-review L1b).
    const probe = await fetch(`${ready.api_origin}/api/v1/health`,
      { signal: AbortSignal.timeout(2000) });
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
    // Containment first: unlike a browser, a raw HTTP client can send `..`
    // segments verbatim (curl --path-as-is), so resolve and refuse anything
    // that would leave the static export root instead of trusting the URL.
    const root = resolve(FRONTEND);
    let file = resolve(root, `.${decodeURIComponent(path)}`);
    if (file !== root && !file.startsWith(root + sep)) {
      throw new Error("path escapes the static export root");
    }
    // In-root symlinks are refused like escapes (#317): stat/readFile FOLLOW
    // them, so a planted link (or one whose target swaps after validation)
    // would serve content the resolve-only containment check never saw.
    let info = await lstat(file);
    if (info.isSymbolicLink()) {
      throw new Error("symlink inside the static export root");
    }
    if (info.isDirectory()) {
      file = join(file, "index.html");
      info = await lstat(file);
      if (info.isSymbolicLink()) {
        throw new Error("symlink inside the static export root");
      }
    }
    const body = await readFile(file);
    res.writeHead(200, { "content-type": MIME[extname(file)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    const body = await readFile(join(FRONTEND, "404.html")).catch(() => "404");
    res.writeHead(404, { "content-type": "text/html" });
    res.end(body);
  }
});
try {
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(STATIC_PORT, "127.0.0.1", resolve);
  });
} catch (error) {
  // A busy 4173 (leftover D5 run, dev server) used to surface as an
  // unhandled 'error' event with a raw stack; name the cause and the fix.
  console.error(
    `D5 FAIL: static port ${STATIC_PORT} on 127.0.0.1 is busy ` +
    `(${error?.code ?? error}) — stop the other listener and retry.`,
  );
  process.exit(1);
}

// ---- 2b-2. sibling-prefix fence probe (round-5 review) --------------------
// The far-escape probe above cannot catch the classic `startsWith(root)`
// (missing `+ sep`) weakening: a resolved sibling of the export root
// (`.../dist/frontend-sibling-probe/...`) fails BOTH the strict and the
// weakened check only when the sibling does not exist - so this probe
// CREATES the sibling with a real file. With the correct fence
// (`startsWith(root + sep)`) it answers 404; a sep-dropped regression
// answers 200 with the file's bytes. The fixture is removed after the
// probe; the server is already listening when it is created (creation
// only needs the filesystem, so the comment's earlier "before the server
// starts" was wrong — ordering with the listener is irrelevant here).
{
  const { mkdir, writeFile, rm } = await import("node:fs/promises");
  const siblingDir = join(FRONTEND, "..", "frontend-sibling-probe");
  const mark = join(siblingDir, "mark.txt");
  let fixture = true;
  try {
    await mkdir(siblingDir, { recursive: true });
    await writeFile(mark, "d5 sibling probe\n", "utf-8");
  } catch {
    fixture = false;
    console.log("D5 sibling-prefix probe skipped: fixture creation failed");
  }
  if (fixture) {
    try {
      const viaSibling = await new Promise((settle) => {
        const timer = setTimeout(() => sock2.destroy(), 3000);
        const settleOnce = (raw) => {
          clearTimeout(timer);
          settle(raw);
        };
        const sock2 = connect(STATIC_PORT, "127.0.0.1", () => {
          sock2.write(
            "GET /../frontend-sibling-probe/mark.txt HTTP/1.1\r\n" +
            "Host: 127.0.0.1\r\nConnection: close\r\n\r\n",
          );
        });
        let raw = "";
        sock2.setEncoding("latin1");
        sock2.on("data", (chunk) => { raw += chunk; });
        sock2.on("close", () => settleOnce(raw));
        sock2.on("error", () => settleOnce(raw));
      });
      const status = Number(viaSibling.split("\r\n")[0]?.split(" ")[1] ?? 0);
      if (status !== 404) {
        fail(`sibling-prefix served: answered ${status} (must be 404)`);
      } else {
        console.log("D5 sibling-prefix fence ok: sibling dir refused with 404");
      }
    } finally {
      await rm(siblingDir, { recursive: true, force: true }).catch(() => {});
    }
  }
}

// ---- 2b. path-fence self-test (N2 audit §7) ------------------------------
// The containment check in the handler above has no other executable
// verification: pin it here with an encoded-traversal request sent over a
// RAW socket. WHATWG URL parsing (fetch) would decode %2e%2e and collapse
// the dot segments before they ever reach the handler; a raw request keeps
// them verbatim so decodeURIComponent + resolve inside the handler is what
// gets exercised. The target must EXIST when the fence is missing - four
// levels up is the repository root - otherwise the handler's generic 404
// would mask the breach.
{
  const traversal = await new Promise((settle) => {
    const timer = setTimeout(() => sock.destroy(), 3000);
    const settleOnce = (raw) => {
      clearTimeout(timer);
      settle(raw);
    };
    const sock = connect(STATIC_PORT, "127.0.0.1", () => {
      sock.write(
        "GET /%2e%2e/%2e%2e/%2e%2e/%2e%2e/package.json HTTP/1.1\r\n" +
        "Host: 127.0.0.1\r\nConnection: close\r\n\r\n",
      );
    });
    let raw = "";
    sock.setEncoding("latin1");
    sock.on("data", (chunk) => { raw += chunk; });
    sock.on("close", () => settleOnce(raw));
    sock.on("error", () => settleOnce(raw));
  });
  const status = Number(traversal.split("\r\n")[0]?.split(" ")[1] ?? 0);
  if (status !== 404) {
    fail(`path fence breached: encoded traversal answered ${status} (must be 404)`);
  } else {
    console.log("D5 path fence ok: encoded traversal refused with 404");
  }
}

// ---- 2c. in-root symlink fence (#317) -------------------------------------
// The lstat refusal above has no other executable verification: plant a link
// INSIDE the export root (so the resolve fence passes) whose target exists,
// and require the server to answer 404 instead of following it. Symlink
// creation can be unavailable (Windows without developer mode); the probe
// skips loudly rather than silently weakening the fence check.
{
  const { symlink, unlink } = await import("node:fs/promises");
  const probe = join(FRONTEND, "d5-symlink-probe.json");
  let planted = true;
  try {
    await symlink(join(REPO_ROOT, "package.json"), probe, "file");
  } catch {
    planted = false;
  }
  if (!planted) {
    console.log("D5 symlink fence skipped: symlink creation unavailable on this host");
  } else {
    try {
      const viaLink = await new Promise((settle) => {
        const timer = setTimeout(() => sock.destroy(), 3000);
        const settleOnce = (raw) => {
          clearTimeout(timer);
          settle(raw);
        };
        const sock = connect(STATIC_PORT, "127.0.0.1", () => {
          sock.write(
            "GET /d5-symlink-probe.json HTTP/1.1\r\n" +
            "Host: 127.0.0.1\r\nConnection: close\r\n\r\n",
          );
        });
        let raw = "";
        sock.setEncoding("latin1");
        sock.on("data", (chunk) => { raw += chunk; });
        sock.on("close", () => settleOnce(raw));
        sock.on("error", () => settleOnce(raw));
      });
      const status = Number(viaLink.split("\r\n")[0]?.split(" ")[1] ?? 0);
      if (status !== 404) {
        fail(`in-root symlink served: answered ${status} (must be 404)`);
      } else {
        console.log("D5 symlink fence ok: in-root link refused with 404");
      }
    } finally {
      await unlink(probe).catch(() => {});
    }
  }
}

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
// The killHelperTree escalation still covers the cooperative-grace case;
// the exit promise above was registered at spawn, so a helper already
// killed by fail() settles immediately instead of hanging on the give-up
// timer. #588: the grace path routes through killHelperTree like every
// other teardown kill (on Windows both calls are the same forced taskkill
// tree-kill; the second is a silent no-op once the tree is gone).
let settled = false;
const finish = (code) => { if (!settled) { settled = true; helper_exit_resolve(code); } };
const giveUp = setTimeout(() => finish(-1), 40000);
const killGrace = setTimeout(() => {
  killHelperTree(helper, "SIGTERM");
  setTimeout(() => killHelperTree(helper, "SIGKILL"), 10000);
}, 15000);
void helper_exit.then((code) => { clearTimeout(killGrace); clearTimeout(giveUp); finish(code); });
const helper_exit_code = (await helper_exit).code;
if (helper_exit_code !== 0) {
    ok = false;
    fail(`helper exit ${helper_exit_code ?? "give-up"}`);
  }
console.log(ok ? "D5 PASS: static export + runtime origin injection + direct CORS-allowed API verified" : "D5 FAILED");
process.exitCode = ok ? 0 : 1;
}

main().catch((error) => {
  console.error("D5 FAIL:", error);
  // Same orphan class as the readiness timeout: this catch sees every
  // rejection/throw that leaves main() without passing through fail()
  // (which kills the group itself). Best-effort, mirroring fail() — and
  // #588: routed through killHelperTree so Windows actually kills the
  // tree instead of swallowing libuv's EINVAL as "already gone". If the
  // helper is already gone the kill stays silent there.
  if (helper) killHelperTree(helper);
  process.exit(1);
});
