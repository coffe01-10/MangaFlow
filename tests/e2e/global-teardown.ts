import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

import { removeOwnedRuntime, stopPidTree, waitUntilPortFree } from "../../scripts/phase2_runner.mjs";

export default async function globalTeardown() {
  let runtime = process.env.MANGAFLOW_E2E_RUNTIME;
  let runId = process.env.MANGAFLOW_E2E_RUN_ID;
  let pid = 0;
  const pointer = path.resolve("output", "playwright", "owned-runtime.json");
  if (existsSync(pointer)) {
    const owned = JSON.parse(readFileSync(pointer, "utf8")) as {
      runtime?: string;
      runId?: string;
      pid?: number;
    };
    runtime = owned.runtime ?? runtime;
    runId = owned.runId ?? runId;
    pid = Number(owned.pid ?? 0);
  }
  if (!runtime || !runId || !runtime.includes(`mangaflow-e2e-${runId}`)) return;
  const pidFile = path.join(runtime, "owner.pid");
  if ((!Number.isInteger(pid) || pid <= 0) && existsSync(pidFile)) {
    pid = Number(readFileSync(pidFile, "utf8").trim());
  }
  if (Number.isInteger(pid) && pid > 0 && pid !== process.pid) {
    try {
      await stopPidTree(pid);
    } catch {
      // already gone; still remove the directory after handles release
    }
  }
  try {
    await waitUntilPortFree(8000, { timeoutMs: 15_000 });
  } catch {
    // keep going so a leftover port does not skip owned-path removal
  }
  await new Promise((resolve) => setTimeout(resolve, 800));
  await removeOwnedRuntime(runtime, runId);
}
