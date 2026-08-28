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
