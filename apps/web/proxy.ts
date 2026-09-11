import { NextResponse, type NextRequest } from "next/server";

import { buildContentSecurityPolicy } from "./lib/csp";

// Issue #300: nonce-based CSP for the server-rendered forms (the plan-B
// desktop WebView via External URL + normal web deployments). Next.js parses
// the Content-Security-Policy REQUEST header set here, extracts the nonce and
// applies it to its own scripts while rendering — which is why
// app/layout.tsx opts every page into dynamic rendering: a statically
// prerendered HTML shell has no request, so no nonce could be baked into it.
//
// The desktop static-export form (MANGAFLOW_STATIC_EXPORT=1) cannot use this
// at all (there is no server left to run a proxy); its tauri.conf CSP keeps
// the separately pinned 'unsafe-inline' debt — see
// apps/desktop/shell-core/tests/delivery_contract.rs.
export function proxy(request: NextRequest) {
  // crypto.randomUUID() is 36 bytes -> 48 base64 characters without '='
  // padding, matching Next's nonce extraction regex
  // 'nonce-([A-Za-z0-9+/_-]+={0,2})'.
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const contentSecurityPolicy = buildContentSecurityPolicy(
    nonce,
    process.env.NODE_ENV === "development",
  );

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  // Next reads this request header to propagate the nonce onto its scripts.
  requestHeaders.set("Content-Security-Policy", contentSecurityPolicy);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  // The browser-facing policy.
  response.headers.set("Content-Security-Policy", contentSecurityPolicy);
  return response;
}

export const config = {
  matcher: [
    // Documents only: skip the API rewrites, immutable static assets and
    // link prefetches (flight responses carry no script tags), per the
    // Next.js CSP guide's recommended matcher.
    {
      source: "/((?!api|_next/static|_next/image|favicon.ico).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
