import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Job } from "@/lib/api";

import type { GenerationWorkspace } from "./use-generation-workspace";
import { InspectionPanel } from "./inspection-panel";

// #546-10：异步检查状态容器必须带 role="status" + aria-live="polite"，
// 屏幕阅读器才能播报「检查任务运行中」的进度变化。
describe("InspectionPanel 异步状态可感知（#546-10）", () => {
  it("等待检查结果时渲染 polite 的 status 区域", () => {
    const reviewJob: Job = {
      id: "job-1",
      project_id: "project-1",
      target_type: "PAGE_CANDIDATE",
      target_id: "candidate-1",
      job_type: "PAGE_INSPECT",
      status: "RUNNING",
      progress: 40,
      attempt_count: 1,
      max_attempts: 3,
      model_alias: null,
      error_code: null,
      error_message: null,
      workflow_run_id: null,
      workflow_node_id: null,
      duration_ms: null,
      usage_summary: {},
      estimated_cost: null,
      result: null,
      created_at: "2026-09-01T00:00:00Z",
      archived_at: null,
    };
    const idleMutation = {
      isPending: false,
      isError: false,
      error: null,
      variables: undefined,
      mutate: () => undefined,
      reset: () => undefined,
    };

    render(
      <InspectionPanel
        latestInspections={[] as GenerationWorkspace["latestInspections"]}
        reviewJob={reviewJob}
        inspectCandidate={idleMutation as unknown as GenerationWorkspace["inspectCandidate"]}
        repairCandidate={idleMutation as unknown as GenerationWorkspace["repairCandidate"]}
        upscaleCandidate={idleMutation as unknown as GenerationWorkspace["upscaleCandidate"]}
        onClose={() => undefined}
      />,
    );

    const status = screen.getByRole("status");
    expect(status).toHaveAttribute("aria-live", "polite");
    expect(status).toHaveTextContent(/检查任务/);
  });
});
