import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  assertPortFree,
  stopOwned,
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
