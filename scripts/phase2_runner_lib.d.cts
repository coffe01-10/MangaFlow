// Types for phase2_runner_lib.cjs consumers (playwright.config.ts,
// tests/e2e/global-setup.ts). assertSupervised returns the Python verifier's
// parsed JSON: run identity plus verified listener PIDs when a port is given.
import type { ChildProcess } from "node:child_process";
import type { Socket } from "node:net";

export const API_ORIGIN: string;
export const WEB_URL: string;

export interface SupervisedContext {
  runId: string;
  [key: string]: unknown;
}

export function assertSupervised(port?: number): SupervisedContext;
export function defaultPython(root: string, platform?: NodeJS.Platform): string;

export function assertPortFree(
  port: number,
  connect?: (options: { host: string; port: number }) => Socket,
): Promise<void>;

export interface OwnedChildProcess extends ChildProcess {
  owned: boolean;
  spawnError: Error | null;
}

export function spawnOwned(
  command: string,
  args: readonly string[],
  options?: { stdio?: ChildProcess["stdio"]; cwd?: string; env?: NodeJS.ProcessEnv; shell?: boolean },
): OwnedChildProcess;

export function waitForOwnedHealth(options: {
  url: string;
  runId: string;
  // The lib only reads spawnError/exitCode (falsy checks); callers without a
  // real process pass a stand-in object ({ exitCode: null }).
  child: { exitCode: number | null; spawnError?: Error | null };
  timeoutMs?: number;
  fetchImpl?: typeof fetch;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
}): Promise<{ e2e_run_id: string; [key: string]: unknown }>;

export function finalizeOwnedRun(options: {
  summary: { errors: string[]; runtime_removed?: boolean; [key: string]: unknown };
  cleanup: () => Promise<void>;
  writeSummary: (summary: unknown) => Promise<void>;
}): Promise<number>;

export function json<T = unknown>(
  url: string,
  init?: RequestInit,
  fetchImpl?: typeof fetch,
): Promise<T | null>;
