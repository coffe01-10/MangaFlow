import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type DirectorCommandGroup,
  type GenerationBatch,
  type Job,
  type MangaPage,
  type ModelCapability,
  type PageCandidate,
} from "@/lib/api";

import { LocalEditWorkspace } from "./local-edit-workspace";

const proposeApi = vi.spyOn(api, "directorProposeCommandGroup");
const acceptApi = vi.spyOn(api, "directorAcceptCommand");
const discardApi = vi.spyOn(api, "directorDiscardCommandGroup");
const cancelJobApi = vi.spyOn(api, "cancelJob");
const batchesApi = vi.spyOn(api, "batches");
const candidatesApi = vi.spyOn(api, "candidates");
const generateCandidateApi = vi.spyOn(api, "generateCandidate");

function pageFixture(overrides: Partial<MangaPage> = {}): MangaPage {
  return {
    id: "page-1",
    chapter_id: "chapter-1",
    page_number: 12,
    revision_no: 1,
    page_function: "dialogue",
    panel_count: 4,
    reading_direction: "rtl",
    resolution: "1K",
    status: "PLANNED",
    estimated_text_chars: 40,
    estimated_bubbles: 4,
    source_coverage: { complete: true, layout_mode: "dynamic", ranges: [{ text: "巷口灯还亮着" }] },
    selected_candidate_id: "candidate-1",
    storyboard_version: 2,
    selected_candidate_ack_version: 1,
    continuity_status: "PASSED",
    scene_ids: [],
    beat_ids: [],
    version: 2,
    ...overrides,
  };
}

function candidateFixture(overrides: Partial<PageCandidate> = {}): PageCandidate {
  return {
    id: "candidate-1",
    batch_id: "batch-1",
    page_id: "page-1",
    ordinal: 3,
    model_alias: "gemini_image",
    resolution: "1K",
    status: "COMPLETED",
    asset_id: "asset-1",
    job_id: null,
    is_favorite: false,
    is_selected: true,
    based_on_storyboard_version: 2,
    version_state: "CURRENT",
    staleness_reasons: [],
    created_at: "2026-09-03T00:00:00Z",
    variant: null,
    prompt_snapshot: {},
    content_url: "/api/v1/assets/asset-1/content",
    thumbnail_url: "/api/v1/assets/asset-1/thumbnail/640",
    ...overrides,
  };
}

function capableModelFixture(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    catalog_id: "catalog-1",
    connection_id: "connection-1",
    provider: "Gemini 3.7",
    protocol: "gemini",
    model_id: "gemini-image",
    logical_alias: "gemini_image",
    display_name: "Gemini Image",
    model_type: "IMAGE",
    input_modalities: ["TEXT", "IMAGE"],
    output_modalities: ["IMAGE"],
    operations: ["image_gen", "image_edit"],
    resolutions: ["1K", "2K"],
    preview_resolutions: [],
    max_reference_images: 3,
    regions: ["global"],
    accepts_explicit_mask: true,
    confidence: "VERIFIED",
    enabled: true,
    display_enabled: true,
    auto_eligible: true,
    priority: 50,
    ...overrides,
  };
}

function batchFixture(overrides: Partial<GenerationBatch> = {}): GenerationBatch {
  return {
    id: "batch-region-1",
    project_id: "project-1",
    chapter_id: "chapter-1",
    page_id: "page-1",
    target_type: null,
    target_id: null,
    ordinal: 7,
    generation_kind: "REGION_REGENERATED",
    status: "OPEN",
    created_at: "2026-09-03T00:00:00Z",
    closed_at: null,
    ...overrides,
  };
}

function previewGroupFixture(commandId: string): DirectorCommandGroup {
  return {
    id: "row-loc-1",
    project_id: "project-1",
    command_group_id: "group-loc-1",
    page_id: "page-1",
    status: "PREVIEWED",
    idempotent_replay: false,
    commands: [{
      command_id: commandId,
      command_group_id: "group-loc-1",
      operation: "regenerate_region",
      status: "PREVIEWED",
      target: { project_id: "project-1", page_id: "page-1" },
      expected_version: { scope: "page", value: 2 },
      payload: {},
      source: { user_prompt: "把选区内的雨改成晴天", reference_asset_ids: [], model: null, raw_output_id: "local_edit_v1" },
      diff: { derived_candidate: { before: null, after: { parent_candidate_id: "candidate-1", mask_regions: 1 } } },
      error: null,
      retry_of_command_id: null,
      inverse_of_command_id: null,
      storyboard_version_after: null,
      version: 1,
    }],
    version: 1,
  };
}

type EditorProps = Parameters<typeof LocalEditWorkspace>[0];

function renderEditor(overrides: Partial<EditorProps> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: EditorProps = {
    id: "project-1",
    page: pageFixture(),
    candidate: candidateFixture(),
    adoptedCandidate: candidateFixture(),
    models: [capableModelFixture()],
    activeDrawModel: "gemini_image",
    onClose: vi.fn(),
    ...overrides,
  };
  const view = render(
    <QueryClientProvider client={client}>
      <LocalEditWorkspace {...props} />
    </QueryClientProvider>,
  );
  const maskStats = () => view.container.querySelector(".local-edit-mask-stats")?.textContent ?? "";
  const regionCount = () => view.container.querySelectorAll(".local-edit-mask-region").length;
  return { client, maskStats, regionCount, ...view };
}

/** jsdom stage fallback: 640×640 display for a 1024×1024 image, top-left at (0,0). */
function imagePoint(clientX: number, clientY: number): [number, number] {
  return [
    Math.round((0.5 + (clientX - 320) / 640) * 1024),
    Math.round((0.5 + (clientY - 320) / 640) * 1024),
  ];
}

function drawRect(source: HTMLElement, from: [number, number], to: [number, number]) {
  fireEvent.pointerDown(source, { button: 0, pointerId: 1, clientX: from[0], clientY: from[1], shiftKey: false });
  fireEvent.pointerMove(window, { pointerId: 1, clientX: to[0], clientY: to[1], shiftKey: false });
  fireEvent.pointerUp(window, { pointerId: 1, clientX: to[0], clientY: to[1], shiftKey: false });
}

async function fillAndPropose() {
  const stage = screen.getByLabelText("局部选区画布");
  drawRect(stage, [160, 160], [480, 480]);
  fireEvent.change(screen.getByLabelText("局部重绘指令"), { target: { value: "把选区内的雨改成晴天" } });
  const generate = screen.getByRole("button", { name: "预览局部命令" });
  expect(generate).toBeEnabled();
  fireEvent.click(generate);
  await waitFor(() => {
    expect(proposeApi).toHaveBeenCalledTimes(1);
  });
  return proposeApi.mock.calls[0][1].commands[0];
}

describe("LocalEditWorkspace 局部选区编辑器（V02-43B）", () => {
  beforeEach(() => {
    proposeApi.mockReset();
    acceptApi.mockReset();
    discardApi.mockReset().mockResolvedValue(previewGroupFixture("cmd-loc-1"));
    cancelJobApi.mockReset().mockResolvedValue(candidateFixture({ job_id: "job-loc-1" }) as unknown as Job);
    batchesApi.mockReset().mockResolvedValue([]);
    candidatesApi.mockReset().mockResolvedValue([]);
    generateCandidateApi.mockReset();
  });

  it("L1 进入编辑器：源徽章锁定进入时那张候选并显示已暂选身份", () => {
    renderEditor();
    expect(screen.getByText("源 · 候选 3")).toBeInTheDocument();
    expect(screen.getByText("已暂选")).toBeInTheDocument();
    expect(screen.getByText(/第 12 页/)).toBeInTheDocument();
    expect(screen.getByAltText("源 · 候选 3")).toBeInTheDocument();
  });

  it("L2 空 mask 禁止生成，按钮禁用并解释", () => {
    renderEditor();
    expect(screen.getByRole("button", { name: "预览局部命令" })).toBeDisabled();
    expect(screen.getByText(/空选区不能生成/)).toBeInTheDocument();
    expect(proposeApi).not.toHaveBeenCalled();
  });

  it("L3 矩形+画笔+擦除+撤销；清空需要确认；区域数有上限提示", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { maskStats, regionCount } = renderEditor();
    const stage = screen.getByLabelText("局部选区画布");

    drawRect(stage, [160, 160], [480, 480]);
    expect(maskStats()).toBe("已选 25% 面积 · 1/8 块");
    expect(regionCount()).toBe(1);

    // 画笔（B）拖一条经过选区的线，成为第二块
    fireEvent.keyDown(window, { key: "b" });
    expect(screen.getByRole("button", { name: "画笔" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.pointerDown(stage, { button: 0, pointerId: 2, clientX: 200, clientY: 500, shiftKey: false });
    fireEvent.pointerMove(window, { pointerId: 2, clientX: 300, clientY: 500, shiftKey: false });
    fireEvent.pointerMove(window, { pointerId: 2, clientX: 340, clientY: 520, shiftKey: false });
    fireEvent.pointerUp(window, { pointerId: 2, clientX: 340, clientY: 520, shiftKey: false });
    expect(regionCount()).toBe(2);
    expect(maskStats()).not.toBe("已选 25% 面积 · 1/8 块");

    // 撤销回到 1 块，重做回到 2 块
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }));
    expect(regionCount()).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: /重做/ }));
    expect(regionCount()).toBe(2);

    // 擦除（E）：单击选区内一点，移除覆盖到的选区块
    fireEvent.keyDown(window, { key: "e" });
    fireEvent.pointerDown(stage, { button: 0, pointerId: 3, clientX: 320, clientY: 320, shiftKey: false });
    fireEvent.pointerUp(window, { pointerId: 3, clientX: 320, clientY: 320, shiftKey: false });
    expect(regionCount()).toBeLessThan(2);

    // 清空需确认：确认框取消则保留
    expect(regionCount()).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(regionCount()).toBeGreaterThan(0);
    confirmSpy.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: /清空/ }));
    expect(maskStats()).toBe("已选 0.0% 面积 · 0/8 块");
    expect(screen.getByRole("button", { name: "预览局部命令" })).toBeDisabled();

    // 撤销可恢复清空前的选区
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }));
    expect(regionCount()).toBeGreaterThan(0);
    expect(imagePoint(160, 160)).toEqual([256, 256]);
    confirmSpy.mockRestore();
  });

  it("L4 缩放改变映射且复位后无漂移：mask 不随视图变化", async () => {
    const { maskStats } = renderEditor();
    const stage = screen.getByLabelText("局部选区画布");
    drawRect(stage, [160, 160], [480, 480]);
    expect(maskStats()).toBe("已选 25% 面积 · 1/8 块");

    fireEvent.click(screen.getByRole("button", { name: "放大画布" }));
    drawRect(stage, [160, 160], [480, 480]);
    // zoom 1.25 时同一屏幕拖拽映射到更小的图像区域（累计 25% + 16%）
    expect(maskStats()).toBe("已选 41% 面积 · 2/8 块");

    fireEvent.click(screen.getByRole("button", { name: "复位画布缩放" }));
    drawRect(stage, [160, 160], [480, 480]);
    expect(maskStats()).toBe("已选 66% 面积 · 3/8 块");
  });

  it("L5 并排比较：源与新槽位同时可见，徽章与空态不同", () => {
    renderEditor();
    expect(screen.getByRole("region", { name: "源与新候选比较" })).toBeInTheDocument();
    expect(screen.getByText("源 · 候选 3")).toBeInTheDocument();
    expect(screen.getByText("尚无新局部候选")).toBeInTheDocument();
    expect(screen.getByText("生成后在此对照")).toBeInTheDocument();
    expect(screen.getByAltText("比较源图 · 候选 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "并排" })).toHaveAttribute("aria-pressed", "true");
    // 仅新视图隐藏源槽位
    fireEvent.click(screen.getByRole("button", { name: "仅新" }));
    expect(screen.queryByAltText("比较源图 · 候选 3")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "仅源" }));
    expect(screen.getByAltText("比较源图 · 候选 3")).toBeInTheDocument();
  });

  it("L6 源≠已暂选：禁用生成并解释父候选规则，可切换查看采用图", () => {
    const adopted = candidateFixture({ id: "candidate-9", ordinal: 1 });
    renderEditor({ adoptedCandidate: adopted });
    expect(screen.getByText("非采用候选")).toBeInTheDocument();
    expect(screen.getByText(/正在编辑 候选 3/)).toBeInTheDocument();
    // 指令与选区都齐备后，剩余的门禁必须是「父候选是采用候选」
    const stage = screen.getByLabelText("局部选区画布");
    drawRect(stage, [160, 160], [480, 480]);
    fireEvent.change(screen.getByLabelText("局部重绘指令"), { target: { value: "把雨改成晴天" } });
    expect(screen.getByRole("button", { name: "预览局部命令" })).toBeDisabled();
    expect(screen.getByText(/局部派生的父候选是当前采用候选/)).toBeInTheDocument();
    expect(screen.getByText(/并非采用候选 候选 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /查看采用图/ }));
    expect(screen.getByAltText("比较采用图 · 候选 1")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看源图" }));
    expect(screen.getByAltText("比较源图 · 候选 3")).toBeInTheDocument();
  });

  it("画布使用原图像素 URL 而不是 640 缩略图", () => {
    renderEditor();
    const canvasImage = screen.getByAltText("源 · 候选 3");
    expect(canvasImage.getAttribute("src") ?? "").toContain("/api/v1/assets/asset-1/content");
    expect(canvasImage.getAttribute("src") ?? "").not.toContain("/thumbnail/640");
  });

  it("L10 连点预览只发一次 propose；payload 是 regenerate_region 而非整页 generateCandidate", async () => {
    proposeApi.mockImplementation(() => new Promise<DirectorCommandGroup>(() => {}));
    renderEditor();
    const stage = screen.getByLabelText("局部选区画布");
    drawRect(stage, [160, 160], [480, 480]);
    fireEvent.change(screen.getByLabelText("局部重绘指令"), { target: { value: "把雨改成晴天" } });
    const button = screen.getByRole("button", { name: "预览局部命令" });
    fireEvent.click(button);
    fireEvent.click(button);
    await waitFor(() => {
      expect(proposeApi).toHaveBeenCalledTimes(1);
    });
    const envelope = proposeApi.mock.calls[0][1].commands[0];
    expect(envelope.operation).toBe("regenerate_region");
    expect(envelope.target).toEqual({ project_id: "project-1", page_id: "page-1" });
    expect(envelope.expected_version).toEqual({ scope: "page", value: 2 });
    expect(envelope.payload.instruction).toBe("把雨改成晴天");
    expect(envelope.payload.model_alias).toBe("gemini_image");
    expect((envelope.payload.mask as { points: number[][] }[])[0].points).toHaveLength(4);
    expect(generateCandidateApi).not.toHaveBeenCalled();
  });

  it("L11 目录无 mask 能力模型：不发付费请求、解释并给取消出口", async () => {
    const onClose = vi.fn();
    renderEditor({ onClose, models: [capableModelFixture({ accepts_explicit_mask: false })] });
    expect(screen.getByRole("alert")).toHaveTextContent("当前模型不能按选区重绘");
    expect(screen.getByText(/不会按整页重绘静默降级/)).toBeInTheDocument();
    const generate = screen.getByRole("button", { name: "预览局部命令" });
    expect(generate).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "取消局部编辑" }));
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
      expect(proposeApi).not.toHaveBeenCalled();
    });
    expect(generateCandidateApi).not.toHaveBeenCalled();
  });

  it("V02-44B 目录只有整图参考模型：屏蔽态如实列出能力声明，不静默降级", () => {
    renderEditor({
      models: [
        capableModelFixture({ accepts_explicit_mask: false, whole_image_reference_only: true }),
        capableModelFixture({
          logical_alias: "instruction_only",
          model_id: "instruction-editor",
          accepts_explicit_mask: false,
          supports_instruction_region_edit: true,
        }),
      ],
    });
    expect(screen.getByRole("alert")).toHaveTextContent("目录中已启用的编辑模型能力声明");
    expect(screen.getByText(/仅整图参考编辑（不保证区域外不变）/)).toBeInTheDocument();
    expect(screen.getByText(/仅 instruction 区域编辑（不支持选区 mask）/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "预览局部命令" })).toBeDisabled();
    expect(proposeApi).not.toHaveBeenCalled();
    expect(generateCandidateApi).not.toHaveBeenCalled();
  });

  it("L5b 预览确认流：propose → 预览卡 → 确认生成才 accept", async () => {
    proposeApi.mockResolvedValue(previewGroupFixture("cmd-loc-1"));
    acceptApi.mockResolvedValue({
      ...previewGroupFixture("cmd-loc-1"),
      status: "COMMITTED",
      commands: [{
        ...previewGroupFixture("cmd-loc-1").commands[0],
        status: "EXECUTED",
      }],
    });
    renderEditor();
    await fillAndPropose();
    const preview = screen.getByRole("region", { name: "局部命令预览" });
    expect(preview).toBeInTheDocument();
    expect(acceptApi).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /确认生成/ }));
    await waitFor(() => {
      expect(acceptApi).toHaveBeenCalledWith("project-1", "cmd-loc-1");
    });
    expect(generateCandidateApi).not.toHaveBeenCalled();
  });

  it("L7 生成中锁画笔并可取消任务；取消后 mask 保留可重来", async () => {
    const commandId = "cmd-loc-7";
    proposeApi.mockResolvedValue(previewGroupFixture(commandId));
    acceptApi.mockResolvedValue({
      ...previewGroupFixture(commandId),
      status: "COMMITTED",
    });
    batchesApi.mockResolvedValue([batchFixture()]);
    candidatesApi.mockResolvedValue([candidateFixture({
      id: "candidate-derived",
      batch_id: "batch-region-1",
      ordinal: 1,
      status: "GENERATING",
      asset_id: null,
      job_id: "job-loc-1",
      is_selected: false,
      prompt_snapshot: { lineage: { source_command_id: commandId } },
    })]);
    const { maskStats } = renderEditor();
    await fillAndPropose();
    fireEvent.click(await screen.findByRole("button", { name: /确认生成/ }));
    await waitFor(() => {
      expect(screen.getByText(/局部候选生成中，画笔已锁定/)).toBeInTheDocument();
    });
    expect(screen.getByLabelText("局部选区画布").className).toContain("locked");
    expect(screen.getByRole("button", { name: "矩形" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消任务" })).toBeEnabled();
    expect(maskStats()).toBe("已选 25% 面积 · 1/8 块");
    fireEvent.click(screen.getByRole("button", { name: "取消任务" }));
    await waitFor(() => {
      expect(cancelJobApi).toHaveBeenCalledWith("job-loc-1");
    });
    expect(maskStats()).toBe("已选 25% 面积 · 1/8 块");
  });

  it("L17/L18 成功：比较槽显示新局部候选（未自动暂选），alt 带源/新身份", async () => {
    const commandId = "cmd-loc-17";
    proposeApi.mockResolvedValue(previewGroupFixture(commandId));
    acceptApi.mockResolvedValue({ ...previewGroupFixture(commandId), status: "COMMITTED" });
    batchesApi.mockResolvedValue([batchFixture()]);
    candidatesApi.mockResolvedValue([candidateFixture({
      id: "candidate-derived",
      batch_id: "batch-region-1",
      ordinal: 1,
      status: "COMPLETED",
      asset_id: "asset-derived",
      job_id: "job-loc-17",
      is_selected: false,
      content_url: "/api/v1/assets/asset-derived/content",
      prompt_snapshot: { lineage: { source_command_id: commandId } },
    })]);
    renderEditor();
    await fillAndPropose();
    fireEvent.click(await screen.findByRole("button", { name: /确认生成/ }));
    expect(await screen.findByText("新局部候选 · 候选 1")).toBeInTheDocument();
    expect(screen.getByText("未自动暂选")).toBeInTheDocument();
    expect(screen.getByAltText("局部候选 1，相对源候选 3")).toBeInTheDocument();
    expect(screen.getByText(/局部候选已生成（未自动暂选）/)).toBeInTheDocument();
    // 源候选采用状态不变：源槽仍在且带已暂选徽章
    expect(screen.getByText("已暂选")).toBeInTheDocument();
    expect(generateCandidateApi).not.toHaveBeenCalled();
  });

  it("L8 propose 失败：错误可见、mask 保留、可重试", async () => {
    proposeApi.mockRejectedValueOnce(new Error("UNSUPPORTED_CAPABILITY：模型不支持 mask"));
    const { maskStats } = renderEditor();
    const stage = screen.getByLabelText("局部选区画布");
    drawRect(stage, [160, 160], [480, 480]);
    fireEvent.change(screen.getByLabelText("局部重绘指令"), { target: { value: "把雨改成晴天" } });
    fireEvent.click(screen.getByRole("button", { name: "预览局部命令" }));
    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts.some((alert) => alert.textContent?.includes("UNSUPPORTED_CAPABILITY"))).toBe(true);
    });
    expect(maskStats()).toBe("已选 25% 面积 · 1/8 块");
    proposeApi.mockResolvedValue(previewGroupFixture("cmd-loc-8"));
    fireEvent.click(screen.getByRole("button", { name: "预览局部命令" }));
    await waitFor(() => {
      expect(proposeApi).toHaveBeenCalledTimes(2);
    });
  });

  it("L13 Esc：有未提交选区需确认后才关闭", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const onClose = vi.fn();
    renderEditor({ onClose });
    const stage = screen.getByLabelText("局部选区画布");
    drawRect(stage, [160, 160], [480, 480]);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(confirmSpy).toHaveBeenCalledOnce();
    expect(onClose).not.toHaveBeenCalled();
    confirmSpy.mockReturnValue(true);
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => {
      expect(onClose).toHaveBeenCalledTimes(1);
    });
    confirmSpy.mockRestore();
  });

  it("L14 键盘 V/B/E 切换工具且 aria-pressed 正确", () => {
    renderEditor();
    expect(screen.getByRole("button", { name: "矩形" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(window, { key: "b" });
    expect(screen.getByRole("button", { name: "画笔" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "矩形" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.keyDown(window, { key: "e" });
    expect(screen.getByRole("button", { name: "擦除" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(window, { key: "v" });
    expect(screen.getByRole("button", { name: "矩形" })).toHaveAttribute("aria-pressed", "true");
  });
});
