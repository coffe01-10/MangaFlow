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

  it("界面轮询周期帮助文本注明仅桌面客户端生效（#367 死配置披露）", async () => {
    renderPage();

    // ui_poll_interval_seconds 在 web 端零消费：字段仍可保存（桌面读取），
    // 但帮助文本必须如实标注生效范围，不再暗示驱动本 UI。
    await screen.findByLabelText(/界面轮询周期/);
    expect(screen.getByText(/仅桌面客户端生效/)).toBeInTheDocument();
  });

  it("保存时把 job_lease_seconds 随载荷提交回后端", async () => {
    updateRuntimeSettingsSpy.mockResolvedValue(runtimeSettings({ job_lease_seconds: 90, version: 8 }));

    renderPage();

    const leaseInput = await screen.findByLabelText(/任务租约/);
    fireEvent.change(leaseInput, { target: { value: "90" } });
    // ClampedNumberInput 只在失焦时提交钳制结果;真实浏览器点击保存按钮会
    // 先让输入框失焦,jsdom 不会自动触发,这里手动派发冒泡 focusout。
    fireEvent(leaseInput, new FocusEvent("focusout", { bubbles: true }));
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

  it("409 版本冲突后失效缓存重拉，重试保存带上服务器新版本成功", async () => {
    // 首次载入 version 7；409 触发的失效重拉必须拿到 version 8。
    runtimeSettingsSpy.mockReset()
      .mockResolvedValueOnce(runtimeSettings({ version: 7 }))
      .mockResolvedValue(runtimeSettings({ version: 8, job_lease_seconds: 90 }));
    updateRuntimeSettingsSpy
      .mockRejectedValueOnce(new ApiError("设置已被其他会话修改，请刷新后重试", 409, { message: "设置已被其他会话修改，请刷新后重试" }))
      .mockResolvedValue(runtimeSettings({ version: 8, job_lease_seconds: 90 }));

    renderPage();
    const leaseInput = await screen.findByLabelText(/任务租约/);
    fireEvent.change(leaseInput, { target: { value: "90" } });
    fireEvent(leaseInput, new FocusEvent("focusout", { bubbles: true }));
    fireEvent.click(screen.getByRole("button", { name: /保存运行设置/ }));

    // 第一次保存用旧版本 7 被拒；onError 必须失效 runtime-settings 触发重拉。
    await waitFor(() => expect(updateRuntimeSettingsSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(runtimeSettingsSpy.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText(/设置已被其他会话修改/)).toBeInTheDocument();

    // 用户重试：重拉后的缓存版本是 8，不再停在旧 version 上无限 409。
    fireEvent.click(screen.getByRole("button", { name: /保存运行设置/ }));
    await waitFor(() => expect(updateRuntimeSettingsSpy).toHaveBeenCalledTimes(2));
    expect(updateRuntimeSettingsSpy).toHaveBeenLastCalledWith(
      expect.objectContaining({ version: 8, job_lease_seconds: 90 }),
    );
    await screen.findByText("运行设置已保存并应用到后续任务");
  });

  it("运行设置草稿未保存时刷新/关闭被 beforeunload 拦截（#546-5）", async () => {
    renderPage();

    await screen.findByLabelText(/任务租约/);
    // 干净状态不拦截。
    const clean = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(clean);
    expect(clean.defaultPrevented).toBe(false);

    fireEvent.change(screen.getByRole("combobox", { name: /队列模式/ }), { target: { value: "LOCAL" } });
    const dirty = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(dirty);
    expect(dirty.defaultPrevented).toBe(true);
  });
});
