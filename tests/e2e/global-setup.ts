import { assertSupervised, waitForOwnedHealth } from "../../scripts/phase2_runner.mjs";

export default async function globalSetup() {
  const { runId } = assertSupervised(8000);
  assertSupervised(3000);
  await waitForOwnedHealth({
    url: "http://127.0.0.1:8000/api/v1/health",
    runId,
    child: { exitCode: null },
    timeoutMs: 10_000,
  });
}
