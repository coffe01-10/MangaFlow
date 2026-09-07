import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type Diagnostics, type RuntimeSettings } from "@/lib/api";

import SystemSettingsPage from "./page";

vi.mock("next/navigation", () => ({ usePathname: () => "/settings" }));

// 供应商管理卡片有自己的测试；这里用占位组件保持运行设置表单测试独立。
vi.mock("@/components/provider-management", () => ({
  ProviderManagement: () => <div data-testid="provider-management-stub" />,
}));

const runtimeSettingsSpy = vi.spyOn(api, "runtimeSettings");
const providersSpy = vi.spyOn(api, "providers");
const diagnosticsSpy = vi.spyOn(api, "diagnostics");
const updateRuntimeSettingsSpy = vi.spyOn(api, "updateRuntimeSettings");

function runtimeSettings(overrides: Partial<RuntimeSettings> = {}): RuntimeSettings {
  return {
    queue_mode: "AUTO",
    job_timeout_seconds: 1800,
    job_lease_seconds: 600,
    max_auto_repairs: 3,
    default_concurrency: 2,
    health_check_interval_seconds: 300,
    ui_poll_interval_seconds: 5000,
    workflow_autosave_ms: 30000,
    database_backend: "postgresql",
    storage_root: "D:/mangaflow/storage",
    upload_root: "D:/mangaflow/uploads",
    redis_configured: true,
    version: 7,
    ...overrides,
  };
}

function diagnostics(overrides: Partial<Diagnostics> = {}): Diagnostics {
  return {
    checks: [],
    checked_at: "2026-09-06T00:00:00Z",
    queue: {
      current_mode: "REDIS",
      actual_executor: "rq",
      redis_state: "CONNECTED",
      can_execute_new_jobs: true,
    },
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SystemSettingsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  runtimeSettingsSpy.mockReset().mockResolvedValue(runtimeSettings());
  providersSpy.mockReset().mockResolvedValue([]);
  diagnosticsSpy.mockReset().mockResolvedValue(diagnostics());
  updateRuntimeSettingsSpy.mockReset();
});

describe("SystemSettingsPage 任务租约设置", () => {
  it("渲染任务租约控件并回填运行设置返回值", async () => {
    runtimeSettingsSpy.mockResolvedValue(runtimeSettings({ job_lease_seconds: 600 }));

    renderPage();

    const leaseInput = await screen.findByLabelText(/任务租约/);
    expect(leaseInput).toHaveValue(600);
    expect(leaseInput).toHaveAttribute("type", "number");
    expect(leaseInput).toHaveAttribute("min", "30");
    expect(leaseInput).toHaveAttribute("max", "3600");
    // 同屏可见的字段关系提示：租约说明与任务超时控件同时出现。
    expect(screen.getByLabelText(/^任务超时/)).toHaveValue(1800);
    expect(screen.getByText(/需不超过任务超时/)).toBeInTheDocument();
  });

  it("保存时把 job_lease_seconds 随载荷提交回后端", async () => {
    updateRuntimeSettingsSpy.mockResolvedValue(runtimeSettings({ job_lease_seconds: 90, version: 8 }));

    renderPage();

    const leaseInput = await screen.findByLabelText(/任务租约/);
    fireEvent.change(leaseInput, { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: /保存运行设置/ }));

    await waitFor(() => expect(updateRuntimeSettingsSpy).toHaveBeenCalledTimes(1));
    expect(updateRuntimeSettingsSpy).toHaveBeenCalledWith(
      expect.objectContaining({ version: 7, job_lease_seconds: 90, job_timeout_seconds: 1800 }),
    );
    await screen.findByText("运行设置已保存并应用到后续任务");
  });

  it("后端 422 指出 job_lease_seconds 问题时展示错误详情", async () => {
    updateRuntimeSettingsSpy.mockRejectedValue(
      new ApiError("job_lease_seconds 不能超过 job_timeout_seconds", 422, {
        message: "job_lease_seconds 不能超过 job_timeout_seconds",
      }),
    );

    renderPage();

    // 先等表单加载（按钮在设置就绪前是禁用的），再触发保存。
    await screen.findByLabelText(/任务租约/);
    fireEvent.click(screen.getByRole("button", { name: /保存运行设置/ }));

    const error = await screen.findByText(/job_lease_seconds 不能超过 job_timeout_seconds/);
    expect(error).toBeInTheDocument();
    expect(error.className).toContain("form-error");
  });
});
