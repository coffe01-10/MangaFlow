import { describe, expect, it } from "vitest";

import { buildContentSecurityPolicy } from "./lib/csp";
import nextConfig from "./next.config";

// Issue #300：web CSP 从 next.config headers() 迁到 proxy.ts 的 nonce 形态。
// 这里钉住两半契约：
// 1. next.config 不得再设 Content-Security-Policy——两个头会被浏览器同时
//    执行，一个无 nonce 的旧头等于重新引入 unsafe-inline 时代的旁路。
// 2. lib/csp.ts 产出的 script-src 必须是 nonce 形态且不含 'unsafe-inline'
//    （tauri.conf 静态形态的债务另行钉在 delivery_contract.rs）。
function directive(csp: string, name: string): string {
  const found = csp
    .split("; ")
    .find((portion) => portion.startsWith(`${name} `));
  if (!found) throw new Error(`directive missing: ${name} in ${csp}`);
  return found;
}

describe("web CSP 契约（#300 nonce 化）", () => {
  it("next.config 只保留非 CSP 安全头", async () => {
    const entries = await nextConfig.headers?.();
    expect(entries).toHaveLength(1);
    expect(entries?.[0]?.source).toBe("/:path*");
    const headers = entries?.[0]?.headers ?? [];
    expect(headers.map((header) => header.key)).toEqual([
      "X-Content-Type-Options",
      "X-Frame-Options",
      "Referrer-Policy",
    ]);
    expect(headers.find((header) => header.key === "X-Content-Type-Options")?.value).toBe("nosniff");
    expect(headers.find((header) => header.key === "X-Frame-Options")?.value).toBe("DENY");
    expect(headers.find((header) => header.key === "Referrer-Policy")?.value).toBe("no-referrer");
  });

  it("script-src 是 nonce 形态：无 unsafe-inline，dev 才有 unsafe-eval", () => {
    for (const isDev of [false, true]) {
      const csp = buildContentSecurityPolicy("FIXTURE-NONCE", isDev);
      const scriptSrc = directive(csp, "script-src");
      expect(scriptSrc).toContain("'nonce-FIXTURE-NONCE'");
      expect(scriptSrc).toContain("'strict-dynamic'");
      expect(scriptSrc).not.toContain("'unsafe-inline'");
      expect(scriptSrc.includes("'unsafe-eval'")).toBe(isDev);
      // 无 wasm 依赖（react/react-query/xyflow/lucide 均不含 WebAssembly）。
      expect(scriptSrc).not.toContain("'wasm-unsafe-eval'");
    }
  });

  it("style-src 保留 unsafe-inline（React style 属性需要，见 lib/csp.ts 注释）", () => {
    const csp = buildContentSecurityPolicy("FIXTURE-NONCE", false);
    expect(directive(csp, "style-src")).toBe("style-src 'self' 'unsafe-inline'");
  });

  it("connect/img 源保持 self + loopback 通配", () => {
    const csp = buildContentSecurityPolicy("FIXTURE-NONCE", false);
    expect(directive(csp, "connect-src")).toBe("connect-src 'self' http://127.0.0.1:*");
    expect(directive(csp, "img-src")).toBe("img-src 'self' data: blob: http://127.0.0.1:*");
    expect(directive(csp, "object-src")).toBe("object-src 'none'");
    expect(directive(csp, "frame-src")).toBe("frame-src 'none'");
  });
});
