import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  assertPortFree,
  finalizeOwnedRun,
  removeOwnedRuntime,
  stopOwned,
  stopPidTree,
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

test("stopPidTree refuses the current process", async () => {
  await assert.rejects(() => stopPidTree(process.pid), /invalid pid/);
  await assert.rejects(() => stopPidTree(0), /invalid pid/);
});

test("stopOwned refuses unknown pids and waits for owned exit", async () => {
  await assert.rejects(() => stopOwned({ pid: 1 }), /does not own/);
  const child = new EventEmitter();
  child.owned = true;
  child.pid = 77;
  child.killed = false;
  const waited = stopOwned(child, {
    platform: "linux",
    spawnImpl: () => {
      throw new Error("should not taskkill");
    },
    killImpl: () => undefined,
    waitForExit: async (target) => {
      assert.equal(target.pid, 77);
    },
  });
  await waited;
});

test("taskkill non-zero exit fails stopOwned", async () => {
  const child = { owned: true, pid: 88, spawnError: null };
  const killer = new EventEmitter();
  killer.stderr = new EventEmitter();
  await assert.rejects(
    () => stopOwned(child, {
      platform: "win32",
      spawnImpl: () => {
        queueMicrotask(() => killer.emit("exit", 1));
        return killer;
      },
      waitForExit: async (target) => {
        if (target === killer) return { code: 1, stderr: "access denied" };
        return { code: 0, stderr: "" };
      },
    }),
    /taskkill exited 1/,
  );
});

test("removeOwnedRuntime refuses foreign paths and retries until gone", async () => {
  await assert.rejects(
    () => removeOwnedRuntime("C:\\\\Temp\\\\other", "abc"),
    /does not own/,
  );
  let exists = true;
  let attempts = 0;
  await removeOwnedRuntime("C:\\\\Temp\\\\mangaflow-e2e-abc", "abc", {
    retries: 3,
    existsImpl: () => {
      attempts += 1;
      if (attempts >= 2) exists = false;
      return exists;
    },
    rmImpl: async () => undefined,
    sleep: async () => undefined,
  });
  assert.equal(attempts >= 2, true);
});

test("removeOwnedRuntime failure stays failed after retries", async () => {
  await assert.rejects(
    () => removeOwnedRuntime("/tmp/mangaflow-e2e-xyz", "xyz", {
      retries: 2,
      existsImpl: () => true,
      rmImpl: async () => {
        throw new Error("busy");
      },
      sleep: async () => undefined,
    }),
    /failed to remove owned runtime/,
  );
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
