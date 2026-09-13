import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Asset, type AssetPurpose, type Character } from "@/lib/api";

import { AssetsSection } from "./assets-section";
import type { AssetsWorkspace } from "./use-assets-workspace";

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}));

function assetFixture(overrides: Partial<Asset> = {}): Asset {
  return {
    id: "asset-1",
    project_id: "project-1",
    kind: "CHARACTER_REFERENCE",
    original_name: "linche-front.png",
    display_name: "林澈正面",
    mime_type: "image/png",
    byte_size: 1200,
    width: 64,
    height: 64,
    status: "UPLOADED",
    created_at: "2026-09-01T00:00:00Z",
    content_url: "/api/v1/assets/asset-1/content",
    thumbnail_url: null,
    ...overrides,
  };
}

const mutation = () => ({
  isPending: false,
  isError: false,
  error: null,
  variables: undefined,
  mutate: vi.fn(),
  reset: vi.fn(),
});

function makeWorkspace(reclassify = mutation()) {
  const workspace = {
    assetKind: "CHARACTER_REFERENCE",
    setAssetKind: vi.fn(),
    currentAssetKind: "CHARACTER_REFERENCE",
    uploadError: null,
    assetDragActive: false,
    setAssetDragActive: vi.fn(),
    characterName: "",
    setCharacterName: vi.fn(),
    characterAliases: "",
    setCharacterAliases: vi.fn(),
    editCharacterName: "",
    setEditCharacterName: vi.fn(),
    editCharacterAliases: "",
    setEditCharacterAliases: vi.fn(),
    editLockedFeatures: "",
    setEditLockedFeatures: vi.fn(),
    editForbiddenChanges: "",
    setEditForbiddenChanges: vi.fn(),
    bindCharacterId: "",
    setBindCharacterId: vi.fn(),
    outfitName: "",
    setOutfitName: vi.fn(),
    outfitLockedFields: "",
    setOutfitLockedFields: vi.fn(),
    editingOutfitId: null,
    styleName: "",
    setStyleName: vi.fn(),
    styleLockedFields: "",
    setStyleLockedFields: vi.fn(),
    styleColorMode: "color",
    selectedOutfitAssets: [],
    setSelectedOutfitAssets: vi.fn(),
    showGeneratedReferencePicker: false,
    setShowGeneratedReferencePicker: vi.fn(),
    selectedStyleAssets: [],
    setSelectedStyleAssets: vi.fn(),
    selectedCharacterOutfitId: "",
    setSelectedCharacterOutfitId: vi.fn(),
    styles: { data: [], isLoading: false },
    generatedReferenceLibrary: { data: [], isLoading: false, isError: false, error: null },
    generatedReferenceCandidates: [],
    boundCharacter: null,
    editingOutfit: null,
    selectedOutfitFiles: [],
    selectedStyleFiles: [],
    assetCandidates: { data: [] },
    upload: mutation(),
    deleteAsset: mutation(),
    reclassifyAsset: reclassify,
    adoptGeneratedReference: mutation(),
    renameAsset: mutation(),
    bindExistingCharacterReference: mutation(),
    unbindExistingCharacterReference: mutation(),
    createCharacter: mutation(),
    updateCharacter: mutation(),
    createOutfit: mutation(),
    updateOutfit: mutation(),
    deleteOutfit: mutation(),
    generateOutfitPreview: mutation(),
    createStyle: mutation(),
    analyzeStyle: mutation(),
    updateStyleMode: mutation(),
    selectStyleMode: vi.fn(),
    resetOutfitForm: vi.fn(),
    beginOutfitEdit: vi.fn(),
    chooseFile: vi.fn(),
    dropReferenceFile: vi.fn(),
    confirmDeleteOutfit: vi.fn(),
  };
  return workspace as unknown as AssetsWorkspace;
}

function renderAssets(workspace: AssetsWorkspace) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AssetsSection
        id="project-1"
        assetView="references"
        draft={{ default_style_id: null }}
        assets={{ data: [assetFixture()] } as never}
        characters={{ data: [] } as never}
        outfits={{ data: [] } as never}
        modelOptions={[]}
        activeDrawModel={null}
        setDrawModel={vi.fn()}
        openPreview={() => undefined}
        rememberWorkspaceScroll={() => undefined}
        workspace={workspace}
      />
    </QueryClientProvider>,
  );
}

describe("AssetsSection 用途重分类（#165）", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("取消确认时回退 select 显示且不提交", () => {
    const workspace = makeWorkspace();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderAssets(workspace);
    const select = screen.getByLabelText("修改素材用途") as HTMLSelectElement;
    expect(select.value).toBe("CHARACTER_REFERENCE");
    fireEvent.change(select, { target: { value: "STYLE_REFERENCE" } });
    // 确认文案说明将从哪一类改为哪一类、可能解除绑定。
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("从「人物参考」改为「漫画风格」"));
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("可能解除已有绑定"));
    // 取消：不提交,select 显示回退到当前用途。
    expect(workspace.reclassifyAsset.mutate).not.toHaveBeenCalled();
    expect(select.value).toBe("CHARACTER_REFERENCE");
    confirmSpy.mockRestore();
  });

  it("确认后提交携带目标用途", () => {
    const workspace = makeWorkspace();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderAssets(workspace);
    fireEvent.change(screen.getByLabelText("修改素材用途"), { target: { value: "SCENE_REFERENCE" } });
    expect(workspace.reclassifyAsset.mutate).toHaveBeenCalledWith({
      assetId: "asset-1",
      kind: "SCENE_REFERENCE" as AssetPurpose,
    });
    confirmSpy.mockRestore();
  });

  it("重分类提交 pending 期间用途 select 禁用", () => {
    const workspace = makeWorkspace({ ...mutation(), isPending: true });
    renderAssets(workspace);
    expect(screen.getByLabelText("修改素材用途")).toBeDisabled();
  });
});

function characterFixture(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    project_id: "project-1",
    primary_name: "林澈",
    aliases: ["小澈"],
    alias_conflict: false,
    canonical_description: "",
    locked_features: ["黑色长发"],
    forbidden_changes: ["发色"],
    status: "ACTIVE",
    version: 1,
    references: [],
    ...overrides,
  };
}

function renderAssetsView(
  workspace: AssetsWorkspace,
  { assetView, characters }: { assetView: string; characters: Character[] },
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AssetsSection
        id="project-1"
        assetView={assetView as never}
        draft={{ default_style_id: null }}
        assets={{ data: [assetFixture()] } as never}
        characters={{ data: characters } as never}
        outfits={{ data: [] } as never}
        modelOptions={[]}
        activeDrawModel={null}
        setDrawModel={vi.fn()}
        openPreview={() => undefined}
        rememberWorkspaceScroll={() => undefined}
        workspace={workspace}
      />
    </QueryClientProvider>,
  );
}

describe("AssetsSection 候选错误面与角色切换草稿守卫（#545-4 / #546-1）", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
    vi.spyOn(api, "assetBatches").mockResolvedValue([]);
    vi.spyOn(api, "candidates").mockResolvedValue([]);
    vi.spyOn(api, "jobs").mockResolvedValue([]);
  });

  it("TEST-ASSET-ERR 服装穿着图候选读取失败显示错误卡与重试（#545-4）", async () => {
    const workspace = makeWorkspace();
    workspace.selectedCharacterOutfitId = "outfit-1";
    workspace.assetCandidates = {
      data: [],
      isError: true,
      error: new Error("候选接口 503"),
      refetch: vi.fn(),
    } as never;
    renderAssetsView(workspace, { assetView: "outfits", characters: [] });

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("服装穿着图读取失败");
    expect(alert).toHaveTextContent("候选接口 503");
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(workspace.assetCandidates.refetch).toHaveBeenCalled());
  });

  it("TEST-ASSET-GUARD1 角色规范有未保存修改时切换角色先确认，取消保留草稿（#546-1）", async () => {
    const first = characterFixture();
    const second = characterFixture({
      id: "character-2",
      primary_name: "苏晚",
      aliases: [],
      locked_features: [],
      forbidden_changes: [],
    });
    const workspace = makeWorkspace();
    workspace.bindCharacterId = first.id;
    workspace.boundCharacter = first;
    // 编辑器字段与服务器不一致 = 脏。
    workspace.editCharacterName = "林小澈";
    workspace.editCharacterAliases = first.aliases.join("，");
    workspace.editLockedFeatures = first.locked_features.join("，");
    workspace.editForbiddenChanges = first.forbidden_changes.join("，");
    renderAssetsView(workspace, { assetView: "characters", characters: [first, second] });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(await screen.findByRole("button", { name: /苏晚/ }));
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("未保存"));
    // 取消：不切换、不覆盖编辑字段。
    expect(workspace.setBindCharacterId).not.toHaveBeenCalled();
    expect(workspace.setEditCharacterName).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: /苏晚/ }));
    await waitFor(() => expect(workspace.setBindCharacterId).toHaveBeenCalledWith("character-2"));
    expect(workspace.setEditCharacterName).toHaveBeenCalledWith("苏晚");
    confirmSpy.mockRestore();
  });

  it("TEST-ASSET-GUARD2 概念设定面板有输入时切换角色不再确认（草稿按角色持久化）", async () => {
    const first = characterFixture();
    const second = characterFixture({
      id: "character-2",
      primary_name: "苏晚",
      aliases: [],
      locked_features: [],
      forbidden_changes: [],
    });
    const workspace = makeWorkspace();
    workspace.bindCharacterId = first.id;
    workspace.boundCharacter = first;
    workspace.editCharacterName = first.primary_name;
    workspace.editCharacterAliases = first.aliases.join("，");
    workspace.editLockedFeatures = first.locked_features.join("，");
    workspace.editForbiddenChanges = first.forbidden_changes.join("，");
    renderAssetsView(workspace, { assetView: "characters", characters: [first, second] });

    // 概念面板动态导入完成后渲染表单；输入即写入按「项目 + 角色」键控的
    // localStorage 草稿，切换角色（面板按 key 重挂载）不丢任何内容。
    // 反转说明：该测试曾断言「概念草稿有输入 → 切换必须确认」，但草稿本身
    // 已持久化并在重挂载后恢复，确认属于过度弹窗；conceptPanelDirty 及
    // CharacterConceptPanel 的 onDirtyChange 接线已按 #441（死道具陷阱）
    // 一并移除，概念输入不再参与切换确认。
    const appearance = await screen.findByPlaceholderText("简述外貌与气质；可留空");
    fireEvent.change(appearance, { target: { value: "黑发黑瞳" } });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: /苏晚/ }));
    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(workspace.setBindCharacterId).toHaveBeenCalledWith("character-2"));
    // 草稿保留在原角色的存储键里，切回即可恢复。
    expect(window.localStorage.getItem("mangaflow:character-concept-draft:project-1:character-1")).toContain("黑发黑瞳");
    confirmSpy.mockRestore();
  });

  it("TEST-ASSET-GUARD3 无任何草稿时切换角色不弹确认（#546-1）", async () => {
    const first = characterFixture();
    const second = characterFixture({
      id: "character-2",
      primary_name: "苏晚",
      aliases: [],
      locked_features: [],
      forbidden_changes: [],
    });
    const workspace = makeWorkspace();
    workspace.bindCharacterId = first.id;
    workspace.boundCharacter = first;
    workspace.editCharacterName = first.primary_name;
    workspace.editCharacterAliases = first.aliases.join("，");
    workspace.editLockedFeatures = first.locked_features.join("，");
    workspace.editForbiddenChanges = first.forbidden_changes.join("，");
    workspace.outfitName = "";
    workspace.selectedOutfitAssets = [];
    renderAssetsView(workspace, { assetView: "characters", characters: [first, second] });

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(await screen.findByRole("button", { name: /苏晚/ }));
    expect(confirmSpy).not.toHaveBeenCalled();
    await waitFor(() => expect(workspace.setBindCharacterId).toHaveBeenCalledWith("character-2"));
    confirmSpy.mockRestore();
  });
});
