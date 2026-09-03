/* eslint-disable @typescript-eslint/no-explicit-any */
// Geometry payloads are asserted loosely in these tests; production code
// validates them against the API contract instead.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type BubbleGeometryShape, type NormalizedRect } from "@/lib/api";

import { StoryboardEditor } from "./index";
import { storyboardCopy } from "./storyboard-copy";

const storyboardQuery = vi.spyOn(api, "storyboard");
const saveGeometry = vi.spyOn(api, "saveStoryboardGeometry");
const updatePanel = vi.spyOn(api, "updatePanel");
const updatePageLayout = vi.spyOn(api, "updatePageLayout");
const updateDialogue = vi.spyOn(api, "updateDialogue");
const createDialogue = vi.spyOn(api, "createDialogue");
const deleteDialogue = vi.spyOn(api, "deleteDialogue");

// Vitest runs with the web app as cwd; the jsdom import.meta.url is not a file URL.
const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");


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

const page2 = { ...page, id: "page-2", page_number: 2 };

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

let data: Record<string, unknown>;

function makeStoryboard() {
  return { page, candidate_count: 0, panels: [panel1, panel2] };
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

function payloadPanel(payload: any, id: string) {
  return payload.panels.find((panel: { panel_id: string }) => panel.panel_id === id) as Record<string, any>;
}

describe("StoryboardEditor canvas (V02-31B)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset();
    storyboardQuery.mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
    updatePanel.mockReset();
    updatePageLayout.mockReset().mockResolvedValue(data as never);
    updateDialogue.mockReset().mockResolvedValue({} as never);
    createDialogue.mockReset().mockResolvedValue({} as never);
    deleteDialogue.mockReset().mockResolvedValue(undefined as never);
  });

  it("S1 单击格：选中轮廓 + 检查器切到该格", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    expect(panelEl("panel-1")).not.toHaveClass("selected");
    fireEvent.pointerDown(panelEl("panel-2"), { button: 0, pointerId: 1, clientX: 500, clientY: 200 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 500, clientY: 200 });
    expect(panelEl("panel-2")).toHaveClass("selected");
    expect(screen.getByText("第二格动作")).toBeTruthy();
  });

  it("S2 拖动矩形格并保存：一次整包 PUT；失败重试复用 request_id；刷新后位置仍在", async () => {
    saveGeometry.mockReset();
    const updated = {
      ...makeStoryboard(),
      panels: [makePanel({ bounds: { x: 0.2, y: 0.1, width: 0.4, height: 0.3 } }), panel2],
    };
    saveGeometry.mockRejectedValueOnce(new Error("网络中断")).mockResolvedValue(updated as never);
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });
    expect(element.style.left).toBe("20%");

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const first = saveGeometry.mock.calls[0][1];
    expect(first.request_id).toBeTruthy();
    expect(first.storyboard_version).toBe(1);
    expect(first.panels).toHaveLength(2);
    expect(payloadPanel(first, "panel-1").bounds.x).toBe(0.2);
    expect(payloadPanel(first, "panel-1").geometry.rect).toEqual(payloadPanel(first, "panel-1").bounds);
    await screen.findByText("网络中断");

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(2));
    expect(saveGeometry.mock.calls[1][1].request_id).toBe(first.request_id);
    // 响应返回的整页快照即新位置（等价于刷新后读取）
    await waitFor(() => expect(panelEl("panel-1").style.left).toBe("20%"));
  });

  it("S3 拖动中 Esc：不发请求，几何回到 drag-start", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    expect(element.style.left).toBe("20%");
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });
    expect(element.style.left).toBe("10%");
    expect(saveGeometry).not.toHaveBeenCalled();
  });

  it("S4 多边形格：无矩形 handle，检查器说明可见", async () => {
    const polygon = makePanel({
      id: "panel-2",
      reading_order: 2,
      bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
      geometry: {
        type: "polygon",
        polygon: [
          { x: 0.55, y: 0.2 },
          { x: 0.9, y: 0.2 },
          { x: 0.7, y: 0.45 },
        ],
        rotation: 0,
        z_order: 2,
      },
      actions: { script_action: "第二格动作" },
    });
    data = { page, candidate_count: 0, panels: [panel1, polygon] };
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    fireEvent.pointerDown(panelEl("panel-2"), { button: 0, pointerId: 1, clientX: 500, clientY: 200 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 500, clientY: 200 });
    expect(panelEl("panel-2")).toHaveClass("polygon-shape");
    expect(panelEl("panel-2")).toHaveClass("selected");
    expect(document.querySelector(".canvas-handle")).toBeNull();
    expect(screen.getAllByText(storyboardCopy.polygonNote).length).toBeGreaterThan(0);
  });

  it("S5 滚轮区缩放/适配/复位为纯前端，不发 PATCH", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    const viewport = document.querySelector(".canvas-viewport")!;
    Object.defineProperty(viewport, "clientWidth", { value: 800, configurable: true });
    Object.defineProperty(viewport, "clientHeight", { value: 600, configurable: true });

    fireEvent.click(screen.getByRole("button", { name: "放大" }));
    expect(screen.getByText("125%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "适配窗口" }));
    expect(screen.getByText("61%")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "复位" }));
    expect(screen.getByText("100%")).toBeTruthy();
    expect(updatePanel).not.toHaveBeenCalled();
    expect(saveGeometry).not.toHaveBeenCalled();
  });

  it("S6 吸附：近边出现对齐线；关闭吸附则无", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 98, clientY: 100 });
    expect(document.querySelector(".canvas-guide-line")).toBeTruthy();
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 98, clientY: 100 });

    fireEvent.click(screen.getByRole("button", { name: "吸附" }));
    fireEvent.pointerDown(element, { button: 0, pointerId: 2, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 2, clientX: 98, clientY: 100 });
    expect(document.querySelector(".canvas-guide-line")).toBeNull();
    fireEvent.pointerUp(window, { pointerId: 2, clientX: 98, clientY: 100 });
  });

  it("S7 方向键 1px / Shift+10px 视口增量写入归一化 bounds", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.keyDown(canvasPage(), { key: "ArrowRight" });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    expect(payloadPanel(saveGeometry.mock.calls[0][1], "panel-1").bounds.x).toBe(0.1016);

    saveGeometry.mockClear();
    fireEvent.keyDown(canvasPage(), { key: "ArrowRight", shiftKey: true });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    expect(payloadPanel(saveGeometry.mock.calls[0][1], "panel-1").bounds.x).toBe(0.1156);
  });

  it("S8 阅读序开关：序号出现/消失，不改 reading_order", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");
    expect(screen.getByText("格 01")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "阅读序" }));
    expect(screen.queryByText("格 01")).toBeNull();
    expect(updatePanel).not.toHaveBeenCalled();
    expect(saveGeometry).not.toHaveBeenCalled();
    expect(panelEl("panel-1").getAttribute("data-reading-order")).toBeNull();
  });

  it("S9 出血/安全区无 canvas 字段：控件 disabled + 说明", async () => {
    data = { page: { ...page, canvas: null }, candidate_count: 0, panels: [panel1, panel2] };
    renderEditor();
    await screen.findByTestId("canvas-page");
    expect(screen.getByRole("button", { name: "出血框" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "安全区" })).toHaveProperty("disabled", true);
    expect(screen.getByText(storyboardCopy.canvasMissing)).toBeTruthy();
  });

  it("S10 多选两格拖动：相对位移，尺寸不变", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    fireEvent.pointerDown(panelEl("panel-1"), { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerDown(panelEl("panel-2"), { button: 0, pointerId: 1, clientX: 448, clientY: 293, shiftKey: true });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 480, clientY: 338 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 480, clientY: 338 });

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const payload = saveGeometry.mock.calls[0][1] as any;
    expect(payloadPanel(payload, "panel-1").bounds).toEqual({ x: 0.15, y: 0.15, width: 0.4, height: 0.3 });
    expect(payloadPanel(payload, "panel-2").bounds).toEqual({ x: 0.6, y: 0.25, width: 0.35, height: 0.25 });
  });

  it("S11 撤销未保存拖拽：回到上一几何，不调用保存", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });
    expect(element.style.left).toBe("20%");

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));
    expect(element.style.left).toBe("10%");
    expect(screen.getByRole("button", { name: "保存本页" })).toHaveProperty("disabled", true);
    expect(saveGeometry).not.toHaveBeenCalled();
  });

  it("S12 保存 409：中文冲突、草稿保留、可放弃并重新加载", async () => {
    saveGeometry.mockReset().mockRejectedValueOnce(new ApiError("分镜版本已变化，请刷新画布后重试", 409));
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));

    await screen.findByRole("alert");
    expect(screen.getByText(storyboardCopy.conflict)).toBeTruthy();
    expect(element.style.left).toBe("20%");

    fireEvent.click(screen.getByRole("button", { name: storyboardCopy.discardReload }));
    await waitFor(() => expect(element.style.left).toBe("10%"));
    expect(screen.queryByText(storyboardCopy.conflict)).toBeNull();
  });

  it("S13 切到生成页有草稿：拦截确认", async () => {
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);
    const element = panelEl("panel-1");
    fireEvent.pointerDown(element, { button: 0, pointerId: 1, clientX: 100, clientY: 100 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 164, clientY: 100 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 164, clientY: 100 });

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const anchor = document.createElement("a");
    anchor.setAttribute("href", "/projects/p1/generate");
    anchor.textContent = "生成页";
    document.body.append(anchor);
    fireEvent.click(anchor);
    expect(confirm).toHaveBeenCalledWith(storyboardCopy.leaveConfirm);
    expect(anchor.href).toBe("http://localhost:3000/projects/p1/generate");
    anchor.remove();
    confirm.mockRestore();
  });

  it("S14 重建 3→5 格：确认后才调用 layout，取消不调用", async () => {
    renderEditor();
    await screen.findByTestId("canvas-page");

    fireEvent.click(screen.getByRole("button", { name: "页菜单" }));
    fireEvent.click(screen.getByRole("menuitem", { name: storyboardCopy.rebuildLayout }));
    const dialog = screen.getByRole("dialog", { name: storyboardCopy.rebuildTitle });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(updatePageLayout).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "页菜单" }));
    fireEvent.click(screen.getByRole("menuitem", { name: storyboardCopy.rebuildLayout }));
    const reopened = screen.getByRole("dialog", { name: storyboardCopy.rebuildTitle });
    fireEvent.click(within(reopened).getByRole("button", { name: "5 格" }));
    fireEvent.click(within(reopened).getByRole("button", { name: storyboardCopy.rebuildConfirm }));
    await waitFor(() => expect(updatePageLayout).toHaveBeenCalledWith("page-1", 5, "dynamic"));
  });

  it("S15 ?page= 与 ?character= 深链仍定位", async () => {
    renderEditor({ pages: [page, page2] as never, initialPageId: "page-2" });
    await screen.findByTestId("canvas-page");
    expect(screen.getAllByText("P.002")[0].closest("button")).toHaveClass("active");
  });

  it("S15b ?character= 自动打开缺服装的出镜格", async () => {
    const withCharacter = makePanel({
      characters: ["char-1"],
      character_presence: { "char-1": "VISIBLE" },
    });
    data = { page, candidate_count: 0, panels: [withCharacter, panel2] };
    renderEditor({ focusCharacterId: "char-1" });
    await screen.findByText(/已定位到缺少服装的出镜格/);
    expect(panelEl("panel-1")).toHaveClass("selected");
  });

  it("S16 画布一个 tab stop，方向键/Tab 在格之间移动", async () => {
    renderEditor();
    const pageElement = await screen.findByTestId("canvas-page");
    expect(panelEl("panel-1").getAttribute("tabindex")).toBeNull();
    fireEvent.keyDown(pageElement, { key: "ArrowRight" });
    expect(panelEl("panel-1")).toHaveClass("selected");
    fireEvent.keyDown(pageElement, { key: "Tab" });
    expect(panelEl("panel-2")).toHaveClass("selected");
    fireEvent.keyDown(pageElement, { key: "Tab", shiftKey: true });
    expect(panelEl("panel-1")).toHaveClass("selected");
    fireEvent.keyDown(pageElement, { key: "Escape" });
    expect(panelEl("panel-1")).not.toHaveClass("selected");
    fireEvent.keyDown(pageElement, { key: "ArrowLeft" });
    expect(panelEl("panel-2")).toHaveClass("selected");
  });

  it("S17 reduced motion：画布过渡与气泡位移被关闭（样式契约）", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\.canvas-panel, \.canvas-bubble/);
    expect(css).toMatch(/\.dialogue-card:hover \{ transform: none; \}/);
  });

  it("S18 900-1099px：检查器底部抽屉，画布仍全宽可见（样式契约）", () => {
    const drawer = css.slice(css.indexOf("@media (max-width: 1099.98px) and (min-width: 900px)"));
    expect(drawer).toContain(".panel-inspector.drawer-open { position: fixed");
    expect(drawer).toContain("bottom: 10px");
    expect(drawer).toContain(".canvas-viewport { min-height: 480px; }");
  });

  it("S19 ≥1280px：画布与检查器并排（样式契约）", () => {
    const wide = css.slice(css.indexOf("@media (min-width: 1280px)"));
    expect(wide).toContain(".storyboard-worktable { grid-template-columns: minmax(320px, 1fr) 10px var(--inspector-width, 390px); }");
  });

  it("S20 气泡几何：拖动/缩放/尾巴进入整包 PUT；旧 region 只读兜底；拖出格外回弹", async () => {
    const withBubble = makePanel({ dialogues: [{
      id: "dlg-1",
      panel_id: "panel-1",
      speaker_character_id: null,
      target_text: "早上好",
      reading_order: 1,
      text_direction: "vertical",
      region: { preferred: "upper_inner" },
      rewrite_forbidden: true,
      bubble: storedBubble,
    }] });
    const withLegacy = makePanel({
      id: "panel-2",
      reading_order: 2,
      bounds: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 },
      geometry: { type: "rect", rect: { x: 0.55, y: 0.2, width: 0.35, height: 0.25 }, rotation: 0, z_order: 2 },
      dialogues: [{
        id: "dlg-2",
        panel_id: "panel-2",
        speaker_character_id: null,
        target_text: " legacy",
        reading_order: 1,
        text_direction: "vertical",
        region: { preferred: "upper_inner" },
        rewrite_forbidden: true,
        bubble: null,
      }],
    });
    data = { page, candidate_count: 0, panels: [withBubble, withLegacy] };
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);

    // 拖动气泡框（保持在本格内）
    fireEvent.pointerDown(bubbleEl("dlg-1"), { button: 0, pointerId: 1, clientX: 160, clientY: 172 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 192, clientY: 172 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 192, clientY: 172 });
    expect(bubbleEl("dlg-1").style.left).toBe("20%");

    // 拖尾巴
    fireEvent.pointerDown(bubbleEl("dlg-1"), { button: 0, pointerId: 2, clientX: 160, clientY: 172 });
    fireEvent.pointerUp(window, { pointerId: 2, clientX: 160, clientY: 172 });
    const tail = document.querySelector('.canvas-handle[data-handle="tail"]')!;
    fireEvent.pointerDown(tail, { button: 0, pointerId: 3, clientX: 192, clientY: Math.round(0.4 * 903) });
    fireEvent.pointerMove(window, { pointerId: 3, clientX: 224, clientY: 406 });
    fireEvent.pointerUp(window, { pointerId: 3, clientX: 224, clientY: 406 });

    fireEvent.click(screen.getByRole("button", { name: "保存本页" }));
    await waitFor(() => expect(saveGeometry).toHaveBeenCalledTimes(1));
    const payload = saveGeometry.mock.calls[0][1] as any;
    const dlg1 = payload.dialogues.find((item: { dialogue_id: string }) => item.dialogue_id === "dlg-1");
    const dlg2 = payload.dialogues.find((item: { dialogue_id: string }) => item.dialogue_id === "dlg-2");
    expect(dlg1.bubble.rect.x).toBe(0.2);
    expect(dlg1.bubble.tail_target.x).toBe(0.35);
    expect(dlg1.bubble).not.toHaveProperty("mapped_from_legacy");
    // 旧 region 只读兜底：未编辑时不落新几何
    expect(dlg2.bubble).toBeNull();
  });

  it("S20b 气泡拖出所属格：回弹并提示，仍属于本格", async () => {
    const withBubble = makePanel({ dialogues: [{
      id: "dlg-1",
      panel_id: "panel-1",
      speaker_character_id: null,
      target_text: "早上好",
      reading_order: 1,
      text_direction: "vertical",
      region: { preferred: "upper_inner" },
      rewrite_forbidden: true,
      bubble: storedBubble,
    }] });
    data = { page, candidate_count: 0, panels: [withBubble, panel2] };
    renderEditor();
    stubRect(await screen.findByTestId("canvas-page"), 640, 903);

    fireEvent.pointerDown(bubbleEl("dlg-1"), { button: 0, pointerId: 1, clientX: 160, clientY: 172 });
    fireEvent.pointerMove(window, { pointerId: 1, clientX: 400, clientY: 172 });
    fireEvent.pointerUp(window, { pointerId: 1, clientX: 400, clientY: 172 });
    expect(screen.getByText(storyboardCopy.bubbleBelongs)).toBeTruthy();
    // 回弹到拖拽前位置；未产生草稿，保存不可用
    expect(bubbleEl("dlg-1").style.left).toBe("15%");
    expect(screen.getByRole("button", { name: "保存本页" })).toHaveProperty("disabled", true);
    expect(saveGeometry).not.toHaveBeenCalled();
  });
});

describe("StoryboardEditor inspector resizer（既有用例）", () => {
  beforeEach(() => {
    window.localStorage.clear();
    data = makeStoryboard();
    storyboardQuery.mockReset().mockImplementation(() => Promise.resolve(data as never));
    saveGeometry.mockReset().mockResolvedValue(data as never);
  });

  it("捕获拖拽指针并持续调整属性面板宽度", async () => {
    renderEditor();
    const separator = await screen.findByRole("separator", { name: "调整属性面板宽度" });
    const worktable = separator.parentElement!;
    let captured = false;
    Object.defineProperties(separator, {
      setPointerCapture: { value: vi.fn(() => { captured = true; }) },
      hasPointerCapture: { value: vi.fn(() => captured) },
      releasePointerCapture: { value: vi.fn(() => { captured = false; }) },
    });
    vi.spyOn(worktable, "getBoundingClientRect").mockReturnValue({
      right: 1000,
    } as DOMRect);

    fireEvent.pointerDown(separator, { pointerId: 7, clientX: 610 });
    fireEvent.pointerMove(separator, { pointerId: 7, clientX: 500 });

    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "500"));
    expect(window.localStorage.getItem("mangaflow.storyboard-inspector-width")).toBe("500");
  });
});
