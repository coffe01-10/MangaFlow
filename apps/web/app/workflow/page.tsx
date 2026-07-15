"use client";

import { AppShell } from "@/components/shell";
import { api } from "@/lib/api";
import { LoaderCircle } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

export default function LegacyWorkflowPage() {
  const router = useRouter();

  useEffect(() => {
    let active = true;
    api.projects()
      .then((projects) => {
        if (!active) return;
        const recent = [...projects].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))[0];
        router.replace(recent ? `/projects/${recent.id}/workflow` : "/");
      })
      .catch(() => active && router.replace("/"));
    return () => { active = false; };
  }, [router]);

  return <AppShell><div className="full-loading"><LoaderCircle className="spin" />正在打开最近项目的流程编排…</div></AppShell>;
}
