// Issue #300: the Content-Security-Policy for every server-rendered web
// response (the desktop plan-B form's WebView loads this server as an
// External URL, and normal web deployments serve the same app).
//
// script-src is nonce-based: proxy.ts generates a per-request nonce, Next
// reads the proxy-set Content-Security-Policy REQUEST header, extracts the
// nonce and applies it to its own scripts — including the inline
// `self.__next_f.push` bootstrap — while dynamically rendering the request,
// so 'unsafe-inline' is no longer needed there. 'strict-dynamic' follows the
// official Next.js CSP guide: nonce-carrying Next scripts may load their
// dependencies even though host allowlists are then ignored.
//
// Deliberately kept loose:
// - style-src keeps 'unsafe-inline': React `style={{...}}` attributes are
//   governed by style-src and dropping it silently strips styling (and dev
//   tooling injects inline styles); style injection is not a script-execution
//   primitive, so the residual risk is visual, not XSS.
// - 'unsafe-eval' is added only in dev: React evaluates code to reconstruct
//   server stack traces during development (Next.js CSP guide).
// - 'wasm-unsafe-eval' was dropped: neither the app nor its runtime
//   dependencies (react, react-query, xyflow, lucide) ship WebAssembly.
export function buildContentSecurityPolicy(nonce: string, isDev: boolean): string {
  return [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob: http://127.0.0.1:*",
    "font-src 'self' data:",
    "connect-src 'self' http://127.0.0.1:*",
    "object-src 'none'",
    "base-uri 'self'",
    "frame-src 'none'",
  ].join("; ");
}
