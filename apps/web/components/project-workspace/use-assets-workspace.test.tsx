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
        characterFixture({ id: "character-b", primary_name: "角色B" }),
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
    // 身份防护只拦表单回填…
    expect(result.current.editCharacterName).toBe("角色A");
    // …缓存版本号必须失效，否则下一次保存带旧 version 撞出假 409。
    expect(client.getQueryState(["characters", "project-1"])?.isInvalidated).toBe(true);
  });

  it("晚到的服装保存即使面板已换服装，也仍然失效 outfits 缓存", async () => {
    const client = createClient();
    await seedFreshCache(client, ["outfits", "project-1"], [outfitFixture()]);
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
