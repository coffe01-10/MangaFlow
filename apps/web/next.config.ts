import type { NextConfig } from "next";

// Rewrites destination resolution (W-15 plan B):
// - Plain `next start` / `next dev` (E2E, browser acceptance, web app):
//   this config file is re-evaluated at server start, so
//   MANGAFLOW_API_ORIGIN — or the historical default :8000 — applies at
//   runtime. Nothing changes for non-desktop forms.
// - Desktop standalone bundle: `next build` compiles rewrites into
//   routes-manifest.json, so the destination is baked at BUILD time.
//   scripts/build-web-standalone.py therefore builds WITH
//   MANGAFLOW_API_ORIGIN=http://127.0.0.1:39443 (the helper's fixed
//   loopback relay port, owned and relayed to the dynamic API port at
//   runtime — see WEB_RELAY_PORT in
//   apps/desktop/sidecar/mangaflow_desktop_helper.py) and verifies the
//   baked manifest, so an accidentally wrong build fails loudly.
const apiOrigin = process.env.MANGAFLOW_API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // W-15 plan B: the desktop shell serves the production app with a bundled
  // node (next standalone server). `output: "standalone"` changes only the
  // artifact layout of a production build (a self-contained server/ tree
  // under .next); regular `next start` and `npm run dev` are unaffected.
  output: "standalone",
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
  // `next dev` and `next build` can run during the same acceptance session.
  // Keep their manifests and compiled CSS separate so a production build
  // cannot make the live development server serve stale assets.
  distDir: process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiOrigin}/api/v1/:path*`,
      },
    ];
  },
  // Security headers for the desktop plan-B form (W-15): the WebView loads
  // this server's documents directly (External URL), so the tauri.conf CSP
  // no longer applies to them — the equivalent policy ships here instead.
  // script-src keeps 'unsafe-inline' for the Next inline bootstrap
  // (self.__next_f.push), the same documented debt as the tauri.conf CSP;
  // connect/img are loopback-only like the shell policy.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob: http://127.0.0.1:*",
              "font-src 'self' data:",
              "connect-src 'self' http://127.0.0.1:*",
              "object-src 'none'",
              "base-uri 'self'",
              "frame-src 'none'",
            ].join("; "),
          },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
        ],
      },
    ];
  },
};

export default nextConfig;
