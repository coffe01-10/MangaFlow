import type { Metadata } from "next";
import { connection } from "next/server";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "MangaFlow AI · 漫画生产工作台",
  description: "从小说原作到连续漫画页面的可控 AI 工作流",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Issue #300: the nonce CSP (proxy.ts) requires per-request rendering —
  // Next applies the proxy-generated nonce to its bootstrap scripts only
  // while rendering the request, so every page must be dynamically rendered.
  // Awaiting connection() is the documented opt-in. The desktop
  // static-export build (MANGAFLOW_STATIC_EXPORT=1) has no request scope and
  // must keep exporting statically: that form is served by the Tauri shell
  // and keeps its separately pinned tauri.conf 'unsafe-inline' debt.
  if (process.env.MANGAFLOW_STATIC_EXPORT !== "1") {
    await connection();
  }
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
