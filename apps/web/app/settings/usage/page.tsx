import { AppShell } from "@/components/shell";
import { UsageDashboard } from "@/components/usage/usage-dashboard";
import { ArrowLeft, Settings } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "用量与成本看板 - MangaFlow",
};

export default function UsageDashboardPage() {
  return (
    <AppShell>
      <div className="paper-texture" />
      <header className="topbar settings-topbar">
        <div className="topbar-title">
          <span>SYSTEM / USAGE &amp; COST</span>
          <strong>系统设置 / 用量与成本看板</strong>
        </div>
        <div className="topbar-actions">
          <Link className="button ghost compact" href="/settings">
            <Settings size={16} />设置首页
          </Link>
          <Link className="button ink compact" href="/">
            <ArrowLeft size={16} />返回项目
          </Link>
        </div>
      </header>
      <main className="settings-page usage-page">
        <UsageDashboard />
      </main>
    </AppShell>
  );
}
