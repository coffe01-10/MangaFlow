import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createEvent, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type ModelCapability, type Project } from "@/lib/api";

import ProjectSettingsPage from "./page";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "project-1" }),
  useRouter: () => ({ replace: vi.fn() }),
}));

const projectSpy = vi.spyOn(api, "project");
const modelsSpy = vi.spyOn(api, "models");
const updateProjectSpy = vi.spyOn(api, "updateProject");

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: "project-1",
    name: "测试项目",
    language: "zh-CN",
    reading_direction: "rtl",
    page_ratio: "b5_portrait",
    default_resolution: "2K",
    draft_resolution: "1K",
    workflow_mode: "SEMI_AUTO",
    default_concurrency: 1,
    default_style_id: null,
    consistency_check_enabled: true,
    text_model_alias: "auto",
    last_image_model_alias: null,
    default_text_model_id: null,
    last_image_model_id: null,
    created_at: "2026-08-30T00:00:00Z",
    updated_at: "2026-08-30T00:00:00Z",
    version: 1,
    ...overrides,
  };
}

function model(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    catalog_id: "model-visible",
    connection_id: "connection-1",
    provider: "Example",
    protocol: "OPENAI_COMPATIBLE",
    model_id: "example-text",
    logical_alias: "text.example",
    display_name: "Visible text",
    model_type: "TEXT",
    input_modalities: ["text", "image"],
    output_modalities: ["text"],
    operations: ["structured_text", "multimodal_analysis"],
    resolutions: [],
    preview_resolutions: [],
    max_reference_images: 1,
    regions: [],
    confidence: "VERIFIED",
    enabled: true,
    display_enabled: true,
    auto_eligible: true,
    priority: 100,
    ...overrides,
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectSettingsPage />
    </QueryClientProvider>,
  );
}

describe("ProjectSettingsPage 模型展示偏好", () => {
  beforeEach(() => {
    projectSpy.mockReset();
    modelsSpy.mockReset();
    updateProjectSpy.mockReset();
  });

  it("隐藏普通候选，但保留当前项目默认模型并标注已隐藏", async () => {
    projectSpy.mockResolvedValue(project({ default_text_model_id: "model-current", text_model_alias: "text.fast" }));
    modelsSpy.mockResolvedValue([
      model(),
      model({ catalog_id: "model-hidden", logical_alias: "text.hidden", display_name: "Hidden other", display_enabled: false }),
      model({ catalog_id: "model-current", logical_alias: "text.current", display_name: "Hidden current", display_enabled: false }),
    ]);

    renderPage();

    const select = await screen.findByRole("combobox", { name: /剧本、风格分析与视觉检查/ });
    expect(select).toHaveValue("model-current");
    expect(screen.getByRole("option", { name: "Example · Visible text" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /Hidden other/ })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Example · Hidden current（已隐藏）" })).toBeInTheDocument();
  });
});

describe("ProjectSettingsPage 409 版本冲突恢复", () => {
  beforeEach(() => {
    projectSpy.mockReset();
    modelsSpy.mockReset().mockResolvedValue([]);
    updateProjectSpy.mockReset();
  });

  it("409 后失效项目缓存重拉，重试保存携带服务器新版本成功", async () => {
    // 首次载入 version 1；409 触发的失效重拉拿到 version 2。
    projectSpy.mockReset()
      .mockResolvedValueOnce(project({ version: 1 }))
      .mockResolvedValue(project({ version: 2, default_concurrency: 3 }));
    updateProjectSpy
      .mockRejectedValueOnce(new ApiError("项目设置已被其他页面修改，请刷新后重试", 409, { message: "项目设置已被其他页面修改，请刷新后重试" }))
      .mockResolvedValue(project({ version: 2, default_concurrency: 3 }));

    renderPage();
    await screen.findByText("测试项目");
    fireEvent.click(screen.getByRole("button", { name: /保存项目设置/ }));

    // 第一次保存用旧版本被拒；onError 必须失效 ["project", id] 触发重拉。
    await waitFor(() => expect(updateProjectSpy).toHaveBeenCalledTimes(1));
    expect(updateProjectSpy).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ version: 1 }));
    await waitFor(() => expect(projectSpy.mock.calls.length).toBeGreaterThanOrEqual(2));
    expect(await screen.findByText(/项目设置已被其他页面修改/)).toBeInTheDocument();

    // 用户重试：mutationFn 读重拉后的 project.data.version（2），不再无限 409。
    fireEvent.click(screen.getByRole("button", { name: /保存项目设置/ }));
    await waitFor(() => expect(updateProjectSpy).toHaveBeenCalledTimes(2));
    expect(updateProjectSpy).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ version: 2 }));
    await screen.findByText("项目设置已保存");
  });
});

describe("ProjectSettingsPage 未保存草稿离开守卫（#546-4）", () => {
  beforeEach(() => {
    projectSpy.mockReset().mockResolvedValue(project());
    modelsSpy.mockReset().mockResolvedValue([]);
    updateProjectSpy.mockReset();
  });

  it("草稿脏时锚点离开先确认、刷新被 beforeunload 拦截，保存后解除", async () => {
    renderPage();
    await screen.findByRole("radiogroup", { name: "工作方式" });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    // 干净状态：站内锚点直接放行，不弹确认。
    const link = screen.getByRole("link", { name: /返回工作区/ });
    const cleanEvent = createEvent.click(link);
    fireEvent(link, cleanEvent);
    expect(confirmSpy).not.toHaveBeenCalled();

    // 修改草稿清晰度 → 脏。
    const draftGroup = screen.getByRole("group", { name: "草稿清晰度" });
    fireEvent.click(within(draftGroup).getByRole("button", { name: "2K" }));

    const blocked = createEvent.click(link);
    fireEvent(link, blocked);
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("尚未保存"));
    expect(blocked.defaultPrevented).toBe(true);

    // 刷新/关闭同样被拦。
    const unload = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(unload);
    expect(unload.defaultPrevented).toBe(true);

    // 保存成功后脏标记回落：不再确认。
    updateProjectSpy.mockResolvedValue(project({ draft_resolution: "2K", version: 2 }));
    fireEvent.click(screen.getByRole("button", { name: /保存项目设置/ }));
    await screen.findByText("项目设置已保存");
    fireEvent.click(link);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();
  });
});

describe("ProjectSettingsPage 保存覆盖在途编辑（#650）", () => {
  beforeEach(() => {
    projectSpy.mockReset().mockResolvedValue(project());
    modelsSpy.mockReset().mockResolvedValue([]);
    updateProjectSpy.mockReset();
  });

  it("保存在途再改清晰度时，响应落地保留本地修改而不是提交快照回弹", async () => {
    let resolveSave: ((value: Project) => void) | undefined;
    updateProjectSpy.mockImplementation(
      () => new Promise<Project>((resolve) => {
        resolveSave = resolve;
      }),
    );

    renderPage();
    await screen.findByRole("radiogroup", { name: "工作方式" });

    // 修改草稿清晰度并保存：请求在途，控件保持可编辑（这是修复的前提）。
    const draftGroup = screen.getByRole("group", { name: "草稿清晰度" });
    fireEvent.click(within(draftGroup).getByRole("button", { name: "2K" }));
    fireEvent.click(screen.getByRole("button", { name: /保存项目设置/ }));
    await waitFor(() => expect(updateProjectSpy).toHaveBeenCalledTimes(1));

    // 保存在途再切换正式清晰度：本地草稿领先于提交快照。
    const defaultGroup = screen.getByRole("group", { name: "正式清晰度" });
    fireEvent.click(within(defaultGroup).getByRole("button", { name: "4K" }));

    // 响应落地：服务器只保存了提交快照（正式清晰度仍是 2K）。旧实现会
    // setLocalDraft({...data...}) 整体覆盖，把 4K 静默回弹成 2K。
    resolveSave?.(project({ draft_resolution: "2K", version: 2 }));

    await screen.findByText("项目设置已保存，之后又有新修改");
    // 在途修改保留：正式清晰度仍是 4K；已确认的草稿 2K 同样保留。
    expect(within(defaultGroup).getByRole("button", { name: "4K" })).toHaveAttribute("aria-pressed", "true");
    expect(within(draftGroup).getByRole("button", { name: "2K" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/^项目设置已保存$/)).not.toBeInTheDocument();
  });

  it("保存在途无新修改时，成功后仍按服务器响应同步并提示已保存", async () => {
    updateProjectSpy.mockResolvedValue(project({ draft_resolution: "2K", version: 2 }));

    renderPage();
    await screen.findByRole("radiogroup", { name: "工作方式" });

    const draftGroup = screen.getByRole("group", { name: "草稿清晰度" });
    fireEvent.click(within(draftGroup).getByRole("button", { name: "2K" }));
    fireEvent.click(screen.getByRole("button", { name: /保存项目设置/ }));

    await screen.findByText("项目设置已保存");
    expect(within(draftGroup).getByRole("button", { name: "2K" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText(/之后又有新修改/)).not.toBeInTheDocument();
  });
});
