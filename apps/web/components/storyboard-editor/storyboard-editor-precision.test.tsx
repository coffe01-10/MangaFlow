/* eslint-disable @typescript-eslint/no-explicit-any */
// Precision editing regressions (画布精准编辑): numeric geometry fields,
// align/distribute/same-size, grid snap, bubble rotation, group scale,
// visual sound effects, z-order ops, live size labels, copy/paste geometry.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type BubbleGeometryShape, type NormalizedRect } from "@/lib/api";

import { StoryboardEditor } from "./index";

const storyboardQuery = vi.spyOn(api, "storyboard");
const saveGeometry = vi.spyOn(api, "saveStoryboardGeometry");
const updatePanel = vi.spyOn(api, "updatePanel");
const updatePageLayout = vi.spyOn(api, "updatePageLayout");
const updateDialogue = vi.spyOn(api, "updateDialogue");
const createDialogue = vi.spyOn(api, "createDialogue");
const deleteDialogue = vi.spyOn(api, "deleteDialogue");

const page = {
  id: "page-1",
  chapter_id: "chapter-1",
  page_number: 1,
  revision_no: 1,
  page_function: "narrative",
  panel_count: 2,
  reading_direction: "rtl",
  resolution: "1k",
  status: "READY",
  estimated_text_chars: 20,
  estimated_bubbles: 1,
  source_coverage: { layout_mode: "dynamic" },
  selected_candidate_id: null,
  storyboard_version: 1,
  selected_candidate_ack_version: null,
  continuity_status: "READY",
  scene_ids: ["scene-1"],
  beat_ids: ["beat-1"],
  version: 1,
  canvas: { width_mm: 182, height_mm: 257, bleed_mm: 3, safe_mm: 5, unit: "mm" },
};

function makePanel(overrides: Record<string, unknown> = {}) {
  const bounds = (overrides.bounds ?? { x: 0.1, y: 0.1, width: 0.4, height: 0.3 }) as NormalizedRect;
  return {
    id: "panel-1",
    page_id: "page-1",
    reading_order: 1,
    bounds,
    shot_type: "establishing",
    camera_angle: "eye_level",
    camera_height: "eye_level",
    characters: [],
    character_presence: {},
    props: [],
    outfits: {},
    actions: { script_action: "第一格动作" },
    expressions: {},
    background: "教室",
    bubble_regions: [],
    sound_effects: [],
    bleed: false,
    borderless: false,
    locked_fields: [],
    version: 1,
    geometry: { type: "rect", rect: bounds, rotation: 0, z_order: 1 },
    dialogues: [],
    ...overrides,
  };
}

const panel1 = makePanel();
const panel2 = makePanel({
  id: "panel-2",
  reading_order: 2,
  bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
  geometry: { type: "rect", rect: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 }, rotation: 0, z_order: 2 },
  actions: { script_action: "第二格动作" },
});

const storedBubble: BubbleGeometryShape = {
  type: "rect",
  rect: { x: 0.15, y: 0.12, width: 0.2, height: 0.14 },
  anchor: { x: 0.25, y: 0.26 },
  tail_target: { x: 0.3, y: 0.4 },
  rotation: 0,
};

const dialogue1 = {
  id: "dialogue-1",
  panel_id: "panel-2",
  speaker_character_id: null,
  target_text: "第二格的气泡",
  reading_order: 1,
  text_direction: "vertical" as const,
  region: {},
  rewrite_forbidden: false,
  bubble: storedBubble,
};

let data: Record<string, unknown>;

function renderEditor(props: Record<string, unknown> = {}) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <StoryboardEditor
        chapterId="chapter-1"
        pages={[page] as never}
        characters={[]}
        outfits={[]}
        onReplan={() => undefined}
        replanPending={false}
        {...props}
      />
    </QueryClientProvider>,
  );
}

function stubRect(element: Element, width: number, height: number) {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: height,
    width,
    height,
    toJSON: () => ({}),
  } as DOMRect);
}

function canvasPage() {
  return screen.getByTestId("canvas-page");
}

function panelEl(id: string) {
  return document.getElementById(`canvas-panel-${id}`)!;
}

function bubbleEl(id: string) {
  return document.getElementById(`canvas-bubble-${id}`)!;
}

function sfxEl(panelId: string, index: number) {
  return document.getElementById(`canvas-sfx-${panelId}-${index}`)!;
}

function payloadPanel(payload: any, id: string) {
  return payload.panels.find((panel: { panel_id: string }) => panel.panel_id === id) as Record<string, any>;
}

function payloadDialogue(payload: any, id: string) {
  return payload.dialogues.find((dialogue: { dialogue_id: string }) => dialogue.dialogue_id === id) as Record<string, any>;
}

/** 点选一格（零位移拖拽，不产生命令）。 */
function clickPanel(id: string, shiftKey = false) {
  fireEvent.pointerDown(panelEl(id), { button: 0, pointerId: 1, clientX: 100, clientY: 100, shiftKey });
  fireEvent.pointerUp(window, { pointerId: 1, clientX: 100, clientY: 100 });
}

async function savePage() {
  fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
  await waitFor(() => expect(saveGeometry).toHaveBeenCalled());
  return saveGeometry.mock.calls[0][1] as any;
}

describe("StoryboardEditor 精准编辑", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = { page, candidate_count: 0, panels: [panel1, panel2] };
    storyboardQuery.mockReset();
    storyboardQuery.mockImplementation(() => Promise.resolve(data as never));
    // 保存响应必须惰性取当前 data：测试会重赋值 data（换面板夹具），
    // mockResolvedValue 捕获旧对象会把不含新格/气泡的数据写回查询缓存，
    // 保存成功后对应 DOM 节点消失（P10/P13 回归原因）。
    saveGeometry.mockReset().mockImplementation(() => Promise.resolve(data as never));
    updatePanel.mockReset().mockResolvedValue(data as never);
    updatePageLayout.mockReset().mockResolvedValue(data as never);
    updateDialogue.mockReset().mockResolvedValue({} as never);
    createDialogue.mockReset().mockResolvedValue({} as never);
    deleteDialogue.mockReset().mockResolvedValue({} as never);
  });

  it("P1 数字输入 mm：X 输入进撤销栈，保存进 bounds", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    // 默认 inspector 展示 panel-1；X=36.4mm → 36.4/182 = 0.2
    const xInput = screen.getByLabelText("X（mm）");
    fireEvent.change(xInput, { target: { value: "36.4" } });
    fireEvent.blur(xInput);
    // 输入落进撤销栈：撤销恢复旧值、画布不再有未保存内容。
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect((screen.getByLabelText("X（mm）") as HTMLInputElement).value).toBe("18.2");
    expect(screen.getByRole("button", { name: "保存本页" })).toHaveProperty("disabled", true);
    // 再输入一次并保存。
    const xInputAgain = screen.getByLabelText("X（mm）");
    fireEvent.change(xInputAgain, { target: { value: "36.4" } });
    fireEvent.blur(xInputAgain);
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-1").bounds.x).toBe(0.2);
    expect(payloadPanel(payload, "panel-1").geometry.rect.x).toBe(0.2);
  });

  it("P2 数字输入 %：单位切换后按百分比提交", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    const fields = within(screen.getByTestId("geometry-fields"));
    fireEvent.click(fields.getByRole("button", { name: "%" }));
    const widthInput = screen.getByLabelText("宽（%）");
    fireEvent.change(widthInput, { target: { value: "50" } });
    fireEvent.blur(widthInput);
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-1").bounds.width).toBe(0.5);
  });

  it("P3 数字输入校验：非法值不产生命令，越界值收进页面", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    const xInput = screen.getByLabelText("X（mm）");
    fireEvent.change(xInput, { target: { value: "abc" } });
    fireEvent.blur(xInput);
    expect(screen.getByRole("button", { name: "保存本页" })).toHaveProperty("disabled", true);
    fireEvent.change(xInput, { target: { value: "-50" } });
    fireEvent.blur(xInput);
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-1").bounds.x).toBe(0);
  });

  it("P4 对齐：多选后左对齐，命令可撤销", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    clickPanel("panel-1");
    clickPanel("panel-2", true);
    fireEvent.click(screen.getByRole("button", { name: "左对齐" }));
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-1").bounds.x).toBe(0.1);
    expect(payloadPanel(payload, "panel-2").bounds.x).toBe(0.1);
  });

  it("P4b 对齐命令可撤销：恢复各自原 x", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    clickPanel("panel-1");
    clickPanel("panel-2", true);
    fireEvent.click(screen.getByRole("button", { name: "左对齐" }));
    // style 是 `${x*100}%` 的浮点文本，按数值断言。
    expect(parseFloat(panelEl("panel-2").style.left)).toBeCloseTo(10, 6);
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(parseFloat(panelEl("panel-2").style.left)).toBeCloseTo(55, 6);
    expect(screen.getByRole("button", { name: "保存本页" })).toHaveProperty("disabled", true);
  });

  it("P5 同宽/同大小：以第一选中格为基准", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    clickPanel("panel-1");
    clickPanel("panel-2", true);
    fireEvent.click(screen.getByRole("button", { name: "同宽" }));
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-2").bounds.width).toBe(0.4);
    // 0.55 + 0.4 = 0.95 不出页，x 保持。
    expect(payloadPanel(payload, "panel-2").bounds.x).toBe(0.55);
  });

  it("P6 水平等距：三格分布，两端固定", async () => {
    const p1 = makePanel({ bounds: { x: 0.05, y: 0.1, width: 0.2, height: 0.2 } });
    const p2 = makePanel({
      id: "panel-2",
      reading_order: 2,
      bounds: { x: 0.45, y: 0.3, width: 0.15, height: 0.15 },
      geometry: { type: "rect", rect: { x: 0.45, y: 0.3, width: 0.15, height: 0.15 }, rotation: 0, z_order: 2 },
    });
    const p3 = makePanel({
      id: "panel-3",
      reading_order: 3,
      bounds: { x: 0.7, y: 0.5, width: 0.2, height: 0.2 },
      geometry: { type: "rect", rect: { x: 0.7, y: 0.5, width: 0.2, height: 0.2 }, rotation: 0, z_order: 3 },
    });
    data = { page, candidate_count: 0, panels: [p1, p2, p3] };
    renderEditor();
    await screen.findByTestId("canvas-page");
    clickPanel("panel-1");
    clickPanel("panel-2", true);
    clickPanel("panel-3", true);
    fireEvent.click(screen.getByRole("button", { name: "水平等距" }));
    const payload = await savePage();
    // 首 0.05 + 宽 0.2 + 等距 gap 0.15 = 0.4
    expect(payloadPanel(payload, "panel-2").bounds.x).toBeCloseTo(0.4, 4);
    expect(payloadPanel(payload, "panel-3").bounds.x).toBe(0.7);
  });

  it("P7 图层置顶：z_order 改写进 geometry，阅读序不动", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    fireEvent.click(screen.getByRole("button", { name: "置顶" }));
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-1").geometry.z_order).toBe(3);
    expect(payloadPanel(payload, "panel-1").reading_order).toBe(1);
    expect(payloadPanel(payload, "panel-2").geometry.z_order).toBe(2);
  });

  it("P8 网格：开关渲染网格线，拖动吸附到 10mm 网格", async () => {
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    expect(document.querySelector(".canvas-grid-line")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "网格" }));
    expect(document.querySelectorAll(".canvas-grid-line").length).toBeGreaterThan(0);
    // 拖到 x≈0.108：距 20mm 线（20/182≈0.1099）在阈值内 → 吸附。
    fireEvent.pointerDown(panelEl("panel-1"), { button: 0, pointerId: 1, clientX: 64, clientY: 90 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 69, clientY: 90 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 69, clientY: 90 });
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-1").bounds.x).toBeCloseTo(20 / 182, 4);
    // 切换间距到 20mm，网格线数量变化。
    fireEvent.change(screen.getByLabelText("网格间距"), { target: { value: "20" } });
    expect(document.querySelectorAll(".canvas-grid-line").length).toBeGreaterThan(0);
  });

  it("P9 多选统一缩放：组 bbox 的 se 手柄等比放大两格", async () => {
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    clickPanel("panel-1");
    clickPanel("panel-2", true);
    // 组 bbox {x:0.1, y:0.1, w:0.8, h:0.35}；se 角在页面 (0.9, 0.45)。
    const seHandle = document.querySelector('.canvas-handles [data-handle="se"]')!;
    expect(seHandle).toBeTruthy();
    fireEvent.pointerDown(seHandle, { button: 0, pointerId: 1, clientX: 576, clientY: 406.35 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 608, clientY: 437.4 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 608, clientY: 437.4 });
    const payload = await savePage();
    const p1 = payloadPanel(payload, "panel-1").bounds;
    const p2 = payloadPanel(payload, "panel-2").bounds;
    // scaleX = 0.85/0.8 = 1.0625
    expect(p1.width).toBeCloseTo(0.425, 4);
    expect(p1.x).toBeCloseTo(0.1, 4);
    expect(p2.x).toBeCloseTo(0.5781, 4);
    expect(p2.width).toBeCloseTo(0.3719, 4);
  });

  it("P10 气泡旋转：旋转手柄拖动 → rotation 入 payload；角度输入同步", async () => {
    const panelWithBubble = makePanel({
      id: "panel-2",
      reading_order: 2,
      bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
      geometry: { type: "rect", rect: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 }, rotation: 0, z_order: 2 },
      dialogues: [dialogue1],
    });
    data = { page, candidate_count: 0, panels: [panel1, panelWithBubble] };
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    // 选中气泡（零位移点击）。
    fireEvent.pointerDown(bubbleEl("dialogue-1"), { button: 0, pointerId: 1, clientX: 160, clientY: 171.57 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 160, clientY: 171.57 });
    // 旋转手柄：中心 (0.25,0.19)，从正上方起点拖到正右方 = +90°。
    const rotateHandle = document.querySelector('.canvas-handles [data-handle="rotate"]')!;
    fireEvent.pointerDown(rotateHandle, { button: 0, pointerId: 2, clientX: 160, clientY: 68 });
    fireEvent.pointerMove(window, { pointerId: 2, clientX: 260, clientY: 171.57 });
    fireEvent.pointerUp(window, { pointerId: 2, clientX: 260, clientY: 171.57 });
    // 检查器气泡几何区：角度字段跟随草稿同步显示。
    const rotationInput = screen.getByLabelText("角度（°）");
    expect((rotationInput as HTMLInputElement).value).toBe("90");
    const payload = await savePage();
    expect(payloadDialogue(payload, "dialogue-1").bubble.rotation).toBeCloseTo(90, 1);

    // 保存后草稿清空回到存储值，可继续数字输入提交新角度。
    saveGeometry.mockClear();
    fireEvent.change(rotationInput, { target: { value: "30" } });
    fireEvent.blur(rotationInput);
    const next = await savePage();
    expect(payloadDialogue(next, "dialogue-1").bubble.rotation).toBe(30);
  });

  it("P11 拟声词画布渲染与拖动：PATCH 面板合并几何后整包 PUT", async () => {
    const sfxPanel = makePanel({
      sound_effects: [{ text: "咚", x: 0.3, y: 0.4, rotation: 0, size: 0.08 }],
    });
    data = { page, candidate_count: 0, panels: [sfxPanel, panel2] };
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    const node = sfxEl("panel-1", 0);
    expect(node.textContent).toContain("咚");
    fireEvent.pointerDown(node, { button: 0, pointerId: 1, clientX: 192, clientY: 361 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 224, clientY: 361 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 224, clientY: 361 });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalled());
    expect(updatePanel).toHaveBeenCalledTimes(1);
    const [panelId, patch] = updatePanel.mock.calls[0];
    expect(panelId).toBe("panel-1");
    expect((patch as any).version).toBe(1);
    expect((patch as any).sound_effects).toEqual([
      { text: "咚", x: 0.35, y: 0.4, rotation: 0, size: 0.08 },
    ]);
    // 拟声词 PATCH 必须先于几何整包 PUT（同一保存动作内）。
    expect(updatePanel.mock.invocationCallOrder[0]).toBeLessThan(saveGeometry.mock.invocationCallOrder[0]);
  });

  it("P12 拟声词旋转/缩放手柄与方向键", async () => {
    const sfxPanel = makePanel({
      sound_effects: [{ text: "砰", x: 0.3, y: 0.4, rotation: 0, size: 0.08 }],
    });
    data = { page, candidate_count: 0, panels: [sfxPanel, panel2] };
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    // 先选中拟声词。
    fireEvent.pointerDown(sfxEl("panel-1", 0), { button: 0, pointerId: 1, clientX: 192, clientY: 361.2 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 192, clientY: 361.2 });
    // 旋转手柄：起点正上方 → 拖到正右 = +90°。
    const rotate = sfxEl("panel-1", 0).querySelector('[data-handle="rotate"]')!;
    fireEvent.pointerDown(rotate, { button: 0, pointerId: 2, clientX: 192, clientY: 330 });
    fireEvent.pointerMove(window, { pointerId: 2, clientX: 242, clientY: 361.2 });
    fireEvent.pointerUp(window, { pointerId: 2, clientX: 242, clientY: 361.2 });
    // 缩放手柄：距中心 0.05 → 拉到 0.1，size ×2。
    const scale = sfxEl("panel-1", 0).querySelector('[data-handle="scale"]')!;
    fireEvent.pointerDown(scale, { button: 0, pointerId: 3, clientX: 224, clientY: 361.2 });
    fireEvent.pointerMove(window, { pointerId: 3, clientX: 256, clientY: 361.2 });
    fireEvent.pointerUp(window, { pointerId: 3, clientX: 256, clientY: 361.2 });
    // 方向键微调最后做：x +1px（归一化 1/640）。
    fireEvent.keyDown(canvasPage(), { key: "ArrowRight" });

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalled());
    const patch = updatePanel.mock.calls[0][1] as any;
    expect(patch.sound_effects[0].x).toBeCloseTo(0.3 + 1 / 640, 4);
    expect(patch.sound_effects[0].rotation).toBeCloseTo(90, 1);
    expect(patch.sound_effects[0].size).toBeCloseTo(0.16, 4);
    // 文本保持。
    expect(patch.sound_effects[0].text).toBe("砰");
  });

  it("P13 复制/粘贴几何：Ctrl+C 选中格 → Ctrl+V 到另一格/气泡", async () => {
    const panelWithBubble = makePanel({
      id: "panel-2",
      reading_order: 2,
      bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
      geometry: { type: "rect", rect: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 }, rotation: 0, z_order: 2 },
      dialogues: [dialogue1],
    });
    data = { page, candidate_count: 0, panels: [panel1, panelWithBubble] };
    renderEditor();
    await screen.findByTestId("canvas-page");
    clickPanel("panel-1");
    fireEvent.keyDown(canvasPage(), { key: "c", ctrlKey: true });
    clickPanel("panel-2");
    fireEvent.keyDown(canvasPage(), { key: "v", ctrlKey: true });
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-2").bounds).toEqual({ x: 0.1, y: 0.1, width: 0.4, height: 0.3 });

    // 再粘贴到气泡：rect 复制，rotation 沿用复制值。
    saveGeometry.mockClear();
    fireEvent.pointerDown(bubbleEl("dialogue-1"), { button: 0, pointerId: 1, clientX: 160, clientY: 172 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 160, clientY: 172 });
    fireEvent.keyDown(canvasPage(), { key: "v", ctrlKey: true });
    const next = await savePage();
    const bubble = payloadDialogue(next, "dialogue-1").bubble;
    expect(bubble.rect).toEqual({ x: 0.1, y: 0.1, width: 0.4, height: 0.3 });
  });

  it("P14 拖动实时尺寸标签：跟随移动显示 mm 尺寸，松手隐藏", async () => {
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    const label = document.querySelector(".canvas-size-label") as HTMLElement;
    expect(label.style.display).toBe("none");
    fireEvent.pointerDown(panelEl("panel-1"), { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 120, clientY: 120 });
    expect(label.style.display).not.toBe("none");
    expect(label.textContent).toMatch(/72\.8 × 77\.1 mm/);
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 120, clientY: 120 });
    expect(label.style.display).toBe("none");
  });

  it("P15 等距智能参考线：拖到两邻等距处出现 gap 线", async () => {
    const p1 = makePanel({ bounds: { x: 0.05, y: 0.1, width: 0.15, height: 0.2 } });
    const p2 = makePanel({
      id: "panel-2",
      reading_order: 2,
      bounds: { x: 0.45, y: 0.4, width: 0.15, height: 0.15 },
      geometry: { type: "rect", rect: { x: 0.45, y: 0.4, width: 0.15, height: 0.15 }, rotation: 0, z_order: 2 },
    });
    const p3 = makePanel({
      id: "panel-3",
      reading_order: 3,
      bounds: { x: 0.75, y: 0.1, width: 0.2, height: 0.2 },
      geometry: { type: "rect", rect: { x: 0.75, y: 0.1, width: 0.2, height: 0.2 }, rotation: 0, z_order: 3 },
    });
    data = { page, candidate_count: 0, panels: [p1, p2, p3] };
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    // 拖 panel-2 向左到 x≈0.405（等距位 0.4，阈值内）：p1 右缘 0.2 / p3 左缘 0.75。
    fireEvent.pointerDown(panelEl("panel-2"), { button: 0, pointerId: 1, clientX: 320, clientY: 430 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 291, clientY: 430 });
    expect(document.querySelectorAll(".canvas-guide-line.gap").length).toBe(2);
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 291, clientY: 430 });
    const payload = await savePage();
    expect(payloadPanel(payload, "panel-2").bounds.x).toBeCloseTo(0.4, 4);
  });

  it("P16 旧字符串拟声词：按默认落位渲染，拖动后带几何 PATCH", async () => {
    const legacyPanel = makePanel({ sound_effects: ["锵"] });
    data = { page, candidate_count: 0, panels: [legacyPanel, panel2] };
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    const node = screen.getByLabelText("拟声词 锵");
    // 默认落位：格内层叠位置（panel bounds {0.1,0.1,0.4,0.3} index 0 → x 0.22, y 0.19）。
    fireEvent.pointerDown(node, { button: 0, pointerId: 1, clientX: 166, clientY: 172 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 198, clientY: 172 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 198, clientY: 172 });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalled());
    const patch = updatePanel.mock.calls[0][1] as any;
    expect(patch.sound_effects[0].text).toBe("锵");
    expect(patch.sound_effects[0].x).toBeCloseTo(0.22 + 0.05, 4);
    expect(patch.sound_effects[0].rotation).toBe(0);
    expect(patch.sound_effects[0].size).toBe(0.05);
  });

  it("M1 命令动效：手势期间挂 is-gesturing 压零过渡，松手摘除", async () => {
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    expect(canvasEl.classList.contains("is-gesturing")).toBe(false);
    fireEvent.pointerDown(panelEl("panel-1"), { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    expect(canvasEl.classList.contains("is-gesturing")).toBe(true);
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 140, clientY: 100 });
    expect(canvasEl.classList.contains("is-gesturing")).toBe(true);
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 140, clientY: 100 });
    expect(canvasEl.classList.contains("is-gesturing")).toBe(false);
  });

  it("M2 命令动效：参考线带签名属性，松手挂 leaving 淡出后摘除", async () => {
    renderEditor();
    const canvasEl = await screen.findByTestId("canvas-page");
    stubRect(canvasEl, 640, 903);
    // 拖 panel-1 右缘到 panel-2 左缘（0.55）附近：+35px → 右缘 0.5547，
    // 吸附阈值（6px≈0.009）内 → 出现吸附参考线。
    fireEvent.pointerDown(panelEl("panel-1"), { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 135, clientY: 100 });
    const live = document.querySelectorAll(".canvas-guide-line:not(.leaving)");
    expect(live.length).toBeGreaterThan(0);
    expect((live[0] as HTMLElement).dataset.guide).toBeTruthy();
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 196, clientY: 100 });
    // 松手后参考线挂 leaving 淡出（transitionend/兜底定时器摘除），不再算活动线。
    const leaving = document.querySelectorAll(".canvas-guide-line.leaving");
    expect(leaving.length).toBeGreaterThan(0);
    expect(document.querySelectorAll(".canvas-guide-line:not(.leaving)")).toHaveLength(0);
  });

  it("M3 命令动效：层序命令给受影响格挂 z-flash，撤销同样触发", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    fireEvent.click(screen.getByRole("button", { name: "置顶" }));
    expect(panelEl("panel-1").classList.contains("z-flash")).toBe(true);
    expect(panelEl("panel-2").classList.contains("z-flash")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(panelEl("panel-1").classList.contains("z-flash")).toBe(true);
  });
});
