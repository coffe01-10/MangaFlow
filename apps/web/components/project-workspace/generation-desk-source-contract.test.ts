import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Source contract for the generation desk's jobs view scope (RC review E1)
 * and the inspection panel's terminal-status semantics (E2).
 *
 * E1: after the user visits 任务中心 → 历史记录, the toggle-scoped jobs query
 * (["jobs", id, true]) returns only archived rows — running PAGE_INSPECT jobs
 * disappear from it. If the generation workspace consumes that view, the
 * inspection poll stops, the terminal invalidation never fires and the
 * production gate silently stays blocked. The desk must consume the pinned
 * active view (`dockJobs`), the same rationale as the queue dock.
 *
 * E2: the inspection wait spinner must use the shared terminal-status
 * predicate so CANCELLED / NEEDS_REVIEW jobs do not render an eternal
 * spinner; the fixed inline list predates the shared semantics.
 */

const workspaceSource = readFileSync(
  join(process.cwd(), "components/project-workspace.tsx"),
  "utf8",
);
const panelSource = readFileSync(
  join(process.cwd(), "components/project-workspace/inspection-panel.tsx"),
  "utf8",
);

describe("生成台 jobs 视图作用域（源码契约）", () => {
  it("useGenerationWorkspace 消费固定近期视图 dockJobs，而非归档切换视图 jobs", () => {
    const call = workspaceSource.match(/useGenerationWorkspace\(\{[\s\S]*?\}\);/);
    expect(call).not.toBeNull();
    expect(call![0]).toContain("jobs: dockJobs");
    expect(call![0]).not.toMatch(/^\s*jobs,$/m);
  });

  it("检查面板等待态使用共享终态谓词 isTerminalTaskStatus", () => {
    expect(panelSource).toContain("isTerminalTaskStatus");
    expect(panelSource).not.toContain('["COMPLETED", "FAILED"].includes');
  });
});
