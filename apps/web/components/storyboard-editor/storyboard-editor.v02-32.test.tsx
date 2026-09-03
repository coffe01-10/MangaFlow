/* eslint-disable @typescript-eslint/no-explicit-any */
// V02-32 usability + 100-node acceptance (docs/v02-storyboard-editor-ui-audit.md
// §4/§5). Geometry payloads are asserted loosely; production code validates
// them against the API contract.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type NormalizedRect } from "@/lib/api";

import { canvasRenderCount, HIT_TEST_OBJECT_LIMIT, resetCanvasRenderStats } from "./page-canvas";
import { StressStoryboardCanvas } from "./stress-canvas";
import { buildStressStoryboard, stressPanelRect } from "./stress-fixture";
import { StoryboardEditor } from "./index";
import { storyboardCopy } from "./storyboard-copy";

const storyboardQuery = vi.spyOn(api, "storyboard");
const saveGeometry = vi.spyOn(api, "saveStoryboardGeometry");
const updatePanel = vi.spyOn(api, "updatePanel");

const page = {
  id: "page-1",
  chapter_id: "chapter-1",
  page_number: 1,
  revision_no: 1,
  page_function: "narrative",
  panel_count: 2,
  reading_direction: "rtl",
  resolution: "1K",
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

let data: Record<string, unknown>;

function makeStoryboard() {
  return { page, candidate_count: 0, panels: [makePanel(), makePanel({
    id: "panel-2",
    reading_order: 2,
    bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
    geometry: { type: "rect", rect: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 }, rotation: 0, z_order: 2 },
  })] };
}

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

function renderStress() {
  return render(<StressStoryboardCanvas />);
}

function stubRect(element: Element, rect: { x?: number; y?: number; width: number; height: number }) {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: rect.x ?? 0,
    y: rect.y ?? 0,
    left: rect.x ?? 0,
    top: rect.y ?? 0,
    right: (rect.x ?? 0) + rect.width,
    bottom: (rect.y ?? 0) + rect.height,
    width: rect.width,
    height: rect.height,
    toJSON: () => ({}),
  } as DOMRect);
}

function canvasPage() {
  return screen.getByTestId("canvas-page");
}

function panelEl(id: string) {
  return document.getElementById(`canvas-panel-${id}`)!;
}

function payloadPanel(payload: any, id: string) {
  return payload.panels.find((panel: { panel_id: string }) => panel.panel_id === id) as Record<string, any>;
}

/** 3–8 格页的非重叠栅格布局（3 列）。 */
function gridPanels(count: number) {
  const columns = 3;
  const rows = Math.ceil(count / columns);
  const margin = 0.03;
  const gap = 0.03;
  const width = (1 - margin * 2 - gap * (columns - 1)) / columns;
  const height = (1 - margin * 2 - gap * (rows - 1)) / rows;
  return Array.from({ length: count }, (_, index) => {
    const bounds = {
      x: margin + (index % columns) * (width + gap),
      y: margin + Math.floor(index / columns) * (height + gap),
      width,
      height,
    };
    return makePanel({
      id: `panel-${index + 1}`,
      reading_order: index + 1,
      bounds,
      geometry: { type: "rect", rect: bounds, rotation: 0, z_order: index + 1 },
    });
  });
}

describe("V02-32 页面宽高比（page_ratio / canvas mm）", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it.each([
    ["B5 竖版 182×257", { width_mm: 182, height_mm: 257 }],
    ["A4 横版 297×210", { width_mm: 297, height_mm: 210 }],
    ["方版 210×210", { width_mm: 210, height_mm: 210 }],
  ])("%s：画布按 mm 比例渲染", async (_label, canvas) => {
    data = { page: { ...page, canvas: { ...canvas, bleed_mm: 3, safe_mm: 5, unit: "mm" } }, candidate_count: 0, panels: [makePanel()] };
    renderEditor();
    const element = await screen.findByTestId("canvas-page");
    expect(element.style.aspectRatio).toBe(`${canvas.width_mm} / ${canvas.height_mm}`);
    expect(element.style.width).toBe("640px");
  });

  it("同一 1px 方向键增量随画布高度换算为不同归一化步长", async () => {
    data = { page: { ...page, canvas: { width_mm: 297, height_mm: 210, bleed_mm: 3, safe_mm: 5, unit: "mm" } }, candidate_count: 0, panels: [makePanel()] };
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 500 });
    fireEvent.pointerDown(panelEl("panel-1"), { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.keyDown(canvasPage(), { key: "ArrowDown" });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    // 1px / 500px 高度 = 0.002，B5 竖版（903px 高）约为 0.0011。
    expect(payloadPanel(saveGeometry.mock.calls[0][1], "panel-1").bounds.y).toBe(0.102);
  });
});

describe("V02-32 3–8 格选择与整包 PUT", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it.each([3, 5, 8])("%i 格页：点选 + 拖动后保存仍走一次整包 PUT，payload 覆盖全部格子", async (count) => {
    data = { page: { ...page, panel_count: count }, candidate_count: 0, panels: gridPanels(count) };
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 903 });
    const target = panelEl(`panel-${count}`);
    fireEvent.pointerDown(target, { button: 0, pointerId: 1, clientX: 200, clientY: 200 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 232, clientY: 232 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 232, clientY: 232 });
    expect(target).toHaveClass("selected");
    // 保存前抓取画布草稿；保存成功后画布以服务端响应为准。
    const draftLeft = parseFloat(target.style.left);

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const payload = saveGeometry.mock.calls[0][1] as any;
    expect(payload.panels).toHaveLength(count);
    expect(payload.request_id).toBeTruthy();
    expect(payload.storyboard_version).toBe(1);
    // 画布渲染与 payload 用同一份草稿（payload 按契约只保留 4 位小数）。
    const dragged = payloadPanel(payload, `panel-${count}`);
    expect(dragged.bounds.x * 100).toBeCloseTo(draftLeft, 2);
  });
});

describe("V02-32 重叠与越界不分叉", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("重叠格允许：拖到另一格上方后画布与 PUT payload 保持同一几何", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 903 });
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 120, clientY: 135 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 448, clientY: 135 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 448, clientY: 135 });
    // 落点与 panel-2（x 0.55–0.9）重叠。
    const draftLeft = parseFloat(element.style.left);
    expect(draftLeft).toBeGreaterThan(50);

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const payload = payloadPanel(saveGeometry.mock.calls[0][1], "panel-1");
    expect(payload.bounds.x * 100).toBeCloseTo(draftLeft, 2);
    expect(payload.geometry.rect).toEqual(payload.bounds);
  });

  it("越界拖拽：本地草稿与即将 PUT 的 payload 不分叉（画布一个几何、保存同一个）", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 903 });
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 3000, clientY: 3000 });
    expect(element.style.left).toBe("100%"); // translateRect 钳制到页右边缘
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 3000, clientY: 3000 });

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const payload = payloadPanel(saveGeometry.mock.calls[0][1], "panel-1");
    // PUT 的正是画布上钳制后的那一份几何，不存在第二份草稿。
    expect(payload.bounds.x).toBe(1);
    expect(payload.bounds.y).toBe(1);
    expect(payload.bounds.width).toBe(0.4);
    expect(payload.bounds.height).toBe(0.3);
    expect(payload.geometry.rect).toEqual(payload.bounds);
  });
});

describe("V02-32 RTL 阅读序号", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("RTL 页序号锚在格右上；LTR 页锚在左上", async () => {
    const rtlView = renderEditor();
    await screen.findByTestId("canvas-page");
    const rtlBadge = screen.getByText("格 01");
    expect(rtlBadge.style.right).toBe("50%");
    expect(rtlBadge.style.left).toBe("");
    rtlView.unmount();

    data = { page: { ...page, reading_direction: "ltr" }, candidate_count: 0, panels: [makePanel(), makePanel({
      id: "panel-2", reading_order: 2, bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
      geometry: { type: "rect", rect: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 }, rotation: 0, z_order: 2 },
    })] };
    renderEditor({ pages: [{ ...page, reading_direction: "ltr" }] as never });
    await screen.findByTestId("canvas-page");
    const ltrBadge = screen.getByText("格 01");
    expect(ltrBadge.style.left).toBe("10%");
    expect(ltrBadge.style.right).toBe("");
  });

  it("阅读序开关只切换显示，不改 reading_order：整包 PUT 仍带服务端序号", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 903 });
    // 先产生一个几何草稿（保存按钮只在有草稿时可用），再切换阅读序显示。
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.click(screen.getByRole("button", { name: "阅读序" }));
    expect(screen.queryByText("格 01")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const payload = saveGeometry.mock.calls[0][1] as any;
    expect(payload.panels.map((panel: any) => panel.reading_order)).toEqual([1, 2]);
    expect(updatePanel).not.toHaveBeenCalled();
  });
});

describe("V02-32 触控板视口", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("普通滚轮平移视口、Ctrl+滚轮缩放，均不发 PUT/PATCH", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    const viewport = document.querySelector(".canvas-viewport")!;

    fireEvent.wheel(viewport, { deltaY: 120 });
    expect(screen.getByText("100%")).toBeTruthy();
    fireEvent.wheel(viewport, { deltaY: -120, ctrlKey: true });
    expect(screen.getByText("125%")).toBeTruthy();
    fireEvent.wheel(viewport, { deltaY: -120, metaKey: true });
    expect(screen.getByText("156%")).toBeTruthy();

    expect(saveGeometry).not.toHaveBeenCalled();
    expect(updatePanel).not.toHaveBeenCalled();
  });
});

describe("V02-32 保存失败不分叉", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("网络错误：草稿保留在画布上，不显示已保存；可放弃草稿并重新加载", async () => {
    saveGeometry.mockReset().mockRejectedValueOnce(new Error("网络中断"));
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 903 });
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));

    await screen.findByText("网络中断");
    const status = document.querySelector(".storyboard-save-status span")!;
    expect(status.textContent).toBe("保存失败");
    // 草稿未丢：画布仍是拖动后的几何。
    expect(element.style.left).toBe("20%");
    // 与 409 一致的逃生门：放弃草稿并重新加载。
    fireEvent.click(screen.getByRole("button", { name: storyboardCopy.discardReload }));
    await waitFor(() => expect(element.style.left).toBe("10%"));
    expect(screen.queryByText("网络中断")).toBeNull();
    expect(document.querySelector(".storyboard-save-status span")!.textContent).toBe("已保存");
  });
});

describe("V02-32 拖动只重绘选中轮廓", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("pointermove 期间零 React 重渲染，位置直接写入选中轮廓；mouseup 才入 state", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), { width: 640, height: 903 });
    const element = panelEl("panel-1");
    resetCanvasRenderStats();
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    const rendersAtGestureStart = canvasRenderCount();

    for (let step = 1; step <= 30; step++) {
      fireEvent.pointerMove(window, { pointerId: 1, clientX: 100 + step, clientY: 100 });
    }
    // 30 次 pointermove 没有触发任何 React 渲染，但选中轮廓已跟随指针。
    expect(canvasRenderCount()).toBe(rendersAtGestureStart);
    expect(parseFloat(element.style.left)).toBeGreaterThan(10);

    fireEvent.pointerUp(window, { pointerId: 1, clientX: 130, clientY: 100 });
    expect(canvasRenderCount()).toBeGreaterThan(rendersAtGestureStart);
    // 右边缘吸附到 0.5 线后 x = 0.15；React 渲染值带浮点噪声，用数值比较。
    expect(parseFloat(element.style.left)).toBeCloseTo(15, 4);
  });
});

describe("V02-32 阅读序号视口裁剪", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("视口外的格不渲染序号文字；滚动后重新计算", async () => {
    renderStress();
    const pageElement = await screen.findByTestId("canvas-page");
    // 页高 903px，视口 800×600，页面滚动到 top=-400：可见页内 y ∈ [400, 1000]。
    stubRect(pageElement, { x: 0, y: -400, width: 640, height: 903 });
    const viewport = document.querySelector(".canvas-viewport")!;
    stubRect(viewport, { width: 800, height: 600 });
    fireEvent.scroll(viewport);

    await waitFor(() => expect(screen.queryByText("格 01")).toBeNull());
    expect(screen.queryByText("格 09")).toBeTruthy();
    expect(screen.queryByText("格 20")).toBeTruthy();

    // 滚回页首后全部可见。
    stubRect(pageElement, { x: 0, y: 0, width: 640, height: 903 });
    fireEvent.scroll(viewport);
    await waitFor(() => expect(screen.getByText("格 01")).toBeTruthy());
  });
});

describe("V02-32 100 节点渲染策略", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
  });

  it("夹具超过命中测试阈值；未选中对象没有 DOM 节点，只有一层 SVG 矢量轮廓", async () => {
    const snapshot = buildStressStoryboard();
    expect(snapshot.panels.length + snapshot.panels.flatMap((panel) => panel.dialogues).length)
      .toBeGreaterThan(HIT_TEST_OBJECT_LIMIT);
    renderStress();
    await screen.findByTestId("canvas-page");
    // 20 格 + 80 气泡全部在矢量层。
    expect(document.querySelectorAll(".canvas-object-layer rect")).toHaveLength(100);
    // 初始无选中：页内没有任何 role=button 的对象节点。
    expect(canvasPage().querySelectorAll('[role="button"]')).toHaveLength(0);
  });

  it("点击命中测试选中最上层对象；只有选中对象挂 DOM 节点与手柄", async () => {
    renderStress();
    const pageElement = await screen.findByTestId("canvas-page");
    stubRect(pageElement, { width: 640, height: 903 });
    // panel-7（index 7）：第 1 行第 3 列，中心约 (0.86, 0.308)。
    fireEvent.pointerDown(pageElement, { button: 0, pointerId: 1, clientX: 550, clientY: 278 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 556, clientY: 278 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 556, clientY: 278 });

    const selected = document.getElementById("canvas-panel-stress-panel-7")!;
    expect(selected).toHaveClass("selected");
    expect(document.getElementById("canvas-panel-stress-panel-6")).toBeNull();
    expect(document.getElementById("canvas-panel-stress-panel-8")).toBeNull();
    expect(document.getElementById("canvas-bubble-stress-dlg-7-0")).toBeNull();
    // 1 个对象节点 + 8 个缩放手柄，绝没有 100 个按钮树。
    expect(canvasPage().querySelectorAll('[role="button"]')).toHaveLength(9);
    // 命中即选中并进入拖动：轮廓已移动约 6px / 640px。
    expect(parseFloat(selected.style.left)).toBeGreaterThan(stressPanelRect(7).x * 100);
  });

  it("画布仍只有一个 tab stop；Tab/方向键在 100 个对象间移动", async () => {
    renderStress();
    const pageElement = await screen.findByTestId("canvas-page");
    stubRect(pageElement, { width: 640, height: 903 });
    expect(pageElement.getAttribute("tabindex")).toBe("0");
    // Tab 进入画布选中第一个对象：格内元素与 SVG 轮廓均不进 Tab 环。
    fireEvent.keyDown(pageElement, { key: "ArrowRight" });
    expect(document.getElementById("canvas-panel-stress-panel-0")).toHaveClass("selected");
    fireEvent.keyDown(pageElement, { key: "Tab" });
    expect(document.getElementById("canvas-panel-stress-panel-1")).toHaveClass("selected");
    expect(document.getElementById("canvas-panel-stress-panel-0")).toBeNull();

    fireEvent.keyDown(pageElement, { key: "ArrowRight" });
    const moved = document.getElementById("canvas-panel-stress-panel-1")!;
    expect(parseFloat(moved.style.left)).toBeGreaterThan(stressPanelRect(1).x * 100);
  });

  it("压力夹具不接 API：没有保存入口，任何交互都不产生请求", async () => {
    const storyboardSpy = vi.spyOn(api, "storyboard");
    renderStress();
    const pageElement = await screen.findByTestId("canvas-page");
    stubRect(pageElement, { width: 640, height: 903 });
    expect(screen.queryByRole("button", { name: "保存本页" })).toBeNull();
    expect(storyboardSpy).not.toHaveBeenCalled();

    fireEvent.pointerDown(pageElement, { button: 0, pointerId: 1, clientX: 550, clientY: 278 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 600, clientY: 300 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 600, clientY: 300 });
    fireEvent.wheel(document.querySelector(".canvas-viewport")!, { deltaY: -120, ctrlKey: true });

    expect(saveGeometry).not.toHaveBeenCalled();
    expect(updatePanel).not.toHaveBeenCalled();
  });
});
