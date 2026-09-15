import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

// 调用方主动取消（AbortSignal.abort 触发的 AbortError）不是连接故障：
// request() 必须原样透传，让调用方按取消语义分支处理，而不是把它改写
// 成「无法连接 MangaFlow 服务」的连接错误。
describe("request cancellation passthrough", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rethrows a caller AbortError untouched instead of masking it as a connection failure", async () => {
    const abortError = new DOMException("The user aborted a request.", "AbortError");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(abortError),
    );

    const rejection = await api.projects().catch((error: unknown) => error);

    expect(rejection).toBe(abortError);
  });

  it("still maps a timeout abort to the localized timeout ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("The operation timed out.", "TimeoutError")),
    );

    const rejection = await api.projects().catch((error: unknown) => error);

    expect(rejection).toBeInstanceOf(Error);
    expect((rejection as Error).message).toContain("请求超时");
  });
});
