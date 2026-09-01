import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type ModelCallAttempt,
  type UsageAttemptPage,
  type UsageSummary,
} from "@/lib/api";

import { UsageDashboard } from "./usage-dashboard";

const projectsApi = vi.spyOn(api, "projects");
const usageSummaryApi = vi.spyOn(api, "usageSummary");
const usageAttemptsApi = vi.spyOn(api, "usageAttempts");

function makeAttempt(overrides: Partial<ModelCallAttempt> = {}): ModelCallAttempt {
  return {
    id: "attempt-1",
    job_id: "job-1",
    project_id: "project-1",
    job_attempt: 1,
    dispatch_no: 1,
    dispatch_request_id: "dispatch-1",
    route_switched: false,
    outcome: "SUCCEEDED",
    channel: "HTTP_API",
    provider: "vertex-ai",
    model_id: "imagen-3.0-generate-002",
    catalog_model_id: null,
    connection_id: null,
    selected_key_id: null,
    request_id: "req-e2e-text",
    probe_id: null,
    chapter_id: null,
    page_id: null,
    panel_id: null,
    candidate_id: null,
    started_at: "2026-09-01T10:00:00Z",
    finished_at: null,
    duration_ms: 1200,
    usage: null,
    usage_status: null,
    usage_source: null,
    unit_kind: null,
    input_tokens: null,
    output_tokens: null,
    cached_input_tokens: null,
    cache_hit: null,
    output_images: null,
    output_image_dims: null,
    output_asset_ids: null,
    route_reason: null,
    route_score: null,
    error_code: null,
    error_message: null,
    ...overrides,
  };
}

const populatedSummary: UsageSummary = {
  groups: [
    {
      day: "2026-09-01",
      provider: "vertex-ai",
      model_id: "imagen-3.0-generate-002",
      channel: "HTTP_API",
      attempt_count: 2,
      succeeded_count: 1,
      failed_count: 1,
      pending_count: 0,
      input_tokens: null,
      output_tokens: null,
      cached_input_tokens: null,
      output_images: 2,
      usage_status_counts: { UNKNOWN: 1, PARTIAL: 0, COMPLETE: 1 },
      estimated_costs: [{ currency: "CNY", amount: "1.20" }],
    },
    {
      day: "2026-09-02",
      provider: "openai",
      model_id: "gpt-4o",
      channel: "HTTP_API",
      attempt_count: 1,
      succeeded_count: 1,
      failed_count: 0,
      pending_count: 0,
      input_tokens: 1200,
      output_tokens: 480,
      cached_input_tokens: 300,
      output_images: null,
      usage_status_counts: { UNKNOWN: 0, PARTIAL: 0, COMPLETE: 1 },
      estimated_costs: [{ currency: "USD", amount: "1.50" }],
    },
    {
      day: "2026-09-02",
      provider: "cli-gateway",
      model_id: "codex-cli",
      channel: "CLI",
      attempt_count: 1,
      succeeded_count: 1,
      failed_count: 0,
      pending_count: 0,
      input_tokens: 900,
      output_tokens: 150,
      cached_input_tokens: null,
      output_images: null,
      usage_status_counts: { UNKNOWN: 0, PARTIAL: 0, COMPLETE: 1 },
      estimated_costs: [],
    },
  ],
  billed: [
    {
      id: "recon-1",
      provider: "vertex-ai",
      model_id: "imagen-3.0-generate-002",
      channel: "HTTP_API",
      connection_id: null,
      billing_account_id: "account-a",
      import_batch_id: "batch-1",
      idempotency_key: "line-1",
      period_start: "2026-08-01T00:00:00Z",
      period_end: "2026-09-01T00:00:00Z",
      currency: "CNY",
      billed_amount: "66.00",
      source_note: "8 月账单",
      entered_by: "operator",
      created_at: "2026-09-01T09:00:00Z",
    },
  ],
};

function page(items: ModelCallAttempt[], nextCursor: string | null): UsageAttemptPage {
  return { items, next_cursor: nextCursor };
}

function attemptsPayload(): ModelCallAttempt[] {
  return [
    makeAttempt({
      id: "att-1",
      dispatch_no: 2,
      route_switched: true,
      usage_status: "COMPLETE",
      usage_source: "PROVIDER_REPORTED",
      input_tokens: 1200,
      output_tokens: 480,
      cached_input_tokens: 300,
      cache_hit: true,
      request_id: "req-e2e-text",
    }),
    makeAttempt({
      id: "att-2",
      usage: null,
      usage_status: "UNKNOWN",
      outcome: "SUCCEEDED",
    }),
  ];
}

function renderDashboard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <UsageDashboard />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  projectsApi.mockResolvedValue([]);
  usageAttemptsApi.mockResolvedValue(page(attemptsPayload(), null));
  usageSummaryApi.mockResolvedValue(populatedSummary);
});

afterEach(() => {
  // Keep the spies installed on `api`; restoreAllMocks would detach them and
  // let later tests hit real fetch.
  vi.clearAllMocks();
});

describe("UsageDashboard cost semantics", () => {
  it("renders KPI per currency and never adds estimated with billed amounts", async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText("¥66.00")).toBeTruthy());
    const kpi = screen.getByLabelText("用量概览指标");
    expect(within(kpi).getByText("¥1.20")).toBeTruthy();
    expect(within(kpi).getByText("$1.50")).toBeTruthy();
    expect(within(kpi).getByText("账单事实与估算永不相加")).toBeTruthy();
    // No merged total: 1.20+1.50 (mixed currency) or 1.20+66.00 (est+billed).
    expect(within(kpi).queryByText("¥2.70")).toBeNull();
    expect(within(kpi).queryByText("¥67.20")).toBeNull();
  });

  it("renders unknown usage as 未知/无用量返回 and never as 0", async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getAllByText("无用量返回").length).toBeGreaterThan(0));
    const breakdown = screen.getByLabelText("供应商与模型分解");
    expect(within(breakdown).getAllByText("未知").length).toBeGreaterThan(0);
    expect(within(breakdown).queryByText("0 / 0")).toBeNull();
  });

  it("marks CLI rows unpriced instead of free", async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getAllByText("codex-cli").length).toBeGreaterThan(0));
    const breakdown = screen.getByLabelText("供应商与模型分解");
    expect(within(breakdown).getAllByText("未定价").length).toBeGreaterThan(0);
  });

  it("opens the attempt drawer from a row and closes on Escape with focus moved to the close button", async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText("调用明细")).toBeTruthy());
    fireEvent.click(screen.getAllByRole("button", { name: "详情" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "调用尝试详情" });
    expect(within(dialog).getByText("req-e2e-text")).toBeTruthy();
    expect(within(dialog).getByText(/第 2 次/)).toBeTruthy();
    expect(within(dialog).getByText("换路")).toBeTruthy();
    expect(within(dialog).getAllByText(/命中/).length).toBeGreaterThan(0);
    expect(within(dialog).getByText(/单次尝试金额不在账本读取接口返回/)).toBeTruthy();

    const closeButton = within(dialog).getByRole("button", { name: /关闭/ });
    expect(document.activeElement).toBe(closeButton);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("loads more attempts with the keyset cursor", async () => {
    usageAttemptsApi
      .mockResolvedValueOnce(page(attemptsPayload(), "cursor-1"))
      .mockResolvedValueOnce(page([makeAttempt({ id: "att-3" })], null));
    renderDashboard();
    await waitFor(() => expect(screen.getByText("调用明细")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    await waitFor(() => {
      const secondCall = usageAttemptsApi.mock.calls[1];
      expect(secondCall?.[1]).toBe("cursor-1");
    });
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: "详情" })).toHaveLength(3),
    );
  });

  it("shows the first-run empty state when nothing has ever been called", async () => {
    usageSummaryApi.mockResolvedValue({ groups: [], billed: [] });
    renderDashboard();
    await waitFor(() => expect(screen.getByText("暂无调用记录")).toBeTruthy());
    expect(screen.getByText("发起剧本分析或单页生成后即可在此查看用量统计")).toBeTruthy();
  });

  it("shows the filtered empty state with a reset action when filters exclude everything", async () => {
    usageSummaryApi.mockImplementation(async (filters) => {
      if (filters?.model_id === "gpt-4o") return { groups: [], billed: [] };
      return populatedSummary;
    });
    renderDashboard();
    const modelSelect = screen.getByLabelText("按模型筛选") as HTMLSelectElement;
    await waitFor(() => expect(modelSelect.options.length).toBeGreaterThan(1));
    fireEvent.change(modelSelect, { target: { value: "gpt-4o" } });
    await waitFor(() => expect(screen.getByText("未找到匹配的调用记录")).toBeTruthy());
    expect(screen.getByRole("button", { name: "重置筛选" })).toBeTruthy();
  });

  it("shows a retryable error state when the summary request fails", async () => {
    usageSummaryApi.mockRejectedValue(new Error("boom"));
    renderDashboard();
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(screen.getByText(/用量数据加载失败/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "重试" })).toBeTruthy();
  });

  it("refetches the summary with a new time window when the range changes", async () => {
    renderDashboard();
    await waitFor(() => expect(screen.getByText("调用明细")).toBeTruthy());
    usageSummaryApi.mockClear();
    fireEvent.change(screen.getByLabelText("选择用量统计时间范围"), {
      target: { value: "7d" },
    });
    await waitFor(() => {
      const summaryCalls = usageSummaryApi.mock.calls.filter(
        (call) => Boolean(call[0] && (call[0] as { since?: string }).since),
      );
      expect(summaryCalls.length).toBeGreaterThan(0);
      const last = summaryCalls[summaryCalls.length - 1];
      const since = new Date((last[0] as { since: string }).since);
      const expected = Date.now() - 7 * 86_400_000;
      expect(Math.abs(since.getTime() - expected)).toBeLessThan(60_000);
    });
  });

  it("exports the summary CSV with per-currency rows", async () => {
    const created: Blob[] = [];
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      writable: true,
      value: (blob: Blob) => {
        created.push(blob);
        return "blob:mock";
      },
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      writable: true,
      value: () => {},
    });
    try {
      renderDashboard();
      await waitFor(() => expect(screen.getByText("调用明细")).toBeTruthy());
      fireEvent.click(screen.getByRole("button", { name: /导出 CSV/ }));
      await waitFor(() => expect(created).toHaveLength(1));
      const text = await created[0].text();
      expect(text).toContain("估算金额（原币种）");
      expect(text).toContain("CNY,1.20");
      expect(text).toContain("USD,1.50");
      expect(text).toContain("无估算数据");
    } finally {
      const urlRecord = URL as unknown as Record<string, unknown>;
      delete urlRecord.createObjectURL;
      delete urlRecord.revokeObjectURL;
      clickSpy.mockRestore();
    }
  });
});
