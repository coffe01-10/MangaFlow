"use client";

import Image from "next/image";
import Link from "next/link";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  ListTodo,
  Menu,
  Settings,
  Workflow,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

import { Pencil } from "lucide-react";
import type { Job, PageCandidate } from "@/lib/api";

import { navigationItems } from "./labels";
import type { WorkspaceSection } from "./types";

export function WorkspaceTopbar({
  navOpen,
  setNavOpen,
  projectName,
  projectPath,
}: {
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  projectName: string;
  projectPath: (target: string) => string;
}) {
  return (
    <header className="workspace-topbar">
      <div className="workspace-crumb"><button className="project-nav-toggle" aria-expanded={navOpen} aria-label={navOpen ? "关闭项目导航" : "打开项目导航"} onClick={() => setNavOpen(!navOpen)}>{navOpen ? <X size={17} /> : <Menu size={17} />}</button><Link href="/"><ArrowLeft size={17} />项目</Link><i /><span>{projectName}</span></div>
      <div className="workspace-status"><span><i />项目工作区</span><Link className="button outline compact" href={projectPath("workflow")}><Workflow size={15} />在工作流中查看</Link><Link className="button ink compact" href={projectPath("settings")}><Settings size={15} />项目设置</Link></div>
    </header>
  );
}

export function WorkspaceSidebar({
  navOpen,
  setNavOpen,
  projectName,
  chapterCount,
  needsChapters,
  pageCount,
  needsPages,
  section,
  projectPath,
  rememberWorkspaceScroll,
  onSidebarResize,
}: {
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  projectName: string;
  chapterCount: number;
  needsChapters: boolean;
  pageCount: number;
  needsPages: boolean;
  section: WorkspaceSection;
  projectPath: (target: string) => string;
  rememberWorkspaceScroll: () => void;
  onSidebarResize: (event: ReactPointerEvent<HTMLButtonElement>) => void;
}) {
  return (
    <>
      <button className={navOpen ? "workspace-nav-backdrop show" : "workspace-nav-backdrop"} onClick={() => setNavOpen(false)} aria-label="关闭项目导航" />
      <aside className={navOpen ? "workspace-left open" : "workspace-left"}>
        <button type="button" className="workspace-resizer" aria-label="拖动调整项目侧边栏宽度" onPointerDown={onSidebarResize} />
        <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{projectName}</h1><p>{needsChapters ? `${chapterCount} 章` : "漫画生产工作区"}{needsPages ? ` · ${pageCount} 页已规划` : ""}</p></div>
        <nav className="workspace-steps">
          {navigationItems.map(([target, label, , index, Icon]) => <Link scroll={false} key={target} href={projectPath(target)} className={section === target ? "active" : ""} aria-current={section === target ? "page" : undefined} onClick={rememberWorkspaceScroll}><Icon size={17} /><span>{label}</span><i>{index}</i></Link>)}
          <span className="workspace-nav-divider" />
          <Link href={projectPath("workflow")} onClick={() => setNavOpen(false)}><Workflow size={17} /><span>流程编排</span><i>FL</i></Link>
          <Link href={projectPath("settings")} onClick={() => setNavOpen(false)}><Settings size={17} /><span>项目设置</span><i>ST</i></Link>
        </nav>
      </aside>
    </>
  );
}

export function ImageLightbox({
  preview,
  onClose,
  onLocalEdit,
}: {
  preview: { url: string; label: string; candidate?: PageCandidate };
  onClose: () => void;
  onLocalEdit?: (candidate: PageCandidate) => void;
}) {
  const [previewZoom, setPreviewZoom] = useState(1);

  return <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={preview.label} onClick={onClose}><button type="button" className="lightbox-close" aria-label="关闭大图" onClick={onClose}><X size={20} /></button><div className="lightbox-shell" onClick={(event) => event.stopPropagation()}><div className="lightbox-toolbar"><strong>{preview.label}</strong><div>{preview.candidate && onLocalEdit && <button type="button" className="lightbox-local-edit" title="进入局部选区编辑器：画 mask 后按 regenerate_region 生成派生候选" onClick={() => onLocalEdit(preview.candidate!)}><Pencil size={15} />局部修改</button>}<button type="button" aria-label="缩小图片" disabled={previewZoom <= .5} onClick={() => setPreviewZoom((value) => Math.max(.5, value - .25))}><ZoomOut size={17} /></button><button type="button" onClick={() => setPreviewZoom(1)}>{Math.round(previewZoom * 100)}%</button><button type="button" aria-label="放大图片" disabled={previewZoom >= 2.5} onClick={() => setPreviewZoom((value) => Math.min(2.5, value + .25))}><ZoomIn size={17} /></button></div></div><div className="lightbox-stage"><Image style={{ transform: `scale(${previewZoom})` }} src={preview.url} alt={preview.label} width={1600} height={1600} unoptimized /></div><span>使用 ＋/－ 调整到 50%–250%，点击背景或右上角关闭</span></div></div>;
}

export function QueueDock({
  queueStats,
  latestJob,
  latestJobLabel,
  section,
  concurrency,
  projectPath,
}: {
  queueStats: { waiting: number; failed: number };
  latestJob: Job | undefined;
  latestJobLabel: string;
  section: WorkspaceSection;
  concurrency: number;
  projectPath: (target: string) => string;
}) {
  const [queueDockHidden, setQueueDockHidden] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("mangaflow.queue-dock-hidden") === "true";
  });
  const toggleQueueDock = (hidden: boolean) => {
    setQueueDockHidden(hidden);
    window.localStorage.setItem("mangaflow.queue-dock-hidden", String(hidden));
  };

  return queueDockHidden ? <button type="button" className="queue-dock-reveal" aria-label="显示任务中心快捷栏" title="显示任务中心快捷栏" onClick={() => toggleQueueDock(false)}><ListTodo size={16} /><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><ChevronUp size={13} /></button> : <><Link className="queue-dock" href={projectPath("jobs")}><div><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><strong>打开任务中心</strong><small>{latestJob ? `${latestJobLabel} · ${latestJob.status}` : section === "jobs" || section === "generate" ? "当前没有任务" : "查看生成、解析与检查进度"}</small></div>{(section === "jobs" || section === "generate") && <div><span>并发上限 {concurrency}</span><i /><span>{queueStats.waiting} 等待</span><i /><span>{queueStats.failed} 失败</span></div>}</Link><button type="button" className="queue-dock-hide" aria-label="隐藏任务中心快捷栏" title="隐藏任务中心快捷栏" onClick={() => toggleQueueDock(true)}><ChevronDown size={15} /></button></>;
}
