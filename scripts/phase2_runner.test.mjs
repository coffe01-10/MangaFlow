import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  assertPortFree,
  finalizeOwnedRun,
  assertSupervised,
  waitForOwnedHealth,
} from "./phase2_runner.mjs";

test("occupied ports fail closed without contacting unknown health", async () => {
  const connect = () => {
    const socket = new EventEmitter();
    queueMicrotask(() => socket.emit("connect"));
    socket.end = () => undefined;
    return socket;
  };
  await assert.rejects(
    () => assertPortFree(8000, connect),
    /port 8000 is occupied; refusing to use an unknown instance/,
  );
});

test("health from another instance is rejected", async () => {
  const child = { exitCode: null, owned: true, pid: 4242 };
  await assert.rejects(
    () => waitForOwnedHealth({
      url: "http://127.0.0.1:8000/api/v1/health",
      runId: "run-a",
      child,
      timeoutMs: 1_000,
      fetchImpl: async () => ({
        ok: true,
        json: async () => ({ status: "ok", e2e_run_id: "someone-else" }),
      }),
      sleep: async () => undefined,
    }),
    /health identity mismatch/,
  );
});

test("spawn failure is not treated as a healthy owned API", async () => {
  const child = { exitCode: null, owned: true, pid: undefined, spawnError: new Error("ENOENT") };
  await assert.rejects(
    () => waitForOwnedHealth({
      url: "http://127.0.0.1:8000/api/v1/health",
      runId: "run-a",
      child,
      timeoutMs: 1_000,
      fetchImpl: async () => ({ ok: true, json: async () => ({ status: "ok" }) }),
      sleep: async () => undefined,
    }),
    /failed to spawn/,
  );
});

test("dead child is not treated as a healthy owned API", async () => {
  const child = { exitCode: 1, owned: true, pid: 9 };
  await assert.rejects(
    () => waitForOwnedHealth({
      url: "http://127.0.0.1:8000/api/v1/health",
      runId: "run-a",
      child,
      timeoutMs: 1_000,
      fetchImpl: async () => ({ ok: true, json: async () => ({ status: "ok" }) }),
      sleep: async () => undefined,
    }),
    /owned process exited 1/,
  );
});

test("unsafe raw PID and path deletion APIs no longer exist", async () => {
  const helpers = await import("./phase2_runner.mjs");
  assert.equal("stopPidTree" in helpers, false);
  assert.equal("removeOwnedRuntime" in helpers, false);
  assert.equal("createOwnedRuntime" in helpers, false);
});

test("uncontrolled entry fails before launching services", () => {
  const original = process.env.MANGAFLOW_E2E_RUN_ID;
  delete process.env.MANGAFLOW_E2E_RUN_ID;
  try {
    assert.throws(() => assertSupervised(), /requires its controller/);
  } finally {
    if (original !== undefined) process.env.MANGAFLOW_E2E_RUN_ID = original;
  }
});

test("unexpected port errors do not count as a free endpoint", async () => {
  const connect = () => {
    const socket = new EventEmitter();
    queueMicrotask(() => socket.emit("error", Object.assign(new Error("denied"), { code: "EACCES" })));
    return socket;
  };
  await assert.rejects(() => assertPortFree(8000, connect), /denied/);
});

test("cleanup errors land in the final summary and fail the run", async () => {
  const summary = { errors: ["lighthouse or fps gate failed"] };
  let wrote = null;
  const exitCode = await finalizeOwnedRun({
    summary,
    cleanup: async () => {
      summary.runtime_removed = false;
      summary.errors.push("failed to remove owned runtime C:\\\\Temp\\\\mangaflow-e2e-abc");
    },
    writeSummary: async (payload) => {
      wrote = { ...payload, errors: [...payload.errors] };
    },
  });
  assert.equal(exitCode, 1);
  assert.equal(wrote.runtime_removed, false);
  assert.equal(wrote.errors.includes("failed to remove owned runtime C:\\\\Temp\\\\mangaflow-e2e-abc"), true);
  assert.ok(wrote.finished_at);
});

test("cleanup throw still writes summary and is non-zero", async () => {
  const summary = { errors: [] };
  let wrote = null;
  const exitCode = await finalizeOwnedRun({
    summary,
    cleanup: async () => {
      throw new Error("failed to remove owned runtime after retries");
    },
    writeSummary: async (payload) => {
      wrote = { ...payload, errors: [...payload.errors] };
    },
  });
  assert.equal(exitCode, 1);
  assert.equal(wrote.runtime_removed, false);
  assert.match(wrote.errors.join(" "), /failed to remove owned runtime/);
});

test("defaultPython prefers the explicit override and maps the venv per platform", async () => {
  const { defaultPython } = await import("./phase2_runner.mjs");
  const original = process.env.MANGAFLOW_PYTHON;
  try {
    process.env.MANGAFLOW_PYTHON = "/opt/custom/python";
    assert.equal(defaultPython("C:/repo", "win32"), "/opt/custom/python");
    assert.equal(defaultPython("/repo"), "/opt/custom/python");
    delete process.env.MANGAFLOW_PYTHON;
    assert.equal(defaultPython("C:/repo", "win32"), "C:/repo\\.venv\\Scripts\\python.exe");
    assert.equal(defaultPython("/repo", "linux"), "/repo/.venv/bin/python");
    // The production entry passes no platform argument: the host platform
    // decides. Conditional-exact (not a disjunction): a disjunction would
    // accept a lib that hard-pins the WRONG platform on every host.
    // (The win32 expectation mirrors line 143's committed contract: the same
    // `${root}\` join as the explicit branch. The forward-slash spelling here
    // was a never-executed-on-Windows typo — POSIX hosts skip this branch.)
    if (process.platform === "win32") {
      assert.equal(defaultPython("/repo"), "/repo\\.venv\\Scripts\\python.exe");
    } else {
      assert.equal(defaultPython("/repo"), "/repo/.venv/bin/python");
    }
  } finally {
    if (original !== undefined) process.env.MANGAFLOW_PYTHON = original;
  }
});

test("a never-answering port probe times out with a named error", async () => {
  const connect = () => {
    // Neither connect nor error ever fires: only the 2s in-lib timer ends
    // it. The shim carries setTimeout so the lib can arm that timer.
    const socket = new EventEmitter();
    socket.setTimeout = (ms, cb) => setTimeout(cb, ms);
    socket.destroy = () => undefined;
    return socket;
  };
  const started = Date.now();
  await assert.rejects(
    () => assertPortFree(8000, connect),
    /port probe timeout/,
  );
  assert.ok(Date.now() - started >= 1_500, "the probe must honor its window");
});

test("waitForOwnedHealth names the last health error at timeout", async () => {
  const child = { exitCode: null, owned: true, pid: 4242 };
  await assert.rejects(
    () => waitForOwnedHealth({
      url: "http://127.0.0.1:8000/api/v1/health",
      runId: "run-a",
      child,
      timeoutMs: 300,
      fetchImpl: async () => ({ ok: false, status: 503 }),
      sleep: async () => undefined,
    }),
    /timed out waiting for owned health.*503/s,
  );
});

test("json() keeps 204 as null, merges content-type, and names the failing call", async () => {
  const { json } = await import("./phase2_runner.mjs");
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push([url, init?.headers?.["content-type"]]);
    if (url.endsWith("no-content")) {
      return { ok: true, status: 204 };
    }
    if (url.endsWith("boom")) {
      return { ok: false, status: 500, text: async () => "boom body" };
    }
    return { ok: true, status: 200, json: async () => ({ ok: 1 }) };
  };
  assert.equal(await json("http://x/no-content", {}, fetchImpl), null);
  const payload = await json("http://x/data", { method: "POST", body: "{}" }, fetchImpl);
  assert.deepEqual(payload, { ok: 1 });
  // content-type must be set even when the caller passes other headers.
  await json("http://x/data", { headers: { "x-trace": "t" } }, fetchImpl);
  assert.equal(seen[2][1], "application/json");
  await assert.rejects(
    () => json("http://x/boom", { method: "DELETE" }, fetchImpl),
    /DELETE http:\/\/x\/boom: 500 boom body/,
  );
});

