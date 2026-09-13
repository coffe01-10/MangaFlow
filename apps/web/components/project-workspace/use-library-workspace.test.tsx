import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Library } from "@/lib/api";

import { localDayBoundariesToUtc, useLibraryWorkspace } from "./use-library-workspace";

// #658：Windows 进程 TZ 不可靠，jsdom 跟随宿主时区，无法用切换 TZ 构造
// 非 UTC 环境；改为固定 mock Date.prototype.getTimezoneOffset 驱动转换。
// 每个用例持有自己的 spy 并在 afterEach 恢复，避免污染其他文件外用例。
let timezoneOffsetSpy: ReturnType<typeof vi.spyOn> | null = null;

function fixedOffset(minutes: number) {
  timezoneOffsetSpy = vi.spyOn(Date.prototype, "getTimezoneOffset").mockReturnValue(minutes);
}

function offsetSwitchingAt(switchMs: number, before: number, after: number) {
  timezoneOffsetSpy = vi.spyOn(Date.prototype, "getTimezoneOffset").mockImplementation(
    function (this: Date) {
      return this.getTime() < switchMs ? before : after;
    } as never,
  );
}

const libraryApi = vi.spyOn(api, "library");
const exportsApi = vi.spyOn(api, "exports");
const chapterProductionApi = vi.spyOn(api, "chapterProductionReadiness");

function libraryFixture(): Library {
  return { groups: [], total_candidates: 0, favorite_count: 0, next_cursor: null, limit: 30 };
}

function LibraryProbe({ collect }: { collect: (workspace: ReturnType<typeof useLibraryWorkspace>) => void }) {
  const workspace = useLibraryWorkspace({ id: "project-1", section: "library", activeChapterId: "chapter-1" });
  collect(workspace);
  return null;
}

function renderLibraryProbe() {
  let latest: ReturnType<typeof useLibraryWorkspace> | null = null;
  const view = render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <LibraryProbe collect={(workspace) => { latest = workspace; }} />
    </QueryClientProvider>,
  );
  return { view, workspace: () => latest! };
}

beforeEach(() => {
  libraryApi.mockReset().mockResolvedValue(libraryFixture());
  exportsApi.mockReset().mockResolvedValue([]);
  chapterProductionApi.mockReset().mockResolvedValue({ ready: false, pages: [] } as never);
});

afterEach(() => {
  timezoneOffsetSpy?.mockRestore();
  timezoneOffsetSpy = null;
});

describe("素材库日期筛选本地边界（#658）", () => {
  it("UTC+8 下选定日期转换为本地日起点/日终点的 aware-UTC，而不是拼 Z 平移 8 小时", () => {
    fixedOffset(-480);
    const boundaries = localDayBoundariesToUtc("2026-09-13", "2026-09-13");
    // 旧实现输出 2026-09-13T00:00:00Z / T23:59:59Z：本地 13 日 0-8 点被
    // 排除、14 日凌晨被混入。本地墙钟换算后窗口精确覆盖本地 13 日全天。
    expect(boundaries.dateFrom).toBe("2026-09-12T16:00:00.000Z");
    expect(boundaries.dateTo).toBe("2026-09-13T15:59:59.999Z");
  });

  it("UTC-5 下同样按本地墙钟换算（负偏移方向也成立）", () => {
    fixedOffset(300);
    const boundaries = localDayBoundariesToUtc("2026-09-13", "2026-09-13");
    expect(boundaries.dateFrom).toBe("2026-09-13T05:00:00.000Z");
    expect(boundaries.dateTo).toBe("2026-09-14T04:59:59.999Z");
  });

  it("夏令时切换日在平移后复核偏移，两端边界仍对齐本地墙钟", () => {
    // 美东 2026-03-08 02:00（EST, +300）切 EDT（+240）：
    // 起点落在切换前，终点落在切换后。
    offsetSwitchingAt(Date.UTC(2026, 2, 8, 7), 300, 240);
    const boundaries = localDayBoundariesToUtc("2026-03-08", "2026-03-08");
    expect(boundaries.dateFrom).toBe("2026-03-08T05:00:00.000Z");
    expect(boundaries.dateTo).toBe("2026-03-09T03:59:59.999Z");
  });

  it("空日期不产生边界（不过滤）", () => {
    const boundaries = localDayBoundariesToUtc("", "");
    expect(boundaries.dateFrom).toBeUndefined();
    expect(boundaries.dateTo).toBeUndefined();
  });

  it("钩子把换算后的本地边界交给 library 查询，而不是原始日期拼串", async () => {
    fixedOffset(-480);
    const { workspace } = renderLibraryProbe();
    act(() => workspace().setLibraryDateFrom("2026-09-13"));
    await vi.waitFor(() => {
      expect(libraryApi).toHaveBeenCalledWith("project-1", expect.objectContaining({
        date_from: "2026-09-12T16:00:00.000Z",
      }));
    });
    act(() => workspace().setLibraryDateTo("2026-09-13"));
    await vi.waitFor(() => {
      expect(libraryApi).toHaveBeenCalledWith("project-1", expect.objectContaining({
        date_from: "2026-09-12T16:00:00.000Z",
        date_to: "2026-09-13T15:59:59.999Z",
      }));
    });
  });
});
