"use client";

import { CircleHelp, FolderKanban, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

const globalNavigation = [
  { label: "项目", href: "/", icon: FolderKanban },
  { label: "帮助", href: "/help", icon: CircleHelp },
  { label: "设置", href: "/settings", icon: Settings },
] as const;

export function GlobalNav() {
  const pathname = usePathname();
  return (
    <nav className="global-nav" aria-label="全局导航">
      {globalNavigation.map(({ label, href, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return <Link key={href} href={href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}><Icon size={15} />{label}</Link>;
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return <div className="app-shell"><main className="app-main">{children}</main></div>;
}
