import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Job } from "@/lib/api";

import { JobsSection } from "./jobs-section";
import { useJobsWorkspace } from "./use-jobs-workspace";

const jobsApi = vi.spyOn(api, "jobs");
const cancelJob = vi.spyOn(api, "cancelJob");
const retryJob = vi.spyOn(api, "retryJob");

function jobFixture(overrides: Partial<Job>): Job {
  return {
    id: "job-1",
    project_id: "project-1",
    target_type: "PAGE_CANDIDATE",
    target_id: "candidate-1",
    job_type: "PAGE_GENERATE",
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
    created_at: "2026-08-29T10:00:00Z",
    archived_at: null,
    ...overrides,
  };
}

function JobsHarness() {
  const workspace = useJobsWorkspace({ id: "project-1", section: "jobs" });
  return (
    <JobsSection
      jobs={workspace.jobs}
      workspace={workspace}
      modelOptions={[]}
      openPreview={() => undefined}
    />
  );
}

describe("JobsSection", () => {
  beforeEach(() => {
    cancelJob.mockReset().mockResolvedValue(jobFixture({ id: "cancelled-job", status: "CANCELLED" }));
    retryJob.mockReset().mockResolvedValue(jobFixture({ id: "retried-job", status: "WAITING" }));
  });

  it("运行中的任务提供取消，失败任务提供重试，且调用对应接口", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({ id: "job-running", status: "RUNNING", progress: 55 }),
      jobFixture({
        id: "job-failed",
        status: "FAILED",
        progress: 100,
        attempt_count: 2,
        error_code: "WORKER_ERROR",
        error_message: "模型调用失败",
      }),
    ]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("失败任务")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => {
      expect(cancelJob).toHaveBeenCalledWith("job-running");
    });

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(retryJob).toHaveBeenCalledWith("job-failed");
    });
  });

  it("近期/历史切换只保留任务中心入口状态，历史视图提供恢复操作", async () => {
    jobsApi.mockReset().mockImplementation((_projectId: string, archived?: boolean) =>
      Promise.resolve(
        archived
          ? [jobFixture({ id: "job-archived", status: "COMPLETED", archived_at: "2026-08-28T00:00:00Z" })]
          : [],
      ),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByText("当前没有任务")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: "历史记录" }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "恢复" })).toBeInTheDocument();
    });
  });
});
