import { describe, expect, it } from "vitest";

import { computeStrip, scaledPageHeight } from "./mobile-preview";

describe("scaledPageHeight", () => {
  it("按宽高比缩放到目标宽度", () => {
    expect(scaledPageHeight(1440, 2160, 1080)).toBe(1620);
    expect(scaledPageHeight(1000, 1000, 750)).toBe(750);
  });

  it("缺尺寸时回退 4:3 占位高", () => {
    expect(scaledPageHeight(null, null, 1080)).toBe(1440);
    expect(scaledPageHeight(0, 100, 1080)).toBe(1440);
  });
});

describe("computeStrip", () => {
  it("空条带返回零高度且无切片", () => {
    const strip = computeStrip([], 16, 4096);
    expect(strip.totalHeight).toBe(0);
    expect(strip.slices).toEqual([]);
  });

  it("总高在片高内时只有一片，末页不带尾间距", () => {
    const strip = computeStrip(
      [{ id: "a", heightPx: 1000 }, { id: "b", heightPx: 1000 }],
      16,
      4096,
    );
    expect(strip.totalHeight).toBe(2016);
    expect(strip.slices).toHaveLength(1);
    expect(strip.slices[0]).toMatchObject({ start: 0, end: 2016, oversized: false });
    expect(strip.slices[0].itemIds).toEqual(["a", "b"]);
  });

  it("超出片高时在页间缝切断，边界落在下一条目顶边", () => {
    // 三条 1000px 条目、间距 16、片高 1100：每片只能装一条。
    const strip = computeStrip(
      [{ id: "a", heightPx: 1000 }, { id: "b", heightPx: 1000 }, { id: "c", heightPx: 1000 }],
      16,
      1100,
    );
    // a: [0,1000) gap:[1000,1016) b: [1016,2016) gap:[2016,2032) c:[2032,3032)
    expect(strip.slices).toHaveLength(3);
    expect(strip.slices[0]).toMatchObject({ start: 0, end: 1016 });
    expect(strip.slices[1]).toMatchObject({ start: 1016, end: 2032 });
    expect(strip.slices[2]).toMatchObject({ start: 2032, end: 3032 });
    expect(strip.slices.map((slice) => slice.itemIds)).toEqual([["a"], ["b"], ["c"]]);
  });

  it("尽量装满：能塞下就不切", () => {
    const strip = computeStrip(
      [{ id: "a", heightPx: 500 }, { id: "b", heightPx: 400 }, { id: "c", heightPx: 400 }],
      16,
      1000,
    );
    // a+gap+b = 916 ≤ 1000；c 顶边 932，底边 1332 > 1000 → 在 932 页间缝处切
    expect(strip.slices).toHaveLength(2);
    expect(strip.slices[0].itemIds).toEqual(["a", "b"]);
    expect(strip.slices[1].itemIds).toEqual(["c"]);
  });

  it("单页超过最大片高时独占一片并标记 oversized", () => {
    const strip = computeStrip(
      [{ id: "a", heightPx: 300 }, { id: "big", heightPx: 5000 }, { id: "c", heightPx: 300 }],
      0,
      1000,
    );
    // a: [0,300) big: [300,5300) c: [5300,5600)
    expect(strip.slices).toHaveLength(3);
    const oversized = strip.slices.find((slice) => slice.oversized);
    expect(oversized).toMatchObject({ start: 300, end: 5300, itemIds: ["big"] });
    expect(strip.slices[0].itemIds).toEqual(["a"]);
    expect(strip.slices[2].itemIds).toEqual(["c"]);
  });

  it("超长页位于片首时同样正确切分", () => {
    const strip = computeStrip([{ id: "big", heightPx: 5000 }], 0, 1000);
    expect(strip.slices).toHaveLength(1);
    expect(strip.slices[0]).toMatchObject({ start: 0, end: 5000, oversized: true });
  });
});
