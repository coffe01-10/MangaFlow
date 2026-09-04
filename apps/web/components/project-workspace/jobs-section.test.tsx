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

function JobsHarness({ openPreview = () => undefined }: { openPreview?: (url: string, label: string) => void }) {
  const workspace = useJobsWorkspace({ id: "project-1", section: "jobs" });
  return (
    <JobsSection
      jobs={workspace.jobs}
      workspace={workspace}
      modelOptions={[]}
      openPreview={openPreview}
    />
  );
}

describe("JobsSection", () => {
  beforeEach(() => {
    cancelJob.mockReset().mockResolvedValue(jobFixture({ id: "cancelled-job", status: "CANCELLED" }));
    retryJob.mockReset().mockResolvedValue(jobFixture({ id: "retried-job", status: "WAITING" }));
  });

  it("明确区分完整估算、部分估算与不可用，且不冒充供应商账单", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({
        id: "job-partial-cost",
        status: "COMPLETED",
        estimated_cost: 0.125,
        estimated_cost_currency: "USD",
        estimated_cost_status: "PARTIAL",
        estimated_cost_pricing_versions: ["provider/model:v1"],
        estimated_cost_note: "仅完整估算 1/2 次调用；其余调用缺少 usage 或价格，估算值不等于供应商账单",
      }),
      jobFixture({
        id: "job-no-cost",
        status: "FAILED",
        estimated_cost: null,
        estimated_cost_currency: null,
        estimated_cost_status: "UNAVAILABLE",
        estimated_cost_pricing_versions: [],
        estimated_cost_note: "缺少调用 usage 或对应价格版本，费用不可估算；估算值不等于供应商账单",
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

    expect(await screen.findByText(/部分估算.*USD.*0\.125/)).toBeInTheDocument();
    expect(screen.getByText(/仅完整估算 1\/2 次调用/)).toBeInTheDocument();
    expect(screen.getByText(/缺少调用 usage 或对应价格版本/)).toBeInTheDocument();
    expect(screen.getAllByText(/估算值不等于供应商账单/)).toHaveLength(2);
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

  it("取消成功后会 invalidate jobs 并再次请求列表", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({ id: "job-running", status: "RUNNING", progress: 55 }),
    ]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    });
    const before = jobsApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => {
      expect(cancelJob).toHaveBeenCalledWith("job-running");
      expect(jobsApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("重试成功后会 invalidate jobs 并再次请求列表", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({
        id: "job-failed",
        status: "FAILED",
        progress: 100,
        error_code: "WORKER_ERROR",
        error_message: "模型调用失败",
      }),
    ]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    });
    const before = jobsApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(retryJob).toHaveBeenCalledWith("job-failed");
      expect(jobsApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("同一任务取消 pending 时重复点击只请求一次，其他行仍可取消，成功后刷新列表", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({ id: "job-a", status: "RUNNING", progress: 20 }),
      jobFixture({ id: "job-b", status: "RUNNING", progress: 45 }),
    ]);
    const resolvers: Partial<Record<string, (job: Job) => void>> = {};
    cancelJob.mockReset().mockImplementation(
      (jobId: string) => new Promise((resolve) => {
        resolvers[jobId] = resolve;
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "取消" })).toHaveLength(2);
    });
    const [firstCancel, otherCancel] = screen.getAllByRole("button", { name: "取消" });
    fireEvent.click(firstCancel);
    fireEvent.click(firstCancel);
    await waitFor(() => {
      expect(cancelJob).toHaveBeenCalledTimes(1);
      expect(cancelJob).toHaveBeenCalledWith("job-a");
      expect(firstCancel).toBeDisabled();
      expect(otherCancel).toBeEnabled();
    });
    const before = jobsApi.mock.calls.length;
    fireEvent.click(otherCancel);
    await waitFor(() => {
      expect(cancelJob).toHaveBeenCalledTimes(2);
      expect(cancelJob).toHaveBeenCalledWith("job-b");
    });
    resolvers["job-a"]?.(jobFixture({ id: "job-a", status: "CANCELLED" }));
    await waitFor(() => {
      expect(jobsApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("同一任务重试 pending 时重复点击只请求一次，其他行仍可重试，成功后刷新列表", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({
        id: "job-a",
        status: "FAILED",
        progress: 100,
        error_code: "WORKER_ERROR",
        error_message: "第一次失败",
      }),
      jobFixture({
        id: "job-b",
        status: "FAILED",
        progress: 100,
        error_code: "WORKER_ERROR",
        error_message: "第二次失败",
      }),
    ]);
    const resolvers: Partial<Record<string, (job: Job) => void>> = {};
    retryJob.mockReset().mockImplementation(
      (jobId: string) => new Promise((resolve) => {
        resolvers[jobId] = resolve;
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getAllByRole("button", { name: "重试" })).toHaveLength(2);
    });
    const [firstRetry, otherRetry] = screen.getAllByRole("button", { name: "重试" });
    fireEvent.click(firstRetry);
    fireEvent.click(firstRetry);
    await waitFor(() => {
      expect(retryJob).toHaveBeenCalledTimes(1);
      expect(retryJob).toHaveBeenCalledWith("job-a");
      expect(firstRetry).toBeDisabled();
      expect(otherRetry).toBeEnabled();
    });
    const before = jobsApi.mock.calls.length;
    fireEvent.click(otherRetry);
    await waitFor(() => {
      expect(retryJob).toHaveBeenCalledTimes(2);
      expect(retryJob).toHaveBeenCalledWith("job-b");
    });
    resolvers["job-a"]?.(jobFixture({ id: "job-a", status: "WAITING" }));
    await waitFor(() => {
      expect(jobsApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("失败任务展示用户可见错误，取消请求失败后仍可再次点击", async () => {
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({
        id: "job-failed",
        status: "FAILED",
        progress: 100,
        error_code: "PROVIDER_ERROR",
        error_message: "上游拒绝了这张参考图",
      }),
      jobFixture({ id: "job-running", status: "RUNNING", progress: 10 }),
    ]);
    cancelJob.mockReset().mockRejectedValueOnce(new Error("任务已终态，不能取消"));
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText(/上游拒绝了这张参考图/)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    await waitFor(() => {
      expect(cancelJob).toHaveBeenCalledWith("job-running");
    });
    expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
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

  it("任务结果入口把原图 content URL 交给 lightbox，不改写为缩略图（V02-51B §7）", async () => {
    const openPreview = vi.fn();
    jobsApi.mockReset().mockResolvedValue([
      jobFixture({
        id: "job-image-result",
        status: "COMPLETED",
        progress: 100,
        result: {
          kind: "IMAGE",
          label: "候选 1",
          candidate_id: "candidate-1",
          page_id: "page-1",
          content_url: "/api/v1/assets/asset-9/content",
          thumbnail_url: "/api/v1/assets/asset-9/thumbnail/640",
        },
      }),
    ]);
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <JobsHarness openPreview={openPreview} />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "查看结果" }));
    await waitFor(() => {
      expect(openPreview).toHaveBeenCalledTimes(1);
    });
    const [lightboxUrl] = openPreview.mock.calls[0];
    expect(lightboxUrl).toContain("/api/v1/assets/asset-9/content");
    expect(lightboxUrl).not.toContain("thumbnail/640");
  });
});
