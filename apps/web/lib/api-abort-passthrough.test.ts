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

  // 读取错误响应体期间（!response.ok 分支的 response.json()）取消/超时同样
  // 以 AbortError/TimeoutError 拒绝：不能被兜底 catch 吞成「请求失败」。
  it("rethrows an AbortError raised while reading an error response body", async () => {
    const abortError = new DOMException("The user aborted a request.", "AbortError");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.reject(abortError),
      }),
    );

    const rejection = await api.projects().catch((error: unknown) => error);

    expect(rejection).toBe(abortError);
  });

  it("maps a timeout raised while reading an error response body to the timeout ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: () => Promise.reject(new DOMException("The operation timed out.", "TimeoutError")),
      }),
    );

    const rejection = await api.projects().catch((error: unknown) => error);

    expect(rejection).toBeInstanceOf(Error);
    expect((rejection as Error).message).toContain("请求超时");
  });
});
