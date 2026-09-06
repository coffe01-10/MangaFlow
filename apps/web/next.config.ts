import type { NextConfig } from "next";

// W-15 plan B: the desktop shell serves this app with a bundled node
// (next standalone server). The standalone bundle compiles rewrites at
// BUILD time (routes-manifest.json), so the API destination cannot be
// re-pointed at runtime. The bundle is therefore built with the fixed
// loopback relay port the helper owns (WEB_RELAY_PORT in
// apps/desktop/sidecar/mangaflow_desktop_helper.py): the helper binds
// 127.0.0.1:39443 for the session and relays those connections to its own
// dynamic API port. Web/dev serving outside the desktop shell keeps the
// env-driven origin below (next dev / next start use it directly).
const WEB_RELAY_ORIGIN = "http://127.0.0.1:39443";
const apiOrigin = process.env.MANGAFLOW_API_ORIGIN ?? WEB_RELAY_ORIGIN;

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
};

export default nextConfig;
