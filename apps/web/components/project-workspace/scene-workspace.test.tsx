import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type SceneAsset } from "@/lib/api";

import { SceneWorkspace } from "./scene-workspace";

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}));

const listApi = vi.spyOn(api, "sceneAssets");
const createApi = vi.spyOn(api, "createSceneAsset");
const updateApi = vi.spyOn(api, "updateSceneAsset");
const deleteApi = vi.spyOn(api, "deleteSceneAsset");
const restoreApi = vi.spyOn(api, "restoreSceneAsset");
const uploadApi = vi.spyOn(api, "uploadAsset");
const bindRefApi = vi.spyOn(api, "bindSceneAssetReference");
const createVariantApi = vi.spyOn(api, "createSceneAssetVariant");
const chaptersApi = vi.spyOn(api, "chapters");
const scriptApi = vi.spyOn(api, "script");

function assetFixture(overrides: Partial<SceneAsset> = {}): SceneAsset {
  return {
    id: "asset-1",
    project_id: "project-1",
    name: "学校天台",
    description: "铁丝网与水箱",
    location_hint: "学校天台",
    structured: { interior: false, place: "校园", fixed_props: ["铁丝网"] },
    status: "UPLOADED",
    deleted_at: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 1,
    references: [],
    variants: [],
    ...overrides,
  };
}

function renderWorkspace() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(
    <SceneWorkspace projectId="project-1" assets={[]} openPreview={() => undefined} />,
    { wrapper },
  );
}

describe("SceneWorkspace", () => {
  beforeEach(() => {
    listApi.mockReset();
    createApi.mockReset();
    updateApi.mockReset();
    deleteApi.mockReset();
    restoreApi.mockReset();
    uploadApi.mockReset();
    bindRefApi.mockReset();
    createVariantApi.mockReset();
    chaptersApi.mockReset().mockResolvedValue([]);
    scriptApi.mockReset().mockResolvedValue({ chapter_id: "c1", status: "READY", revision_no: 1, coverage: {}, scenes: [] });
  });

  it("TEST-SCENE-01 首次进入展示空状态，成功返回后渲染卡片", async () => {
    listApi.mockResolvedValueOnce([]);
    const { rerender } = renderWorkspace();
    expect(await screen.findByText("尚未创建场景资产")).toBeInTheDocument();

    listApi.mockResolvedValue([assetFixture()]);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    rerender(
      <QueryClientProvider client={client}>
        <SceneWorkspace projectId="project-1" assets={[]} openPreview={() => undefined} />
      </QueryClientProvider>,
    );
    const card = await screen.findByRole("option", { name: /学校天台/ });
    expect(card).toBeInTheDocument();
    expect(card).toHaveTextContent("室外");
  });

  it("TEST-SCENE-01 加载失败时展示错误而不是空列表", async () => {
    listApi.mockRejectedValue(new Error("场景资产无法连接"));
    renderWorkspace();
    expect(await screen.findByText("场景资产无法载入")).toBeInTheDocument();
    expect(screen.getByText("场景资产无法连接")).toBeInTheDocument();
    expect(screen.queryByText("尚未创建场景资产")).not.toBeInTheDocument();
  });

  it("TEST-SCENE-02 名称为空时阻止提交，回车保存后选中新卡片", async () => {
    listApi.mockResolvedValue([]);
    const created = assetFixture({ id: "asset-new", name: "林间木屋", status: "UPLOADED" });
    createApi.mockImplementation(async () => {
      listApi.mockResolvedValue([created]);
      return created;
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "新建场景" }));
    const dialog = screen.getByRole("dialog", { name: "新建场景资产" });
    const name = within(dialog).getByLabelText("场景名称");
    fireEvent.submit(dialog.querySelector("form")!);
    expect(createApi).not.toHaveBeenCalled();
    fireEvent.change(name, { target: { value: "林间木屋" } });
    fireEvent.submit(dialog.querySelector("form")!);
    await waitFor(() => {
      expect(createApi).toHaveBeenCalledWith("project-1", expect.objectContaining({ name: "林间木屋" }));
    });
    expect(await screen.findByRole("option", { name: /林间木屋/ })).toHaveAttribute("aria-selected", "true");
  });

  it("TEST-SCENE-03 上传后调用真实绑定接口，并可把资产标为 CANONICAL", async () => {
    const uploaded = {
      id: "file-1",
      project_id: "project-1",
      kind: "SCENE_REFERENCE",
      original_name: "roof.png",
      display_name: null,
      mime_type: "image/png",
      byte_size: 1200,
      width: 64,
      height: 64,
      status: "UPLOADED",
      created_at: "2026-09-01T00:00:00Z",
      content_url: "/api/v1/assets/file-1/content",
      thumbnail_url: null,
    };
    let current = assetFixture();
    listApi.mockImplementation(async () => [current]);
    uploadApi.mockResolvedValue(uploaded);
    bindRefApi.mockImplementation(async () => {
      current = {
        ...current,
        references: [{
          id: "ref-1",
          scene_asset_id: current.id,
          asset_id: uploaded.id,
          role: "main",
          is_canonical: true,
          created_at: "2026-09-01T00:00:00Z",
        }],
      };
      return current.references[0];
    });
    updateApi.mockImplementation(async (_project, _id, payload) => {
      current = { ...current, status: payload.status ?? current.status, version: current.version + 1 };
      return current;
    });
    renderWorkspace();
    await screen.findByRole("option", { name: /学校天台/ });
    const file = new File(["png"], "roof.png", { type: "image/png" });
    const fileInput = document.querySelector('input[aria-label="上传场景参考图"]');
    expect(fileInput).toBeTruthy();
    fireEvent.change(fileInput as HTMLInputElement, { target: { files: [file] } });
    await waitFor(() => {
      expect(uploadApi).toHaveBeenCalledWith("project-1", "SCENE_REFERENCE", file);
      expect(bindRefApi).toHaveBeenCalledWith("project-1", "asset-1", expect.objectContaining({
        asset_id: "file-1",
      }));
    });
    fireEvent.click(screen.getByRole("button", { name: "设为规范参考" }));
    await waitFor(() => {
      expect(updateApi).toHaveBeenCalledWith("project-1", "asset-1", expect.objectContaining({
        status: "CANONICAL",
        version: 1,
      }));
    });
    expect(await screen.findByLabelText("场景状态 已就绪 · 可直接用于剧本与分镜")).toBeInTheDocument();
  });

  it("TEST-SCENE-04 可以创建只覆盖允许字段的环境变体", async () => {
    let current = assetFixture();
    listApi.mockImplementation(async () => [current]);
    createVariantApi.mockImplementation(async (_project, _id, payload) => {
      const variant = {
        id: "variant-1",
        scene_asset_id: current.id,
        name: payload.name,
        structured_overrides: payload.structured_overrides ?? {},
        is_canonical: payload.is_canonical ?? false,
        deleted_at: null,
        version: 1,
        references: [],
      };
      current = { ...current, variants: [variant] };
      return variant;
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: /添加变体/ }));
    const dialog = screen.getByRole("dialog", { name: "添加环境变体" });
    fireEvent.change(within(dialog).getByLabelText("变体名称"), { target: { value: "暴雨黄昏" } });
    fireEvent.change(within(dialog).getByLabelText("变体时间"), { target: { value: "dusk" } });
    fireEvent.change(within(dialog).getByLabelText("变体天气"), { target: { value: "rain" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "保存变体" }));
    await waitFor(() => {
      expect(createVariantApi).toHaveBeenCalledWith("project-1", "asset-1", expect.objectContaining({
        name: "暴雨黄昏",
        structured_overrides: expect.objectContaining({ time_of_day: "dusk", weather: "rain" }),
      }));
    });
    expect(createVariantApi.mock.calls[0][2].structured_overrides).not.toHaveProperty("place");
    expect(await screen.findByText(/暴雨黄昏/)).toBeInTheDocument();
  });

  it("409 乐观锁展示刷新而不是静默覆盖", async () => {
    listApi.mockResolvedValue([assetFixture()]);
    updateApi.mockRejectedValue(new ApiError("场景资产已被更新，请刷新后重试", 409));
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "设为规范参考" }));
    expect(await screen.findByText("数据已变化，请刷新后重试")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
  });

  it("TEST-SCENE-07 归档确认展示引用数量，恢复走 restore 接口", async () => {
    const live = assetFixture();
    const archived = assetFixture({ deleted_at: "2026-09-01T00:00:00Z", status: "UPLOADED" });
    listApi.mockResolvedValue([live]);
    chaptersApi.mockResolvedValue([{
      id: "chapter-1",
      project_id: "project-1",
      title: "一",
      ordinal: 1,
      status: "READY",
      current_source_revision_id: null,
      source_character_count: 0,
      segment_count: 0,
      page_count: 1,
      coverage_ratio: 1,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      version: 1,
    }]);
    scriptApi.mockResolvedValue({
      chapter_id: "chapter-1",
      status: "READY",
      revision_no: 1,
      coverage: {},
      scenes: [{
        id: "scene-1",
        ordinal: 1,
        location: "学校天台",
        scene_asset_id: "asset-1",
        scene_asset_variant_id: null,
        time_label: "",
        weather: "",
        purpose: "",
        emotional_arc: "",
        source_range: {},
        outfit_assignments: {},
        locked_fields: [],
        version: 1,
        beats: [],
      }],
    });
    deleteApi.mockImplementation(async () => {
      listApi.mockResolvedValue([archived]);
    });
    restoreApi.mockImplementation(async () => {
      listApi.mockResolvedValue([live]);
      return live;
    });
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "归档" }));
    expect(await screen.findByText(/当前项目中有 1 个剧本场景绑定了该资产/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认归档" }));
    await waitFor(() => expect(deleteApi).toHaveBeenCalledWith("project-1", "asset-1"));
    fireEvent.click(screen.getByLabelText("显示已归档"));
    expect(await screen.findByRole("button", { name: "恢复" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    await waitFor(() => expect(restoreApi).toHaveBeenCalledWith("project-1", "asset-1"));
  });
});
