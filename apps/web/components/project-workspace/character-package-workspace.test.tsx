import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  type Asset,
  type Character,
  type CharacterModelPackage,
  type CharacterPackageListQuery,
  type CharacterPackageSummary,
  type Outfit,
  type PackageDiff,
  type PackageVersion,
} from "@/lib/api";

import { CharacterPackageWorkspace } from "./character-package-workspace";
import {
  buildDefaultReferenceSelections,
  isGenerationReferenceReady,
  isPackageModeSelection,
} from "./reference-selection";

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}));

const listApi = vi.spyOn(api, "characterPackages");
const detailApi = vi.spyOn(api, "characterPackage");
const createApi = vi.spyOn(api, "createCharacterPackage");
const updateApi = vi.spyOn(api, "updateCharacterPackage");
const deriveApi = vi.spyOn(api, "deriveCharacterPackageVersion");
const publishApi = vi.spyOn(api, "publishCharacterPackageVersion");
const activateApi = vi.spyOn(api, "activateCharacterPackageVersion");
const archiveVersionApi = vi.spyOn(api, "archiveCharacterPackageVersion");
const restoreVersionApi = vi.spyOn(api, "restoreCharacterPackageVersion");
const deleteDraftApi = vi.spyOn(api, "deleteCharacterPackageVersion");
const bindRefApi = vi.spyOn(api, "bindCharacterPackageReference");
const unbindRefApi = vi.spyOn(api, "unbindCharacterPackageReference");
const coverApi = vi.spyOn(api, "setCharacterPackageCover");
const bindOutfitApi = vi.spyOn(api, "bindCharacterPackageOutfit");
const defaultOutfitApi = vi.spyOn(api, "setCharacterPackageOutfitDefault");
const diffApi = vi.spyOn(api, "characterPackageDiff");
const uploadApi = vi.spyOn(api, "uploadAsset");
const outfitsApi = vi.spyOn(api, "outfits");

function characterFixture(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    project_id: "project-1",
    primary_name: "林澈",
    aliases: ["小澈"],
    alias_conflict: false,
    canonical_description: "",
    locked_features: [],
    forbidden_changes: [],
    status: "ACTIVE",
    version: 1,
    references: [{
      id: "cr-1",
      character_id: "character-1",
      asset_id: "asset-1",
      angle: "front",
      is_canonical: true,
    }],
    ...overrides,
  };
}

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

function outfitFixture(overrides: Partial<Outfit> = {}): Outfit {
  return {
    id: "outfit-1",
    project_id: "project-1",
    character_id: "character-1",
    name: "青藤高校夏季校服",
    components: {},
    state_rules: {},
    locked_fields: [],
    reference_asset_ids: ["outfit-asset-1"],
    status: "ACTIVE",
    version: 1,
    ...overrides,
  };
}

function versionFixture(overrides: Partial<PackageVersion> = {}): PackageVersion {
  return {
    id: "version-1",
    package_id: "pkg-1",
    version_number: 1,
    status: "DRAFT",
    spec_snapshot: { identity_spec: {}, visual_spec: {}, negative_constraints: [], frozen_from: "package" },
    derived_from_version_id: null,
    published_at: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 5,
    references: [],
    outfits: [],
    completeness: { score: 20, missing: [] },
    ...overrides,
  };
}

const frontRef = {
  id: "ref-front",
  version_id: "version-2",
  asset_id: "asset-1",
  role: "front",
  label: "",
  sort_order: 0,
  created_at: "2026-09-01T00:00:00Z",
};

function packageFixture(overrides: Partial<CharacterModelPackage> = {}): CharacterModelPackage {
  return {
    id: "pkg-1",
    character_id: "character-1",
    project_id: "project-1",
    identity_spec: { age_appearance: "17 岁高中生", gender: "女" },
    visual_spec: { hair: "黑色碎短发" },
    negative_constraints: ["禁止改变发色"],
    published_version_id: "version-1",
    status: "ACTIVE",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 2,
    versions: [
      versionFixture({ id: "version-2", version_number: 2, status: "DRAFT", version: 7, references: [frontRef] }),
      versionFixture({ id: "version-1", version_number: 1, status: "READY", published_at: "2026-09-01T01:00:00Z" }),
    ],
    completeness: { score: 85, missing: [] },
    ...overrides,
  };
}

function summaryFixture(overrides: Partial<CharacterPackageSummary> = {}): CharacterPackageSummary {
  return {
    id: "pkg-1",
    character_id: "character-1",
    project_id: "project-1",
    status: "ACTIVE",
    published_version_id: "version-1",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 1,
    character: { id: "character-1", primary_name: "林澈", aliases: ["小澈"], alias_conflict: false },
    published_version_number: 1,
    published_completeness: { score: 85, missing: [] },
    ...overrides,
  };
}

function renderWorkspace({ characters = [characterFixture()], assets = [assetFixture()] } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(
    <CharacterPackageWorkspace projectId="project-1" characters={characters} assets={assets} />,
    { wrapper },
  );
}

describe("CharacterPackageWorkspace", () => {
  beforeEach(() => {
    listApi.mockReset().mockResolvedValue([]);
    detailApi.mockReset();
    createApi.mockReset();
    updateApi.mockReset();
    deriveApi.mockReset();
    publishApi.mockReset();
    activateApi.mockReset();
    archiveVersionApi.mockReset();
    restoreVersionApi.mockReset();
    deleteDraftApi.mockReset();
    bindRefApi.mockReset();
    unbindRefApi.mockReset();
    coverApi.mockReset();
    bindOutfitApi.mockReset();
    defaultOutfitApi.mockReset();
    diffApi.mockReset();
    uploadApi.mockReset();
    outfitsApi.mockReset().mockResolvedValue([]);
  });

  it("TEST-PKG-01 首次进入展示空状态，加载后渲染角色卡与完整度", async () => {
    const { rerender } = renderWorkspace();
    expect(await screen.findByText("尚未创建角色模型包")).toBeInTheDocument();

    listApi.mockResolvedValue([summaryFixture()]);
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <CharacterPackageWorkspace projectId="project-1" characters={[characterFixture()]} assets={[assetFixture()]} />
      </QueryClientProvider>,
    );
    // 只在包列表里找卡片：新建角色的下拉 option 也是隐式 role="option"。
    const listbox = await screen.findByRole("listbox", { name: "角色模型包列表" });
    const card = await within(listbox).findByRole("option", { name: /林澈/ });
    expect(card).toHaveTextContent("已发布 V1");
    expect(card).toHaveTextContent("完整度 85%");
    // 地址锚点：列表只以 character_id 寻址，且渲染不自动建包。
    expect(createApi).not.toHaveBeenCalled();
  });

  it("TEST-PKG-01 角色包列表分页拉取全部页，短页结束且第二页可选", async () => {
    detailApi.mockResolvedValue(packageFixture());
    const firstPage = Array.from({ length: 200 }, (_, index) => summaryFixture({
      id: `pkg-${1000 + index}`,
      character_id: `character-${1000 + index}`,
      character: { id: `character-${1000 + index}`, primary_name: `角色${index}`, aliases: [], alias_conflict: false },
      published_version_id: null,
      published_version_number: null,
      published_completeness: null,
    }));
    listApi.mockImplementation((_projectId: string, query?: CharacterPackageListQuery) =>
      Promise.resolve((query?.offset ?? 0) === 0 ? firstPage : [summaryFixture()]));
    renderWorkspace();
    await waitFor(() => {
      expect(listApi).toHaveBeenCalledTimes(2);
    });
    expect(listApi).toHaveBeenCalledWith("project-1", { limit: 200, offset: 0 });
    expect(listApi).toHaveBeenLastCalledWith("project-1", { limit: 200, offset: 200 });
    const listbox = await screen.findByRole("listbox", { name: "角色模型包列表" });
    expect(within(listbox).getAllByRole("option")).toHaveLength(201);
    fireEvent.click(within(listbox).getByRole("option", { name: /林澈/ }));
    expect(await screen.findByRole("heading", { name: "林澈 的角色模型包" })).toBeInTheDocument();
    expect(detailApi).toHaveBeenCalledWith("project-1", "character-1");
  });

  it("TEST-PKG-02 完整度百分比来自 API 且缺失项可见；无参考图时阻止发布", async () => {
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockResolvedValue(packageFixture({
      versions: [versionFixture({
        id: "version-2",
        version_number: 2,
        version: 7,
        references: [],
        completeness: {
          score: 20,
          missing: [{ code: "MISSING_VIEW", field: "front", message: "缺少正面参考", suggestion: "上传或生成正面图后重新发布版本" }],
        },
      })],
      published_version_id: "version-1",
    }));
    const first = renderWorkspace();
    const gauge = await screen.findByRole("progressbar", { name: "角色包完整度" });
    expect(gauge).toHaveAttribute("aria-valuenow", "20");
    expect(await screen.findByText("缺少正面参考")).toBeInTheDocument();
    expect(screen.getByText("上传或生成正面图后重新发布版本")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发布当前版本 V2/ })).toBeDisabled();
    first.unmount();

    // 完整度只来自服务端读取路径：API 返回更高分时展示随之更新，前端不本地计算。
    detailApi.mockResolvedValue(packageFixture({
      versions: [versionFixture({
        id: "version-2",
        version_number: 2,
        version: 7,
        references: [frontRef],
        completeness: { score: 80, missing: [] },
      })],
      published_version_id: "version-1",
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CharacterPackageWorkspace projectId="project-1" characters={[characterFixture()]} assets={[assetFixture()]} />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("progressbar", { name: "角色包完整度" })).toHaveAttribute("aria-valuenow", "80");
    expect(screen.getByRole("button", { name: /发布当前版本 V2/ })).toBeEnabled();
  });

  it("TEST-PKG-03 空槽可上传或绑定已有素材，已绑槽位可解绑", async () => {
    let current = packageFixture();
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockImplementation(async () => current);
    const uploaded = assetFixture({ id: "asset-1", original_name: "side.png", display_name: null });
    uploadApi.mockResolvedValue(uploaded);
    bindRefApi.mockImplementation(async (_project, _character, _version, payload) => {
      current = {
        ...current,
        versions: current.versions.map((item) => item.status === "DRAFT" ? {
          ...item,
          references: [...item.references, {
            id: `ref-${payload.role}`,
            version_id: item.id,
            asset_id: payload.asset_id,
            role: payload.role,
            label: payload.label ?? "",
            sort_order: 0,
            created_at: "2026-09-01T00:00:00Z",
          }],
        } : item),
      };
      return {
        id: `ref-${payload.role}`,
        version_id: "version-2",
        asset_id: payload.asset_id,
        role: payload.role,
        label: payload.label ?? "",
        sort_order: 0,
        created_at: "2026-09-01T00:00:00Z",
      };
    });
    renderWorkspace();
    const listbox = await screen.findByRole("listbox", { name: "角色模型包列表" });
    await within(listbox).findByRole("option", { name: /林澈/ });
    await screen.findByLabelText("多角度视角矩阵");

    const file = new File(["png"], "side.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传右侧面参考图"), { target: { files: [file] } });
    await waitFor(() => {
      expect(uploadApi).toHaveBeenCalledWith("project-1", "CHARACTER_REFERENCE", file);
      expect(bindRefApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", expect.objectContaining({
        asset_id: "asset-1",
        role: "side",
        version: 7,
      }));
    });
    expect(await screen.findByRole("img", { name: "林澈 V2 - 右侧面参考图" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("为3/4 侧面绑定已有素材"), { target: { value: "asset-1" } });
    await waitFor(() => {
      expect(bindRefApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", expect.objectContaining({
        asset_id: "asset-1",
        role: "three_quarter",
      }));
    });

    // 解绑只作用于正面槽位卡片；其余槽位此时也已有各自的解绑按钮。
    const frontCard = screen.getByLabelText("正面（主视）槽位已绑定");
    fireEvent.click(within(frontCard).getByRole("button", { name: "解绑" }));
    await waitFor(() => {
      expect(unbindRefApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", "ref-front", 7);
    });
  });

  it("TEST-PKG-04 发布后版本冻结，派生新草稿解除锁定", async () => {
    let current = packageFixture({
      published_version_id: null,
      versions: [versionFixture({ id: "version-1", version_number: 1, status: "DRAFT", version: 5, references: [frontRef] })],
    });
    listApi.mockResolvedValue([summaryFixture({ published_version_id: null, published_version_number: null, published_completeness: null })]);
    detailApi.mockImplementation(async () => current);
    publishApi.mockImplementation(async () => {
      current = {
        ...current,
        published_version_id: "version-1",
        versions: [versionFixture({
          id: "version-1",
          version_number: 1,
          status: "READY",
          published_at: "2026-09-01T02:00:00Z",
          references: [frontRef],
          spec_snapshot: { identity_spec: { age_appearance: "17 岁高中生" }, visual_spec: {}, negative_constraints: ["禁止改变发色"], frozen_from: "package" },
        })],
      };
      return current.versions[0];
    });
    deriveApi.mockImplementation(async () => {
      current = {
        ...current,
        versions: [
          versionFixture({ id: "version-2", version_number: 2, status: "DRAFT", version: 9 }),
          current.versions[0],
        ],
      };
      return current.versions[0];
    });
    renderWorkspace();
    const publishButton = await screen.findByRole("button", { name: /发布当前版本 V1/ });
    expect(screen.getByRole("button", { name: /派生新版本/ })).toBeDisabled();
    fireEvent.click(publishButton);
    const dialog = screen.getByRole("dialog", { name: "发布版本 V1？" });
    expect(dialog).toHaveTextContent("发布后该版本将固化为不可变版本");
    fireEvent.click(within(dialog).getByRole("button", { name: "确认发布" }));
    await waitFor(() => {
      expect(publishApi).toHaveBeenCalledWith("project-1", "character-1", "version-1");
    });

    // 发布后冻结版本仍可读（§9.2）：规格、矩阵与服装集只读展示，输入全部消失。
    const frozenBlock = await screen.findByLabelText("已冻结版本 V1");
    expect(within(frozenBlock).getByRole("img", { name: "林澈 V1 - 正面（主视）参考图" })).toBeInTheDocument();
    expect(within(frozenBlock).getByText("17 岁高中生")).toBeInTheDocument();
    expect(within(frozenBlock).getByText(/禁止改变发色/)).toBeInTheDocument();
    expect(within(frozenBlock).queryByLabelText("上传正面（主视）参考图")).not.toBeInTheDocument();
    expect(within(frozenBlock).queryByLabelText("表情标签")).not.toBeInTheDocument();
    expect(within(frozenBlock).queryByRole("button", { name: "解绑" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("角色包规格工作集")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /派生新版本/ })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: /派生新版本/ }));
    await waitFor(() => {
      expect(deriveApi).toHaveBeenCalledWith("project-1", "character-1", "version-1");
    });
    expect(screen.queryByLabelText("已冻结版本 V1")).not.toBeInTheDocument();
    expect(await screen.findByText("草稿 V2")).toBeInTheDocument();
    expect(screen.getByLabelText("角色包规格工作集")).toBeInTheDocument();
  });

  it("TEST-PKG-05 差异对比模态框展示规格与矩阵差异，Esc 关闭", async () => {
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockResolvedValue(packageFixture());
    const diff: PackageDiff = {
      base_version_id: "version-1",
      target_version_id: "version-2",
      identity_spec: { added: {}, removed: {}, changed: [] },
      visual_spec: { added: {}, removed: {}, changed: [{ field: "hair", base_value: "黑色碎短发", target_value: "银白狼尾" }] },
      negative_constraints: { added: ["禁止添加眼镜"], removed: [] },
      references: {
        added: [{ role: "expression", label: "joy", asset_id: "asset-5", asset_deleted: false }],
        removed: [],
        changed: [],
      },
      outfits: { added: [{ outfit_id: "outfit-1", is_default: true, sort_order: 0 }], removed: [], changed: [] },
    };
    diffApi.mockResolvedValue(diff);
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: /对比历史/ }));
    const dialog = screen.getByRole("dialog", { name: "版本差异对比：林澈" });
    expect(within(dialog).getByLabelText("选择基线版本")).toHaveFocus();
    await waitFor(() => {
      expect(diffApi).toHaveBeenCalledWith("project-1", "character-1", "version-1", "version-2");
    });
    expect(await within(dialog).findByText("发型")).toBeInTheDocument();
    expect(within(dialog).getByText("黑色碎短发 → 银白狼尾")).toBeInTheDocument();
    expect(within(dialog).getByText("禁止添加眼镜")).toBeInTheDocument();
    expect(within(dialog).getByText("表情 · joy")).toBeInTheDocument();
    fireEvent.keyDown(dialog, { key: "Escape" });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "版本差异对比：林澈" })).not.toBeInTheDocument();
    });
  });

  it("TEST-PKG-06 无包角色不受影响：列表为空且默认继承不发送 package_version_id", async () => {
    const selections = buildDefaultReferenceSelections(["character-1"], [characterFixture()], [outfitFixture()], []);
    // 旧路径逐字段不变：未发布包的角色继续发送人物参考图选择。
    expect(selections).toEqual({
      "character-1": { character_asset_id: "asset-1", outfit_id: null, outfit_asset_id: null },
    });
    expect(isGenerationReferenceReady(selections, ["character-1"], [outfitFixture()])).toBe(true);
    expect(isPackageModeSelection("character-1", selections)).toBe(false);
    // 有发布版本的角色进入包模式：人物参考图由服务端从版本矩阵解析。
    const packageSelections = buildDefaultReferenceSelections(["character-1"], [characterFixture()], [outfitFixture()], [], { "character-1": "version-1" });
    expect(packageSelections).toEqual({
      "character-1": { character_asset_id: null, outfit_id: null, outfit_asset_id: null, package_version_id: null },
    });
    expect(isGenerationReferenceReady(packageSelections, ["character-1"], [outfitFixture()], { "character-1": "version-1" })).toBe(true);
    // 包模式下服装仍需有可用参考图：分镜指定服装没有任何参考图时仍然拦截。
    const explicitVersion = {
      "character-1": { character_asset_id: null, outfit_id: "outfit-1", outfit_asset_id: null, package_version_id: "version-9" },
    };
    expect(isGenerationReferenceReady(explicitVersion, ["character-1"], [outfitFixture({ reference_asset_ids: [] })])).toBe(false);
    expect(isGenerationReferenceReady(explicitVersion, ["character-1"], [outfitFixture()])).toBe(true);
  });

  it("TEST-PKG-07 迁移产生的 V1 草稿可读取，既有素材可绑定，不自动建包", async () => {
    listApi.mockResolvedValue([summaryFixture({ published_version_id: null, published_version_number: null, published_completeness: null })]);
    detailApi.mockResolvedValue(packageFixture({
      published_version_id: null,
      versions: [versionFixture({
        id: "version-1",
        version_number: 1,
        status: "DRAFT",
        spec_snapshot: { identity_spec: {}, visual_spec: {}, negative_constraints: [], frozen_from: "migration" },
      })],
    }));
    renderWorkspace({ characters: [characterFixture()], assets: [assetFixture(), assetFixture({ id: "asset-2", original_name: "legacy.png", display_name: null })] });
    expect(await screen.findByText("草稿 V1")).toBeInTheDocument();
    // 包以 character_id 寻址，读取迁移工作集字段；不触发任何自动建包。
    expect(detailApi).toHaveBeenCalledWith("project-1", "character-1");
    expect(createApi).not.toHaveBeenCalled();
    const identity = screen.getByLabelText("身份锚点 年龄段外观");
    expect(identity).toHaveValue("17 岁高中生");
    // 既有 CharacterReference 素材与未绑定素材都在可绑定列表：版本关系引用同一批 Asset ID，不复制实体。
    const bindSelect = screen.getByLabelText("为正面（主视）绑定已有素材");
    expect(within(bindSelect).getByRole("option", { name: "林澈正面" })).toBeInTheDocument();
    expect(within(bindSelect).getByRole("option", { name: "legacy.png" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发布当前版本 V1/ })).toBeDisabled();
  });

  it("规格保存 409 乐观锁冲突时展示刷新提示", async () => {
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockResolvedValue(packageFixture());
    updateApi.mockRejectedValue(new ApiError("角色模型包已被更新，请刷新后重试", 409));
    renderWorkspace();
    await screen.findByText("草稿 V2");
    fireEvent.click(screen.getByRole("button", { name: /保存草稿规格/ }));
    expect(await screen.findByText("数据已变化，请刷新后重试")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新" })).toBeInTheDocument();
  });

  it("服装集支持关联、设默认；历史版本可归档与恢复", async () => {
    let current = packageFixture({ published_version_id: null });
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockImplementation(async () => current);
    outfitsApi.mockResolvedValue([outfitFixture(), outfitFixture({ id: "outfit-2", name: "黑色连帽卫衣常服" })]);
    bindOutfitApi.mockImplementation(async (_project, _character, _version, payload) => {
      current = {
        ...current,
        versions: current.versions.map((item) => item.status === "DRAFT" ? {
          ...item,
          outfits: [...item.outfits, {
            id: `rel-${payload.outfit_id}`,
            version_id: item.id,
            outfit_id: payload.outfit_id,
            is_default: payload.is_default ?? false,
            sort_order: 0,
            created_at: "2026-09-01T00:00:00Z",
          }],
        } : item),
      };
      return {
        id: `rel-${payload.outfit_id}`,
        version_id: "version-2",
        outfit_id: payload.outfit_id,
        is_default: payload.is_default ?? false,
        sort_order: 0,
        created_at: "2026-09-01T00:00:00Z",
      };
    });
    defaultOutfitApi.mockImplementation(async (_project, _character, _version, outfitId, payload) => {
      current = {
        ...current,
        versions: current.versions.map((item) => item.status === "DRAFT" ? {
          ...item,
          outfits: item.outfits.map((relation) => ({ ...relation, is_default: relation.outfit_id === outfitId && payload.is_default })),
        } : item),
      };
      return {
        id: `rel-${outfitId}`,
        version_id: "version-2",
        outfit_id: outfitId,
        is_default: true,
        sort_order: 0,
        created_at: "2026-09-01T00:00:00Z",
      };
    });
    archiveVersionApi.mockImplementation(async () => {
      current = {
        ...current,
        versions: current.versions.map((item) => item.id === "version-1" ? { ...item, status: "ARCHIVED" } : item),
      };
      return current.versions[1];
    });
    renderWorkspace();
    fireEvent.change(await screen.findByLabelText("添加服装到版本"), { target: { value: "outfit-1" } });
    await waitFor(() => {
      expect(bindOutfitApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", expect.objectContaining({
        outfit_id: "outfit-1",
        version: 7,
      }));
    });
    fireEvent.click(await screen.findByRole("button", { name: "设为默认" }));
    await waitFor(() => {
      expect(defaultOutfitApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", "outfit-1", { is_default: true, version: 7 });
    });
    expect(await screen.findByText(/默认服装 · 1 张参考图/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "归档" }));
    await waitFor(() => {
      expect(archiveVersionApi).toHaveBeenCalledWith("project-1", "character-1", "version-1");
    });
    fireEvent.click(await screen.findByRole("button", { name: "恢复" }));
    await waitFor(() => {
      expect(restoreVersionApi).toHaveBeenCalledWith("project-1", "character-1", "version-1");
    });
  });

  it("发布过的历史版本可切换为发布指针，携带 CAS 令牌", async () => {
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockResolvedValue(packageFixture({
      published_version_id: null,
      versions: [
        versionFixture({ id: "version-2", version_number: 2, status: "DRAFT", version: 7 }),
        versionFixture({ id: "version-1", version_number: 1, status: "IN_PRODUCTION" }),
      ],
    }));
    activateApi.mockResolvedValue(packageFixture({ published_version_id: "version-1" }));
    renderWorkspace();
    fireEvent.click(await screen.findByRole("button", { name: "设为发布版本" }));
    await waitFor(() => {
      // 无发布指针时 CAS 令牌显式为 null（契约 §5.3-8 必填但可为 null）。
      expect(activateApi).toHaveBeenCalledWith("project-1", "character-1", {
        version_id: "version-1",
        expected_published_version_id: null,
      });
    });
  });

  it("封面可通过已有素材或上传设置", async () => {
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockResolvedValue(packageFixture());
    coverApi.mockResolvedValue(frontRef);
    renderWorkspace();
    fireEvent.change(await screen.findByLabelText("绑定封面素材"), { target: { value: "asset-1" } });
    await waitFor(() => {
      expect(coverApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", { asset_id: "asset-1", version: 7 });
    });
    const uploaded = assetFixture({ id: "asset-8", original_name: "cover.png", display_name: null });
    uploadApi.mockResolvedValue(uploaded);
    const file = new File(["png"], "cover.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("上传封面参考图"), { target: { files: [file] } });
    await waitFor(() => {
      expect(uploadApi).toHaveBeenCalledWith("project-1", "CHARACTER_REFERENCE", file);
      expect(coverApi).toHaveBeenCalledWith("project-1", "character-1", "version-2", { asset_id: "asset-8", version: 7 });
    });
  });
});
