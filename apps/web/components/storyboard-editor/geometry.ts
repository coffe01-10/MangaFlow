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
export const MIN_SFX_SIZE = 0.01;
export const SNAP_THRESHOLD_PX = 6;
export const BASE_PAGE_WIDTH = 640;
export const ZOOM_STEP = 1.25;
export const ZOOM_MIN = 0.25;
export const ZOOM_MAX = 4;
export const ROTATION_SNAP_DEG = 15;
export const MAX_ROTATION = 360;

export type { GeometryPoint };

export const round4 = (value: number) => Math.round(value * 10000) / 10000;
export const clamp01 = (value: number) => Math.min(1, Math.max(0, value));

export interface SnapGuide {
  axis: "x" | "y";
  at: number;
  /** "gap" guides mark an equal-spacing snap between two neighbours. */
  kind?: "edge" | "gap";
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

// --- precision editing helpers ----------------------------------------------

export const sameRect = (a: NormalizedRect, b: NormalizedRect) =>
  a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;

export interface RectChange {
  id: string;
  before: NormalizedRect;
  after: NormalizedRect;
}

export function rectsBoundingBox(rects: NormalizedRect[]): NormalizedRect | null {
  if (!rects.length) return null;
  let x = 1;
  let y = 1;
  let right = 0;
  let bottom = 0;
  for (const rect of rects) {
    x = Math.min(x, rect.x);
    y = Math.min(y, rect.y);
    right = Math.max(right, rect.x + rect.width);
    bottom = Math.max(bottom, rect.y + rect.height);
  }
  return { x, y, width: right - x, height: bottom - y };
}

export type AlignMode = "left" | "centerX" | "right" | "top" | "middleY" | "bottom";

/** Align every rect in `ids` to the shared selection bounding-box edge/center. */
export function alignRects(
  rects: Record<string, NormalizedRect>,
  ids: string[],
  mode: AlignMode,
): RectChange[] {
  const targets = ids.map((id) => rects[id]).filter(Boolean);
  const bbox = rectsBoundingBox(targets);
  if (!bbox || ids.length < 2) return [];
  const changes: RectChange[] = [];
  for (const id of ids) {
    const before = rects[id];
    if (!before) continue;
    let after = before;
    if (mode === "left") after = { ...before, x: bbox.x };
    else if (mode === "centerX") after = { ...before, x: bbox.x + (bbox.width - before.width) / 2 };
    else if (mode === "right") after = { ...before, x: bbox.x + bbox.width - before.width };
    else if (mode === "top") after = { ...before, y: bbox.y };
    else if (mode === "middleY") after = { ...before, y: bbox.y + (bbox.height - before.height) / 2 };
    else after = { ...before, y: bbox.y + bbox.height - before.height };
    // round4 keeps draft values clean (0.4 instead of 0.39999999999999997):
    // without it float dust would register phantom changes in sameRect and
    // pollute both the undo diff and the inspector readout.
    after = { x: round4(after.x), y: round4(after.y), width: round4(after.width), height: round4(after.height) };
    if (!sameRect(before, after)) changes.push({ id, before, after });
  }
  return changes;
}

/** Distribute `ids` so gaps between consecutive rects along `axis` are equal;
 * the two outermost rects keep their position (sort order follows position). */
export function distributeRects(
  rects: Record<string, NormalizedRect>,
  ids: string[],
  axis: "x" | "y",
): RectChange[] {
  if (ids.length < 3) return [];
  const sorted = ids
    .map((id) => ({ id, rect: rects[id] }))
    .filter((item) => item.rect)
    .sort((a, b) => a.rect[axis] - b.rect[axis]);
  if (sorted.length < 3) return [];
  const startKey = axis;
  const sizeKey = axis === "x" ? "width" : "height";
  const first = sorted[0].rect;
  const last = sorted[sorted.length - 1].rect;
  const span = last[startKey] + last[sizeKey] - first[startKey];
  const totalSize = sorted.reduce((sum, item) => sum + item.rect[sizeKey], 0);
  const gap = (span - totalSize) / (sorted.length - 1);
  const changes: RectChange[] = [];
  let cursor = first[startKey] + first[sizeKey] + gap;
  for (let index = 1; index < sorted.length - 1; index++) {
    const { id, rect } = sorted[index];
    // round4 as in alignRects: the accumulated cursor carries float dust that
    // would otherwise register a phantom change for an already-placed rect.
    const after = { ...rect, [startKey]: round4(cursor) };
    cursor += rect[sizeKey] + gap;
    if (rect[startKey] !== after[startKey]) changes.push({ id, before: rect, after });
  }
  return changes;
}

export type SameSizeMode = "width" | "height" | "size";

/** Make every rect in `ids` adopt the first id's width/height/size, keeping
 * each rect's own top-left and clamping into the page. */
export function sameSizeRects(
  rects: Record<string, NormalizedRect>,
  ids: string[],
  mode: SameSizeMode,
): RectChange[] {
  if (ids.length < 2) return [];
  const reference = rects[ids[0]];
  if (!reference) return [];
  const changes: RectChange[] = [];
  for (const id of ids.slice(1)) {
    const before = rects[id];
    if (!before) continue;
    const after = clampRect(
      {
        ...before,
        width: mode === "height" ? before.width : reference.width,
        height: mode === "width" ? before.height : reference.height,
      },
      MIN_PANEL_SIZE,
    );
    if (!sameRect(before, after)) changes.push({ id, before, after });
  }
  return changes;
}

export type ZOrderOp = "up" | "down" | "top" | "bottom";

/** Compute z_order edits for a layer op. Every entry is one panel's change;
 * ops may touch other panels (swaps / renumber bumps) so callers push all of
 * them into a single undo step. */
export function zOrderChanges(
  zOrders: Record<string, number>,
  targetId: string,
  op: ZOrderOp,
): { id: string; before: number; after: number }[] {
  const current = zOrders[targetId];
  if (current === undefined) return [];
  const ids = Object.keys(zOrders);
  const maxZ = Math.max(...ids.map((id) => zOrders[id]));
  const minZ = Math.min(...ids.map((id) => zOrders[id]));
  if (op === "top") {
    return current === maxZ && ids.filter((id) => zOrders[id] === maxZ).length === 1
      ? []
      : [{ id: targetId, before: current, after: maxZ + 1 }];
  }
  if (op === "bottom") {
    if (minZ - 1 >= 1) return [{ id: targetId, before: current, after: minZ - 1 }];
    // z_order must stay >= 1: bump everyone else up one rank instead.
    const changes = ids
      .filter((id) => id !== targetId)
      .map((id) => ({ id, before: zOrders[id], after: zOrders[id] + 1 }));
    changes.push({ id: targetId, before: current, after: 1 });
    return changes;
  }
  const above = ids
    .filter((id) => id !== targetId && zOrders[id] > current)
    .sort((a, b) => zOrders[a] - zOrders[b]);
  const below = ids
    .filter((id) => id !== targetId && zOrders[id] < current)
    .sort((a, b) => zOrders[b] - zOrders[a]);
  const neighbour = op === "up" ? above[0] : below[0];
  if (!neighbour) return [];
  return [
    { id: targetId, before: current, after: zOrders[neighbour] },
    { id: neighbour, before: zOrders[neighbour], after: current },
  ];
}

/** Grid snap targets for a physical-mm step on the canvas. A step that lands
 * off-canvas or below the minimum panel size contributes no lines. */
export function gridLinesFor(canvas: CanvasInfo, stepMm: number): { x: number[]; y: number[] } {
  const axis = (totalMm: number) => {
    if (stepMm <= 0 || totalMm <= 0 || stepMm >= totalMm) return [] as number[];
    const lines: number[] = [];
    for (let position = stepMm; position < totalMm - 1e-6; position += stepMm) {
      lines.push(round4(position / totalMm));
    }
    return lines;
  };
  return { x: axis(canvas.width_mm), y: axis(canvas.height_mm) };
}

/** Equal-gap ("smart spacing") snap: while moving `rect` between two
 * neighbours, snap so the gap on both sides matches. Returns only the axes
 * where no edge snap already fired. */
export function equalGapSnap(
  rect: NormalizedRect,
  others: NormalizedRect[],
  threshold: number,
  skipAxis: { x: boolean; y: boolean },
): { deltaX: number; deltaY: number; guides: SnapGuide[] } {
  const result = { deltaX: 0, deltaY: 0, guides: [] as SnapGuide[] };
  if (!skipAxis.x) {
    const leftEdges = others.filter((item) => item.x + item.width <= rect.x + threshold);
    const rightEdges = others.filter((item) => item.x >= rect.x + rect.width - threshold);
    for (const left of leftEdges) {
      for (const right of rightEdges) {
        // Place rect so gap-to-left equals gap-to-right between these two.
        const candidate = (left.x + left.width + right.x - rect.width) / 2;
        const delta = candidate - rect.x;
        if (Math.abs(delta) <= threshold && Math.abs(delta) > 1e-9) {
          result.deltaX = delta;
          result.guides.push(
            { axis: "x", at: round4(left.x + left.width), kind: "gap" },
            { axis: "x", at: round4(right.x), kind: "gap" },
          );
        }
      }
    }
  }
  if (!skipAxis.y) {
    const above = others.filter((item) => item.y + item.height <= rect.y + threshold);
    const below = others.filter((item) => item.y >= rect.y + rect.height - threshold);
    for (const top of above) {
      for (const bottom of below) {
        const candidate = (top.y + top.height + bottom.y - rect.height) / 2;
        const delta = candidate - rect.y;
        if (Math.abs(delta) <= threshold && Math.abs(delta) > 1e-9) {
          result.deltaY = delta;
          result.guides.push(
            { axis: "y", at: round4(top.y + top.height), kind: "gap" },
            { axis: "y", at: round4(bottom.y), kind: "gap" },
          );
        }
      }
    }
  }
  return result;
}

/** Scale `rect` the same way `from` was resized into `to` (group resize). */
export function scaleRectWithBounds(
  rect: NormalizedRect,
  from: NormalizedRect,
  to: NormalizedRect,
  minSize: number,
): NormalizedRect {
  const scaleX = from.width > 0 ? to.width / from.width : 1;
  const scaleY = from.height > 0 ? to.height / from.height : 1;
  return clampRect(
    {
      x: to.x + (rect.x - from.x) * scaleX,
      y: to.y + (rect.y - from.y) * scaleY,
      width: Math.max(rect.width * scaleX, 0),
      height: Math.max(rect.height * scaleY, 0),
    },
    minSize,
  );
}

/** Normalize a rotation into the contract's (-360, 360] range. */
export function normalizeRotation(degrees: number): number {
  let value = degrees % MAX_ROTATION;
  if (value <= -MAX_ROTATION) value += MAX_ROTATION;
  return value;
}

/** Rotate `point` around `center` by `degrees` (page-normalized space; the
 * page is not square so we rotate in a y-aspect-corrected space). */
export function rotatePointAround(
  point: GeometryPoint,
  center: GeometryPoint,
  degrees: number,
  aspect = 1,
): GeometryPoint {
  const radians = (degrees * Math.PI) / 180;
  const dx = point.x - center.x;
  const dy = point.y - center.y;
  const cos = Math.cos(radians);
  const sin = Math.sin(radians);
  // Rotate in pixel space (x*W, y*H) then normalize back; aspect = H/W.
  return {
    x: center.x + dx * cos - dy * aspect * sin,
    y: center.y + (dx * sin) / aspect + dy * cos,
  };
}

/** Center of a rect in normalized space. */
export function rectCenter(rect: NormalizedRect): GeometryPoint {
  return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
}

/** Angle (degrees) from `center` to `point`, in aspect-corrected space. */
export function angleBetween(center: GeometryPoint, point: GeometryPoint, aspect = 1): number {
  return (Math.atan2((point.y - center.y) * aspect, point.x - center.x) * 180) / Math.PI;
}

/** Default position for a sound effect whose x/y are still unset: cascade
 * inside its panel like the legacy bubble fallback does. */
export function defaultSfxPosition(panelBounds: NormalizedRect, index: number): GeometryPoint {
  return {
    x: clamp01(panelBounds.x + panelBounds.width * (0.5 + (index % 3 - 1) * 0.2)),
    y: clamp01(panelBounds.y + panelBounds.height * (0.3 + Math.floor(index / 3) * 0.2)),
  };
}
