import { describe, expect, it } from "vitest";

import nextConfig from "./next.config";

// Issue #300 Stage 0：把 CSP 响应头钉进契约。script-src 的 'unsafe-inline'
// 是记录在案的债务（Next App Router 的内联 self.__next_f.push 引导脚本；
// nonce 化需要每请求渲染，静态预渲染页面做不到，见 issue 讨论），而不是
// 无意的漂移——任何改动都必须是有意的契约变更。
describe("next.config 安全响应头（#300 钉契约）", () => {
  it("CSP 恰好是钉住的值；其余安全头在场", async () => {
    const entries = await nextConfig.headers?.();
    expect(entries).toHaveLength(1);
    expect(entries?.[0]?.source).toBe("/:path*");
    const headers = entries?.[0]?.headers ?? [];
    expect(headers.map((header) => header.key)).toEqual([
      "Content-Security-Policy",
      "X-Content-Type-Options",
      "X-Frame-Options",
      "Referrer-Policy",
    ]);
    expect(headers.find((header) => header.key === "Content-Security-Policy")?.value).toBe([
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: http://127.0.0.1:*",
      "font-src 'self' data:",
      "connect-src 'self' http://127.0.0.1:*",
      "object-src 'none'",
      "base-uri 'self'",
      "frame-src 'none'",
    ].join("; "));
    expect(headers.find((header) => header.key === "X-Content-Type-Options")?.value).toBe("nosniff");
    expect(headers.find((header) => header.key === "X-Frame-Options")?.value).toBe("DENY");
    expect(headers.find((header) => header.key === "Referrer-Policy")?.value).toBe("no-referrer");
  });
});
