import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { activePollInterval } from "./task-status";

type JobLike = { status: string };

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
  };
}

describe("任务轮询行为", () => {
  it("任务停留在检查/修复阶段时持续轮询，进入终态后停止", async () => {
    vi.useFakeTimers();
    try {
      const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
      const queryFn = vi.fn((): Promise<JobLike[]> => Promise.resolve([{ status: "CONSISTENCY_CHECKING" }]));
      const { unmount } = renderHook(
        () => useQuery({
          queryKey: ["task-polling"],
          queryFn,
          staleTime: 0,
          refetchInterval: (query) => activePollInterval(query.state.data, 1000),
        }),
        { wrapper: createWrapper(queryClient) },
      );

      await vi.advanceTimersByTimeAsync(50);
      expect(queryFn).toHaveBeenCalledTimes(1);

      // 一致性检查阶段：每秒继续拉取
      await vi.advanceTimersByTimeAsync(3500);
      const duringChecking = queryFn.mock.calls.length;
      expect(duringChecking).toBeGreaterThanOrEqual(3);

      // 后端转入修复阶段（同 key 数据更新），仍须继续轮询
      queryClient.setQueryData(["task-polling"], [{ status: "REPAIRING" }]);
      await vi.advanceTimersByTimeAsync(2500);
      expect(queryFn.mock.calls.length).toBeGreaterThan(duringChecking);

      // 进入 COMPLETED 终态：先放过可能残留的一次已排定请求，之后不得再有新请求
      queryClient.setQueryData(["task-polling"], [{ status: "COMPLETED" }]);
      await vi.advanceTimersByTimeAsync(5000);
      const stoppedAt = queryFn.mock.calls.length;
      await vi.advanceTimersByTimeAsync(5000);
      expect(queryFn.mock.calls.length).toBe(stoppedAt);

      unmount();
    } finally {
      vi.useRealTimers();
    }
  });
});
