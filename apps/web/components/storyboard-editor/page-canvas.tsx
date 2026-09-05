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
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, RefObject } from "react";

import type { GeometryCommandChange } from "./command-stack";
import {
  BASE_PAGE_WIDTH,
  MIN_BUBBLE_SIZE,
  MIN_PANEL_SIZE,
  SNAP_THRESHOLD_PX,
  applyResize,
  clamp01,
  clampRectInto,
  isPolygonPanel,
  panelGeometry,
  panelZOrder,
  pointInRect,
  rectCovers,
  snapRect,
  snapTargets,
  translateRect,
  type GeometryPoint,
  type SnapGuide,
} from "./geometry";
import { BubbleNode } from "./bubble-node";
import { GuidesOverlay, ReadingOrderOverlay } from "./guides-overlay";
import { PanelNode } from "./panel-node";
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
  | null;

type Gesture =
  | { kind: "move-panels"; start: GeometryPoint; origin: Record<string, NormalizedRect>; ids: string[] }
  | { kind: "resize-panel"; handle: HandleName; start: GeometryPoint; origin: NormalizedRect; panelId: string; ratioLock: boolean; fromCenter: boolean }
  | { kind: "move-bubble"; start: GeometryPoint; origin: BubbleGeometryShape; dialogueId: string; panelRect: NormalizedRect }
  | { kind: "resize-bubble"; handle: HandleName; start: GeometryPoint; origin: BubbleGeometryShape; dialogueId: string; panelRect: NormalizedRect; ratioLock: boolean; fromCenter: boolean }
  | { kind: "move-point"; point: "tail_target" | "anchor"; start: GeometryPoint; origin: BubbleGeometryShape; dialogueId: string };

interface GestureResult {
  panels?: Record<string, NormalizedRect>;
  bubble?: { id: string; shape: BubbleGeometryShape };
  guides: SnapGuide[];
}

const FULL_PAGE: NormalizedRect = { x: 0, y: 0, width: 1, height: 1 };

const sameRect = (a: NormalizedRect, b: NormalizedRect) =>
  a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;

const setElementRect = (element: HTMLElement | null | undefined, rect: NormalizedRect) => {
  if (!element) return;
  element.style.left = `${rect.x * 100}%`;
  element.style.top = `${rect.y * 100}%`;
  element.style.width = `${rect.width * 100}%`;
  element.style.height = `${rect.height * 100}%`;
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
  zoom,
  viewportRef,
  snapEnabled,
  showReadingOrder,
  showBleed,
  showSafe,
  interactive,
  selection,
  onCommand,
  onSelectPanels,
  onSelectBubble,
  onClearSelection,
  onOpenInspector,
  onDeleteBubble,
  onBubbleBounce,
  onZoomStep,
}: {
  page: MangaPage;
  canvas: CanvasInfo;
  panels: StoryboardPanel[];
  panelRects: Record<string, NormalizedRect>;
  bubbles: CanvasBubble[];
  zoom: number;
  viewportRef: RefObject<HTMLDivElement | null>;
  snapEnabled: boolean;
  showReadingOrder: boolean;
  showBleed: boolean;
  showSafe: boolean;
  interactive: boolean;
  selection: CanvasSelection;
  onCommand: (label: string, changes: GeometryCommandChange[]) => void;
  onSelectPanels: (ids: string[]) => void;
  onSelectBubble: (dialogueId: string) => void;
  onClearSelection: () => void;
  onOpenInspector: () => void;
  onDeleteBubble: (dialogueId: string) => void;
  onBubbleBounce: () => void;
  onZoomStep: (direction: 1 | -1) => void;
}) {
  const renderCountRef = useRef(0);
  // Runs once per render (no deps): exposes the render count for V02-32 tests
  // that assert gestures do not re-render the canvas. Harmless in production.
  useEffect(() => {
    renderCountRef.current += 1;
    (window as RenderStatsScope).__pageCanvasRenders = renderCountRef.current;
  });
  const pageRef = useRef<HTMLDivElement | null>(null);
  const panelRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const bubbleRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const handlesRef = useRef<HTMLDivElement | null>(null);
  const snapGuidesRef = useRef<HTMLDivElement | null>(null);
  const [gesture, setGesture] = useState<Gesture | null>(null);

  const hitTestMode = panels.length + bubbles.length > HIT_TEST_OBJECT_LIMIT;
  const selectedPanelIds = selection?.kind === "panels" ? selection.ids : [];
  const selectedBubbleId = selection?.kind === "bubble" ? selection.dialogueId : null;
  const selectedPanel = selectedPanelIds.length === 1
    ? panels.find((panel) => panel.id === selectedPanelIds[0]) ?? null
    : null;
  const selectedBubble = bubbles.find((bubble) => bubble.dialogue.id === selectedBubbleId) ?? null;
  const announcement = selectedPanel
    ? storyboardCopy.selectedAnnouncement(selectedPanel.reading_order, selectedPanel.bleed)
    : selectedBubble
      ? storyboardCopy.bubbleSelectedAnnouncement(selectedBubble.dialogue.reading_order)
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
  const otherPanelRects = (excludeId: string) =>
    panels.filter((panel) => panel.id !== excludeId).map((panel) => panelRects[panel.id]).filter(Boolean);

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
          const snapped = snapRect(
            moved[primaryId],
            snapTargets(active.origin[primaryId], otherPanelRects(primaryId)),
            snapThreshold(),
          );
          const fixX = snapped.rect.x - moved[primaryId].x;
          const fixY = snapped.rect.y - moved[primaryId].y;
          for (const id of active.ids) moved[id] = translateRect(active.origin[id], dx + fixX, dy + fixY);
          guides = snapped.guides;
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
          snapTargets(active.origin, otherPanelRects(active.panelId)),
          snapThreshold(),
        );
        return { panels: { [active.panelId]: snapped.rect }, guides: snapped.guides };
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
      case "move-point": {
        const point = { x: clamp01(pointer.x), y: clamp01(pointer.y) };
        return {
          bubble: { id: active.dialogueId, shape: { ...active.origin, [active.point]: point } },
          guides: [],
        };
      }
    }
  };

  const commitGesture = (active: Gesture, pointer: GeometryPoint) => {
    const result = computeResult(active, pointer);
    if (active.kind === "move-panels" || active.kind === "resize-panel") {
      const ids = active.kind === "move-panels" ? active.ids : [active.panelId];
      const changes: GeometryCommandChange[] = [];
      for (const id of ids) {
        const after = result.panels?.[id];
        const before = active.kind === "move-panels" ? active.origin[id] : active.origin;
        if (!after || sameRect(before, after)) continue;
        changes.push({ kind: "panel", id, before, after });
      }
      if (changes.length) onCommand(active.kind === "move-panels" ? "拖动格子" : "缩放格子", changes);
      return;
    }
    if (!result.bubble) return;
    const ownerRect = active.kind === "move-bubble" || active.kind === "resize-bubble" ? active.panelRect : null;
    if (active.kind === "move-bubble" && ownerRect && !rectCovers(result.bubble.shape.rect, ownerRect)) {
      // 拖出所属格：回弹到拖拽前位置并提示（不跨格移动）。
      onBubbleBounce();
      return;
    }
    const before = active.origin;
    if (JSON.stringify(before) === JSON.stringify(result.bubble.shape)) return;
    onCommand(
      active.kind === "move-bubble" ? "拖动气泡" : active.kind === "resize-bubble" ? "缩放气泡" : "调整气泡尾巴",
      [{ kind: "bubble", id: result.bubble.id, before, after: result.bubble.shape }],
    );
  };

  // --- imperative gesture painting (audit §4: no per-pointermove re-render) -

  const paintHandles = (rect: NormalizedRect | null, kind: "panel" | "bubble", anchor?: { x: number; y: number } | null, tailTarget?: { x: number; y: number } | null) => {
    const container = handlesRef.current;
    if (!container || !rect) return;
    const positions = handlePositions(rect, kind, anchor, tailTarget);
    for (const element of Array.from(container.querySelectorAll<HTMLElement>("[data-handle]"))) {
      const position = positions.get(element.dataset.handle ?? "");
      if (!position) continue;
      element.style.left = `${position.left * 100}%`;
      element.style.top = `${position.top * 100}%`;
    }
  };

  const paintGuides = (guides: SnapGuide[]) => {
    const layer = snapGuidesRef.current;
    if (!layer) return;
    layer.textContent = "";
    for (const guide of guides) {
      const line = document.createElement("div");
      line.className = guide.axis === "x" ? "canvas-guide-line vertical" : "canvas-guide-line horizontal";
      if (guide.axis === "x") {
        line.style.left = `${guide.at * 100}%`;
        line.style.top = "0";
        line.style.bottom = "0";
      } else {
        line.style.top = `${guide.at * 100}%`;
        line.style.left = "0";
        line.style.right = "0";
      }
      layer.appendChild(line);
    }
  };

  const paintGesture = (active: Gesture, result: GestureResult) => {
    if (result.panels && (active.kind === "move-panels" || active.kind === "resize-panel")) {
      for (const [id, rect] of Object.entries(result.panels)) setElementRect(panelRefs.current[id], rect);
      const primary = active.kind === "move-panels" ? active.ids[0] : active.panelId;
      paintHandles(result.panels[primary] ?? null, "panel");
      paintGuides(result.guides);
      return;
    }
    if (result.bubble) {
      setElementRect(bubbleRefs.current[result.bubble.id], result.bubble.shape.rect);
      paintHandles(
        result.bubble.shape.rect,
        "bubble",
        result.bubble.shape.anchor ?? null,
        result.bubble.shape.tail_target ?? null,
      );
    }
    paintGuides(result.guides);
  };

  // Reset the dragged outlines to the last rendered (state) geometry. Called on
  // every gesture end: after a commit React immediately repaints the new
  // drafts on top of this, and after a cancel/bounce this is the restore.
  const restoreAfterGesture = () => {
    for (const [id, rect] of Object.entries(panelRects)) setElementRect(panelRefs.current[id], rect);
    for (const bubble of bubbles) setElementRect(bubbleRefs.current[bubble.dialogue.id], bubble.rect);
    paintHandles(
      selectedPanel ? panelRects[selectedPanel.id] ?? null : selectedBubble?.rect ?? null,
      selectedPanel ? "panel" : "bubble",
      selectedBubble?.shape?.anchor ?? null,
      selectedBubble?.shape?.tail_target ?? null,
    );
    paintGuides([]);
  };

  useEffect(() => {
    if (!gesture) return;
    const move = (event: PointerEvent) => paintGesture(gesture, computeResult(gesture, pointerNorm(event)));
    const finish = (event: PointerEvent) => {
      commitGesture(gesture, pointerNorm(event));
      setGesture(null);
      restoreAfterGesture();
    };
    const cancel = () => {
      setGesture(null);
      restoreAfterGesture();
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
    setGesture({ kind: "move-panels", start: pointerNorm(event), origin, ids: movableIds });
  };

  const startPanelResize = (panelId: string, handle: HandleName, event: ReactPointerEvent<HTMLDivElement>) => {
    const origin = panelRects[panelId];
    if (!origin || !interactive) return;
    setGesture({
      kind: "resize-panel",
      handle,
      start: pointerNorm(event),
      origin,
      panelId,
      ratioLock: event.shiftKey,
      fromCenter: event.altKey,
    });
  };

  const startBubbleMove = (bubble: CanvasBubble, event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || !interactive) return;
    event.stopPropagation();
    onSelectBubble(bubble.dialogue.id);
    setGesture({
      kind: "move-bubble",
      start: pointerNorm(event),
      origin: bubble.shape ?? syntheticBubbleShape(bubble),
      dialogueId: bubble.dialogue.id,
      panelRect: bubble.panelRect,
    });
  };

  const startBubbleResize = (bubble: CanvasBubble, handle: HandleName, event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    setGesture({
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

  const startBubblePoint = (bubble: CanvasBubble, point: "tail_target" | "anchor", event: ReactPointerEvent<HTMLDivElement>) => {
    if (!interactive) return;
    setGesture({
      kind: "move-point",
      point,
      start: pointerNorm(event),
      origin: bubble.shape ?? syntheticBubbleShape(bubble),
      dialogueId: bubble.dialogue.id,
    });
  };

  const handleCanvasKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (gesture) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onClearSelection();
      return;
    }
    if (event.key === "Enter" && selectedPanel) {
      event.preventDefault();
      onOpenInspector();
      return;
    }
    if ((event.key === "Delete" || event.key === "Backspace") && selectedBubble) {
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
      : undefined;
  const selectionRect = selectedPanel
    ? panelRects[selectedPanel.id] ?? null
    : selectedBubble
      ? selectedBubble.rect
      : null;

  return <div
    className="canvas-viewport"
    ref={viewportRef}
    onWheel={(event) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      onZoomStep(event.deltaY > 0 ? -1 : 1);
    }}
  >
    <div
      ref={pageRef}
      data-testid="canvas-page"
      className="canvas-page"
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
          key={panel.id}
          panel={panel}
          rect={rect}
          selected={selectedPanelIds.includes(panel.id)}
          interactive={interactive}
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
          selected={bubble.dialogue.id === selectedBubbleId}
          interactive={interactive}
          elementRef={(element) => {
            bubbleRefs.current[bubble.dialogue.id] = element;
          }}
          onPointerDown={(dialogue, event) => {
            const target = bubbles.find((item) => item.dialogue.id === dialogue.id);
            if (target) startBubbleMove(target, event);
          }}
        />;
      })}
      <GuidesOverlay
        bleedInset={bleedInset}
        safeInset={safeInset}
        showBleed={showBleed}
        showSafe={showSafe}
      />
      <div className="canvas-guides-layer" ref={snapGuidesRef} aria-hidden="true" />
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
        disabled={!interactive}
        innerRef={handlesRef}
        onHandlePointerDown={(handle, event) => {
          if (handle === "tail" || handle === "anchor") return;
          startPanelResize(selectedPanel.id, handle, event);
        }}
      />}
      {selectedBubble && selectionRect && <TransformHandles
        rect={selectionRect}
        kind="bubble"
        disabled={!interactive}
        innerRef={handlesRef}
        anchor={selectedBubble.shape?.anchor ?? null}
        tailTarget={selectedBubble.shape?.tail_target ?? null}
        onHandlePointerDown={(handle, event) => {
          if (handle === "anchor") startBubblePoint(selectedBubble, "anchor", event);
          else if (handle === "tail") startBubblePoint(selectedBubble, "tail_target", event);
          else startBubbleResize(selectedBubble, handle, event);
        }}
      />}
    </div>
    <p className="canvas-live" aria-live="polite">{announcement}</p>
  </div>;
}
