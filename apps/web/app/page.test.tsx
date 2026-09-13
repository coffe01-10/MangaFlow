import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ProjectDashboard } from "@/lib/api";

import HomePage from "./page";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

const dashboardSpy = vi.spyOn(api, "dashboard");

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <HomePage />
    </QueryClientProvider>,
  );
}

describe("HomePage AI 连接摘要", () => {
  beforeEach(() => dashboardSpy.mockReset());

  it("只用 Dashboard 聚合展示连接与模型，不渲染供应商专属卡", async () => {
    dashboardSpy.mockResolvedValue({
      totals: {
        project_count: 0,
        page_count: 0,
        selected_page_count: 0,
        review_page_count: 0,
        pending_job_count: 0,
      },
      ai_overview: {
        enabled_model_count: 4,
        healthy_connection_count: 1,
        configured_connection_count: 2,
      },
      projects: [],
    } satisfies ProjectDashboard);

    renderPage();

    expect(await screen.findByText("AI 连接已就绪")).toBeInTheDocument();
    expect(screen.getAllByText("1 个 AI 连接健康")).toHaveLength(2);
    expect(screen.getByText("4 个可用模型")).toBeInTheDocument();
    expect(screen.queryByText(/Vertex|Gemini|Nano Banana/i)).not.toBeInTheDocument();
    expect(dashboardSpy).toHaveBeenCalledTimes(1);
  });

  it("仪表盘读取失败显示错误面板并提供重试（#545-6）", async () => {
    dashboardSpy.mockReset().mockResolvedValue({
      totals: {
        project_count: 0,
        page_count: 0,
        selected_page_count: 0,
        review_page_count: 0,
        pending_job_count: 0,
      },
      ai_overview: {
        enabled_model_count: 0,
        healthy_connection_count: 0,
        configured_connection_count: 0,
      },
      projects: [],
    } satisfies ProjectDashboard).mockRejectedValueOnce(new Error("API 未启动"));

    renderPage();

    expect(await screen.findByText("无法连接 MangaFlow API")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "重试" });
    fireEvent.click(retry);
    await waitFor(() => expect(dashboardSpy.mock.calls.length).toBeGreaterThanOrEqual(2));
    // 重试成功后回到正常视图（错误面板消失）。
    await waitFor(() => expect(screen.queryByText("无法连接 MangaFlow API")).not.toBeInTheDocument());
  });
});
