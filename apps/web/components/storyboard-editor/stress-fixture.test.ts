// V02-32: the 100-node stress fixture is a pure client-side synthesis. These
// tests pin its node accounting, determinism and geometry so the render layer
// can rely on it, and document why it can never be persisted (product API caps
// real pages at 3–8 panels and 8 bubbles).
import { describe, expect, it } from "vitest";

import {
  buildStressStoryboard,
  STRESS_BUBBLES_PER_PANEL,
  STRESS_NODE_COUNT,
  STRESS_PANEL_COUNT,
  stressPanelRect,
} from "./stress-fixture";

function pointInRect(point: { x: number; y: number }, rect: { x: number; y: number; width: number; height: number }) {
  return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height;
}

describe("stress fixture (V02-32)", () => {
  it("凑整 100 个可命中对象：20 个格轮廓 + 20 格 × 每格 4 气泡 = 100", () => {
    expect(STRESS_PANEL_COUNT * (1 + STRESS_BUBBLES_PER_PANEL)).toBe(STRESS_NODE_COUNT);
    const snapshot = buildStressStoryboard();
    expect(snapshot.panels).toHaveLength(STRESS_PANEL_COUNT);
    expect(snapshot.panels.flatMap((panel) => panel.dialogues)).toHaveLength(STRESS_NODE_COUNT - STRESS_PANEL_COUNT);
    expect(snapshot.candidate_count).toBe(0);
  });

  it("确定性：两次构建逐字节一致，不依赖随机或时间", () => {
    expect(JSON.stringify(buildStressStoryboard())).toBe(JSON.stringify(buildStressStoryboard()));
  });

  it("几何合法：每个格都在页内，每个气泡都在自己的格内，reading_order 连续", () => {
    const snapshot = buildStressStoryboard();
    snapshot.panels.forEach((panel, index) => {
      expect(panel.reading_order).toBe(index + 1);
      const bounds = stressPanelRect(index);
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.y).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(1);
      expect(bounds.y + bounds.height).toBeLessThanOrEqual(1);
      expect(panel.id).toBe(`stress-panel-${index}`);
      panel.dialogues.forEach((dialogue) => {
        expect(dialogue.panel_id).toBe(panel.id);
        const rect = dialogue.bubble?.rect;
        expect(rect).toBeDefined();
        expect(pointInRect({ x: rect!.x, y: rect!.y }, bounds)).toBe(true);
        expect(pointInRect({ x: rect!.x + rect!.width, y: rect!.y + rect!.height }, bounds)).toBe(true);
      });
    });
  });

  it("超出产品门禁（20 格 > 8 上限、80 气泡 > 每页 8 上限）：只能前端合成，不能经 API 落库", () => {
    const snapshot = buildStressStoryboard();
    expect(snapshot.page.panel_count).toBeGreaterThan(8);
    expect(snapshot.page.estimated_bubbles).toBeGreaterThan(8);
    // 页 id 是固定合成值，不是任何真实 MangaPage 主键。
    expect(snapshot.page.id).toBe("stress-page");
  });
});
