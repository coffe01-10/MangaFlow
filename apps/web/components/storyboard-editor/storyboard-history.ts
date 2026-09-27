// Client-side storyboard history features (V02-33): layout templates,
// named version snapshots with ghost compare, and session replay. All three
// share one "resolved canvas state" model — drafts merged in, numbers
// rounded — so a snapshot, a replay frame and a template application all
// diff/apply through the same code path. Persistence is localStorage
// (templates global, snapshots per page); nothing here touches the API.
import type { BubbleGeometryShape, NormalizedRect } from "@/lib/api";

import type { GeometryCommandChange, SoundEffectGeometry } from "./command-stack";
import { clamp01, clampRect, clampRectInto, MIN_BUBBLE_SIZE, round4, sameRect } from "./geometry";

/** Resolved per-panel canvas state: bounds plus editable meta (rotation /
 * z_order, which are payload fields rather than rendered transforms). */
export interface CanvasPanelState {
  rect: NormalizedRect;
  rotation: number;
  z_order: number;
}

/** Everything a replay frame or version snapshot needs to re-render the
 * canvas exactly as it looked: effective geometry, never raw drafts. */
export interface CanvasState {
  panels: Record<string, CanvasPanelState>;
  /** dialogueId → effective bubble shape (legacy fallbacks synthesized). */
  bubbles: Record<string, BubbleGeometryShape>;
  /** panelId → entry index → effective sound-effect geometry. */
  sfx: Record<string, Record<number, SoundEffectGeometry>>;
}

export interface StoryboardSnapshot {
  id: string;
  name: string;
  createdAt: number;
  state: CanvasState;
}

export interface LayoutTemplate {
  id: string;
  name: string;
  /** Presets are code-owned and cannot be deleted; user templates persist. */
  builtIn: boolean;
  cells: NormalizedRect[];
  createdAt: number;
}

export interface ReplayEntry {
  label: string;
  at: number;
  state: CanvasState;
}

export const TEMPLATES_KEY = "mangaflow.storyboard-templates";
export const snapshotsKey = (pageId: string) => `mangaflow.storyboard-snapshots.${pageId}`;

const roundRect = (rect: NormalizedRect): NormalizedRect => ({
  x: round4(rect.x),
  y: round4(rect.y),
  width: round4(rect.width),
  height: round4(rect.height),
});

const roundPoint = (point: { x: number; y: number }) => ({ x: round4(point.x), y: round4(point.y) });

const roundShape = (shape: BubbleGeometryShape): BubbleGeometryShape => ({
  ...shape,
  rect: roundRect(shape.rect),
  anchor: shape.anchor ? roundPoint(shape.anchor) : shape.anchor,
  tail_target: shape.tail_target ? roundPoint(shape.tail_target) : shape.tail_target,
  rotation: round4(shape.rotation ?? 0),
  text_region: shape.text_region ? roundRect(shape.text_region) : shape.text_region,
});

const roundSfx = (value: SoundEffectGeometry): SoundEffectGeometry => ({
  x: round4(value.x),
  y: round4(value.y),
  rotation: round4(value.rotation),
  size: round4(value.size),
});

/** Freeze the resolved projections (panelRects/bubbles/sfxNodes already
 * merged drafts) into a comparable state. Round4 everywhere so float tails
 * cannot fake diffs between frames. */
export function captureCanvasState(input: {
  panelIds: string[];
  rects: Record<string, NormalizedRect>;
  meta: Record<string, { rotation: number; z_order: number }>;
  bubbles: { id: string; shape: BubbleGeometryShape }[];
  sfx: { panelId: string; index: number; value: SoundEffectGeometry }[];
}): CanvasState {
  const panels: CanvasState["panels"] = {};
  for (const id of input.panelIds) {
    const rect = input.rects[id];
    if (!rect) continue;
    panels[id] = {
      rect: roundRect(rect),
      rotation: round4(input.meta[id]?.rotation ?? 0),
      z_order: input.meta[id]?.z_order ?? 0,
    };
  }
  const bubbles: CanvasState["bubbles"] = {};
  for (const bubble of input.bubbles) bubbles[bubble.id] = roundShape(bubble.shape);
  const sfx: CanvasState["sfx"] = {};
  for (const item of input.sfx) {
    sfx[item.panelId] = { ...sfx[item.panelId], [item.index]: roundSfx(item.value) };
  }
  return { panels, bubbles, sfx };
}

export function sameCanvasState(a: CanvasState, b: CanvasState): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Diff two states into one undoable command's change list. Entries present
 * only in `after` (a node added since the snapshot) are skipped — restoring
 * never invents nodes; entries missing in `after` keep their current value. */
export function stateChanges(before: CanvasState, after: CanvasState): GeometryCommandChange[] {
  const changes: GeometryCommandChange[] = [];
  for (const [id, next] of Object.entries(after.panels)) {
    const prev = before.panels[id];
    if (!prev) continue;
    if (!sameRect(prev.rect, next.rect)) {
      changes.push({ kind: "panel", id, before: prev.rect, after: next.rect });
    }
    const metaBefore: { rotation?: number; z_order?: number } = {};
    const metaAfter: { rotation?: number; z_order?: number } = {};
    if (prev.rotation !== next.rotation) {
      metaBefore.rotation = prev.rotation;
      metaAfter.rotation = next.rotation;
    }
    if (prev.z_order !== next.z_order) {
      metaBefore.z_order = prev.z_order;
      metaAfter.z_order = next.z_order;
    }
    if (Object.keys(metaAfter).length) {
      changes.push({ kind: "panel-meta", id, before: metaBefore, after: metaAfter });
    }
  }
  for (const [id, next] of Object.entries(after.bubbles)) {
    const prev = before.bubbles[id];
    if (prev && JSON.stringify(prev) !== JSON.stringify(next)) {
      changes.push({ kind: "bubble", id, before: prev, after: next });
    }
  }
  for (const [panelId, byIndex] of Object.entries(after.sfx)) {
    for (const [indexKey, next] of Object.entries(byIndex)) {
      const index = Number(indexKey);
      const prev = before.sfx[panelId]?.[index];
      if (prev && JSON.stringify(prev) !== JSON.stringify(next)) {
        changes.push({ kind: "sfx", panelId, index, before: prev, after: next });
      }
    }
  }
  return changes;
}

// --- layout templates -------------------------------------------------------

const cell = (x: number, y: number, width: number, height: number): NormalizedRect => ({ x, y, width, height });

export const BUILT_IN_TEMPLATES: LayoutTemplate[] = [
  {
    id: "preset-yon-koma",
    name: "标准四格",
    builtIn: true,
    createdAt: 0,
    cells: [
      cell(0.06, 0.04, 0.42, 0.44),
      cell(0.52, 0.04, 0.42, 0.44),
      cell(0.06, 0.52, 0.42, 0.44),
      cell(0.52, 0.52, 0.42, 0.44),
    ],
  },
  {
    id: "preset-three-row",
    name: "三行竖排",
    builtIn: true,
    createdAt: 0,
    cells: [
      cell(0.08, 0.05, 0.84, 0.27),
      cell(0.08, 0.365, 0.84, 0.27),
      cell(0.08, 0.68, 0.84, 0.27),
    ],
  },
  {
    id: "preset-big-two-small",
    name: "一大两小",
    builtIn: true,
    createdAt: 0,
    cells: [
      cell(0.06, 0.04, 0.88, 0.52),
      cell(0.06, 0.6, 0.43, 0.36),
      cell(0.51, 0.6, 0.43, 0.36),
    ],
  },
  {
    id: "preset-hero-side",
    name: "主格加副格",
    builtIn: true,
    createdAt: 0,
    cells: [
      cell(0.06, 0.04, 0.6, 0.92),
      cell(0.7, 0.04, 0.24, 0.44),
      cell(0.7, 0.52, 0.24, 0.44),
    ],
  },
  {
    id: "preset-two-column",
    name: "左右对开",
    builtIn: true,
    createdAt: 0,
    cells: [cell(0.06, 0.06, 0.42, 0.88), cell(0.52, 0.06, 0.42, 0.88)],
  },
  {
    id: "preset-six-grid",
    name: "六格网格",
    builtIn: true,
    createdAt: 0,
    cells: [
      cell(0.06, 0.04, 0.42, 0.29),
      cell(0.52, 0.04, 0.42, 0.29),
      cell(0.06, 0.355, 0.42, 0.29),
      cell(0.52, 0.355, 0.42, 0.29),
      cell(0.06, 0.67, 0.42, 0.29),
      cell(0.52, 0.67, 0.42, 0.29),
    ],
  },
];

const FULL_PAGE: NormalizedRect = { x: 0, y: 0, width: 1, height: 1 };

/** Affine-map a child rect through its panel's old→new bounds: proportional
 * position and size inside the panel, then re-clamped into the page. */
const mapRectThrough = (rect: NormalizedRect, from: NormalizedRect, to: NormalizedRect): NormalizedRect => {
  const fx = to.width / from.width;
  const fy = to.height / from.height;
  return clampRectInto(
    clampRect({
      x: to.x + (rect.x - from.x) * fx,
      y: to.y + (rect.y - from.y) * fy,
      width: rect.width * fx,
      height: rect.height * fy,
    }, MIN_BUBBLE_SIZE),
    FULL_PAGE,
  );
};

const mapPointThrough = (point: { x: number; y: number }, from: NormalizedRect, to: NormalizedRect) => ({
  x: clamp01(to.x + (point.x - from.x) * (to.width / from.width)),
  y: clamp01(to.y + (point.y - from.y) * (to.height / from.height)),
});

/** Pair panels (reading order) with template cells and remap each panel's
 * bounds plus its children (bubbles + sound effects) through the same
 * affine transform. Panels beyond the cell count stay put; extra cells are
 * ignored — the notice in the caller tells the user how many applied. */
export function templateChanges(input: {
  orderedPanelIds: string[];
  rects: Record<string, NormalizedRect>;
  bubblesByPanel: Record<string, { id: string; shape: BubbleGeometryShape }[]>;
  sfxByPanel: Record<string, Record<number, SoundEffectGeometry>>;
  cells: NormalizedRect[];
}): GeometryCommandChange[] {
  const changes: GeometryCommandChange[] = [];
  input.orderedPanelIds.forEach((panelId, index) => {
    const target = input.cells[index];
    const from = input.rects[panelId];
    if (!target || !from || from.width <= 0 || from.height <= 0) return;
    const to = clampRectInto(clampRect(target, 0.02), FULL_PAGE);
    if (!sameRect(from, to)) {
      changes.push({ kind: "panel", id: panelId, before: from, after: roundRect(to) });
    }
    for (const bubble of input.bubblesByPanel[panelId] ?? []) {
      const after: BubbleGeometryShape = roundShape({
        ...bubble.shape,
        rect: mapRectThrough(bubble.shape.rect, from, to),
        anchor: bubble.shape.anchor ? mapPointThrough(bubble.shape.anchor, from, to) : bubble.shape.anchor,
        tail_target: bubble.shape.tail_target ? mapPointThrough(bubble.shape.tail_target, from, to) : bubble.shape.tail_target,
        text_region: bubble.shape.text_region ? mapRectThrough(bubble.shape.text_region, from, to) : bubble.shape.text_region,
      });
      if (JSON.stringify(bubble.shape) !== JSON.stringify(after)) {
        changes.push({ kind: "bubble", id: bubble.id, before: bubble.shape, after });
      }
    }
    const scale = (to.width / from.width + to.height / from.height) / 2;
    for (const [indexKey, value] of Object.entries(input.sfxByPanel[panelId] ?? {})) {
      const mapped = mapPointThrough(value, from, to);
      const after = roundSfx({
        ...value,
        x: mapped.x,
        y: mapped.y,
        size: clamp01(value.size * scale),
      });
      if (JSON.stringify(value) !== JSON.stringify(after)) {
        changes.push({ kind: "sfx", panelId, index: Number(indexKey), before: value, after });
      }
    }
  });
  return changes;
}

// --- localStorage parsing ----------------------------------------------------

export function parseUserTemplates(raw: string): LayoutTemplate[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw) as LayoutTemplate[];
    if (!Array.isArray(list)) return [];
    return list.filter((item) =>
      item && typeof item.id === "string" && Array.isArray(item.cells) && item.cells.length > 0,
    );
  } catch {
    return [];
  }
}

export function parseSnapshots(raw: string): StoryboardSnapshot[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw) as StoryboardSnapshot[];
    if (!Array.isArray(list)) return [];
    return list.filter((item) => item && typeof item.id === "string" && item.state && typeof item.state === "object");
  } catch {
    return [];
  }
}

// --- freehand annotations ----------------------------------------------------

/** One freehand stroke on the annotation layer. Points are page-normalized
 * (0-1) so strokes survive zoom/resize like every other canvas element.
 * Annotations are local marks — they persist per page in localStorage and are
 * never part of the geometry save payload or the undo stack. */
export interface AnnotationStroke {
  id: string;
  points: { x: number; y: number }[];
}

export const annotationsKey = (pageId: string) => `mangaflow.storyboard-annotations.${pageId}`;

export function parseAnnotations(raw: string): AnnotationStroke[] {
  if (!raw) return [];
  try {
    const list = JSON.parse(raw) as AnnotationStroke[];
    if (!Array.isArray(list)) return [];
    return list
      .map((item): AnnotationStroke | null => {
        if (!item || typeof item.id !== "string" || !Array.isArray(item.points)) return null;
        const points = item.points
          .filter((point) => point && Number.isFinite(point.x) && Number.isFinite(point.y))
          .map((point) => ({ x: clamp01(point.x), y: clamp01(point.y) }));
        return points.length >= 2 ? { id: item.id, points } : null;
      })
      .filter((item): item is AnnotationStroke => item !== null);
  } catch {
    return [];
  }
}
