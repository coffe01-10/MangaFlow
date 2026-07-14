import type { Metadata } from "next";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "MangaFlow AI · 漫画生产工作台",
  description: "从小说原作到连续漫画页面的可控 AI 工作流",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
