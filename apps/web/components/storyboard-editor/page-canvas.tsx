"use client";

// Page canvas: viewport transform, hit gestures and page drawing (audit §2/§4).
// Gestures mutate local preview state only; on release they emit a geometry
// command for the editor's undo stack. No canvas library — DOM + pointer events.
//
// V02-32 render strategy for 100-node stress pages (audit §4): past
// HIT_TEST_OBJECT_LIMIT the canvas stops mounting one DOM node per object and
// draws unselected objects as a single SVG vector layer; selection runs
// through a pointer hit-test, and only the selected object mounts DOM handles.
// While a gesture is in flight the preview is painted imperatively onto the
// dragged outline (no React re-render per pointermove); state is written once
// on pointerup.
import type { BubbleGeometryShape, CanvasInfo, MangaPage, NormalizedRect, PanelDialogue, StoryboardPanel } from "@/lib/api";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, ReactNode, RefObject } from "react";

import type { GeometryCommandChange, SoundEffectGeometry } from "./command-stack";
import {
  angleBetween,
  applyResize,
  BASE_PAGE_WIDTH,
  clamp01,
  clampRectInto,
  equalGapSnap,
  isPolygonPanel,
  MIN_BUBBLE_SIZE,
  MIN_PANEL_SIZE,
  MIN_SFX_SIZE,
  normalizeRotation,
  panelGeometry,
  panelZOrder,
  pointInRect,
  rectCenter,
  rectCovers,
  rectsBoundingBox,
  ROTATION_SNAP_DEG,
  sameRect,
  scaleRectWithBounds,
  SNAP_THRESHOLD_PX,
  snapRect,
  snapTargets,
  translateRect,
  type GeometryPoint,
  type SnapGuide,
} from "./geometry";
import { BubbleNode } from "./bubble-node";
import { GuidesOverlay, ReadingOrderOverlay } from "./guides-overlay";
import type { AnnotationStroke } from "./storyboard-history";
import { PanelNode } from "./panel-node";
import { SfxNode, type SfxNodeData } from "./sfx-node";
import { TransformHandles, handlePositions, type HandleName } from "./transform-handles";
import { storyboardCopy } from "./storyboard-copy";

/** Product pages top out at 8 panels + 8 bubbles = 16 objects; only the
 * synthetic stress fixture can exceed this, so anything past the limit renders
 * in vector + hit-test mode. */
export const HIT_TEST_OBJECT_LIMIT = 32;

// Test instrumentation (V02-32): Vitest asserts that pointer gestures do not
// re-render the canvas. Window-scoped so tests can reset it; harmless in
// production where nothing reads it.
type RenderStatsScope = { __pageCanvasRenders?: number };

export function resetCanvasRenderStats() {
  if (typeof window !== "undefined") (window as RenderStatsScope).__pageCanvasRenders = 0;
}

export function canvasRenderCount(): number {
  return typeof window === "undefined" ? 0 : (window as RenderStatsScope).__pageCanvasRenders ?? 0;
}

export interface CanvasBubble {
  dialogue: PanelDialogue;
  panelId: string;
  panelRect: NormalizedRect;
  rect: NormalizedRect;
  shape: BubbleGeometryShape | null;
  legacy: boolean;
  shapeType: "rect" | "ellipse";
}

export type CanvasSelection =
  | { kind: "panels"; ids: string[] }
  | { kind: "bubble"; dialogueId: string }
  | { kind: "sfx"; panelId: string; index: number }
  | null;

type Gesture =
  | { kind: "move-panels"; start: GeometryPoint; origin: Record<string, NormalizedRect>; ids: string[] }
  | { kind: "resize-panel"; handle: HandleName; start: GeometryPoint; origin: NormalizedRect; panelId: string; ratioLock: boolean; fromCenter: boolean }
  | { kind: "resize-panels"; handle: HandleName; start: GeometryPoint; originBbox: NormalizedRect; origins: Record<string, NormalizedRect>; ratioLock: boolean }
  | { kind: "move-bubble"; start: GeometryPoint; origin: BubbleGeometryShape; dialogueId: string; panelRect: NormalizedRect }
  | { kind: "resize-bubble"; handle: HandleName; start: GeometryPoint; origin: BubbleGeometryShape; dialogueId: string; panelRect: NormalizedRect; ratioLock: boolean; fromCenter: boolean }
  | { kind: "rotate-bubble"; center: GeometryPoint; startAngle: number; origin: BubbleGeometryShape; dialogueId: string; snapAngle: boolean }
  | { kind: "move-point"; point: "tail_target" | "anchor"; start: GeometryPoint; origin: BubbleGeometryShape; dialogueId: string }
  | { kind: "move-sfx"; start: GeometryPoint; origin: SoundEffectGeometry; panelId: string; index: number }
  | { kind: "rotate-sfx"; center: GeometryPoint; startAngle: number; origin: SoundEffectGeometry; panelId: string; index: number; snapAngle: boolean }
  | { kind: "scale-sfx"; center: GeometryPoint; startDistance: number; origin: SoundEffectGeometry; panelId: string; index: number };

interface GestureResult {
  panels?: Record<string, NormalizedRect>;
  bbox?: NormalizedRect;
  bubble?: { id: string; shape: BubbleGeometryShape };
  sfx?: { panelId: string; index: number; value: SoundEffectGeometry };
  guides: SnapGuide[];
}

const FULL_PAGE: NormalizedRect = { x: 0, y: 0, width: 1, height: 1 };

const setElementRect = (element: HTMLElement | null | undefined, rect: NormalizedRect) => {
  if (!element) return;
  element.style.left = `${rect.x * 100}%`;
  element.style.top = `${rect.y * 100}%`;
  element.style.width = `${rect.width * 100}%`;
  element.style.height = `${rect.height * 100}%`;
};

const setBubbleTransform = (element: HTMLElement | null | undefined, rotation: number) => {
  if (!element) return;
  element.style.transform = rotation ? `rotate(${rotation}deg)` : "";
};

const setSfxNode = (element: HTMLElement | null | undefined, value: SoundEffectGeometry, fontPx: number) => {
  if (!element) return;
  element.style.left = `${value.x * 100}%`;
  element.style.top = `${value.y * 100}%`;
  element.style.fontSize = `${Math.max(fontPx, 6)}px`;
  element.style.transform = `translate(-50%, -50%) rotate(${value.rotation}deg)`;
};

export function syntheticBubbleShape(bubble: CanvasBubble): BubbleGeometryShape {
  return { type: bubble.shapeType, rect: bubble.rect, rotation: 0 };
}

export function PageCanvas({
  page,
  canvas,
  panels,
  panelRects,
  bubbles,
  sfx,
  zoom,
  viewportRef,
  snapEnabled,
  extraSnapTargets,
  grid,
  showReadingOrder,
  showBleed,
  showSafe,
  interactive,
  selection,
  onCommand,
  onSelectPanels,
  onSelectBubble,
  onSelectSfx,
  onClearSelection,
  onOpenInspector,
  onDeleteBubble,
  onBubbleBounce,
  onNotice,
  onZoomFactor,
  zFlash,
  annotating = false,
  annotations,
  onAnnotateStroke,
  ghosts,
  overlay,
  beforePage,
  afterPage,
}: {
  page: MangaPage;
  canvas: CanvasInfo;
  panels: StoryboardPanel[];
  panelRects: Record<string, NormalizedRect>;
  bubbles: CanvasBubble[];
  sfx: SfxNodeData[];
  zoom: number;
  viewportRef: RefObject<HTMLDivElement | null>;
  snapEnabled: boolean;
  /** Extra snap lines (grid); merged into move/resize targets when present. */
  extraSnapTargets?: { x: number[]; y: number[] } | null;
  /** Grid lines drawn under the objects when the grid toggle is on. */
  grid?: { x: number[]; y: number[] } | null;
  showReadingOrder: boolean;
  showBleed: boolean;
  showSafe: boolean;
  interactive: boolean;
  selection: CanvasSelection;
  onCommand: (label: string, changes: GeometryCommandChange[]) => void;
  onSelectPanels: (ids: string[]) => void;
  onSelectBubble: (dialogueId: string) => void;
  onSelectSfx: (panelId: string, index: number) => void;
  onClearSelection: () => void;
  onOpenInspector: () => void;
  onDeleteBubble: (dialogueId: string) => void;
  onBubbleBounce: () => void;
  onNotice?: (text: string) => void;
  /** 连续缩放：factor 由滚轮 deltaY 指数换算（触控板捏合是密集小步）。 */
  onZoomFactor: (factor: number) => void;
  /** 层序命令的透明度脉冲（对齐原生 PulseZIndex）：token 变化重放一次 z-flash。 */
  zFlash?: { token: number; ids: string[] } | null;
  /** 手绘批注模式：开启后按下拖动画自由笔画，面板/气泡手势停用。 */
  annotating?: boolean;
  /** 本页已存的批注笔画（逐页本地持久化，渲染在对象之上）。 */
  annotations?: AnnotationStroke[];
  onAnnotateStroke?: (points: GeometryPoint[]) => void;
  /** 版本快照对比（V02-33）：幽灵轮廓画在当前画布上，纯展示不响应指针。
   *  A/B 对比时 variant 区分两侧配色（a=紫、b=青）。 */
  ghosts?: { key: string; rect: NormalizedRect; ellipse?: boolean; variant?: "a" | "b" }[] | null;
  /** 覆盖层插槽（回放控制条）：渲染在 canvas-viewport 内、页面之外。 */
  overlay?: ReactNode;
  /** 对开预览插槽：邻页骨架作为页面的左右 flex 邻居挂在 viewport 里，
   * 随页面一起滚动、随 zoom 缩放。 */
  beforePage?: ReactNode;
  afterPage?: ReactNode;
}) {
  const renderCountRef = useRef(0);
  // Runs once per render (no deps): exposes the render count for V02-32 tests
  // that assert gestures do not re-render the canvas. Harmless in production.
  useEffect(() => {
    renderCountRef.current += 1;
    (window as RenderStatsScope).__pageCanvasRenders = renderCountRef.current;
  });
  const pageRef = useRef<HTMLDivElement | null>(null);
  const zoomAnchorRef = useRef<{ fx: number; fy: number; clientX: number; clientY: number } | null>(null);
  const panelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const bubbleRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const sfxRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const handlesRef = useRef<HTMLDivElement | null>(null);
  const snapGuidesRef = useRef<HTMLDivElement | null>(null);
  const sizeLabelRef = useRef<HTMLDivElement | null>(null);
  const copiedGeometryRef = useRef<{ rect: NormalizedRect; rotation?: number } | SoundEffectGeometry | null>(null);
  const [gesture, setGesture] = useState<Gesture | null>(null);
  // 批注笔画草稿：pointermove 期间同步 React state（笔画是低频输入，不像格
  // 子手势那样要求逐帧免重渲染）；松手 commit 给父级持久化。
  const [draftStroke, setDraftStroke] = useState<GeometryPoint[] | null>(null);
  // 手势起点即挂 is-gesturing（不等 React 首帧）：几何过渡压零，命令式逐帧
  // 绘制立即生效；结束态由 gesture state 的 className 维持一致。
  const beginGesture = (next: Gesture) => {
    pageRef.current?.classList.add("is-gesturing");
    // 新手势立即清掉上一段仍在淡出的参考线残影（S6：fresh drag 不背旧拖尾）。
    snapGuidesRef.current?.querySelectorAll(".canvas-guide-line.leaving").forEach((line) => line.remove());
    setGesture(next);
  };
  const zFlashIds = useMemo(() => new Set(zFlash?.ids ?? []), [zFlash]);
  // 批注模式下画布对象不接管指针，否则落笔会顺手拖动面板/气泡。
  const effectiveInteractive = interactive && !annotating;

  const hitTestMode = panels.length + bubbles.length > HIT_TEST_OBJECT_LIMIT;
  const selectedPanelIds = selection?.kind === "panels" ? selection.ids : [];
  const selectedBubbleId = selection?.kind === "bubble" ? selection.dialogueId : null;
  const selectedSfxKey = selection?.kind === "sfx" ? `${selection.panelId}:${selection.index}` : null;
  const selectedPanel = selectedPanelIds.length === 1
    ? panels.find((panel) => panel.id === selectedPanelIds[0]) ?? null
    : null;
  const selectedBubble = bubbles.find((bubble) => bubble.dialogue.id === selectedBubbleId) ?? null;
  const selectedSfx = sfx.find((item) => `${item.panelId}:${item.index}` === selectedSfxKey) ?? null;
  const announcement = selectedPanel
    ? storyboardCopy.selectedAnnouncement(selectedPanel.reading_order, selectedPanel.bleed)
    : selectedBubble
      ? storyboardCopy.bubbleSelectedAnnouncement(selectedBubble.dialogue.reading_order)
      : selectedSfx
        ? storyboardCopy.sfxSelectedAnnouncement(selectedSfx.text)
        : "";

  const pagePixelWidth = () => {
    const rect = pageRef.current?.getBoundingClientRect();
    if (rect && rect.width > 0) return rect.width;
    return BASE_PAGE_WIDTH * zoom;
  };
  const pagePixelHeight = () => {
    const rect = pageRef.current?.getBoundingClientRect();
    if (rect && rect.height > 0) return rect.height;
    return pagePixelWidth() * (canvas.height_mm / canvas.width_mm);
  };
  const pageAspect = () => {
    const width = pagePixelWidth();
    return width > 0 ? pagePixelHeight() / width : 1;
  };
  // Render-safe equivalents of the ref-based metrics above: the page element's
  // size is fully determined by the inline width + aspect-ratio, so render
  // code must compute from props instead of reading pageRef (react-hooks/refs).
  const canvasAspect = canvas.width_mm > 0 ? canvas.height_mm / canvas.width_mm : 1;
  const sfxFontPx = (size: number) => size * BASE_PAGE_WIDTH * zoom * canvasAspect;
  const pointerNorm = (event: { clientX: number; clientY: number }): GeometryPoint => {
    const rect = pageRef.current?.getBoundingClientRect();
    const width = rect && rect.width > 0 ? rect.width : BASE_PAGE_WIDTH * zoom;
    const height = rect && rect.height > 0 ? rect.height : width;
    return {
      x: (event.clientX - (rect?.left ?? 0)) / width,
      y: (event.clientY - (rect?.top ?? 0)) / height,
    };
  };
  const snapThreshold = () => SNAP_THRESHOLD_PX / pagePixelWidth();
  const otherPanelRects = (excludeIds: string[] = []) =>
    panels.filter((panel) => !excludeIds.includes(panel.id)).map((panel) => panelRects[panel.id]).filter(Boolean);

  const mergedSnapTargets = (excludeRect: NormalizedRect | null, others: NormalizedRect[]) => {
    const targets = snapTargets(excludeRect, others);
    if (extraSnapTargets) {
      targets.x.push(...extraSnapTargets.x);
      targets.y.push(...extraSnapTargets.y);
    }
    return targets;
  };

  const computeResult = (active: Gesture, pointer: GeometryPoint): GestureResult => {
    switch (active.kind) {
      case "move-panels": {
        const primaryId = active.ids[0];
        const dx = pointer.x - active.start.x;
        const dy = pointer.y - active.start.y;
        const moved: Record<string, NormalizedRect> = {};
        for (const id of active.ids) moved[id] = translateRect(active.origin[id], dx, dy);
        let guides: SnapGuide[] = [];
        if (snapEnabled) {
          // Edge snap keeps the old contract: the primary rect snaps against
          // every panel except itself — other selected panels count too, since
          // they sit at their origin for the whole gesture.
          const snapped = snapRect(
            moved[primaryId],
            mergedSnapTargets(active.origin[primaryId], otherPanelRects([primaryId])),
            snapThreshold(),
          );
          // Equal-gap ("smart spacing") only makes sense for a single moving
          // panel sitting between two neighbours; group moves skip it, and
          // fellow selection members are excluded from the gap sides.
          const gapSnap = active.ids.length === 1
            ? equalGapSnap(
              snapped.rect,
              otherPanelRects(active.ids),
              snapThreshold(),
              { x: Boolean(snapped.guides.some((guide) => guide.axis === "x")), y: Boolean(snapped.guides.some((guide) => guide.axis === "y")) },
            )
            : { deltaX: 0, deltaY: 0, guides: [] as SnapGuide[] };
          const fixX = snapped.rect.x - moved[primaryId].x + gapSnap.deltaX;
          const fixY = snapped.rect.y - moved[primaryId].y + gapSnap.deltaY;
          for (const id of active.ids) moved[id] = translateRect(active.origin[id], dx + fixX, dy + fixY);
          guides = [...snapped.guides, ...gapSnap.guides];
        }
        return { panels: moved, guides };
      }
      case "resize-panel": {
        const resized = applyResize(active.origin, active.handle, pointer, {
          ratioLock: active.ratioLock,
          fromCenter: active.fromCenter,
          minSize: MIN_PANEL_SIZE,
        });
        if (!snapEnabled) return { panels: { [active.panelId]: resized }, guides: [] };
        const snapped = snapRect(
          resized,
          mergedSnapTargets(active.origin, otherPanelRects([active.panelId])),
          snapThreshold(),
        );
        return { panels: { [active.panelId]: snapped.rect }, guides: snapped.guides };
      }
      case "resize-panels": {
        const resizedBbox = applyResize(active.originBbox, active.handle, pointer, {
          ratioLock: active.ratioLock,
          fromCenter: false,
          minSize: MIN_PANEL_SIZE,
        });
        let nextBbox = resizedBbox;
        let guides: SnapGuide[] = [];
        if (snapEnabled) {
          const snapped = snapRect(
            resizedBbox,
            mergedSnapTargets(active.originBbox, otherPanelRects(Object.keys(active.origins))),
            snapThreshold(),
          );
          nextBbox = snapped.rect;
          guides = snapped.guides;
        }
        const scaled: Record<string, NormalizedRect> = {};
        for (const [id, origin] of Object.entries(active.origins)) {
          scaled[id] = scaleRectWithBounds(origin, active.originBbox, nextBbox, MIN_PANEL_SIZE);
        }
        return { panels: scaled, bbox: nextBbox, guides };
      }
      case "move-bubble": {
        const dx = pointer.x - active.start.x;
        const dy = pointer.y - active.start.y;
        const rect = clampRectInto(translateRect(active.origin.rect, dx, dy), FULL_PAGE);
        return { bubble: { id: active.dialogueId, shape: { ...active.origin, rect } }, guides: [] };
      }
      case "resize-bubble": {
        const rect = clampRectInto(
          applyResize(active.origin.rect, active.handle, pointer, {
            ratioLock: active.ratioLock,
            fromCenter: active.fromCenter,
            minSize: MIN_BUBBLE_SIZE,
          }),
          active.panelRect,
        );
        return { bubble: { id: active.dialogueId, shape: { ...active.origin, rect } }, guides: [] };
      }
      case "rotate-bubble": {
        const aspect = pageAspect();
        const delta = angleBetween(active.center, pointer, aspect) - active.startAngle;
        let rotation = normalizeRotation(active.origin.rotation + delta);
        if (active.snapAngle) rotation = Math.round(rotation / ROTATION_SNAP_DEG) * ROTATION_SNAP_DEG;
        return { bubble: { id: active.dialogueId, shape: { ...active.origin, rotation } }, guides: [] };
      }
      case "move-point": {
        const point = { x: clamp01(pointer.x), y: clamp01(pointer.y) };
        return {
          bubble: { id: active.dialogueId, shape: { ...active.origin, [active.point]: point } },
          guides: [],
        };
      }
      case "move-sfx": {
        const dx = pointer.x - active.start.x;
        const dy = pointer.y - active.start.y;
        return {
          sfx: {
            panelId: active.panelId,
            index: active.index,
            value: { ...active.origin, x: clamp01(active.origin.x + dx), y: clamp01(active.origin.y + dy) },
          },
          guides: [],
        };
      }
      case "rotate-sfx": {
        const aspect = pageAspect();
        const delta = angleBetween(active.center, pointer, aspect) - active.startAngle;
        let rotation = normalizeRotation(active.origin.rotation + delta);
        if (active.snapAngle) rotation = Math.round(rotation / ROTATION_SNAP_DEG) * ROTATION_SNAP_DEG;
        return {
          sfx: {
            panelId: active.panelId,
            index: active.index,
            value: { ...active.origin, rotation },
          },
          guides: [],
        };
      }
      case "scale-sfx": {
        const aspect = pageAspect();
        const dx = pointer.x - active.center.x;
        const dy = (pointer.y - active.center.y) * aspect;
        const distance = Math.hypot(dx, dy);
        const size = active.startDistance > 1e-6
          ? active.origin.size * (distance / active.startDistance)
          : active.origin.size;
        return {
          sfx: {
            panelId: active.panelId,
            index: active.index,
            value: { ...active.origin, size: Math.min(1, Math.max(MIN_SFX_SIZE, size)) },
          },
          guides: [],
        };
      }
    }
  };

  const commitGesture = (active: Gesture, pointer: GeometryPoint) => {
    const result = computeResult(active, pointer);
    if (active.kind === "move-panels" || active.kind === "resize-panel" || active.kind === "resize-panels") {
      const ids = active.kind === "resize-panel" ? [active.panelId] : Object.keys(result.panels ?? {});
      const changes: GeometryCommandChange[] = [];
      for (const id of ids) {
        const after = result.panels?.[id];
        const before = active.kind === "resize-panel" ? active.origin : active.kind === "move-panels" ? active.origin[id] : active.origins[id];
        if (!after || !before || sameRect(before, after)) continue;
        changes.push({ kind: "panel", id, before, after });
      }
      if (changes.length) {
        onCommand(
          active.kind === "move-panels" ? "拖动格子" : active.kind === "resize-panels" ? "缩放多格" : "缩放格子",
          changes,
        );
      }
      return;
    }
    if (result.sfx) {
      const { panelId, index, value } = result.sfx;
      const before = active.origin as SoundEffectGeometry;
      if (JSON.stringify(before) === JSON.stringify(value)) return;
      onCommand(
        active.kind === "move-sfx" ? "移动拟声词" : active.kind === "rotate-sfx" ? "旋转拟声词" : "缩放拟声词",
        [{ kind: "sfx", panelId, index, before, after: value }],
      );
      return;
    }
    if (!result.bubble) return;
    const ownerRect = active.kind === "move-bubble" || active.kind === "resize-bubble" ? active.panelRect : null;
    if (active.kind === "move-bubble" && ownerRect && !rectCovers(result.bubble.shape.rect, ownerRect)) {
      // 拖出所属格：回弹到拖拽前位置并提示（不跨格移动）。
      onBubbleBounce();
      return;
    }
    // sfx gestures returned above; the remaining kinds all carry a bubble shape.
    const before = active.origin as BubbleGeometryShape;
    if (JSON.stringify(before) === JSON.stringify(result.bubble.shape)) return;
    onCommand(
      active.kind === "move-bubble"
        ? "拖动气泡"
        : active.kind === "resize-bubble"
          ? "缩放气泡"
          : active.kind === "rotate-bubble"
            ? "旋转气泡"
            : "调整气泡尾巴",
      [{ kind: "bubble", id: result.bubble.id, before, after: result.bubble.shape }],
    );
  };

  // --- imperative gesture painting (audit §4: no per-pointermove re-render) -

  const paintHandles = (
    rect: NormalizedRect | null,
    kind: "panel" | "bubble",
    anchor?: { x: number; y: number } | null,
    tailTarget?: { x: number; y: number } | null,
    rotation = 0,
  ) => {
    const container = handlesRef.current;
    if (!container || !rect) return;
    const positions = handlePositions(rect, kind, anchor, tailTarget, rotation, pageAspect());
    for (const element of Array.from(container.querySelectorAll<HTMLElement>("[data-handle]"))) {
      const position = positions.get(element.dataset.handle ?? "");
      if (!position) continue;
      element.style.left = `${position.left * 100}%`;
      element.style.top = `${position.top * 100}%`;
    }
  };

  // 参考线复用式淡入淡出（对齐原生 ShowGuide 签名复用）：按签名保留存活线避免
  // 吸附稳定时闪断；新线播 guide-in 淡入；移除的线挂 .leaving 淡出后摘除。
  const paintGuides = (guides: SnapGuide[]) => {
    const layer = snapGuidesRef.current;
    if (!layer) return;
    const spare = new Map<string, HTMLElement>();
    for (const line of Array.from(layer.querySelectorAll<HTMLElement>(".canvas-guide-line"))) {
      spare.set(line.dataset.guide ?? "", line);
    }
    for (const guide of guides) {
      const signature = `${guide.axis}:${guide.kind ?? "edge"}:${guide.at.toFixed(4)}`;
      let line = spare.get(signature);
      if (!line) {
        line = document.createElement("div");
        line.className = `canvas-guide-line ${guide.axis === "x" ? "vertical" : "horizontal"}${guide.kind === "gap" ? " gap" : ""}`;
        line.dataset.guide = signature;
        layer.appendChild(line);
      } else {
        line.classList.remove("leaving");
      }
      if (guide.axis === "x") {
        line.style.left = `${guide.at * 100}%`;
        line.style.top = "0";
        line.style.bottom = "0";
      } else {
        line.style.top = `${guide.at * 100}%`;
        line.style.left = "0";
        line.style.right = "0";
      }
      spare.delete(signature);
    }
    for (const leftover of spare.values()) {
      leftover.classList.add("leaving");
      // 复活的线会再播一次淡入 transitionend——只有仍挂 .leaving 的才摘除。
      leftover.addEventListener("transitionend", () => {
        if (leftover.classList.contains("leaving")) leftover.remove();
      }, { once: true });
      // transitionend 兜底：reduced-motion/隐藏页签不派发事件，限时摘除。
      setTimeout(() => {
        if (leftover.classList.contains("leaving")) leftover.remove();
      }, 260);
    }
  };

  const paintSizeLabel = (text: string | null, at?: { x: number; y: number }) => {
    const label = sizeLabelRef.current;
    if (!label) return;
    if (!text || !at) {
      label.style.display = "none";
      return;
    }
    label.style.display = "";
    label.textContent = text;
    label.style.left = `${Math.min(at.x + 0.01, 0.98) * 100}%`;
    label.style.top = `${Math.min(at.y + 0.005, 0.98) * 100}%`;
  };

  const rectSizeLabel = (rect: NormalizedRect) =>
    `${(rect.width * canvas.width_mm).toFixed(1)} × ${(rect.height * canvas.height_mm).toFixed(1)} mm`;

  const paintGesture = (active: Gesture, result: GestureResult) => {
    if (result.panels) {
      for (const [id, rect] of Object.entries(result.panels)) setElementRect(panelRefs.current[id], rect);
      const primary = active.kind === "resize-panels"
        ? result.bbox ?? null
        : result.panels[active.kind === "move-panels" ? active.ids[0] : active.kind === "resize-panel" ? active.panelId : ""];
      paintHandles(primary, "panel");
      paintGuides(result.guides);
      const labelRect = active.kind === "resize-panels" ? result.bbox : primary;
      paintSizeLabel(labelRect ? rectSizeLabel(labelRect) : null, labelRect ? { x: labelRect.x + labelRect.width, y: labelRect.y + labelRect.height } : undefined);
      return;
    }
    if (result.bubble) {
      const element = bubbleRefs.current[result.bubble.id];
      setElementRect(element, result.bubble.shape.rect);
      setBubbleTransform(element, result.bubble.shape.rotation ?? 0);
      paintHandles(
        result.bubble.shape.rect,
        "bubble",
        result.bubble.shape.anchor ?? null,
        result.bubble.shape.tail_target ?? null,
        result.bubble.shape.rotation ?? 0,
      );
      paintSizeLabel(
        active.kind === "rotate-bubble"
          ? `${Math.round(result.bubble.shape.rotation)}°`
          : rectSizeLabel(result.bubble.shape.rect),
        { x: result.bubble.shape.rect.x + result.bubble.shape.rect.width, y: result.bubble.shape.rect.y + result.bubble.shape.rect.height },
      );
    }
    if (result.sfx) {
      const element = sfxRefs.current[`${result.sfx.panelId}:${result.sfx.index}`];
      setSfxNode(element, result.sfx.value, sfxFontPx(result.sfx.value.size));
      paintSizeLabel(
        active.kind === "rotate-sfx" ? `${Math.round(result.sfx.value.rotation)}°` : `${(result.sfx.value.size * canvas.height_mm).toFixed(1)} mm`,
        { x: result.sfx.value.x, y: result.sfx.value.y },
      );
    }
    paintGuides(result.guides);
  };

  // Reset the dragged outlines to the last rendered (state) geometry. Called on
  // every gesture end: after a commit React immediately repaints the new
  // drafts on top of this, and after a cancel/bounce this is the restore.
  const restoreAfterGesture = () => {
    for (const [id, rect] of Object.entries(panelRects)) setElementRect(panelRefs.current[id], rect);
    for (const bubble of bubbles) {
      const element = bubbleRefs.current[bubble.dialogue.id];
      setElementRect(element, bubble.rect);
      setBubbleTransform(element, bubble.shape?.rotation ?? 0);
    }
    for (const item of sfx) {
      setSfxNode(
        sfxRefs.current[`${item.panelId}:${item.index}`],
        { x: item.x, y: item.y, rotation: item.rotation, size: item.size },
        sfxFontPx(item.size),
      );
    }
    const handleRect = selectedPanel
      ? panelRects[selectedPanel.id] ?? null
      : selectedBubble?.rect ?? (selectedPanelIds.length > 1 ? rectsBoundingBox(selectedPanelIds.map((id) => panelRects[id]).filter(Boolean)) : null);
    paintHandles(
      handleRect,
      selectedBubble ? "bubble" : "panel",
      selectedBubble?.shape?.anchor ?? null,
      selectedBubble?.shape?.tail_target ?? null,
      selectedBubble?.shape?.rotation ?? 0,
    );
    paintGuides([]);
    paintSizeLabel(null);
  };

  // Ctrl+wheel zoom must be a native non-passive listener: React 17+ attaches
  // wheel passively at the root, so preventDefault() inside onWheel is a
  // no-op and the browser's page zoom fights the canvas zoom (same fix as
  // local-edit-workspace).
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      // 记录光标下的页面归一化点；zoom 应用后由下方 layout effect 滚动补偿，
      // 实现「以光标为锚点」的缩放。
      const pageEl = pageRef.current;
      if (pageEl) {
        const rect = pageEl.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0) {
          zoomAnchorRef.current = {
            fx: (event.clientX - rect.left) / rect.width,
            fy: (event.clientY - rect.top) / rect.height,
            clientX: event.clientX,
            clientY: event.clientY,
          };
        }
      }
      onZoomFactor(Math.exp(-event.deltaY * 0.0016));
    };
    viewport.addEventListener("wheel", onWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", onWheel);
  }, [onZoomFactor, viewportRef]);

  // zoom 落入 DOM 后校正滚动：让锚点保持在光标原位。页面用 margin:auto
  // 居中，没有滚动空间时补偿被 scrollLeft/Top 自然钳制，退化为居中缩放。
  useLayoutEffect(() => {
    const anchor = zoomAnchorRef.current;
    zoomAnchorRef.current = null;
    if (!anchor) return;
    const viewport = viewportRef.current;
    const pageEl = pageRef.current;
    if (!viewport || !pageEl) return;
    const rect = pageEl.getBoundingClientRect();
    viewport.scrollLeft += rect.left + anchor.fx * rect.width - anchor.clientX;
    viewport.scrollTop += rect.top + anchor.fy * rect.height - anchor.clientY;
  }, [zoom, viewportRef]);

  useEffect(() => {
    if (!gesture) return;
    const move = (event: PointerEvent) => paintGesture(gesture, computeResult(gesture, pointerNorm(event)));
    const finish = (event: PointerEvent) => {
      commitGesture(gesture, pointerNorm(event));
      setGesture(null);
      restoreAfterGesture();
      pageRef.current?.classList.remove("is-gesturing");
    };
    const cancel = () => {
      setGesture(null);
      restoreAfterGesture();
      pageRef.current?.classList.remove("is-gesturing");
    };
    const key = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      cancel();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", cancel);
    window.addEventListener("keydown", key, true);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancel);
      window.removeEventListener("keydown", key, true);
    };
    // Gesture-local closures: panel/rect/bubble inputs are stable while a
    // pointer gesture is in flight because drafts only change on commit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gesture]);

  const startPanelMove = (panel: StoryboardPanel, event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !interactive) return;
    event.stopPropagation();
    const additive = event.shiftKey;
    const selectionIds = additive && selectedPanelIds.length
      ? (selectedPanelIds.includes(panel.id) ? selectedPanelIds : [...selectedPanelIds, panel.id])
      : [panel.id];
    onSelectPanels(selectionIds);
    // 多边形格只读：可以保持选中，但绝不进入移动组（多选拖动不得改写其 bounds）。
    const movableIds = panels
      .filter((item) => selectionIds.includes(item.id) && !isPolygonPanel(item) && panelRects[item.id])
      .map((item) => item.id);
    if (!movableIds.length) return;
    const origin: Record<string, NormalizedRect> = {};
    for (const id of movableIds) origin[id] = panelRects[id];
    beginGesture({ kind: "move-panels", start: pointerNorm(event), origin, ids: movableIds });
  };

  const startPanelResize = (panelId: string, handle: HandleName, event: ReactPointerEvent<HTMLDivElement>) => {
    const origin = panelRects[panelId];
    if (!origin || !interactive) return;
    beginGesture({
      kind: "resize-panel",
      handle,
      start: pointerNorm(event),
      origin,
      panelId,
      ratioLock: event.shiftKey,
      fromCenter: event.altKey,
    });
  };

  const startGroupResize = (handle: HandleName, event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    const movableIds = selectedPanelIds.filter((id) => {
      const panel = panels.find((item) => item.id === id);
      return panel && !isPolygonPanel(panel) && panelRects[id];
    });
    if (movableIds.length < 2) return;
    const origins: Record<string, NormalizedRect> = {};
    for (const id of movableIds) origins[id] = panelRects[id];
    const originBbox = rectsBoundingBox(Object.values(origins));
    if (!originBbox) return;
    beginGesture({
      kind: "resize-panels",
      handle,
      start: pointerNorm(event),
      originBbox,
      origins,
      ratioLock: event.shiftKey,
    });
  };

  const startBubbleMove = (bubble: CanvasBubble, event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !interactive) return;
    event.stopPropagation();
    onSelectBubble(bubble.dialogue.id);
    beginGesture({
      kind: "move-bubble",
      start: pointerNorm(event),
      origin: bubble.shape ?? syntheticBubbleShape(bubble),
      dialogueId: bubble.dialogue.id,
      panelRect: bubble.panelRect,
    });
  };

  const startBubbleResize = (bubble: CanvasBubble, handle: HandleName, event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    beginGesture({
      kind: "resize-bubble",
      handle,
      start: pointerNorm(event),
      origin: bubble.shape ?? syntheticBubbleShape(bubble),
      dialogueId: bubble.dialogue.id,
      panelRect: bubble.panelRect,
      ratioLock: event.shiftKey,
      fromCenter: event.altKey,
    });
  };

  const startBubbleRotate = (bubble: CanvasBubble, event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    const shape = bubble.shape ?? syntheticBubbleShape(bubble);
    const center = rectCenter(shape.rect);
    const pointer = pointerNorm(event);
    beginGesture({
      kind: "rotate-bubble",
      center,
      startAngle: angleBetween(center, pointer, pageAspect()) - (shape.rotation ?? 0),
      origin: shape,
      dialogueId: bubble.dialogue.id,
      snapAngle: event.shiftKey,
    });
  };

  const startBubblePoint = (bubble: CanvasBubble, point: "tail_target" | "anchor", event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    beginGesture({
      kind: "move-point",
      point,
      start: pointerNorm(event),
      origin: bubble.shape ?? syntheticBubbleShape(bubble),
      dialogueId: bubble.dialogue.id,
    });
  };

  const sfxGeometry = (item: SfxNodeData): SoundEffectGeometry => ({
    x: item.x,
    y: item.y,
    rotation: item.rotation,
    size: item.size,
  });

  const startSfxMove = (item: SfxNodeData, event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !interactive) return;
    event.stopPropagation();
    onSelectSfx(item.panelId, item.index);
    beginGesture({
      kind: "move-sfx",
      start: pointerNorm(event),
      origin: sfxGeometry(item),
      panelId: item.panelId,
      index: item.index,
    });
  };

  const startSfxHandle = (item: SfxNodeData, handle: "rotate" | "scale", event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    const pointer = pointerNorm(event);
    const origin = sfxGeometry(item);
    const center = { x: origin.x, y: origin.y };
    if (handle === "rotate") {
      beginGesture({
        kind: "rotate-sfx",
        center,
        startAngle: angleBetween(center, pointer, pageAspect()) - origin.rotation,
        origin,
        panelId: item.panelId,
        index: item.index,
        snapAngle: event.shiftKey,
      });
      return;
    }
    const dx = pointer.x - center.x;
    const dy = (pointer.y - center.y) * pageAspect();
    beginGesture({
      kind: "scale-sfx",
      center,
      startDistance: Math.max(Math.hypot(dx, dy), 1e-4),
      origin,
      panelId: item.panelId,
      index: item.index,
    });
  };

  const copySelectionGeometry = () => {
    if (selectedPanel && !isPolygonPanel(selectedPanel)) {
      const rect = panelRects[selectedPanel.id];
      if (rect) {
        copiedGeometryRef.current = { rect: { ...rect } };
        onNotice?.(`${storyboardCopy.copyGeometry}：${storyboardCopy.layerPage}`);
      }
      return;
    }
    if (selectedBubble) {
      copiedGeometryRef.current = {
        rect: { ...selectedBubble.rect },
        rotation: selectedBubble.shape?.rotation ?? 0,
      };
      onNotice?.(`${storyboardCopy.copyGeometry}：气泡`);
      return;
    }
    if (selectedSfx) {
      copiedGeometryRef.current = { ...sfxGeometry(selectedSfx) };
      onNotice?.(`${storyboardCopy.copyGeometry}：${selectedSfx.text}`);
    }
  };

  const pasteSelectionGeometry = () => {
    const copied = copiedGeometryRef.current;
    if (!copied || !interactive) return;
    const changes: GeometryCommandChange[] = [];
    if (selectedPanelIds.length && "rect" in copied) {
      for (const id of selectedPanelIds) {
        const panel = panels.find((item) => item.id === id);
        if (!panel || isPolygonPanel(panel)) continue;
        const before = panelRects[id];
        if (!before || sameRect(before, copied.rect)) continue;
        changes.push({ kind: "panel", id, before, after: { ...copied.rect } });
      }
      if (changes.length) onCommand(storyboardCopy.pasteGeometry, changes);
      return;
    }
    if (selectedBubble && "rect" in copied) {
      const before = selectedBubble.shape ?? syntheticBubbleShape(selectedBubble);
      const after = { ...before, rect: { ...copied.rect }, rotation: copied.rotation ?? before.rotation };
      if (JSON.stringify(before) !== JSON.stringify(after)) {
        onCommand(storyboardCopy.pasteGeometry, [{ kind: "bubble", id: selectedBubble.dialogue.id, before, after }]);
      }
      return;
    }
    if (selectedSfx) {
      const before = sfxGeometry(selectedSfx);
      const after: SoundEffectGeometry = "rect" in copied
        ? { ...before, x: copied.rect.x + copied.rect.width / 2, y: copied.rect.y + copied.rect.height / 2, rotation: copied.rotation ?? before.rotation }
        : { ...copied };
      if (JSON.stringify(before) !== JSON.stringify(after)) {
        onCommand(storyboardCopy.pasteGeometry, [{ kind: "sfx", panelId: selectedSfx.panelId, index: selectedSfx.index, before, after }]);
      }
    }
  };

  const handleCanvasKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (gesture) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onClearSelection();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "c" || event.key === "C")) {
      copySelectionGeometry();
      return;
    }
    if ((event.ctrlKey || event.metaKey) && (event.key === "v" || event.key === "V")) {
      event.preventDefault();
      if (selection) pasteSelectionGeometry();
      else onNotice?.(storyboardCopy.pasteEmptyNotice);
      return;
    }
    if (event.key === "Enter" && selectedPanel) {
      event.preventDefault();
      onOpenInspector();
      return;
    }
    // 气泡删除是版本化写入：几何/叙事保存在途（interactive=false）时键盘
    // Delete 与指针手势一样不发起，避免并发写制造虚假 409。
    if ((event.key === "Delete" || event.key === "Backspace") && selectedBubble && interactive) {
      event.preventDefault();
      onDeleteBubble(selectedBubble.dialogue.id);
      return;
    }
    if (event.key === "Tab") {
      // 画布只有一个 tab stop；Tab/Shift+Tab 在格之间移动（audit §5）。
      // 到达首/末格时放行，让焦点回到页面正常 Tab 序，避免键盘困死在画布内。
      if (navigatePanels(event.shiftKey ? -1 : 1)) {
        event.preventDefault();
      }
      return;
    }
    if (event.key.startsWith("Arrow")) {
      // 方向键微调与指针手势/Delete 同受 interactive 门禁（#637）：几何保存在
      // 途时微调命令会被保存成功的 clearGeometryDrafts 一并清掉，静默丢失。
      // 与 Delete 分支一致：不拦截不冒泡，键盘保持默认行为。
      if (!interactive) return;
      event.preventDefault();
      if (!selection) {
        navigatePanels(event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1);
        return;
      }
      const px = event.shiftKey ? 10 : 1;
      const dx = event.key === "ArrowRight" ? px : event.key === "ArrowLeft" ? -px : 0;
      const dy = event.key === "ArrowDown" ? px : event.key === "ArrowUp" ? -px : 0;
      if (!dx && !dy) return;
      const nx = dx / pagePixelWidth();
      const ny = dy / pagePixelHeight();
      if (selectedPanelIds.length) {
        const changes: GeometryCommandChange[] = [];
        for (const id of selectedPanelIds) {
          const panel = panels.find((item) => item.id === id);
          if (!panel || isPolygonPanel(panel)) continue; // 多边形格只读：方向键不改写 bounds
          const before = panelRects[id];
          if (!before) continue;
          changes.push({ kind: "panel", id, before, after: translateRect(before, nx, ny) });
        }
        // 仅多边形格被选中时不产生任何几何变更。
        if (changes.length) onCommand("方向键微调", changes);
        return;
      }
      if (selectedBubble) {
        const origin = selectedBubble.shape ?? syntheticBubbleShape(selectedBubble);
        const rect = clampRectInto(translateRect(origin.rect, nx, ny), selectedBubble.panelRect);
        onCommand("方向键微调", [{ kind: "bubble", id: selectedBubble.dialogue.id, before: origin, after: { ...origin, rect } }]);
        return;
      }
      if (selectedSfx) {
        const before = sfxGeometry(selectedSfx);
        const after = { ...before, x: clamp01(before.x + nx), y: clamp01(before.y + ny) };
        onCommand("方向键微调", [{ kind: "sfx", panelId: selectedSfx.panelId, index: selectedSfx.index, before, after }]);
      }
    }
  };

  const navigatePanels = (step: 1 | -1): boolean => {
    if (!panels.length) return false;
    const currentIndex = selectedPanelIds.length
      ? panels.findIndex((panel) => panel.id === selectedPanelIds[selectedPanelIds.length - 1])
      : -1;
    const nextIndex = currentIndex === -1 ? (step === 1 ? 0 : panels.length - 1) : Math.min(Math.max(currentIndex + step, 0), panels.length - 1);
    const next = panels[nextIndex];
    if (!next) return false;
    onSelectPanels([next.id]);
    // Report whether focus moved so Tab at the boundary can fall through to the
    // page tab order instead of trapping keyboard users inside the canvas.
    return nextIndex !== currentIndex || currentIndex === -1;
  };

  // --- hit-testing (audit §4: unselected objects carry no DOM nodes) --------

  const hitTest = (point: GeometryPoint): { kind: "panel" | "bubble"; id: string } | null => {
    // Bubbles paint above panels; later array order paints above earlier.
    for (let index = bubbles.length - 1; index >= 0; index--) {
      const bubble = bubbles[index];
      if (pointInRect(point, bubble.rect)) return { kind: "bubble", id: bubble.dialogue.id };
    }
    let bestIndex = -1;
    let bestZ = -Infinity;
    for (let index = 0; index < panels.length; index++) {
      const rect = panelRects[panels[index].id];
      if (!rect || !pointInRect(point, rect)) continue;
      const z = panelZOrder(panels[index]);
      if (z >= bestZ) {
        bestZ = z;
        bestIndex = index;
      }
    }
    return bestIndex === -1 ? null : { kind: "panel", id: panels[bestIndex].id };
  };

  const pagePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button === 1) {
      const viewport = viewportRef.current;
      if (!viewport) return;
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      const { scrollLeft, scrollTop } = viewport;
      const drag = (moveEvent: PointerEvent) => {
        viewport.scrollLeft = scrollLeft - (moveEvent.clientX - startX);
        viewport.scrollTop = scrollTop - (moveEvent.clientY - startY);
      };
      const stop = () => {
        window.removeEventListener("pointermove", drag);
        window.removeEventListener("pointerup", stop);
      };
      window.addEventListener("pointermove", drag);
      window.addEventListener("pointerup", stop);
      return;
    }
    if (event.button !== 0) return;
    // 批注模式优先于选中/手势：按下开始记点，移动追加，松手提交一笔。
    if (annotating) {
      event.preventDefault();
      const points: GeometryPoint[] = [{ x: clamp01(pointerNorm(event).x), y: clamp01(pointerNorm(event).y) }];
      setDraftStroke(points.slice());
      const move = (moveEvent: PointerEvent) => {
        const point = pointerNorm(moveEvent);
        points.push({ x: clamp01(point.x), y: clamp01(point.y) });
        setDraftStroke(points.slice());
      };
      const stop = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", stop);
        setDraftStroke(null);
        if (points.length >= 2) onAnnotateStroke?.(points);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", stop);
      return;
    }
    if (!hitTestMode) {
      onClearSelection();
      return;
    }
    const hit = hitTest(pointerNorm(event));
    if (!hit) {
      onClearSelection();
      return;
    }
    if (hit.kind === "bubble") {
      const bubble = bubbles.find((item) => item.dialogue.id === hit.id);
      if (bubble) startBubbleMove(bubble, event);
      return;
    }
    const panel = panels.find((item) => item.id === hit.id);
    if (panel) startPanelMove(panel, event);
  };

  const pageDoubleClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!hitTestMode) return; // normal mode: nodes handle their own double click
    const hit = hitTest(pointerNorm(event));
    if (!hit) return;
    if (hit.kind === "bubble") {
      onSelectBubble(hit.id);
      return;
    }
    onSelectPanels([hit.id]);
    onOpenInspector();
  };

  const bleedInset = canvas.bleed_mm > 0 ? { x: canvas.bleed_mm / canvas.width_mm, y: canvas.bleed_mm / canvas.height_mm } : null;
  const safeInset = canvas.safe_mm > 0 ? { x: canvas.safe_mm / canvas.width_mm, y: canvas.safe_mm / canvas.height_mm } : null;
  const activeDescendant = selectedPanel
    ? `canvas-panel-${selectedPanel.id}`
    : selectedBubble
      ? `canvas-bubble-${selectedBubble.dialogue.id}`
      : selectedSfx
        ? `canvas-sfx-${selectedSfx.panelId}-${selectedSfx.index}`
        : undefined;
  const groupBbox = selectedPanelIds.length > 1
    ? rectsBoundingBox(
      selectedPanelIds
        .filter((id) => {
          const panel = panels.find((item) => item.id === id);
          return panel && !isPolygonPanel(panel);
        })
        .map((id) => panelRects[id])
        .filter(Boolean),
    )
    : null;
  const selectionRect = selectedPanel
    ? panelRects[selectedPanel.id] ?? null
    : selectedBubble
      ? selectedBubble.rect
      : null;

  return <div
    className="canvas-viewport"
    ref={viewportRef}
  >
    {beforePage}
    <div
      ref={pageRef}
      data-testid="canvas-page"
      className={`canvas-page${gesture ? " is-gesturing" : ""}${annotating ? " annotating" : ""}`}
      role="group"
      aria-label={storyboardCopy.canvasLabel}
      tabIndex={0}
      aria-activedescendant={activeDescendant}
      onKeyDown={handleCanvasKeyDown}
      onPointerDown={pagePointerDown}
      onDoubleClick={pageDoubleClick}
      style={{
        width: `${BASE_PAGE_WIDTH * zoom}px`,
        aspectRatio: `${canvas.width_mm} / ${canvas.height_mm}`,
      }}
    >
      {!panels.length && <p className="canvas-empty">{storyboardCopy.noPanels}</p>}
      {hitTestMode && <svg
        className="canvas-object-layer"
        viewBox="0 0 100 100"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {panels.map((panel) => {
          const rect = panelRects[panel.id];
          if (!rect) return null;
          const geometry = panelGeometry(panel);
          if (geometry?.type === "polygon" && geometry.polygon?.length) {
            const points = geometry.polygon.map((point) => `${point.x * 100},${point.y * 100}`).join(" ");
            return <polygon key={panel.id} className="canvas-object-panel polygon" points={points} />;
          }
          return <rect
            key={panel.id}
            className="canvas-object-panel"
            x={rect.x * 100}
            y={rect.y * 100}
            width={rect.width * 100}
            height={rect.height * 100}
          />;
        })}
        {bubbles.map((bubble) => <rect
          key={bubble.dialogue.id}
          className={bubble.shapeType === "ellipse" ? "canvas-object-bubble ellipse" : "canvas-object-bubble"}
          x={bubble.rect.x * 100}
          y={bubble.rect.y * 100}
          width={bubble.rect.width * 100}
          height={bubble.rect.height * 100}
          rx={bubble.shapeType === "ellipse" ? bubble.rect.width * 50 : undefined}
          ry={bubble.shapeType === "ellipse" ? bubble.rect.height * 50 : undefined}
        />)}
      </svg>}
      {panels.map((panel) => {
        // 100-node mode: unselected panels live in the SVG layer above.
        if (hitTestMode && !selectedPanelIds.includes(panel.id)) return null;
        const rect = panelRects[panel.id];
        if (!rect) return null;
        return <PanelNode
          key={zFlashIds.has(panel.id) ? `${panel.id}@${zFlash?.token}` : panel.id}
          panel={panel}
          rect={rect}
          selected={selectedPanelIds.includes(panel.id)}
          flashing={zFlashIds.has(panel.id)}
          interactive={effectiveInteractive}
          elementRef={(element) => {
            panelRefs.current[panel.id] = element;
          }}
          onPointerDown={startPanelMove}
          onDoubleClick={() => {
            onSelectPanels([panel.id]);
            onOpenInspector();
          }}
        />;
      })}
      <svg className="canvas-tail-layer" aria-hidden="true">
        {bubbles.map(({ dialogue, shape }) => {
          if (!shape?.anchor || !shape?.tail_target) return null;
          return <g key={dialogue.id}>
            <line
              className="canvas-tail-line"
              x1={`${shape.anchor.x * 100}%`}
              y1={`${shape.anchor.y * 100}%`}
              x2={`${shape.tail_target.x * 100}%`}
              y2={`${shape.tail_target.y * 100}%`}
            />
            <circle className="canvas-tail-dot" cx={`${shape.tail_target.x * 100}%`} cy={`${shape.tail_target.y * 100}%`} r="3" />
          </g>;
        })}
      </svg>
      {bubbles.map((bubble) => {
        // 100-node mode: unselected bubbles live in the SVG layer above.
        if (hitTestMode && bubble.dialogue.id !== selectedBubbleId) return null;
        return <BubbleNode
          key={bubble.dialogue.id}
          dialogue={bubble.dialogue}
          rect={bubble.rect}
          shapeType={bubble.shapeType}
          rotation={bubble.shape?.rotation ?? 0}
          selected={bubble.dialogue.id === selectedBubbleId}
          interactive={effectiveInteractive}
          elementRef={(element) => {
            bubbleRefs.current[bubble.dialogue.id] = element;
          }}
          onPointerDown={(dialogue, event) => {
            const target = bubbles.find((item) => item.dialogue.id === dialogue.id);
            if (target) startBubbleMove(target, event);
          }}
        />;
      })}
      {sfx.map((item) => {
        const key = `${item.panelId}:${item.index}`;
        return <SfxNode
          key={key}
          sfx={item}
          fontPx={sfxFontPx(item.size)}
          selected={key === selectedSfxKey}
          interactive={effectiveInteractive}
          elementRef={(element) => {
            sfxRefs.current[key] = element;
          }}
          onPointerDown={(sfxItem, event) => startSfxMove(sfxItem, event)}
          onHandlePointerDown={(handle, event) => startSfxHandle(item, handle, event)}
        />;
      })}
      {ghosts?.map((ghost) => (
        <div
          key={ghost.key}
          className={`canvas-ghost${ghost.ellipse ? " ellipse" : ""}${ghost.variant === "b" ? " variant-b" : ""}`}
          aria-hidden="true"
          style={{
            left: `${ghost.rect.x * 100}%`,
            top: `${ghost.rect.y * 100}%`,
            width: `${ghost.rect.width * 100}%`,
            height: `${ghost.rect.height * 100}%`,
          }}
        />
      ))}
      <GuidesOverlay
        bleedInset={bleedInset}
        safeInset={safeInset}
        showBleed={showBleed}
        showSafe={showSafe}
        grid={grid ?? null}
      />
      <div className="canvas-guides-layer" ref={snapGuidesRef} aria-hidden="true" />
      <div className="canvas-size-label" ref={sizeLabelRef} style={{ display: "none" }} aria-hidden="true" />
      {showReadingOrder && <ReadingOrderOverlay
        panels={panels}
        rects={panelRects}
        readingDirection={page.reading_direction}
        pageRef={pageRef}
        viewportRef={viewportRef}
      />}
      {selectedPanel && selectionRect && !isPolygonPanel(selectedPanel) && <TransformHandles
        rect={selectionRect}
        kind="panel"
        disabled={!effectiveInteractive}
        innerRef={handlesRef}
        onHandlePointerDown={(handle, event) => {
          if (handle === "tail" || handle === "anchor" || handle === "rotate") return;
          startPanelResize(selectedPanel.id, handle, event);
        }}
      />}
      {groupBbox && selectedPanelIds.length > 1 && <TransformHandles
        rect={groupBbox}
        kind="panel"
        disabled={!effectiveInteractive}
        innerRef={handlesRef}
        onHandlePointerDown={(handle, event) => {
          if (handle === "tail" || handle === "anchor" || handle === "rotate") return;
          startGroupResize(handle, event);
        }}
      />}
      {selectedBubble && selectionRect && <TransformHandles
        rect={selectionRect}
        kind="bubble"
        disabled={!effectiveInteractive}
        rotation={selectedBubble.shape?.rotation ?? 0}
        aspect={canvasAspect}
        innerRef={handlesRef}
        anchor={selectedBubble.shape?.anchor ?? null}
        tailTarget={selectedBubble.shape?.tail_target ?? null}
        onHandlePointerDown={(handle, event) => {
          if (handle === "anchor") startBubblePoint(selectedBubble, "anchor", event);
          else if (handle === "tail") startBubblePoint(selectedBubble, "tail_target", event);
          else if (handle === "rotate") startBubbleRotate(selectedBubble, event);
          else startBubbleResize(selectedBubble, handle, event);
        }}
      />}
      {((annotations?.length ?? 0) > 0 || draftStroke) && <svg
        className="annotation-layer"
        viewBox="0 0 1000 1000"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        {(annotations ?? []).map((stroke) => (
          <polyline
            key={stroke.id}
            className="annotation-stroke"
            points={stroke.points.map((point) => `${point.x * 1000},${point.y * 1000}`).join(" ")}
          />
        ))}
        {draftStroke && draftStroke.length >= 2 && (
          <polyline
            className="annotation-stroke draft"
            points={draftStroke.map((point) => `${point.x * 1000},${point.y * 1000}`).join(" ")}
          />
        )}
      </svg>}
    </div>
    {afterPage}
    {overlay}
    <p className="canvas-live" aria-live="polite">{announcement}</p>
  </div>;
}
