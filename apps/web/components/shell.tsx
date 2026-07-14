"use client";

import {
  BookOpenText,
  Boxes,
  CircleHelp,
  Clapperboard,
  FolderKanban,
  Images,
  Settings,
  Sparkles,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const navigation = [
  { label: "项目", href: "/", icon: FolderKanban, enabled: true },
  { label: "原作", href: "#", icon: BookOpenText, enabled: false },
  { label: "资产", href: "#", icon: Boxes, enabled: false },
  { label: "分镜", href: "#", icon: Clapperboard, enabled: false },
  { label: "成品", href: "#", icon: Images, enabled: false },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="app-shell">
      <aside className="rail" aria-label="主导航">
        <Link href="/" className="brand-mark" aria-label="MangaFlow 首页">
          <span>漫</span>
          <i />
        </Link>
        <nav className="rail-nav">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active = item.enabled && (pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href)));
            return item.enabled ? (
              <Link key={item.label} href={item.href} className={active ? "rail-link active" : "rail-link"} title={item.label}>
                <Icon size={19} strokeWidth={1.8} />
                <span>{item.label}</span>
              </Link>
            ) : (
              <span key={item.label} className="rail-link disabled" title={`${item.label} · 请在项目内使用`}>
                <Icon size={19} strokeWidth={1.8} />
                <span>{item.label}</span>
              </span>
            );
          })}
        </nav>
        <div className="rail-bottom">
          <span className="rail-link disabled" title="帮助">
            <CircleHelp size={18} />
          </span>
          <span className="rail-link disabled" title="设置">
            <Settings size={18} />
          </span>
        </div>
        <div className="rail-signature"><Sparkles size={12} /> MF</div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
