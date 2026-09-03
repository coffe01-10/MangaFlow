import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Character, type GenerationWorkbench, type InspectionResult, type Job, type MangaPage, type PageCandidate, type SceneAsset, type Script, type StoryboardPanel } from "@/lib/api";

import { GenerateSection } from "./generate-section";
import { useGenerationWorkspace } from "./use-generation-workspace";
import { useJobsWorkspace } from "./use-jobs-workspace";
import { useWorkspaceQueries } from "./use-workspace-queries";

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a>,
}));

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
const keepSelected = vi.spyOn(api, "keepSelectedCandidate");
const inspectCandidate = vi.spyOn(api, "inspectCandidate");
const repairCandidate = vi.spyOn(api, "repairCandidate");
const generateCandidate = vi.spyOn(api, "generateCandidate");
const startBatch = vi.spyOn(api, "startBatch");
const characterPackagesApi = vi.spyOn(api, "characterPackages");
const characterPackageApi = vi.spyOn(api, "characterPackage");
const directorGroupsApi = vi.spyOn(api, "directorCommandGroups");
const directorProposeApi = vi.spyOn(api, "directorProposeCommandGroup");

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
    is_selected: true,
    based_on_storyboard_version: 1,
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
    id: "job-inspect",
    project_id: "project-1",
    target_type: "PAGE_CANDIDATE",
    target_id: "candidate-1",
    job_type: "PAGE_INSPECT",
    status: "COMPLETED",
    progress: 100,
    attempt_count: 1,
    max_attempts: 3,
    model_alias: null,
    error_code: null,
    error_message: null,
    workflow_run_id: null,
    workflow_node_id: null,
    duration_ms: 1200,
    usage_summary: {},
    estimated_cost: null,
    result: null,
    created_at: "2026-08-29T10:00:00Z",
    archived_at: null,
    ...overrides,
  };
}

function workbenchFixture(overrides: Partial<GenerationWorkbench> = {}): GenerationWorkbench {
  const page = overrides.page ?? pageFixture();
  const candidate = overrides.selected_candidate === undefined
    ? null
    : overrides.selected_candidate;
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
    current_batch: {
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
    },
    candidates: [],
    selected_candidate: candidate,
    selected_candidate_state: candidate?.version_state ?? "NONE",
    ...overrides,
  };
}

function characterFixture(overrides: Partial<Character> = {}): Character {
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

function panelFixture(overrides: Partial<StoryboardPanel> = {}): StoryboardPanel {
  return {
    id: "panel-1",
    page_id: "page-1",
    reading_order: 1,
    bounds: {},
    shot_type: "MS",
    camera_angle: "eye",
    camera_height: "normal",
    characters: ["character-1"],
    character_presence: { "character-1": "VISIBLE" },
    props: [],
    outfits: {},
    actions: {},
    expressions: {},
    background: "",
    bubble_regions: [],
    sound_effects: [],
    bleed: false,
    borderless: false,
    locked_fields: [],
    version: 1,
    dialogues: [],
    ...overrides,
  };
}

function createClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
}

function GenerateHarness() {
  const queries = useWorkspaceQueries({
    id: "project-1",
    section: "generate",
    assetView: "characters",
    selectedChapterId: "chapter-1",
  });
  const jobsWorkspace = useJobsWorkspace({ id: "project-1", section: "generate" });
  const [selectedPageId, setSelectedPageId] = useState<string | null>("page-1");
  const workspace = useGenerationWorkspace({
    id: "project-1",
    section: "generate",
    activeChapterId: "chapter-1",
    models: queries.models,
    pages: queries.pages,
    jobs: jobsWorkspace.jobs,
    characters: queries.characters,
    outfits: queries.outfits,
    selectedPageId,
    setSelectedPageId,
    setDraft: () => undefined,
    activeDrawModel: "image.nano_banana_2",
    requireDrawModel: () => "image.nano_banana_2",
  });
  return (
    <GenerateSection
      id="project-1"
      pages={queries.pages}
      assets={queries.assets}
      characters={queries.characters}
      outfits={queries.outfits}
      script={queries.script}
      sceneAssets={queries.sceneAssets}
      modelOptions={[{
        alias: "image.nano_banana_2",
        name: "Nano Banana 2",
        id: "nano-2",
        provider: "vertex-ai",
      }]}
      catalogModelOptions={[{
        alias: "image.nano_banana_2",
        name: "Nano Banana 2",
        id: "nano-2",
        provider: "vertex-ai",
      }]}
      activeDrawModel="image.nano_banana_2"
      setDrawModel={() => undefined}
      openPreview={() => undefined}
      projectPath={(target) => `/projects/project-1/${target}`}
      setSelectedPageId={setSelectedPageId}
      workspace={workspace}
    />
  );
}

function renderGenerate() {
  const client = createClient();
  const view = render(
    <QueryClientProvider client={client}>
      <GenerateHarness />
    </QueryClientProvider>,
  );
  return { client, ...view };
}

describe("GenerateSection 关键行为", () => {
  beforeEach(() => {
    projectApi.mockReset().mockResolvedValue({
      id: "project-1",
      name: "演练",
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
    });
    modelsApi.mockReset().mockResolvedValue([{
      catalog_id: "model-1",
      connection_id: "conn-1",
      provider: "vertex-ai",
      protocol: "VERTEX_NATIVE",
      model_id: "nano-2",
      logical_alias: "image.nano_banana_2",
      display_name: "Nano Banana 2",
      model_type: "IMAGE",
      input_modalities: ["TEXT", "IMAGE"],
      output_modalities: ["IMAGE"],
      operations: ["image_generate", "image_edit"],
      resolutions: ["1K"],
      preview_resolutions: ["1K"],
      max_reference_images: 1,
      regions: [],
      confidence: "HIGH",
      enabled: true,
      display_enabled: true,
      auto_eligible: true,
      priority: 1,
    }]);
    assetsApi.mockReset().mockResolvedValue([]);
    chaptersApi.mockReset().mockResolvedValue([{
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
    }]);
    charactersApi.mockReset().mockResolvedValue([]);
    outfitsApi.mockReset().mockResolvedValue([]);
    scriptApi.mockReset().mockResolvedValue({
      chapter_id: "chapter-1",
      status: "READY",
      revision_no: 1,
      coverage: {},
      scenes: [],
    } satisfies Script);
    sceneAssetsApi.mockReset().mockResolvedValue([] satisfies SceneAsset[]);
    pagesApi.mockReset().mockResolvedValue([pageFixture()]);
    jobsApi.mockReset().mockResolvedValue([]);
    batchesApi.mockReset().mockResolvedValue([]);
    candidatesApi.mockReset().mockResolvedValue([]);
    inspectionsApi.mockReset().mockResolvedValue([]);
    keepSelected.mockReset();
    inspectCandidate.mockReset().mockResolvedValue(jobFixture());
    repairCandidate.mockReset();
    generateCandidate.mockReset();
    startBatch.mockReset();
    characterPackagesApi.mockReset().mockResolvedValue([]);
    characterPackageApi.mockReset();
    workbenchApi.mockReset().mockResolvedValue(workbenchFixture());
  });

  it("生产门禁未通过时展示阻塞文案，且不发起生成请求", async () => {
    renderGenerate();
    await waitFor(() => {
      expect(screen.getByText("当前页尚未生产通过")).toBeInTheDocument();
    });
    expect(screen.getAllByText("请先人工校对文字并暂选一张当前页候选").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "生成下一页" })).toBeDisabled();
    expect(screen.getByText("这个批次还没有候选")).toBeInTheDocument();
    expect(generateCandidate).not.toHaveBeenCalled();
  });

  it("旧候选横幅沿用并重新检查会调用 keepSelectedCandidate 并刷新 workbench", async () => {
    const stale = candidateFixture({ version_state: "STALE", is_selected: true });
    workbenchApi.mockResolvedValue(workbenchFixture({
      selected_candidate: stale,
      candidates: [stale],
      production: {
        page_id: "page-1",
        state: "STALE",
        ready: false,
        selected_candidate_id: stale.id,
        blockers: [{
          code: "STORYBOARD_VERSION_UNCONFIRMED",
          message: "分镜已经变化，请明确沿用旧候选或按当前分镜重新生成",
          section: "generate",
          candidate_id: stale.id,
        }],
      },
    }));
    candidatesApi.mockResolvedValue([stale]);
    keepSelected.mockResolvedValue(pageFixture({ selected_candidate_id: stale.id }));
    renderGenerate();
    await waitFor(() => {
      expect(screen.getByText("版本需要决定")).toBeInTheDocument();
    });
    const before = workbenchApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "沿用并重新检查" }));
    await waitFor(() => {
      expect(keepSelected).toHaveBeenCalledWith("page-1", "candidate-1", 2);
    });
    await waitFor(() => {
      expect(workbenchApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("检查任务未终态时持续拉取 workbench，完成后停止", async () => {
    vi.useFakeTimers();
    try {
      const candidate = candidateFixture({ status: "COMPLETED" });
      workbenchApi.mockResolvedValue(workbenchFixture({
        candidates: [candidate],
        selected_candidate: candidate,
      }));
      jobsApi.mockResolvedValue([jobFixture({ status: "CONSISTENCY_CHECKING", progress: 40 })]);
      const client = createClient();
      const wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const view = render(<GenerateHarness />, { wrapper });
      await vi.advanceTimersByTimeAsync(80);
      const during = workbenchApi.mock.calls.length;
      expect(during).toBeGreaterThanOrEqual(1);
      await vi.advanceTimersByTimeAsync(9000);
      expect(workbenchApi.mock.calls.length).toBeGreaterThan(during);
      jobsApi.mockResolvedValue([jobFixture({ status: "COMPLETED" })]);
      client.setQueryData(["jobs", "project-1", false], [jobFixture({ status: "COMPLETED" })]);
      await vi.advanceTimersByTimeAsync(200);
      const stoppedAt = workbenchApi.mock.calls.length;
      await vi.advanceTimersByTimeAsync(9000);
      expect(workbenchApi.mock.calls.length).toBe(stoppedAt);
      view.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("修复任务非终态时持续拉取 workbench 与候选，FAILED 后停止", async () => {
    vi.useFakeTimers();
    try {
      const repairing = candidateFixture({
        id: "candidate-2",
        ordinal: 2,
        status: "REPAIRING",
        is_selected: false,
        job_id: "job-repair",
      });
      const repairingWorkbench = workbenchFixture({
        candidates: [repairing],
        selected_candidate: candidateFixture(),
      });
      workbenchApi.mockResolvedValue(repairingWorkbench);
      candidatesApi.mockResolvedValue([repairing]);
      jobsApi.mockResolvedValue([
        jobFixture({
          id: "job-repair",
          job_type: "PAGE_REPAIR",
          target_id: "candidate-2",
          status: "REPAIRING",
          progress: 35,
        }),
      ]);
      const client = createClient();
      const wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const view = render(<GenerateHarness />, { wrapper });
      await vi.advanceTimersByTimeAsync(80);
      const workbenchDuring = workbenchApi.mock.calls.length;
      const candidatesDuring = candidatesApi.mock.calls.length;
      expect(workbenchDuring).toBeGreaterThanOrEqual(1);
      expect(candidatesDuring).toBeGreaterThanOrEqual(1);
      await vi.advanceTimersByTimeAsync(9000);
      expect(workbenchApi.mock.calls.length).toBeGreaterThan(workbenchDuring);
      expect(candidatesApi.mock.calls.length).toBeGreaterThan(candidatesDuring);

      const failed = { ...repairing, status: "FAILED" };
      const failedWorkbench = workbenchFixture({
        candidates: [failed],
        selected_candidate: candidateFixture(),
      });
      const failedJob = jobFixture({
        id: "job-repair",
        job_type: "PAGE_REPAIR",
        target_id: "candidate-2",
        status: "FAILED",
        progress: 100,
      });
      workbenchApi.mockResolvedValue(failedWorkbench);
      candidatesApi.mockResolvedValue([failed]);
      jobsApi.mockResolvedValue([failedJob]);
      client.setQueryData(["generation-workbench", "page-1"], failedWorkbench);
      client.setQueryData(["candidates", "batch-1"], [failed]);
      client.setQueryData(["jobs", "project-1", false], [failedJob]);
      await vi.advanceTimersByTimeAsync(200);
      const workbenchStopped = workbenchApi.mock.calls.length;
      const candidatesStopped = candidatesApi.mock.calls.length;
      await vi.advanceTimersByTimeAsync(9000);
      expect(workbenchApi.mock.calls.length).toBe(workbenchStopped);
      expect(candidatesApi.mock.calls.length).toBe(candidatesStopped);
      view.unmount();
    } finally {
      vi.useRealTimers();
    }
  });

  it("视觉检查失败时展示用户可见错误，成功后可发起修复", async () => {
    const candidate = candidateFixture();
    workbenchApi.mockResolvedValue(workbenchFixture({
      candidates: [candidate],
      selected_candidate: candidate,
    }));
    candidatesApi.mockResolvedValue([candidate]);
    inspectCandidate.mockRejectedValueOnce(new Error("检查服务暂时不可用"));
    renderGenerate();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "视觉检查" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "视觉检查" }));
    await waitFor(() => {
      expect(inspectCandidate).toHaveBeenCalledWith("candidate-1");
      expect(screen.getByText("检查服务暂时不可用")).toBeInTheDocument();
    });

    const inspection: InspectionResult = {
      id: "insp-character",
      candidate_id: "candidate-1",
      category: "CHARACTER",
      outcome: "FAIL",
      score: 0.1,
      details: { summary: "角色不一致" },
      regions: [],
      severity: "ERROR",
      created_at: "2026-08-29T10:00:00Z",
    };
    inspectCandidate.mockResolvedValue(jobFixture({ status: "COMPLETED" }));
    inspectionsApi.mockResolvedValue([inspection]);
    repairCandidate.mockResolvedValue({
      job_id: "job-repair",
      job_status: "WAITING",
      candidate: candidateFixture({ id: "candidate-2", ordinal: 2, is_selected: false }),
    });
    fireEvent.click(screen.getByRole("button", { name: "视觉检查" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "修复单格" })).toBeInTheDocument();
    });
    const jobsBefore = jobsApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "修复单格" }));
    await waitFor(() => {
      expect(repairCandidate).toHaveBeenCalledWith(
        "candidate-1",
        expect.objectContaining({
          inspection_result_id: "insp-character",
          repair_type: "PANEL",
        }),
      );
      expect(jobsApi.mock.calls.length).toBeGreaterThan(jobsBefore);
    });
  });

  it("慢检查请求期间检查按钮保持 pending，并发点击不会丢失 invalidation", async () => {
    const candidate = candidateFixture();
    workbenchApi.mockResolvedValue(workbenchFixture({
      candidates: [candidate],
      selected_candidate: candidate,
    }));
    candidatesApi.mockResolvedValue([candidate]);
    let release: ((job: Job) => void) | undefined;
    inspectCandidate.mockImplementation(
      () => new Promise((resolve) => {
        release = resolve;
      }),
    );
    renderGenerate();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "视觉检查" })).toBeEnabled();
    });
    const button = screen.getByRole("button", { name: "视觉检查" });
    fireEvent.click(button);
    await waitFor(() => {
      expect(button).toBeDisabled();
      expect(inspectCandidate).toHaveBeenCalledTimes(1);
    });
    fireEvent.click(button);
    expect(inspectCandidate).toHaveBeenCalledTimes(1);
    const before = workbenchApi.mock.calls.length;
    release?.(jobFixture({ status: "QUEUED" }));
    await waitFor(() => {
      expect(workbenchApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("生成 mutation 失败时展示用户可见错误", async () => {
    workbenchApi.mockResolvedValue(workbenchFixture({
      production: {
        page_id: "page-1",
        state: "READY",
        ready: true,
        selected_candidate_id: "candidate-1",
        blockers: [],
      },
    }));
    generateCandidate.mockRejectedValue(new Error("供应商返回 429"));
    renderGenerate();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "生成 1 个 1K 彩色候选" })).toBeEnabled();
    });
    fireEvent.click(screen.getByRole("button", { name: "生成 1 个 1K 彩色候选" }));
    await waitFor(() => {
      expect(generateCandidate).toHaveBeenCalled();
      expect(screen.getByText("供应商返回 429")).toBeInTheDocument();
    });
  });

  it("TEST-SCENE-06 生成区展示持久化场景绑定，归档引用不显示为已就绪", async () => {
    scriptApi.mockResolvedValue({
      chapter_id: "chapter-1",
      status: "READY",
      revision_no: 1,
      coverage: {},
      scenes: [{
        id: "scene-1",
        ordinal: 1,
        location: "学校天台",
        scene_asset_id: "scene-asset-1",
        scene_asset_variant_id: "variant-1",
        time_label: "黄昏",
        weather: "雨",
        purpose: "",
        emotional_arc: "",
        source_range: {},
        outfit_assignments: {},
        locked_fields: [],
        version: 1,
        beats: [],
      }, {
        id: "scene-2",
        ordinal: 2,
        location: "旧教学楼",
        scene_asset_id: "missing-asset",
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
    sceneAssetsApi.mockResolvedValue([{
      id: "scene-asset-1",
      project_id: "project-1",
      name: "学校天台",
      description: "",
      location_hint: "学校天台",
      structured: { interior: false, place: "校园" },
      status: "CANONICAL",
      deleted_at: "2026-09-01T00:00:00Z",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      version: 2,
      references: [],
      variants: [{
        id: "variant-1",
        scene_asset_id: "scene-asset-1",
        name: "暴雨黄昏",
        structured_overrides: { weather: "rain" },
        is_canonical: true,
        deleted_at: null,
        version: 1,
        references: [],
      }],
    }]);
    pagesApi.mockResolvedValue([pageFixture({ scene_ids: ["scene-1", "scene-2"] })]);
    workbenchApi.mockResolvedValue(workbenchFixture({
      page: pageFixture({ scene_ids: ["scene-1", "scene-2"] }),
    }));
    renderGenerate();
    await waitFor(() => {
      expect(screen.getByText("本页主场景将进入生成输入")).toBeInTheDocument();
    });
    expect(screen.getByText(/场景资产已归档，不会作为已就绪参考/)).toBeInTheDocument();
    expect(screen.getByText(/本页另外关联了 1 个场景，它们不进入本次生成输入/)).toBeInTheDocument();
    expect(screen.queryByText(/引用的场景资产不可用/)).not.toBeInTheDocument();
    expect(screen.queryByText("已就绪 · 可直接用于剧本与分镜")).not.toBeInTheDocument();
  });

  it("TEST-PKG-06 角色包列表未返回前保持骨架，不为已发布角色发送 legacy 参考载荷", async () => {
    // 入镜角色带规范参考图：一旦在未知包状态下放行，默认载荷就会携带 legacy character_asset_id。
    characterPackagesApi.mockImplementation(() => new Promise(() => undefined));
    charactersApi.mockResolvedValue([characterFixture()]);
    workbenchApi.mockResolvedValue(workbenchFixture({
      storyboard: { page: pageFixture(), panels: [panelFixture()], candidate_count: 0 },
    }));
    renderGenerate();
    expect(await screen.findByText("正在载入生成工作台…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成 1 个 1K 彩色候选" })).not.toBeInTheDocument();
    expect(generateCandidate).not.toHaveBeenCalled();
    expect(startBatch).not.toHaveBeenCalled();
  });

  it("TEST-PKG-06 角色包列表加载失败时展示可重试错误，恢复后工作台可用", async () => {
    characterPackagesApi.mockRejectedValue(new Error("角色模型包服务不可用"));
    renderGenerate();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("角色模型包状态无法载入");
    expect(alert).toHaveTextContent("角色模型包服务不可用");
    expect(screen.queryByRole("button", { name: "生成 1 个 1K 彩色候选" })).not.toBeInTheDocument();
    expect(generateCandidate).not.toHaveBeenCalled();

    characterPackagesApi.mockResolvedValue([]);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("button", { name: "生成 1 个 1K 彩色候选" })).toBeInTheDocument();
    expect(generateCandidate).not.toHaveBeenCalled();
  });

  it("TEST-PKG-06 归档包角色在参考覆盖区显示版本选择器且保留 legacy 参考", async () => {
    characterPackagesApi.mockResolvedValue([{
      id: "pkg-1",
      character_id: "character-1",
      project_id: "project-1",
      status: "ARCHIVED",
      published_version_id: null,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      version: 1,
      character: { id: "character-1", primary_name: "林澈", aliases: [], alias_conflict: false },
      published_version_number: null,
      published_completeness: null,
    }]);
    characterPackageApi.mockResolvedValue({
      id: "pkg-1",
      character_id: "character-1",
      project_id: "project-1",
      identity_spec: {},
      visual_spec: {},
      negative_constraints: [],
      published_version_id: null,
      status: "ARCHIVED",
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      version: 1,
      versions: [{
        id: "version-1",
        package_id: "pkg-1",
        version_number: 1,
        status: "ARCHIVED",
        spec_snapshot: {},
        derived_from_version_id: null,
        published_at: "2026-09-01T01:00:00Z",
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
        version: 1,
        references: [],
        outfits: [],
        completeness: { score: 40, missing: [] },
      }],
      completeness: null,
    });
    charactersApi.mockResolvedValue([characterFixture()]);
    workbenchApi.mockResolvedValue(workbenchFixture({
      storyboard: { page: pageFixture(), panels: [panelFixture()], candidate_count: 0 },
    }));
    renderGenerate();
    fireEvent.click(await screen.findByRole("button", { name: "本页更换" }));
    expect(await screen.findByLabelText("林澈的角色模型包版本")).toBeInTheDocument();
    // 归档包不参与默认继承：未显式选择版本时 legacy 人物参考选择仍然可用。
    expect(screen.getByLabelText("人物参考图")).toBeInTheDocument();
  });

  describe("导演模式（V02-41B）", () => {
    beforeEach(() => {
      directorGroupsApi.mockReset().mockResolvedValue([]);
      directorProposeApi.mockReset();
    });

    it("D18 未开导演台时行为与今日相同，不出现导演命令栏", async () => {
      renderGenerate();
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "生成 1 个 1K 彩色候选" })).toBeInTheDocument();
      });
      const group = screen.getByRole("group", { name: "生成台模式" });
      expect(within(group).getByRole("button", { name: "抽卡" })).toHaveAttribute("aria-pressed", "true");
      expect(within(group).getByRole("button", { name: "导演" })).toHaveAttribute("aria-pressed", "false");
      expect(screen.queryByLabelText("导演指令")).not.toBeInTheDocument();
      expect(directorGroupsApi).not.toHaveBeenCalled();
      expect(directorProposeApi).not.toHaveBeenCalled();
    });

    it("D1 打开导演模式出现命令栏、历史与作用域芯片，抽卡按钮降为次要且可切回", async () => {
      renderGenerate();
      await waitFor(() => {
        expect(screen.getByRole("button", { name: "生成 1 个 1K 彩色候选" })).toBeInTheDocument();
      });
      fireEvent.click(screen.getByRole("button", { name: "导演" }));
      expect(await screen.findByLabelText("导演指令")).toBeInTheDocument();
      expect(screen.getAllByText("规则解析，非模型").length).toBeGreaterThan(0);
      expect(screen.getByText("HISTORY / 命令历史")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "整页" })).toBeInTheDocument();
      const demoted = screen.getByRole("button", { name: /抽卡生成 1 个候选/ });
      expect(demoted).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "生成 1 个 1K 彩色候选" })).not.toBeInTheDocument();
      expect(directorGroupsApi).toHaveBeenCalledWith("project-1", "page-1");

      fireEvent.click(within(screen.getByRole("group", { name: "生成台模式" })).getByRole("button", { name: "抽卡" }));
      expect(screen.queryByLabelText("导演指令")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "生成 1 个 1K 彩色候选" })).toBeInTheDocument();
    });

    it("D11 页面生成进行中时导演整页口令被拦下，不发 propose", async () => {
      const generating = candidateFixture({ status: "GENERATING", is_selected: false, job_id: "job-gen" });
      workbenchApi.mockResolvedValue(workbenchFixture({
        candidates: [generating],
        selected_candidate: null,
      }));
      candidatesApi.mockResolvedValue([generating]);
      const { client } = renderGenerate();
      client.setQueryData(["candidates", "batch-1"], [generating]);
      fireEvent.click(await screen.findByRole("button", { name: "导演" }));
      const input = await screen.findByLabelText("导演指令");
      fireEvent.change(input, { target: { value: "改成 6 格" } });
      fireEvent.click(screen.getByRole("button", { name: "预览" }));
      await waitFor(() => {
        expect(document.querySelector(".director-shell")?.textContent).toContain("生成任务进行中");
      });
      expect(directorProposeApi).not.toHaveBeenCalled();
    });
  });
});
