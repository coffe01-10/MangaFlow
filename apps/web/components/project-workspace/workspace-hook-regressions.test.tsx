/**
 * Route-sweep frontend regressions (issues #210-4, #246-1, #246-2):
 *
 * - 上传返回“其他用途”素材时必须呈现错误状态，而不是静默按当前用途消费；
 * - 修复按钮在没有已选候选（分辨率未知）时拒绝提交，不再静默回退 "1K"；
 * - 修复/升清成功后清理 reviewCandidateId（服务端已关闭当前批次）；
 * - 剧本轮询由活跃 SOURCE_PARSE 任务驱动（后端从不输出 PROCESSING）。
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type Character,
  type GenerationWorkbench,
  type InspectionResult,
  type Job,
  type MangaPage,
  type PageCandidate,
  type SceneAsset,
  type Script,
} from "@/lib/api";

import { useAssetsWorkspace } from "./use-assets-workspace";
import { useGenerationWorkspace, type GenerationWorkspace } from "./use-generation-workspace";
import { chapterParseJob, scriptPollInterval, useWorkspaceQueries } from "./use-workspace-queries";

const projectApi = vi.spyOn(api, "project");
const modelsApi = vi.spyOn(api, "models");
const assetsApi = vi.spyOn(api, "assets");
const chaptersApi = vi.spyOn(api, "chapters");
const charactersApi = vi.spyOn(api, "characters");
const outfitsApi = vi.spyOn(api, "outfits");
const scriptApi = vi.spyOn(api, "script");
const sceneAssetsApi = vi.spyOn(api, "sceneAssets");
const pagesApi = vi.spyOn(api, "pages");
const jobsApi = vi.spyOn(api, "jobs");
const workbenchApi = vi.spyOn(api, "generationWorkbench");
const batchesApi = vi.spyOn(api, "batches");
const candidatesApi = vi.spyOn(api, "candidates");
const inspectionsApi = vi.spyOn(api, "inspections");
const repairApi = vi.spyOn(api, "repairCandidate");
const upscaleApi = vi.spyOn(api, "upscaleCandidate");
const uploadApi = vi.spyOn(api, "uploadAsset");
const bindReferenceApi = vi.spyOn(api, "bindCharacterReference");
const characterPackagesApi = vi.spyOn(api, "characterPackagesAll");

function pageFixture(overrides: Partial<MangaPage> = {}): MangaPage {
  return {
    id: "page-1",
    chapter_id: "chapter-1",
    page_number: 1,
    revision_no: 1,
    page_function: "dialogue",
    panel_count: 4,
    reading_direction: "rtl",
    resolution: "1K",
    status: "PLANNED",
    estimated_text_chars: 40,
    estimated_bubbles: 2,
    source_coverage: { complete: true, ranges: [{ text: "巷口灯还亮着" }] },
    selected_candidate_id: null,
    storyboard_version: 2,
    selected_candidate_ack_version: 1,
    continuity_status: "PASSED",
    scene_ids: ["scene-1"],
    beat_ids: ["beat-1"],
    version: 1,
    ...overrides,
  };
}

function candidateFixture(overrides: Partial<PageCandidate> = {}): PageCandidate {
  return {
    id: "candidate-1",
    batch_id: "batch-1",
    page_id: "page-1",
    ordinal: 1,
    model_alias: "image.nano_banana_2",
    resolution: "1K",
    status: "COMPLETED",
    asset_id: "asset-1",
    job_id: "job-gen",
    is_favorite: false,
    is_selected: false,
    based_on_storyboard_version: 2,
    version_state: "CURRENT",
    staleness_reasons: [],
    created_at: "2026-08-29T10:00:00Z",
    variant: null,
    prompt_snapshot: {},
    content_url: "/api/v1/assets/asset-1/content",
    thumbnail_url: null,
    ...overrides,
  };
}

function jobFixture(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-parse",
    project_id: "project-1",
    target_type: "CHAPTER",
    target_id: "chapter-1",
    job_type: "SOURCE_PARSE",
    status: "QUEUED",
    progress: 0,
    attempt_count: 1,
    max_attempts: 3,
    model_alias: null,
    error_code: null,
    error_message: null,
    workflow_run_id: null,
    workflow_node_id: null,
    duration_ms: null,
    usage_summary: {},
    estimated_cost: null,
    result: null,
    created_at: "2026-08-29T10:00:00Z",
    archived_at: null,
    ...overrides,
  };
}

function inspectionFixture(): InspectionResult {
  return {
    id: "inspection-1",
    candidate_id: "candidate-1",
    storyboard_version: 2,
    category: "CHARACTER",
    outcome: "MISMATCH",
    score: 0.4,
    details: {},
    regions: [],
    severity: "ERROR",
    created_at: "2026-08-29T10:00:00Z",
  };
}

function workbenchFixture(): GenerationWorkbench {
  const page = pageFixture();
  const batch = {
    id: "batch-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    page_id: page.id,
    target_type: "PAGE",
    target_id: page.id,
    ordinal: 1,
    generation_kind: "PAGE",
    status: "OPEN",
    created_at: "2026-08-29T10:00:00Z",
    closed_at: null,
  };
  return {
    page,
    storyboard: { page, panels: [], candidate_count: 0 },
    readiness: {
      page_id: page.id,
      ready: true,
      source_complete: true,
      script_complete: true,
      visible_characters: [],
      mentioned_characters: [],
      props: [],
      style: {
        style_id: null,
        name: null,
        color_mode: null,
        status: null,
        palette_confirmed: true,
        test_image_approved: true,
      },
      provider: {
        configured: true,
        health_state: "HEALTHY",
        text_model_access: "OK",
        image_model_access: "OK",
        image_model_alias: "image.nano_banana_2",
        usable_image_model_count: 1,
        auto_image_model_count: 1,
      },
      worker: {
        queue_mode: "LOCAL",
        executor: "LOCAL",
        can_execute: true,
        redis_state: "SKIPPED",
      },
      blockers: [],
      estimated_image_calls: 1,
      estimated_cost_note: "估算",
    },
    production: {
      page_id: page.id,
      state: "AWAITING_SELECTION",
      ready: false,
      selected_candidate_id: null,
      blockers: [{
        code: "CANDIDATE_NOT_SELECTED",
        message: "请先人工校对文字并暂选一张当前页候选",
        section: "generate",
        candidate_id: null,
      }],
    },
    current_batch: batch,
    candidates: [],
    selected_candidate: null,
    selected_candidate_state: "NONE",
  };
}

function characterFixture(): Character {
  return {
    id: "character-1",
    project_id: "project-1",
    primary_name: "林澈",
    aliases: [],
    alias_conflict: false,
    canonical_description: "",
    locked_features: [],
    forbidden_changes: [],
    status: "ACTIVE",
    version: 1,
    references: [],
  };
}

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  projectApi.mockResolvedValue({
    id: "project-1",
    name: "回归",
    language: "zh-CN",
    reading_direction: "rtl",
    page_ratio: "b5_portrait",
    default_resolution: "2K",
    draft_resolution: "1K",
    workflow_mode: "SEMI_AUTO",
    default_concurrency: 4,
    default_style_id: null,
    consistency_check_enabled: true,
    text_model_alias: "text.fast",
    last_image_model_alias: "image.nano_banana_2",
    default_text_model_id: null,
    last_image_model_id: null,
    created_at: "2026-08-29T10:00:00Z",
    updated_at: "2026-08-29T10:00:00Z",
    version: 1,
  } as never);
  modelsApi.mockResolvedValue([{
    catalog_id: "model-1",
    connection_id: "conn-1",
    // Provider-neutral identifiers: the neutrality gate forbids concrete
    // provider/protocol literals in web code outside the allowlist.
    provider: "provider-a",
    protocol: "OPENAI_COMPATIBLE",
    model_id: "nano-2",
    logical_alias: "image.nano_banana_2",
    display_name: "Nano Banana 2",
    model_type: "IMAGE",
    input_modalities: ["TEXT", "IMAGE"],
    output_modalities: ["IMAGE"],
    operations: ["image_generate", "image_edit"],
    resolutions: ["1K", "2K", "4K"],
    preview_resolutions: ["1K"],
    max_reference_images: 1,
    regions: [],
    confidence: "HIGH",
    enabled: true,
    display_enabled: true,
    auto_eligible: true,
    priority: 1,
  }] as never);
  assetsApi.mockResolvedValue([]);
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
    created_at: "2026-08-29T10:00:00Z",
    updated_at: "2026-08-29T10:00:00Z",
    version: 1,
  }] as never);
  charactersApi.mockResolvedValue([characterFixture()]);
  outfitsApi.mockResolvedValue([]);
  scriptApi.mockResolvedValue({
    chapter_id: "chapter-1",
    status: "READY",
    revision_no: 1,
    coverage: {},
    scenes: [],
  } satisfies Script);
  sceneAssetsApi.mockResolvedValue([] satisfies SceneAsset[]);
  pagesApi.mockResolvedValue([pageFixture()]);
  jobsApi.mockResolvedValue([]);
  workbenchApi.mockResolvedValue(workbenchFixture());
  batchesApi.mockResolvedValue([]);
  candidatesApi.mockResolvedValue([]);
  inspectionsApi.mockResolvedValue([]);
  characterPackagesApi.mockResolvedValue([]);
});

function Probe({ collect }: { collect: (value: unknown) => void }) {
  const queries = useWorkspaceQueries({
    id: "project-1",
    section: "generate",
    assetView: "characters",
    selectedChapterId: "chapter-1",
  });
  const workspace = useGenerationWorkspace({
    id: "project-1",
    section: "generate",
    activeChapterId: "chapter-1",
    models: queries.models,
    pages: queries.pages,
    jobs: { data: [], isLoading: false, isError: false } as never,
    characters: queries.characters,
    outfits: queries.outfits,
    selectedPageId: "page-1",
    setSelectedPageId: () => undefined,
    setDraft: () => undefined,
    activeDrawModel: "image.nano_banana_2",
    requireDrawModel: () => "image.nano_banana_2",
  });
  useEffect(() => collect(workspace));
  return null;
}

function renderGenerationProbe() {
  let latest: GenerationWorkspace | null = null;
  const collect = (value: unknown) => {
    latest = value as GenerationWorkspace;
  };
  const client = createClient();
  const element = (
    <QueryClientProvider client={client}>
      <Probe collect={collect} />
    </QueryClientProvider>
  );
  const view = render(element);
  return {
    view,
    workspace: () => latest!,
    rerender: () => view.rerender(element),
  };
}

describe("生成工作台修复/升清回归", () => {
  it("没有已选检查候选时修复拒绝提交，而不是静默回退 1K", async () => {
    batchesApi.mockResolvedValue([workbenchFixture().current_batch!]);
    candidatesApi.mockResolvedValue([candidateFixture({ resolution: "2K" })]);
    const { workspace } = renderGenerationProbe();
    // reviewCandidateId 未设置：旧代码会以 resolution: "1K" 直接提交计费修复。
    workspace().repairCandidate.mutate(inspectionFixture());

    await vi.waitFor(() => {
      expect(workspace().repairCandidate.isError).toBe(true);
    });
    expect(repairApi).not.toHaveBeenCalled();
    expect(workspace().repairCandidate.error?.message).toContain("请先选择要修复的候选");
  });

  it("跨批次检查的候选从工作台选中候选回退解析分辨率，而不是误报未知", async () => {
    // 沿用并重新检查会把上一批次的候选放进检查面板；当前查看批次的
    // 候选列表里没有它（reviewCandidate 为 null），旧代码因此以“候选
    // 分辨率未知”误报且刷新也无法自愈。回退源是工作台的
    // selected_candidate（同一 id 时），它独立于批次列表。
    batchesApi.mockResolvedValue([workbenchFixture().current_batch!]);
    candidatesApi.mockResolvedValue([candidateFixture({ id: "candidate-other" })]);
    workbenchApi.mockResolvedValue({
      ...workbenchFixture(),
      selected_candidate: candidateFixture({ resolution: "2K" }),
    });
    repairApi.mockResolvedValue({
      job_id: "job-repair",
      job_status: "QUEUED",
      candidate: candidateFixture(),
    });
    const { workspace } = renderGenerationProbe();
    await vi.waitFor(() => {
      expect(workspace().selectedWorkbenchCandidate?.id).toBe("candidate-1");
    });
    workspace().setReviewCandidateId("candidate-1");
    // 等待重渲染落地（mutate 的闭包取自最近一次渲染），再提交修复。
    await vi.waitFor(() => {
      expect(workspace().reviewCandidateId).toBe("candidate-1");
    });
    workspace().repairCandidate.mutate(inspectionFixture());
    await vi.waitFor(() => {
      expect(workspace().repairCandidate.isSuccess).toBe(true);
    });
    expect(repairApi).toHaveBeenCalledWith("candidate-1", expect.objectContaining({ resolution: "2K" }));
  });

  it("共享场景资产查询包含已归档资产，激活绑定方的归档分支", async () => {
    // 归档不清除绑定；排除软删除行的共享列表让“当前绑定已归档”分支
    // 成为死代码，且绑定下拉框看起来像未绑定。
    renderGenerationProbe();
    await vi.waitFor(() => {
      expect(sceneAssetsApi).toHaveBeenCalledWith(
        "project-1",
        expect.objectContaining({ include_deleted: true }),
      );
    });
  });

  it("修复与升清成功后清理 reviewCandidateId（批次已被服务端关闭）", async () => {
    batchesApi.mockResolvedValue([workbenchFixture().current_batch!]);
    candidatesApi.mockResolvedValue([candidateFixture({ resolution: "2K" })]);
    repairApi.mockResolvedValue({
      job_id: "job-repair",
      job_status: "QUEUED",
      candidate: candidateFixture({ id: "candidate-repair" }),
    });
    upscaleApi.mockResolvedValue({
      job_id: "job-upscale",
      job_status: "QUEUED",
      candidate: candidateFixture({ id: "candidate-upscale", resolution: "2K" }),
    });
    const { workspace } = renderGenerationProbe();

    // 候选列表先落地，再打开检查面板（设置 reviewCandidateId）。
    await vi.waitFor(() => {
      expect(workspace().candidates.data?.[0]?.id).toBe("candidate-1");
    });
    workspace().setReviewCandidateId("candidate-1");
    await vi.waitFor(() => {
      expect(workspace().reviewCandidate?.id).toBe("candidate-1");
    });

    workspace().repairCandidate.mutate(inspectionFixture());
    await vi.waitFor(() => {
      expect(workspace().repairCandidate.isSuccess).toBe(true);
    });
    expect(workspace().reviewCandidateId).toBe(null);

    workspace().setReviewCandidateId("candidate-1");
    await vi.waitFor(() => {
      expect(workspace().reviewCandidateId).toBe("candidate-1");
    });
    workspace().upscaleCandidate.mutate({ candidateId: "candidate-1", resolution: "2K" });
    await vi.waitFor(() => {
      expect(workspace().upscaleCandidate.isSuccess).toBe(true);
    });
    expect(workspace().reviewCandidateId).toBe(null);
  });
});

function AssetsProbe({ collect }: { collect: (value: unknown) => void }) {
  const queries = useWorkspaceQueries({
    id: "project-1",
    section: "assets",
    assetView: "references",
    selectedChapterId: "chapter-1",
  });
  const workspace = useAssetsWorkspace({
    id: "project-1",
    section: "assets",
    assetView: "references",
    router: { push: () => undefined, replace: () => undefined } as never,
    projectPath: (target: string) => `/projects/project-1/${target}`,
    activeChapterId: "chapter-1",
    assets: queries.assets,
    characters: queries.characters,
    outfits: queries.outfits,
    requireDrawModel: () => "image.nano_banana_2",
    initialCharacterId: null,
  });
  useEffect(() => collect(workspace));
  return null;
}

function renderAssetsProbe() {
  let latest: ReturnType<typeof useAssetsWorkspace> | null = null;
  const collect = (value: unknown) => {
    latest = value as ReturnType<typeof useAssetsWorkspace>;
  };
  const view = render(
    <QueryClientProvider client={createClient()}>
      <AssetsProbe collect={collect} />
    </QueryClientProvider>,
  );
  return {
    view,
    workspace: () => latest!,
  };
}

describe("资产上传用途守卫", () => {
  it("上传返回其他用途素材时呈现错误，且不绑定、不进入选中集合", async () => {
    const { workspace } = renderAssetsProbe();
    uploadApi.mockResolvedValue({
      id: "asset-wrong-kind",
      project_id: "project-1",
      kind: "OUTFIT_REFERENCE",
      original_name: "参考图.png",
      display_name: null,
      mime_type: "image/png",
      byte_size: 100,
      width: 16,
      height: 12,
      status: "UPLOADED",
      created_at: "2026-08-29T10:00:00Z",
      content_url: "/api/v1/assets/asset-wrong-kind/content",
      thumbnail_url: "/api/v1/assets/asset-wrong-kind/thumbnail/640",
    });

    workspace().upload.mutate(new File([new Uint8Array([1])], "参考图.png", { type: "image/png" }));

    await vi.waitFor(() => {
      expect(workspace().upload.isError).toBe(true);
    });
    expect(workspace().uploadError).toContain("其他参考用途");
    expect(bindReferenceApi).not.toHaveBeenCalled();
    expect(workspace().selectedOutfitAssets).toEqual([]);
    expect(workspace().selectedStyleAssets).toEqual([]);
  });
});

describe("剧本轮询词汇表（SOURCE_PARSE 驱动）", () => {
  it("chapterParseJob 只匹配本章的 SOURCE_PARSE 任务", () => {
    const parse = jobFixture();
    expect(chapterParseJob([parse], "chapter-1")?.id).toBe("job-parse");
    expect(chapterParseJob([parse], "chapter-2")).toBeNull();
    expect(
      chapterParseJob([jobFixture({ job_type: "PAGE_GENERATE", target_type: "PAGE_CANDIDATE" })], "chapter-1"),
    ).toBeNull();
  });

  it("scriptPollInterval 跟随活跃解析任务，终态或无任务即停", () => {
    expect(scriptPollInterval([jobFixture({ status: "GENERATING" })], "chapter-1")).toBe(4000);
    expect(scriptPollInterval([jobFixture({ status: "COMPLETED" })], "chapter-1")).toBe(false);
    expect(scriptPollInterval([jobFixture({ status: "FAILED" })], "chapter-1")).toBe(false);
    expect(scriptPollInterval([], "chapter-1")).toBe(false);
    // READY 状态本身不再驱动轮询（后端从不输出 PROCESSING）。
    expect(scriptPollInterval(undefined, "chapter-1")).toBe(false);
  });
});
