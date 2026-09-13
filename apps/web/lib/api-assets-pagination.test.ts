import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type Asset } from "./api";

// #665：assets 的 200 上限翻页循环此前在全库只被整体 spy 替换
// （generate-section.test.tsx / workspace-hook-regressions.test.tsx 均
// mockResolvedValue([])），循环体从未真实执行过。这里照抄
// api-scene-assets.test.ts 的 fetch stub 惯例，走真实 request 实现，
// 让 offset 累加与短页终止第一次被断言覆盖。
function assetFixture(overrides: Partial<Asset> = {}): Asset {
  return {
    id: "asset-1",
    project_id: "project-1",
    kind: "CHARACTER_REFERENCE",
    original_name: "reference.png",
    display_name: null,
    mime_type: "image/png",
    byte_size: 1024,
    width: null,
    height: null,
    status: "READY",
    created_at: "2026-09-01T00:00:00Z",
    content_url: null,
    thumbnail_url: null,
    ...overrides,
  };
}

// request 拼出的地址形如 "/api/v1/assets?project_id=…&limit=200&offset=0"，
// 是相对路径，不能直接 new URL()；只取查询串解析。
function queryParamsOf(input: unknown): URLSearchParams {
  const raw = String(input);
  const query = raw.slice(raw.indexOf("?") + 1);
  return new URLSearchParams(query);
}

describe("api.assets 翻页（#665）", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("首页恰好 200 条时翻到 offset=200，短页终止后不再发第三次请求", async () => {
    // 205 条：第一页 slice(0, 200) 恰好是满页 200 条，第二页只剩 5 条短页。
    const all = Array.from({ length: 205 }, (_, index) =>
      assetFixture({ id: `asset-${index}` }));
    const fetchMock = vi.fn(async (input: unknown) => {
      const offset = Number(queryParamsOf(input).get("offset") ?? "0");
      return {
        ok: true,
        status: 200,
        json: async () => all.slice(offset, offset + 200),
      };
    });
    vi.stubGlobal("fetch", fetchMock);

    const merged = await api.assets("project-1");

    // 两页按顺序合并，第 201 条起不丢失、不重复。
    expect(merged.map((asset) => asset.id)).toEqual(all.map((asset) => asset.id));
    // 请求序列：满页之后必须按 limit 步进到 offset=200，两页都带最大 limit。
    const calls = fetchMock.mock.calls.map(([input]) => queryParamsOf(input));
    expect(calls.map((params) => params.get("offset"))).toEqual(["0", "200"]);
    expect(calls.map((params) => params.get("limit"))).toEqual(["200", "200"]);
    expect(calls.every((params) => params.get("project_id") === "project-1")).toBe(true);
    // 短页即终止：mock 对第三次请求照样能返回空页，恰好两次说明循环停了。
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
