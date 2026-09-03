// Geometry math for the visual storyboard canvas (V02-31B).
// Coordinates are 0-1 normalized page space (V02-30 contract); the viewport
// converts pointer events through the page element's bounding rect.
import type {
  BubbleGeometryShape,
  CanvasInfo,
  GeometryPoint,
  MangaPage,
  NormalizedRect,
  PanelDialogue,
  PanelGeometryShape,
  StoryboardPanel,
} from "@/lib/api";

export const MIN_PANEL_SIZE = 0.03;
export const MIN_BUBBLE_SIZE = 0.02;
export const SNAP_THRESHOLD_PX = 6;
export const BASE_PAGE_WIDTH = 640;
export const ZOOM_STEP = 1.25;
export const ZOOM_MIN = 0.25;
export const ZOOM_MAX = 4;

export type { GeometryPoint };

export const round4 = (value: number) => Math.round(value * 10000) / 10000;
export const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

export interface SnapGuide {
  axis: "x" | "y";
  at: number;
}

export function clampRect(rect: NormalizedRect, minSize: number): NormalizedRect {
  const width = Math.min(Math.max(rect.width, minSize), 1);
  const height = Math.min(Math.max(rect.height, minSize), 1);
  return {
    x: clamp01(Math.min(rect.x, 1 - width)),
    y: clamp01(Math.min(rect.y, 1 - height)),
    width,
    height,
  };
}

export function translateRect(rect: NormalizedRect, dx: number, dy: number): NormalizedRect {
  return { ...rect, x: clamp01(rect.x + dx), y: clamp01(rect.y + dy) };
}

export function panelRect(panel: StoryboardPanel): NormalizedRect {
  const bounds = panel.bounds ?? {};
  return clampRect(
    {
      x: Number(bounds.x ?? 0),
      y: Number(bounds.y ?? 0),
      width: Number(bounds.width ?? 1),
      height: Number(bounds.height ?? 1),
    },
    MIN_PANEL_SIZE,
  );
}

export function defaultCanvas(page?: MangaPage | null): CanvasInfo {
  const stored = page?.canvas;
  if (stored && typeof stored === "object" && stored.width_mm > 0 && stored.height_mm > 0) {
    return stored;
  }
  return { width_mm: 182, height_mm: 257, bleed_mm: 3, safe_mm: 5, unit: "mm" };
}

export function panelGeometry(panel: StoryboardPanel): PanelGeometryShape | null {
  const stored = panel.geometry;
  if (!stored || typeof stored !== "object" || typeof stored.type !== "string") return null;
  return stored as PanelGeometryShape;
}

export function isPolygonPanel(panel: StoryboardPanel): boolean {
  return panelGeometry(panel)?.type === "polygon";
}

export function panelZOrder(panel: StoryboardPanel): number {
  const z = panelGeometry(panel)?.z_order;
  return Number.isFinite(z) && (z as number) >= 1 ? (z as number) : Math.max(panel.reading_order, 1);
}

/** Stored structured bubble; `legacy: true` means it was derived from a legacy
 * `region` anchor at read time and must never be written back as geometry. */
export function bubbleGeometry(dialogue: PanelDialogue): { shape: BubbleGeometryShape | null; legacy: boolean } {
  const stored = dialogue.bubble;
  if (!stored || typeof stored !== "object" || !stored.rect) return { shape: null, legacy: false };
  if (stored.mapped_from_legacy) return { shape: null, legacy: true };
  return { shape: stored as BubbleGeometryShape, legacy: false };
}

export function bubbleRect(
  dialogue: PanelDialogue,
  drafts: Record<string, BubbleGeometryShape | null>,
): NormalizedRect | null {
  const draft = drafts[dialogue.id];
  if (draft !== undefined) return draft?.rect ?? null;
  return bubbleGeometry(dialogue).shape?.rect ?? null;
}

/** Fallback placement for a legacy-region bubble so it stays visible on the
 * canvas without ever persisting the derived geometry. */
export function legacyBubbleRect(panel: StoryboardPanel, dialogue: PanelDialogue, index: number): NormalizedRect {
  const panelBounds = panelRect(panel);
  const width = Math.min(0.2, panelBounds.width * 0.6);
  const height = Math.min(0.13, panelBounds.height * 0.35);
  return {
    x: clamp01(panelBounds.x + 0.03 + (index % 3) * 0.06),
    y: clamp01(panelBounds.y + 0.03 + Math.floor(index / 3) * 0.16),
    width,
    height,
  };
}

export interface ResizeOptions {
  ratioLock?: boolean;
  fromCenter?: boolean;
  minSize: number;
}

/** Resize `origin` by dragging `handle` toward `pointer`; clamps to the page. */
export function applyResize(
  origin: NormalizedRect,
  handle: string,
  pointer: GeometryPoint,
  options: ResizeOptions,
): NormalizedRect {
  const east = handle.includes("e");
  const west = handle.includes("w");
  const south = handle.includes("s");
  const north = handle.includes("n");
  let left = origin.x;
  let top = origin.y;
  let right = origin.x + origin.width;
  let bottom = origin.y + origin.height;
  if (east) right = clamp01(pointer.x);
  if (west) left = clamp01(pointer.x);
  if (south) bottom = clamp01(pointer.y);
  if (north) top = clamp01(pointer.y);
  if (options.fromCenter) {
    if (east || west) {
      const center = origin.x + origin.width / 2;
      const offset = Math.min(Math.abs(pointer.x - center), Math.min(center, 1 - center));
      left = center - offset;
      right = center + offset;
    }
    if (south || north) {
      const center = origin.y + origin.height / 2;
      const offset = Math.min(Math.abs(pointer.y - center), Math.min(center, 1 - center));
      top = center - offset;
      bottom = center + offset;
    }
  }
  let width = Math.max(right - left, 0);
  let height = Math.max(bottom - top, 0);
  if (options.ratioLock && origin.width > 0 && origin.height > 0) {
    const ratio = origin.width / origin.height;
    if (width / height > ratio) width = height * ratio;
    else height = width / ratio;
    if (east) right = left + width;
    else left = right - width;
    if (south) bottom = top + height;
    else top = bottom - height;
  }
  if (width < options.minSize) {
    if (west && !east) left = Math.max(right - options.minSize, 0);
    else right = Math.min(left + options.minSize, 1);
    width = Math.min(options.minSize, 1);
  }
  if (height < options.minSize) {
    if (north && !south) top = Math.max(bottom - options.minSize, 0);
    else bottom = Math.min(top + options.minSize, 1);
    height = Math.min(options.minSize, 1);
  }
  return clampRect(
    { x: Math.min(left, right), y: Math.min(top, bottom), width, height },
    options.minSize,
  );
}

/** Snap the moving rect's edges/center onto the given guide lines.
 * Returns the position-adjusted rect plus the lines that snapped for the overlay. */
export function snapRect(
  rect: NormalizedRect,
  targets: { x: number[]; y: number[] },
  threshold: number,
): { rect: NormalizedRect; guides: SnapGuide[] } {
  const guides: SnapGuide[] = [];
  const snapAxis = (
    edges: Array<{ value: number; center: boolean }>,
    lines: number[],
  ): { delta: number; at: number } | null => {
    let best: { delta: number; at: number; center: boolean } | null = null;
    for (const line of lines) {
      for (const edge of edges) {
        const delta = line - edge.value;
        if (Math.abs(delta) > threshold) continue;
        if (best === null
          || Math.abs(delta) < Math.abs(best.delta)
          || (Math.abs(delta) === Math.abs(best.delta) && edge.center && !best.center)) {
          best = { delta, at: line, center: edge.center };
        }
      }
    }
    return best ? { delta: best.delta, at: best.at } : null;
  };
  const snappedX = snapAxis(
    [
      { value: rect.x + rect.width / 2, center: true },
      { value: rect.x, center: false },
      { value: rect.x + rect.width, center: false },
    ],
    targets.x,
  );
  const snappedY = snapAxis(
    [
      { value: rect.y + rect.height / 2, center: true },
      { value: rect.y, center: false },
      { value: rect.y + rect.height, center: false },
    ],
    targets.y,
  );
  const next = { ...rect };
  if (snappedX) {
    next.x = Math.min(Math.max(rect.x + snappedX.delta, 0), 1 - rect.width);
    guides.push({ axis: "x", at: snappedX.at });
  }
  if (snappedY) {
    next.y = Math.min(Math.max(rect.y + snappedY.delta, 0), 1 - rect.height);
    guides.push({ axis: "y", at: snappedY.at });
  }
  return { rect: next, guides };
}

export function snapTargets(excludeRect: NormalizedRect | null, others: NormalizedRect[]): { x: number[]; y: number[] } {
  const x = [0, 0.5, 1];
  const y = [0, 0.5, 1];
  for (const rect of others) {
    if (rect === excludeRect) continue;
    x.push(rect.x, rect.x + rect.width / 2, rect.x + rect.width);
    y.push(rect.y, rect.y + rect.height / 2, rect.y + rect.height);
  }
  return { x, y };
}

export function pointInRect(point: GeometryPoint, rect: NormalizedRect): boolean {
  return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height;
}

export function rectCovers(rect: NormalizedRect, outer: NormalizedRect): boolean {
  return (
    rect.x >= outer.x - 0.0005
    && rect.y >= outer.y - 0.0005
    && rect.x + rect.width <= outer.x + outer.width + 0.0005
    && rect.y + rect.height <= outer.y + outer.height + 0.0005
  );
}

/** Clamp a rect fully inside `outer` (kept when resizing inside a panel). */
export function clampRectInto(rect: NormalizedRect, outer: NormalizedRect): NormalizedRect {
  const width = Math.min(rect.width, outer.width);
  const height = Math.min(rect.height, outer.height);
  return {
    x: Math.min(Math.max(rect.x, outer.x), outer.x + outer.width - width),
    y: Math.min(Math.max(rect.y, outer.y), outer.y + outer.height - height),
    width,
    height,
  };
}

export const percent = (value: number) => `${(value * 100).toFixed(1).replace(/\.0$/, "")}%`;

export function geometryReadout(rect: NormalizedRect): string {
  return `X ${percent(rect.x)} · Y ${percent(rect.y)} · 宽 ${percent(rect.width)} · 高 ${percent(rect.height)}`;
}

export function newRequestId(): string {
  const cryptoRef = globalThis.crypto;
  if (cryptoRef && typeof cryptoRef.randomUUID === "function") return cryptoRef.randomUUID();
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Payload bubbles must match the BubbleGeometry schema exactly: the read-path
 * `mapped_from_legacy` marker is never sent back, and every coordinate is
 * rounded to the contract's 4-decimal precision. */
export function toPayloadBubble(shape: BubbleGeometryShape | null): BubbleGeometryShape | null {
  if (!shape) return null;
  const point = (value?: GeometryPoint | null) =>
    value ? { x: round4(value.x), y: round4(value.y) } : undefined;
  return {
    type: shape.type,
    rect: {
      x: round4(shape.rect.x),
      y: round4(shape.rect.y),
      width: round4(shape.rect.width),
      height: round4(shape.rect.height),
    },
    anchor: point(shape.anchor),
    tail_target: point(shape.tail_target),
    rotation: shape.rotation ?? 0,
    text_region: shape.text_region
      ? {
        x: round4(shape.text_region.x),
        y: round4(shape.text_region.y),
        width: round4(shape.text_region.width),
        height: round4(shape.text_region.height),
      }
      : undefined,
  };
}

/** Round a payload rect to the contract's 4-decimal coordinate precision. */
export function toPayloadRect(rect: NormalizedRect): NormalizedRect {
  return {
    x: round4(rect.x),
    y: round4(rect.y),
    width: round4(rect.width),
    height: round4(rect.height),
  };
}
