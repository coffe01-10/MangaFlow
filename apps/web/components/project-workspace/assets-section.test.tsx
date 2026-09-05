import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Asset, AssetPurpose } from "@/lib/api";

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
