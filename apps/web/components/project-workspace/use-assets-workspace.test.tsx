import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Asset, type Character, type Outfit, type StyleProfile } from "@/lib/api";

import { useAssetsWorkspace } from "./use-assets-workspace";

const stylesApi = vi.spyOn(api, "styles");
const libraryApi = vi.spyOn(api, "library");
const updateCharacterApi = vi.spyOn(api, "updateCharacter");
const updateOutfitApi = vi.spyOn(api, "updateOutfit");
const deleteOutfitApi = vi.spyOn(api, "deleteOutfit");
const updateAssetApi = vi.spyOn(api, "updateAsset");
const uploadAssetApi = vi.spyOn(api, "uploadAsset");
const bindCharacterReferenceApi = vi.spyOn(api, "bindCharacterReference");
const unbindCharacterReferenceApi = vi.spyOn(api, "unbindCharacterReference");
const updateStyleModeApi = vi.spyOn(api, "updateStyleMode");

function characterFixture(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-a",
    project_id: "project-1",
    primary_name: "角色A",
    aliases: [],
    alias_conflict: false,
    canonical_description: "",
    locked_features: [],
    forbidden_changes: [],
    status: "ACTIVE",
    version: 1,
    references: [],
    ...overrides,
  };
}

function outfitFixture(overrides: Partial<Outfit> = {}): Outfit {
  return {
    id: "outfit-a",
    project_id: "project-1",
    character_id: "character-a",
    name: "校服",
    components: {},
    state_rules: {},
    locked_fields: [],
    reference_asset_ids: [],
    status: "ACTIVE",
    version: 1,
    ...overrides,
  };
}

function styleFixture(overrides: Partial<StyleProfile> = {}): StyleProfile {
  return {
    id: "style-1",
    project_id: "project-1",
    name: "黑白网点风格",
    color_mode: "monochrome",
    profile: {},
    locked_fields: [],
    status: "ANALYZED",
    version: 1,
    ...overrides,
  };
}

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderAssets(
  client: QueryClient,
  initialCharacterId?: string,
  wrapper?: ({ children }: { children: ReactNode }) => ReactNode,
) {
  const defaultWrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(() => useAssetsWorkspace({
    id: "project-1",
    section: "assets",
    assetView: "references",
    router: { push: vi.fn() } as never,
    projectPath: (target) => `/projects/project-1/${target}`,
    activeChapterId: "chapter-1",
    assets: { data: [] } as never,
    characters: {
      data: [
        characterFixture(),
        characterFixture({
          id: "character-b",
          primary_name: "角色B",
          aliases: ["小B"],
          locked_features: ["银发"],
          forbidden_changes: ["左眼泪痣"],
        }),
      ],
    } as never,
    outfits: {
      data: [
        outfitFixture(),
        outfitFixture({ id: "outfit-b", name: "便服", character_id: "character-b" }),
      ],
    } as never,
    requireDrawModel: () => "image.nano_banana_2",
    initialCharacterId,
  }), { wrapper: wrapper ?? defaultWrapper });
  return hook;
}

// fetchQuery creates a fresh (not invalidated) cache entry without mounting an
// observer, so isInvalidated flips only when a mutation really invalidates.
async function seedFreshCache(client: QueryClient, key: unknown[], data: unknown) {
  await client.fetchQuery({
    queryKey: key,
    queryFn: () => Promise.resolve(data),
  });
}

describe("useAssetsWorkspace 缓存失效与晚到保存防护", () => {
  beforeEach(() => {
    stylesApi.mockReset().mockResolvedValue([] satisfies StyleProfile[]);
    libraryApi.mockReset();
    updateCharacterApi.mockReset();
    updateOutfitApi.mockReset();
    deleteOutfitApi.mockReset().mockResolvedValue({ ok: true } as never);
    updateAssetApi.mockReset().mockResolvedValue({
      id: "asset-1",
      kind: "STYLE_REFERENCE",
    } as Asset);
    uploadAssetApi.mockReset().mockResolvedValue({
      id: "asset-9",
      kind: "CHARACTER_REFERENCE",
    } as Asset);
    bindCharacterReferenceApi.mockReset().mockResolvedValue({} as never);
    unbindCharacterReferenceApi.mockReset().mockResolvedValue({} as never);
    updateStyleModeApi.mockReset().mockResolvedValue(styleFixture() as never);
  });

  it("晚到的角色保存即使面板已切人，也仍然失效 characters 缓存", async () => {
    const client = createClient();
    await seedFreshCache(client, ["characters", "project-1"], [characterFixture()]);
    const { result } = renderAssets(client, "character-a");
    // 深链预填：表单初始为角色A。
    await waitFor(() => {
      expect(result.current.editCharacterName).toBe("角色A");
    });
    let resolveUpdate: (character: Character) => void = () => undefined;
    updateCharacterApi.mockImplementation(
      () => new Promise((resolve) => {
        resolveUpdate = resolve;
      }),
    );
    await act(async () => {
      result.current.updateCharacter.mutate();
    });
    // 保存进行中用户切到角色B：晚到的 A 结果不允许回填表单。
    act(() => {
      result.current.setBindCharacterId("character-b");
    });
    await act(async () => {
      resolveUpdate(characterFixture({ primary_name: "角色A改", version: 2 }));
      await waitFor(() => {
        expect(result.current.updateCharacter.isSuccess).toBe(true);
      });
    });
    // 身份防护只拦表单回填：#647 后改绑即重播种，表单此刻已是角色B的数据
    // （既不是旧的「角色A」、更不是晚到的「角色A改」——晚到保存不得覆盖）。
    await waitFor(() => {
      expect(result.current.editCharacterName).toBe("角色B");
    });
    // …缓存版本号必须失效，否则下一次保存带旧 version 撞出假 409。
    expect(client.getQueryState(["characters", "project-1"])?.isInvalidated).toBe(true);
  });

  it("晚到的服装保存即使面板已换服装，也仍然失效 outfits 缓存", async () => {
    const client = createClient();
    await seedFreshCache(client, ["outfits", "project-1"], [outfitFixture()]);
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderAssets(client, "character-a");
    act(() => {
      result.current.beginOutfitEdit(outfitFixture());
    });
    expect(result.current.outfitName).toBe("校服");
    let resolveUpdate: (outfit: Outfit) => void = () => undefined;
    updateOutfitApi.mockImplementation(
      () => new Promise<Outfit>((resolve) => {
        resolveUpdate = resolve;
      }),
    );
    await act(async () => {
      result.current.updateOutfit.mutate();
    });
    // 保存进行中用户改编辑另一件服装：晚到结果不允许清空当前表单。
    act(() => {
      result.current.beginOutfitEdit(outfitFixture({ id: "outfit-b", name: "便服" }));
    });
    await act(async () => {
      resolveUpdate(outfitFixture({ version: 2 }));
      await waitFor(() => {
        expect(result.current.updateOutfit.isSuccess).toBe(true);
      });
    });
    expect(result.current.outfitName).toBe("便服");
    expect(result.current.editingOutfitId).toBe("outfit-b");
    expect(client.getQueryState(["outfits", "project-1"])?.isInvalidated).toBe(true);
    // #544：保存改变了服装参考集合 → 生成就绪输入变化，生成工作台必须一并
    // 失效（与 deleteOutfit 同族），否则生成台按旧参考继续禁用生成。
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["generation-workbench"] });
  });

  it("#544 上传并自动绑定人物参考后失效 generation-workbench：就绪阻塞项立即解除", async () => {
    const client = createClient();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderAssets(client, "character-a");
    await waitFor(() => {
      expect(result.current.boundCharacter?.id).toBe("character-a");
    });
    // assetView=references 的当前用途是 CHARACTER_REFERENCE，且已选中角色：
    // 上传后会走 upload + bindCharacterReference 组合路径。
    await act(async () => {
      result.current.upload.mutate(new File(["x"], "ref.png", { type: "image/png" }));
      await waitFor(() => {
        expect(result.current.upload.isSuccess).toBe(true);
      });
    });
    expect(bindCharacterReferenceApi).toHaveBeenCalledWith("character-a", "asset-9");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["assets", "project-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["characters", "project-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["generation-workbench"] });
  });

  it("#544 绑定既有素材为人物参考后失效 generation-workbench", async () => {
    const client = createClient();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderAssets(client, "character-a");
    await waitFor(() => {
      expect(result.current.boundCharacter?.id).toBe("character-a");
    });
    await act(async () => {
      result.current.bindExistingCharacterReference.mutate("asset-9");
      await waitFor(() => {
        expect(result.current.bindExistingCharacterReference.isSuccess).toBe(true);
      });
    });
    expect(bindCharacterReferenceApi).toHaveBeenCalledWith("character-a", "asset-9");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["generation-workbench"] });
  });

  it("#544 解绑人物参考后失效 generation-workbench：解掉最后一个参考会重建 MISSING_CHARACTER_REFERENCE 阻塞", async () => {
    const client = createClient();
    await seedFreshCache(client, ["generation-workbench", "page-1"], { stale: true });
    const { result } = renderAssets(client, "character-a");
    await act(async () => {
      result.current.unbindExistingCharacterReference.mutate("reference-1");
      await waitFor(() => {
        expect(result.current.unbindExistingCharacterReference.isSuccess).toBe(true);
      });
    });
    expect(unbindCharacterReferenceApi).toHaveBeenCalledWith("reference-1");
    // 与镜像的绑定路径一致：就绪判定必须立即变旧，而不是等 15s staleTime。
    expect(client.getQueryState(["generation-workbench", "page-1"])?.isInvalidated).toBe(true);
  });

  it("#544 切换风格色彩模式后失效 generation-workbench：STYLE_NOT_COLOR 阻塞即时反映", async () => {
    const client = createClient();
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    const { result } = renderAssets(client, "character-a");
    await act(async () => {
      result.current.updateStyleMode.mutate({ style: styleFixture(), colorMode: "color" });
      await waitFor(() => {
        expect(result.current.updateStyleMode.isSuccess).toBe(true);
      });
    });
    expect(updateStyleModeApi).toHaveBeenCalledWith("style-1", 1, "color");
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["styles", "project-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["generation-workbench"] });
  });

  it("删除服装档案会失效 generation-workbench，与 deleteAsset 行为一致", async () => {
    const client = createClient();
    await seedFreshCache(client, ["generation-workbench", "page-1"], { stale: true });
    const { result } = renderAssets(client, "character-a");
    await act(async () => {
      result.current.deleteOutfit.mutate(outfitFixture());
      await waitFor(() => {
        expect(result.current.deleteOutfit.isSuccess).toBe(true);
      });
    });
    expect(client.getQueryState(["generation-workbench", "page-1"])?.isInvalidated).toBe(true);
  });

  it("重分类素材会失效 generation-workbench：CharacterReference 解绑改变生成就绪", async () => {
    const client = createClient();
    await seedFreshCache(client, ["generation-workbench", "page-1"], { stale: true });
    const { result } = renderAssets(client, "character-a");
    await act(async () => {
      result.current.reclassifyAsset.mutate({ assetId: "asset-1", kind: "STYLE_REFERENCE" });
      await waitFor(() => {
        expect(result.current.reclassifyAsset.isSuccess).toBe(true);
      });
    });
    expect(client.getQueryState(["generation-workbench", "page-1"])?.isInvalidated).toBe(true);
  });
});

describe("useAssetsWorkspace 风格色彩模式的水合安全", () => {
  beforeEach(() => {
    window.localStorage.clear();
    stylesApi.mockReset().mockResolvedValue([] satisfies StyleProfile[]);
    libraryApi.mockReset();
  });

  it("持久化的彩色偏好经 useSyncExternalStore 直接生效，无渲染期 setState", async () => {
    window.localStorage.setItem("mangaflow.style-mode.project-1", "color");
    const client = createClient();
    const { result } = renderAssets(client, "character-a");
    // 客户端首帧即读到存储值；服务端水合走 getServerSnapshot 的默认「黑白」，
    // 水合完成后由 React 切到客户端快照，不产生水合不匹配或级联渲染。
    await waitFor(() => {
      expect(result.current.styleColorMode).toBe("color");
    });
  });

  it("存储值不精确等于 color 时保持黑白；selectStyleMode 仍写同一键并即时生效", async () => {
    window.localStorage.setItem("mangaflow.style-mode.project-1", "COLOR");
    const client = createClient();
    const { result } = renderAssets(client, "character-a");
    await waitFor(() => {
      expect(result.current.styleColorMode).toBe("monochrome");
    });

    act(() => {
      result.current.selectStyleMode("color");
    });
    expect(result.current.styleColorMode).toBe("color");
    expect(window.localStorage.getItem("mangaflow.style-mode.project-1")).toBe("color");
  });
});

describe("useAssetsWorkspace 改绑重播种与脏确认（#647）", () => {
  beforeEach(() => {
    window.localStorage.clear();
    stylesApi.mockReset().mockResolvedValue([] satisfies StyleProfile[]);
    libraryApi.mockReset();
    updateCharacterApi.mockReset().mockResolvedValue(
      characterFixture({ id: "character-b", primary_name: "角色B", version: 2 }),
    );
    updateOutfitApi.mockReset();
    deleteOutfitApi.mockReset().mockResolvedValue({ ok: true } as never);
  });

  // 支持以不同 initialCharacterId 重渲染（深链前进/后退路径），其余 props 与
  // renderAssets 保持同构。
  function renderAssetsHook(initialProps: { initialCharacterId?: string | null }) {
    const client = createClient();
    const hook = renderHook(
      (props: { initialCharacterId?: string | null }) => useAssetsWorkspace({
        id: "project-1",
        section: "assets",
        assetView: "references",
        router: { push: vi.fn() } as never,
        projectPath: (target) => `/projects/project-1/${target}`,
        activeChapterId: "chapter-1",
        assets: { data: [] } as never,
        characters: {
          data: [
            characterFixture(),
            characterFixture({
              id: "character-b",
              primary_name: "角色B",
              aliases: ["小B"],
              locked_features: ["银发"],
              forbidden_changes: ["左眼泪痣"],
            }),
          ],
        } as never,
        outfits: {
          data: [
            outfitFixture(),
            outfitFixture({ id: "outfit-b", name: "便服", character_id: "character-b" }),
          ],
        } as never,
        requireDrawModel: () => "image.nano_banana_2",
        initialCharacterId: props.initialCharacterId,
      }),
      {
        wrapper: ({ children }: { children: ReactNode }) => (
          <QueryClientProvider client={client}>{children}</QueryClientProvider>
        ),
        initialProps,
      },
    );
    return hook;
  }

  it("改绑后编辑表单按新角色重播种，保存写入的是新角色（#647）", async () => {
    const { result } = renderAssetsHook({ initialCharacterId: "character-a" });
    await waitFor(() => {
      expect(result.current.editCharacterName).toBe("角色A");
    });
    // 服装视图「所属角色」select 确认后的落地路径：直接换绑。
    act(() => {
      result.current.setBindCharacterId("character-b");
    });
    await waitFor(() => {
      expect(result.current.boundCharacter?.id).toBe("character-b");
      expect(result.current.editCharacterName).toBe("角色B");
      expect(result.current.editCharacterAliases).toBe("小B");
      expect(result.current.editLockedFeatures).toBe("银发");
      expect(result.current.editForbiddenChanges).toBe("左眼泪痣");
    });
    // 保存不得再把旧角色的规范字段写进新角色：目标是 character-b、内容是 B 的。
    await act(async () => {
      result.current.updateCharacter.mutate();
      await waitFor(() => {
        expect(result.current.updateCharacter.isSuccess).toBe(true);
      });
    });
    expect(updateCharacterApi).toHaveBeenCalledWith(
      "character-b",
      1,
      "角色B",
      ["小B"],
      ["银发"],
      ["左眼泪痣"],
    );
  });

  it("脏状态下 beginOutfitEdit 跨角色改绑先确认，拒绝保持原绑定且不进入编辑（#647）", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { result } = renderAssetsHook({ initialCharacterId: "character-a" });
    await waitFor(() => {
      expect(result.current.editCharacterName).toBe("角色A");
    });
    act(() => {
      result.current.setEditCharacterName("角色A（未保存）");
    });
    act(() => {
      result.current.beginOutfitEdit(outfitFixture({ id: "outfit-b", name: "便服", character_id: "character-b" }));
    });
    // 与 switchBoundCharacter 同一确认文案；拒绝不改绑、不清表单、不进编辑态。
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("未保存"));
    expect(result.current.bindCharacterId).toBe("character-a");
    expect(result.current.editCharacterName).toBe("角色A（未保存）");
    expect(result.current.editingOutfitId).toBeNull();
    expect(result.current.outfitName).toBe("");
    // 确认后放行：服装编辑态落地、绑定切到归属角色、编辑表单重播种为 B。
    confirmSpy.mockReturnValue(true);
    act(() => {
      result.current.beginOutfitEdit(outfitFixture({ id: "outfit-b", name: "便服", character_id: "character-b" }));
    });
    expect(result.current.editingOutfitId).toBe("outfit-b");
    expect(result.current.outfitName).toBe("便服");
    await waitFor(() => {
      expect(result.current.bindCharacterId).toBe("character-b");
      expect(result.current.editCharacterName).toBe("角色B");
    });
    confirmSpy.mockRestore();
  });

  it("beginOutfitEdit 编辑当前绑定角色的服装不触发改绑确认", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { result } = renderAssetsHook({ initialCharacterId: "character-a" });
    await waitFor(() => {
      expect(result.current.editCharacterName).toBe("角色A");
    });
    act(() => {
      result.current.beginOutfitEdit(outfitFixture());
    });
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(result.current.editingOutfitId).toBe("outfit-a");
    expect(result.current.outfitName).toBe("校服");
    confirmSpy.mockRestore();
  });

  it("深链改绑在脏状态下先确认，拒绝保持原绑定（#647）", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { result, rerender } = renderAssetsHook({ initialCharacterId: "character-a" });
    await waitFor(() => {
      expect(result.current.editCharacterName).toBe("角色A");
    });
    act(() => {
      result.current.setEditCharacterName("角色A（未保存）");
    });
    rerender({ initialCharacterId: "character-b" });
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("未保存"));
    expect(result.current.bindCharacterId).toBe("character-a");
    expect(result.current.editCharacterName).toBe("角色A（未保存）");
    // 拒绝后再导航（目标变化）且确认接受：正常改绑并按新角色重播种。
    confirmSpy.mockReturnValue(true);
    rerender({ initialCharacterId: null });
    await waitFor(() => {
      expect(result.current.bindCharacterId).toBe("");
    });
    expect(result.current.editCharacterName).toBe("角色A（未保存）");
    confirmSpy.mockRestore();
  });
});
