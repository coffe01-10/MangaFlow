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
const unbindRefApi = vi.spyOn(api, "unbindSceneAssetReference");
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
    unbindRefApi.mockReset();
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

  it("TEST-SCENE-01b 列表超过单页 200 条时循环取全，不再静默截断（#369）", async () => {
    // 201 条：单页 limit 200 会丢掉最后 1 条；sceneAssetsAll 应翻页取全。
    const many = Array.from({ length: 201 }, (_, index) =>
      assetFixture({ id: `asset-${index}`, name: `场景 ${String(index + 1).padStart(3, "0")}` }));
    listApi.mockImplementation(async (_projectId: string, query = {}) => {
      const offset = query.offset ?? 0;
      return many.slice(offset, offset + 200);
    });
    renderWorkspace();

    expect(await screen.findByText("201 个场景")).toBeInTheDocument();
    // 尾部资产可见：截断时第 201 条永远不出现。
    expect(screen.getByRole("option", { name: /场景 201/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /场景 200/ })).toBeInTheDocument();
    // 确实发生了第二页请求（offset 200）。
    expect(listApi).toHaveBeenLastCalledWith("project-1", expect.objectContaining({ limit: 200, offset: 200 }));
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

  it("编辑对话框输入时焦点不会被拉回名称框", async () => {
    listApi.mockResolvedValue([]);
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "新建场景" }));
    const dialog = screen.getByRole("dialog", { name: "新建场景资产" });
    const description = within(dialog).getByLabelText("场景描述");
    description.focus();
    fireEvent.change(description, { target: { value: "壁炉在正北墙面" } });
    expect(description).toHaveFocus();
  });

  it("没有规范参考图时不能把资产标为已就绪", async () => {
    listApi.mockResolvedValue([assetFixture({ status: "UPLOADED", references: [] })]);
    renderWorkspace();
    expect(await screen.findByRole("button", { name: "设为规范参考" })).toBeDisabled();
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
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "设为规范参考" })).toBeEnabled();
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

  it("409 乐观锁展示后端语义提示而不是静默覆盖（#156）", async () => {
    listApi.mockResolvedValue([assetFixture({
      references: [{
        id: "ref-1",
        scene_asset_id: "asset-1",
        asset_id: "file-1",
        role: "main",
        is_canonical: true,
        created_at: "2026-09-01T00:00:00Z",
      }],
    })]);
    updateApi.mockRejectedValue(new ApiError("场景资产已被更新，请刷新后重试", 409));
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "设为规范参考" }));
    // ae0c6ee 之后 ApiError.message 即后端 detail:语义化 409 原样透出。
    expect(await screen.findByText("场景资产已被更新，请刷新后重试")).toBeInTheDocument();
    expect(screen.queryByText("数据已变化，请刷新后重试")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
  });

  it("规范参考换绑失败：解除已提交后立即刷新列表并明确提示参考已被解除（#162）", async () => {
    let current = assetFixture({
      references: [{
        id: "ref-1",
        scene_asset_id: "asset-1",
        asset_id: "file-1",
        role: "main",
        is_canonical: false,
        created_at: "2026-09-01T00:00:00Z",
      }],
    });
    listApi.mockImplementation(async () => [current]);
    // unbind 成功并已提交——服务端此刻处于零引用状态。
    unbindRefApi.mockImplementation(async () => {
      current = { ...current, references: [] };
      return undefined as never;
    });
    bindRefApi.mockRejectedValue(new Error("绑定接口暂时不可用"));
    renderWorkspace();
    await screen.findByRole("option", { name: /学校天台/ });
    // 卡片当前显示为已绑定的非规范参考(main)。
    expect(await screen.findByText(/main/)).toBeInTheDocument();
    const before = listApi.mock.calls.length;
    // 参考卡上的「设为规范参考」(状态按钮在无规范参考时禁用)。
    const swapButtons = await waitFor(() => {
      const buttons = screen.getAllByRole("button", { name: "设为规范参考" }).filter((item) => !item.hasAttribute("disabled"));
      expect(buttons).toHaveLength(1);
      return buttons;
    });
    fireEvent.click(swapButtons[0]);
    await waitFor(() => {
      expect(unbindRefApi).toHaveBeenCalledWith("project-1", "asset-1", "file-1");
      expect(bindRefApi).toHaveBeenCalledWith("project-1", "asset-1", expect.objectContaining({
        asset_id: "file-1",
        is_canonical: true,
      }));
    });
    // 列表被 refetch,UI 回到真实的未绑定状态,不再静默显示已绑定。
    await waitFor(() => {
      expect(listApi.mock.calls.length).toBeGreaterThan(before);
    });
    expect(await screen.findByText("尚未绑定场景参考图")).toBeInTheDocument();
    expect(screen.queryByText("规范参考")).not.toBeInTheDocument();
    expect(await screen.findByText(/原规范参考已被解除，重新绑定失败/)).toBeInTheDocument();
  });

  it("规范参考换绑在解除阶段就失败：不宣称「已被解除」，按刷新引导提示", async () => {
    listApi.mockResolvedValue([assetFixture({
      references: [{
        id: "ref-1",
        scene_asset_id: "asset-1",
        asset_id: "file-1",
        role: "main",
        is_canonical: false,
        created_at: "2026-09-01T00:00:00Z",
      }],
    })]);
    // unbind 直接失败：原规范参考仍绑在服务端，bind 从未发出。
    unbindRefApi.mockRejectedValue(new ApiError("场景资产已被更新，请刷新后重试", 409));
    renderWorkspace();
    await screen.findByRole("option", { name: /学校天台/ });
    const swapButtons = await waitFor(() => {
      const buttons = screen.getAllByRole("button", { name: "设为规范参考" }).filter((item) => !item.hasAttribute("disabled"));
      expect(buttons).toHaveLength(1);
      return buttons;
    });
    fireEvent.click(swapButtons[0]);
    await waitFor(() => {
      expect(unbindRefApi).toHaveBeenCalledWith("project-1", "asset-1", "file-1");
    });
    expect(bindRefApi).not.toHaveBeenCalled();
    // 解除阶段失败：不能把「原规范参考已被解除」说成事实。
    expect(await screen.findByText("场景资产已被更新，请刷新后重试")).toBeInTheDocument();
    expect(screen.queryByText(/原规范参考已被解除/)).not.toBeInTheDocument();
    // 语义化 409 自带「请刷新」引导：刷新按钮可见。
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

  it("归档引用计数慢返回不覆盖后打开资产的确认框内容", async () => {
    listApi.mockResolvedValue([
      assetFixture({ id: "asset-a", name: "学校天台" }),
      assetFixture({ id: "asset-b", name: "车站前街" }),
    ]);
    // 资产 A 的 chapters 挂起（慢），资产 B 立即返回空（0 绑定）。
    let releaseChaptersA: ((chapters: unknown[]) => void) | undefined;
    let chaptersCalls = 0;
    chaptersApi.mockImplementation(() => {
      chaptersCalls += 1;
      if (chaptersCalls === 1) {
        return new Promise((resolve) => {
          releaseChaptersA = resolve;
        }) as never;
      }
      return Promise.resolve([]) as never;
    });
    // A 的链路最终会数出 1 个绑定：若晚到结果没被令牌拦下，会盖掉 B 的 0。
    scriptApi.mockResolvedValue({
      scenes: [{ scene_asset_id: "asset-a" }],
    } as never);
    renderWorkspace();
    // 打开 A 的归档确认（引用计数挂起中）后取消。
    fireEvent.click(await screen.findByRole("button", { name: "归档" }));
    expect(await screen.findByText("正在确认剧本引用…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    // 打开 B 的归档确认：0 绑定文案立即可见。
    fireEvent.click(screen.getByRole("option", { name: /车站前街/ }));
    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    expect(await screen.findByText(/当前已加载的剧本中没有发现绑定/)).toBeInTheDocument();
    // 此时 A 的慢计数返回 1——令牌已过期，必须被丢弃。
    releaseChaptersA?.([{ id: "chapter-1" }] as never);
    await waitFor(() => {
      expect(scriptApi).toHaveBeenCalledWith("chapter-1");
    });
    await new Promise((resolve) => {
      setTimeout(resolve, 0);
    });
    expect(screen.getByText(/当前已加载的剧本中没有发现绑定/)).toBeInTheDocument();
    expect(screen.queryByText(/1 个剧本场景绑定了该资产/)).not.toBeInTheDocument();
    expect(screen.getByText("归档场景“车站前街”？")).toBeInTheDocument();
  });
});
