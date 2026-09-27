// Unit tests for the precision-editing geometry helpers: align/distribute/
// same-size, z-order ops, grid lines, equal-gap snap, group scale, rotation.
import { describe, expect, it } from "vitest";

import type { CanvasInfo, NormalizedRect } from "@/lib/api";

import {
  alignRects,
  angleBetween,
  defaultSfxPosition,
  distributeRects,
  equalGapSnap,
  gridLinesFor,
  normalizeRotation,
  rectCenter,
  rectsBoundingBox,
  rotatePointAround,
  sameSizeRects,
  scaleRectWithBounds,
  zOrderChanges,
} from "./geometry";

const canvas: CanvasInfo = { width_mm: 182, height_mm: 257, bleed_mm: 3, safe_mm: 5, unit: "mm" };

describe("precision geometry helpers", () => {
  const rects: Record<string, NormalizedRect> = {
    a: { x: 0.1, y: 0.1, width: 0.3, height: 0.2 },
    b: { x: 0.5, y: 0.3, width: 0.2, height: 0.4 },
    c: { x: 0.8, y: 0.5, width: 0.15, height: 0.1 },
  };

  it("alignRects: 左/水平居中/右对齐", () => {
    expect(alignRects(rects, ["a", "b"], "left")).toEqual([
      { id: "b", before: rects.b, after: { ...rects.b, x: 0.1 } },
    ]);
    expect(alignRects(rects, ["a", "b"], "right")).toEqual([
      { id: "a", before: rects.a, after: { ...rects.a, x: 0.4 } },
    ]);
    // bbox center x = 0.35: a is already centered... a center = 0.25, bbox
    // spans 0.1..0.7 → center 0.4 → a.x = 0.4 - 0.15 = 0.25 → actually moves.
    const centered = alignRects(rects, ["a", "b"], "centerX");
    expect(centered.find((change) => change.id === "a")?.after.x).toBeCloseTo(0.25, 6);
    expect(centered.find((change) => change.id === "b")?.after.x).toBeCloseTo(0.3, 6);
  });

  it("alignRects: 顶/垂直居中/底对齐", () => {
    expect(alignRects(rects, ["a", "c"], "top")).toEqual([
      { id: "c", before: rects.c, after: { ...rects.c, y: 0.1 } },
    ]);
    expect(alignRects(rects, ["a", "b"], "bottom")).toEqual([
      { id: "a", before: rects.a, after: { ...rects.a, y: 0.5 } },
    ]);
  });

  it("alignRects: 单选不产生变更", () => {
    expect(alignRects(rects, ["a"], "left")).toEqual([]);
  });

  it("distributeRects: 中间格等距排开，两端固定", () => {
    // span 0.05..0.95, sizes 0.6 → gap 0.15 → b 应落在 a 右缘 + gap = 0.4
    const three: Record<string, NormalizedRect> = {
      a: { x: 0.05, y: 0, width: 0.2, height: 0.1 },
      b: { x: 0.4, y: 0, width: 0.2, height: 0.1 },
      c: { x: 0.75, y: 0, width: 0.2, height: 0.1 },
    };
    expect(distributeRects(three, ["a", "b", "c"], "x")).toEqual([]);
    const moved = { ...three, b: { x: 0.3, y: 0, width: 0.2, height: 0.1 } };
    const applied = distributeRects(moved, ["a", "b", "c"], "x");
    expect(applied).toHaveLength(1);
    expect(applied[0].id).toBe("b");
    expect(applied[0].after.x).toBeCloseTo(0.4, 6);
  });

  it("distributeRects: 不足三格不动作", () => {
    expect(distributeRects(rects, ["a", "b"], "x")).toEqual([]);
  });

  it("sameSizeRects: 以第一格为准同步宽/高/大小，页边自动收回", () => {
    const widths = sameSizeRects(rects, ["a", "b", "c"], "width");
    expect(widths).toHaveLength(2);
    expect(widths[0]).toEqual({ id: "b", before: rects.b, after: { ...rects.b, width: 0.3 } });
    // c 在 x=0.8，同步到 0.3 宽会出页 → clampRect 收回到 x=0.7
    expect(widths[1].id).toBe("c");
    expect(widths[1].after.x).toBeCloseTo(0.7, 6);
    expect(widths[1].after.width).toBeCloseTo(0.3, 6);
    expect(sameSizeRects(rects, ["a", "b"], "size")).toEqual([
      { id: "b", before: rects.b, after: { ...rects.b, width: 0.3, height: 0.2 } },
    ]);
  });

  it("zOrderChanges: 上移与下一层互换、置顶 = max+1", () => {
    const z = { a: 1, b: 2, c: 3 };
    expect(zOrderChanges(z, "a", "up")).toEqual([
      { id: "a", before: 1, after: 2 },
      { id: "b", before: 2, after: 1 },
    ]);
    expect(zOrderChanges(z, "a", "top")).toEqual([{ id: "a", before: 1, after: 4 }]);
    expect(zOrderChanges(z, "c", "down")).toEqual([
      { id: "c", before: 3, after: 2 },
      { id: "b", before: 2, after: 3 },
    ]);
    // 已在顶层：无副作用
    expect(zOrderChanges(z, "c", "up")).toEqual([]);
    expect(zOrderChanges(z, "c", "top")).toEqual([]);
  });

  it("zOrderChanges: 置底在最小值为 1 时把其他格整体上移", () => {
    const z = { a: 1, b: 2, c: 3 };
    const changes = zOrderChanges(z, "c", "bottom");
    expect(changes).toContainEqual({ id: "c", before: 3, after: 1 });
    expect(changes).toContainEqual({ id: "a", before: 1, after: 2 });
    expect(changes).toContainEqual({ id: "b", before: 2, after: 3 });
  });

  it("gridLinesFor: 10mm 网格落在归一化线上，步长超页幅返回空", () => {
    const grid = gridLinesFor(canvas, 10);
    // x: 10..180mm（180 < 182）共 18 条；y: 10..250mm 共 25 条。
    expect(grid.x).toHaveLength(18);
    expect(grid.y).toHaveLength(25);
    expect(grid.x[0]).toBeCloseTo(10 / 182, 4);
    expect(grid.y[0]).toBeCloseTo(10 / 257, 4);
    expect(gridLinesFor(canvas, 500)).toEqual({ x: [], y: [] });
  });

  it("equalGapSnap: 两邻之间的等距位置吸附并给出 gap 参考线", () => {
    // a 右缘 0.3、b 左缘 0.7 → 等距中心位 = (0.3+0.7-0.2)/2 = 0.4
    const moving: NormalizedRect = { x: 0.405, y: 0.6, width: 0.2, height: 0.1 };
    const result = equalGapSnap(
      moving,
      [
        { x: 0.1, y: 0.5, width: 0.2, height: 0.3 },
        { x: 0.7, y: 0.5, width: 0.2, height: 0.3 },
      ],
      0.01,
      { x: false, y: false },
    );
    expect(result.deltaX).toBeCloseTo(-0.005, 6);
    expect(result.guides).toEqual([
      { axis: "x", at: 0.3, kind: "gap" },
      { axis: "x", at: 0.7, kind: "gap" },
    ]);
  });

  it("equalGapSnap: 已吸附的轴不再叠加等距", () => {
    const moving: NormalizedRect = { x: 0.405, y: 0.6, width: 0.2, height: 0.1 };
    const result = equalGapSnap(
      moving,
      [
        { x: 0.1, y: 0.5, width: 0.2, height: 0.3 },
        { x: 0.7, y: 0.5, width: 0.2, height: 0.3 },
      ],
      0.01,
      { x: true, y: false },
    );
    expect(result.deltaX).toBe(0);
    expect(result.guides).toEqual([]);
  });

  it("scaleRectWithBounds: 组缩放按比例映射每个格", () => {
    const from: NormalizedRect = { x: 0.1, y: 0.1, width: 0.8, height: 0.35 };
    const to: NormalizedRect = { x: 0.1, y: 0.1, width: 0.85, height: 0.35 };
    const scaled = scaleRectWithBounds(rects.a, from, to, 0.03);
    expect(scaled.width).toBeCloseTo(0.3 * (0.85 / 0.8), 6);
    expect(scaled.x).toBeCloseTo(0.1, 6);
    const scaledB = scaleRectWithBounds(rects.b, from, to, 0.03);
    expect(scaledB.x).toBeCloseTo(0.1 + (0.5 - 0.1) * (0.85 / 0.8), 6);
  });

  it("normalizeRotation: 收进 (-360, 360]", () => {
    expect(normalizeRotation(370)).toBeCloseTo(10, 6);
    expect(normalizeRotation(-370)).toBeCloseTo(-10, 6);
    expect(normalizeRotation(0)).toBe(0);
  });

  it("rotatePointAround / angleBetween: 纵横比校正下往返一致", () => {
    const center = { x: 0.5, y: 0.5 };
    const point = { x: 0.8, y: 0.5 };
    const rotated = rotatePointAround(point, center, 90, 1.4);
    expect(rotated.x).toBeCloseTo(0.5, 6);
    expect(rotated.y).toBeCloseTo(0.5 + 0.3 / 1.4, 6);
    expect(angleBetween(center, point, 1.4)).toBeCloseTo(0, 6);
    expect(angleBetween(center, rotated, 1.4)).toBeCloseTo(90, 6);
  });

  it("rectsBoundingBox / rectCenter / defaultSfxPosition", () => {
    const bbox = rectsBoundingBox([rects.a, rects.b])!;
    expect(bbox.x).toBeCloseTo(0.1, 6);
    expect(bbox.y).toBeCloseTo(0.1, 6);
    expect(bbox.width).toBeCloseTo(0.6, 6);
    expect(bbox.height).toBeCloseTo(0.6, 6);
    expect(rectCenter(rects.a)).toEqual({ x: 0.25, y: 0.2 });
    const fallback = defaultSfxPosition({ x: 0.1, y: 0.1, width: 0.4, height: 0.3 }, 1);
    expect(fallback.x).toBeGreaterThan(0);
    expect(fallback.y).toBeGreaterThan(0);
  });
});
