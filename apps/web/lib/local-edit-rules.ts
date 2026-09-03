// V02-43B local-edit domain rules. Pure mask math + capability gating +
// regenerate_region envelope compilation for the local edit workspace. The
// backend contract is V02-42B (apps/api/app/domain/director_commands.py):
// at most 8 regions, at most 64 points per region, instruction required, and
// the derived parent is always the page's adopted (selected) candidate. This
// module never calls the API — the workspace component owns mutations, and
// local edit must never reach api.generateCandidate (whole-page POST).

import type {
  DirectorCommandEnvelope,
  MangaPage,
  ModelCapability,
} from "@/lib/api";

/** Mirrors MASK_MAX_REGIONS in apps/api/app/domain/director_commands.py. */
export const LOCAL_EDIT_MAX_REGIONS = 8;
/** Mirrors MASK_MAX_POINTS in apps/api/app/domain/director_commands.py. */
export const LOCAL_EDIT_MAX_POINTS = 64;
/** Local undo stack depth for mask strokes (issue #100: at least 20). */
export const LOCAL_EDIT_HISTORY_LIMIT = 50;

export type MaskPoint = [number, number];

export interface MaskRegion {
  points: MaskPoint[];
}

export type MaskModel = Pick<
  ModelCapability,
  | "logical_alias"
  | "display_name"
  | "provider"
  | "model_id"
  | "model_type"
  | "operations"
  | "enabled"
  | "accepts_explicit_mask"
  | "supports_instruction_region_edit"
  | "preserves_outside_region"
  | "whole_image_reference_only"
  | "resolutions"
>;

export interface MaskHistoryState {
  past: MaskRegion[][];
  present: MaskRegion[];
  future: MaskRegion[][];
}

export function clampPoint(point: MaskPoint, width: number, height: number): MaskPoint {
  return [
    Math.min(width, Math.max(0, point[0])),
    Math.min(height, Math.max(0, point[1])),
  ];
}

/** Axis-aligned rectangle drag → 4-point polygon in image pixel space. */
export function rectRegion(
  start: MaskPoint,
  end: MaskPoint,
  width: number,
  height: number,
  square = false,
): MaskRegion {
  let [x1, y1] = end;
  if (square) {
    const dx = end[0] - start[0];
    const dy = end[1] - start[1];
    const size = Math.max(Math.abs(dx), Math.abs(dy));
    x1 = start[0] + Math.sign(dx || 1) * size;
    y1 = start[1] + Math.sign(dy || 1) * size;
  }
  const a = clampPoint([Math.min(start[0], x1), Math.min(start[1], y1)], width, height);
  const b = clampPoint([Math.max(start[0], x1), Math.max(start[1], y1)], width, height);
  return { points: [a, [b[0], a[1]], b, [a[0], b[1]]] };
}

/** Appends a sample to a freehand stroke; drops repeated points. */
export function extendStroke(stroke: MaskPoint[], point: MaskPoint): MaskPoint[] {
  const last = stroke[stroke.length - 1];
  if (last && last[0] === point[0] && last[1] === point[1]) return stroke;
  return [...stroke, point];
}

/** Even sampling so a stroke never exceeds the backend point cap. */
export function simplifyStroke(points: MaskPoint[]): MaskPoint[] {
  if (points.length <= LOCAL_EDIT_MAX_POINTS) return points;
  const stride = (points.length - 1) / (LOCAL_EDIT_MAX_POINTS - 1);
  const sampled: MaskPoint[] = [];
  for (let index = 0; index < LOCAL_EDIT_MAX_POINTS; index += 1) {
    sampled.push(points[Math.round(index * stride)]);
  }
  return sampled;
}

/**
 * Finishes a freehand stroke as a closed polygon. A dab (1–2 samples, e.g. a
 * click) becomes a small square of the brush diameter; longer strokes become
 * a ribbon outline offset by the brush radius on both sides, so the region
 * the backend receives has real brush thickness and area.
 */
export function strokeToRegion(
  stroke: MaskPoint[],
  brushDiameter: number,
  width: number,
  height: number,
): MaskRegion | null {
  if (!stroke.length) return null;
  if (stroke.length < 3) {
    const [cx, cy] = stroke[0];
    const radius = Math.max(brushDiameter, 4) / 2;
    return rectRegion([cx - radius, cy - radius], [cx + radius, cy + radius], width, height);
  }
  const radius = Math.max(brushDiameter, 4) / 2;
  const left: MaskPoint[] = [];
  const right: MaskPoint[] = [];
  for (let index = 0; index < stroke.length; index += 1) {
    const previous = stroke[Math.max(0, index - 1)];
    const next = stroke[Math.min(stroke.length - 1, index + 1)];
    const dx = next[0] - previous[0];
    const dy = next[1] - previous[1];
    const length = Math.hypot(dx, dy) || 1;
    const offsetX = (-dy / length) * radius;
    const offsetY = (dx / length) * radius;
    left.push([stroke[index][0] + offsetX, stroke[index][1] + offsetY]);
    right.push([stroke[index][0] - offsetX, stroke[index][1] - offsetY]);
  }
  const points = simplifyStroke([...left, ...right.reverse()])
    .map((point) => clampPoint(point, width, height));
  return { points };
}

function segmentIntersects(
  a: MaskPoint,
  b: MaskPoint,
  c: MaskPoint,
  d: MaskPoint,
): boolean {
  const orientation = (p: MaskPoint, q: MaskPoint, r: MaskPoint) => {
    const value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    if (Math.abs(value) < 1e-9) return 0;
    return value > 0 ? 1 : 2;
  };
  const onSegment = (p: MaskPoint, q: MaskPoint, r: MaskPoint) =>
    q[0] >= Math.min(p[0], r[0]) && q[0] <= Math.max(p[0], r[0])
    && q[1] >= Math.min(p[1], r[1]) && q[1] <= Math.max(p[1], r[1]);
  const o1 = orientation(a, b, c);
  const o2 = orientation(a, b, d);
  const o3 = orientation(c, d, a);
  const o4 = orientation(c, d, b);
  if (o1 !== o2 && o3 !== o4) return true;
  if (o1 === 0 && onSegment(a, c, b)) return true;
  if (o2 === 0 && onSegment(a, d, b)) return true;
  if (o3 === 0 && onSegment(c, a, d)) return true;
  if (o4 === 0 && onSegment(c, b, d)) return true;
  return false;
}

export function pointInPolygon(point: MaskPoint, points: MaskPoint[]): boolean {
  let inside = false;
  for (let index = 0, j = points.length - 1; index < points.length; j = index, index += 1) {
    const [xi, yi] = points[index];
    const [xj, yj] = points[j];
    if (
      (yi > point[1]) !== (yj > point[1])
      && point[0] < ((xj - xi) * (point[1] - yi)) / (yj - yi) + xi
    ) {
      inside = !inside;
    }
  }
  return inside;
}

/** Erase removes whole regions the stroke path touches (polygon granularity). */
export function eraseRegions(
  regions: MaskRegion[],
  stroke: MaskPoint[],
): MaskRegion[] {
  if (!stroke.length) return regions;
  return regions.filter((region) => {
    const points = region.points;
    for (const point of stroke) {
      if (pointInPolygon(point, points)) return false;
    }
    for (let index = 0; index < stroke.length - 1; index += 1) {
      for (let edge = 0; edge < points.length; edge += 1) {
        const next = (edge + 1) % points.length;
        if (segmentIntersects(stroke[index], stroke[index + 1], points[edge], points[next])) {
          return false;
        }
      }
    }
    return true;
  });
}

/** Shoelace area of one polygon. */
export function polygonArea(points: MaskPoint[]): number {
  let sum = 0;
  for (let index = 0, j = points.length - 1; index < points.length; j = index, index += 1) {
    sum += points[j][0] * points[index][1] - points[index][0] * points[j][1];
  }
  return Math.abs(sum / 2);
}

/** Selected share of the source image (0..1) for the「已选 X% 面积」note. */
export function maskAreaRatio(regions: MaskRegion[], width: number, height: number): number {
  if (!width || !height) return 0;
  const area = regions.reduce((total, region) => total + polygonArea(region.points), 0);
  return area / (width * height);
}

export function formatMaskArea(regions: MaskRegion[], width: number, height: number): string {
  const percent = maskAreaRatio(regions, width, height) * 100;
  return percent >= 10 ? percent.toFixed(0) : percent.toFixed(1);
}

export function pushMaskHistory(
  state: MaskHistoryState,
  next: MaskRegion[],
): MaskHistoryState {
  const past = [...state.past, state.present];
  if (past.length > LOCAL_EDIT_HISTORY_LIMIT) past.splice(0, past.length - LOCAL_EDIT_HISTORY_LIMIT);
  return { past, present: next, future: [] };
}

export function undoMask(state: MaskHistoryState): MaskHistoryState {
  if (!state.past.length) return state;
  const past = [...state.past];
  const present = past.pop()!;
  return { past, present, future: [state.present, ...state.future] };
}

export function redoMask(state: MaskHistoryState): MaskHistoryState {
  if (!state.future.length) return state;
  const [present, ...future] = state.future;
  return { past: [...state.past, state.present], present, future };
}

/**
 * Models the local editor may offer for the paid call. Anything the catalog
 * does not explicitly declare (missing/unknown bit, disabled row, no
 * image_edit operation) is treated as unsupported — fail closed, never guess.
 */
export function maskCapableModels(models: MaskModel[]): MaskModel[] {
  return models.filter((model) =>
    model.model_type === "IMAGE"
    && model.operations.includes("image_edit")
    && model.enabled
    && model.accepts_explicit_mask === true);
}

/**
 * V02-44B (capability matrix §7): honest surface label for one model. The
 * region-edit bits are declared per catalog model — explicit mask beats
 * instruction-only beats whole-image-reference; anything undeclared stays
 * "unsupported" instead of being guessed. A model that only edits the whole
 * image (or only takes instructions) is never silently treated as a local
 * editor.
 */
export function regionEditSurfaceLabel(model: MaskModel): string {
  if (model.accepts_explicit_mask === true) return "显式 mask 局部编辑";
  if (model.supports_instruction_region_edit === true)
    return "仅 instruction 区域编辑（不支持选区 mask）";
  if (model.whole_image_reference_only === true)
    return "仅整图参考编辑（不保证区域外不变）";
  return "未声明区域编辑能力（按不支持处理）";
}

/**
 * Distinct declared surfaces across the catalog's enabled image-edit models,
 * for the blocked-state explanation. Null once a genuinely mask-capable model
 * exists (the picker can proceed without an honesty note).
 */
export function catalogRegionSurfaceSummary(models: MaskModel[]): string | null {
  const editable = models.filter(
    (model) => model.model_type === "IMAGE"
      && model.operations.includes("image_edit")
      && model.enabled);
  if (!editable.length) return null;
  const labels = Array.from(new Set(editable.map(regionEditSurfaceLabel)));
  const allMaskCapable = editable.every((model) => model.accepts_explicit_mask === true);
  if (allMaskCapable) return null;
  return labels.join("；");
}

export function maskCapabilityNotice(models: MaskModel[]): string | null {
  if (maskCapableModels(models).length) return null;
  return "当前模型不能按选区重绘：目录中没有已启用且声明显式 mask 能力（accepts_explicit_mask）的模型。可选：到系统设置更换/启用支持 mask 局部编辑的模型，或取消本次局部编辑。局部编辑不会按整页重绘降级。";
}

export interface LocalEditGateInput {
  hasMask: boolean;
  instruction: string;
  capableModels: MaskModel[];
  sourceIsAdopted: boolean;
  sourceLabel: string;
  adoptedLabel: string | null;
}

/**
 * Generation gate. Order matters: the capability gate fires before the paid
 * request can even be proposed, and an empty mask can never generate. A
 * source that is not the page's adopted candidate is refused because V02-42B
 * always derives from page.selected_candidate_id — drawing on different
 * pixels would silently regenerate the wrong base image.
 */
export function localEditGate(input: LocalEditGateInput): { ok: boolean; reason: string | null } {
  const notice = maskCapabilityNotice(input.capableModels);
  if (notice) return { ok: false, reason: notice };
  if (!input.instruction.trim()) return { ok: false, reason: "请先填写局部重绘指令（要改什么）" };
  if (!input.hasMask) return { ok: false, reason: "空选区不能生成：请先用矩形或画笔画出要重绘的区域" };
  if (!input.sourceIsAdopted) {
    return {
      ok: false,
      reason: `局部派生的父候选是当前采用候选（V02-42B）。正在编辑的 ${input.sourceLabel} 并非${input.adoptedLabel ? `采用候选 ${input.adoptedLabel}` : "采用候选：本页还没有已暂选候选"}；请先暂选这张候选再回到局部编辑，或回到采用候选进入。`,
    };
  }
  return { ok: true, reason: null };
}

export interface RegionEnvelopeInput {
  projectId: string;
  page: Pick<MangaPage, "id" | "version" | "page_number">;
  regions: MaskRegion[];
  instruction: string;
  modelAlias: string;
  resolution?: string | null;
  newId?: () => string;
  now?: () => string;
}

/**
 * Compiles the mask into a V02-40 regenerate_region envelope for the existing
 * director propose → preview → accept flow. Points are rounded to 2 decimals
 * so the payload stays well under the 16KB command cap.
 */
export function buildRegionRegenerateEnvelope(input: RegionEnvelopeInput): DirectorCommandEnvelope {
  const newId = input.newId ?? defaultId;
  const now = input.now ?? (() => new Date().toISOString());
  const regions = input.regions.slice(0, LOCAL_EDIT_MAX_REGIONS).map((region) => ({
    points: simplifyStroke(region.points)
      .slice(0, LOCAL_EDIT_MAX_POINTS)
      .map((point) => [
        Math.round(point[0] * 100) / 100,
        Math.round(point[1] * 100) / 100,
      ] as MaskPoint),
  }));
  const payload: Record<string, unknown> = {
    instruction: input.instruction.trim(),
    mask: regions,
    model_alias: input.modelAlias,
  };
  if (input.resolution) payload.resolution = input.resolution;
  return {
    schema_version: 1,
    command_id: newId(),
    command_group_id: newId(),
    created_at: now(),
    target: { project_id: input.projectId, page_id: input.page.id },
    expected_version: { scope: "page", value: input.page.version },
    retry_of_command_id: null,
    operation: "regenerate_region",
    payload,
    source: {
      user_prompt: input.instruction.trim(),
      reference_asset_ids: [],
      model: null,
      raw_output_id: "local_edit_v1",
    },
  };
}

function defaultId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = Math.trunc(Math.random() * 16);
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

export function candidateMatchesCommand(candidate: PageCandidateLike, commandId: string): boolean {
  const lineage = (candidate.prompt_snapshot ?? {}).lineage;
  return Boolean(lineage)
    && typeof lineage === "object"
    && (lineage as Record<string, unknown>).source_command_id === commandId;
}

export interface PageCandidateLike {
  prompt_snapshot: Record<string, unknown>;
}

export type DerivedCandidatePhase = "none" | "pending" | "done" | "failed" | "canceled";

const TERMINAL_FAILED = new Set(["FAILED"]);
const TERMINAL_CANCELED = new Set(["CANCELED", "CANCELLED"]);

/** Maps the derived candidate's queue state onto the editor's job phase. */
export function derivedCandidatePhase(candidate: {
  status: string;
  asset_id: string | null;
} | null | undefined): DerivedCandidatePhase {
  if (!candidate) return "none";
  if (TERMINAL_CANCELED.has(candidate.status)) return "canceled";
  if (TERMINAL_FAILED.has(candidate.status)) return "failed";
  if (candidate.asset_id) return "done";
  return "pending";
}
