import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
