"use client";

import Image from "next/image";
import Link from "next/link";
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  ListTodo,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  Workflow,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent } from "react";

import { Pencil } from "lucide-react";
import type { Job, PageCandidate } from "@/lib/api";
import { useLocalStorageValue, writeLocalStorage } from "@/lib/local-storage-store";

import { jobStatusLabels, navigationItems } from "./labels";
import type { WorkspaceSection } from "./types";

export function WorkspaceTopbar({
  navOpen,
  setNavOpen,
  navCollapsed,
  setNavCollapsed,
  projectName,
  projectPath,
}: {
  navOpen: boolean;
  setNavOpen: (open: boolean) => void;
  navCollapsed: boolean;
  setNavCollapsed: (collapsed: boolean) => void;
  projectName: string;
  projectPath: (target: string) => string;
}) {
  return (
    <header className="workspace-topbar">
      <div className="workspace-crumb"><button className="project-nav-toggle" aria-expanded={navOpen} aria-label={navOpen ? "关闭项目导航" : "打开项目导航"} onClick={() => setNavOpen(!navOpen)}>{navOpen ? <X size={17} /> : <Menu size={17} />}</button><button type="button" className="workspace-nav-collapse" aria-pressed={navCollapsed} aria-label={navCollapsed ? "展开项目侧边栏" : "把项目侧边栏折叠成图标轨"} title={navCollapsed ? "展开侧边栏" : "折叠成图标轨"} onClick={() => setNavCollapsed(!navCollapsed)}>{navCollapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}</button><Link href="/"><ArrowLeft size={17} />项目</Link><i /><span>{projectName}</span></div>
      <div className="workspace-status"><span><i />项目工作区</span><Link className="button outline compact" href={projectPath("workflow")}><Workflow size={15} />在工作流中查看</Link><Link className="button ink compact" href={projectPath("settings")}><Settings size={15} />项目设置</Link></div>
    </header>
  );
}

export function WorkspaceSidebar({
  navOpen,
  navCollapsed,
  setNavOpen,
  projectName,
  summary,
  section,
  projectPath,
  rememberWorkspaceScroll,
  onSidebarResize,
}: {
  navOpen: boolean;
  navCollapsed: boolean;
  setNavOpen: (open: boolean) => void;
  projectName: string;
  summary: string;
  section: WorkspaceSection;
  projectPath: (target: string) => string;
  rememberWorkspaceScroll: () => void;
  onSidebarResize: (event: ReactPointerEvent<HTMLButtonElement>) => void;
}) {
  return (
    <>
      <button className={navOpen ? "workspace-nav-backdrop show" : "workspace-nav-backdrop"} onClick={() => setNavOpen(false)} aria-label="关闭项目导航" />
      <aside className={["workspace-left", navOpen ? "open" : "", navCollapsed ? "rail" : ""].filter(Boolean).join(" ")}>
        <button type="button" className="workspace-resizer" aria-label="拖动调整项目侧边栏宽度" onPointerDown={onSidebarResize} />
        <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{projectName}</h1><p>{summary}</p></div>
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

type LightboxPreview = {
  url: string;
  label: string;
  candidate?: PageCandidate;
  /** 触发缩略图的视口矩形：打开时大图从这里长出，关闭时缩回去。 */
  originRect?: { left: number; top: number; width: number; height: number };
};

const LIGHTBOX_ZOOM_MIN = 0.25;
const LIGHTBOX_ZOOM_MAX = 4;
const LIGHTBOX_ZOOM_STEP = 1.25;
const EXIT_DURATION_MS = 200;

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** FLIP helper：把 wrapper 从当前渲染矩形平移/缩放到目标矩形（或回 identity）。 */
function flipTransform(wrapper: HTMLElement, origin: LightboxPreview["originRect"]) {
  const rect = wrapper.getBoundingClientRect();
  if (!origin || origin.width <= 0 || origin.height <= 0 || rect.width <= 0 || rect.height <= 0) return null;
  const dx = origin.left + origin.width / 2 - (rect.left + rect.width / 2);
  const dy = origin.top + origin.height / 2 - (rect.top + rect.height / 2);
  const scale = Math.min(origin.width / rect.width, origin.height / rect.height);
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || !Number.isFinite(scale)) return null;
  return `translate(${dx}px, ${dy}px) scale(${scale})`;
}

export function ImageLightbox({
  preview,
  onClose,
  onLocalEdit,
}: {
  preview: LightboxPreview;
  onClose: () => void;
  onLocalEdit?: (candidate: PageCandidate) => void;
}) {
  const [previewZoom, setPreviewZoom] = useState(1);
  const [loaded, setLoaded] = useState(false);
  const [closing, setClosing] = useState(false);
  // 关闭按钮的点击会冒泡到背景层触发第二次 requestClose；state 来不及更新，
  // 用 ref 做同步防重入。
  const closingRef = useRef(false);
  const [panning, setPanning] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const flipRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  // 缩放锚点：滚轮/双击记录光标下的图片归一化点，zoom 应用后通过滚动补偿
  // 让该点留在光标下。
  const zoomAnchorRef = useRef<{ fx: number; fy: number; clientX: number; clientY: number } | null>(null);
  // 图片在 zoom=1 时的布局尺寸：放大不靠 transform（不改滚动范围），而是真实
  // 放大布局宽度，让 stage 的原生滚动接管平移。
  const [fitSize, setFitSize] = useState<{ width: number; height: number } | null>(null);

  const clampZoom = (value: number) => Math.min(LIGHTBOX_ZOOM_MAX, Math.max(LIGHTBOX_ZOOM_MIN, value));

  const zoomWithAnchor = (next: number, clientX: number, clientY: number) => {
    const stage = stageRef.current;
    const img = imgRef.current;
    if (stage && img) {
      const rect = img.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        zoomAnchorRef.current = {
          fx: Math.min(1, Math.max(0, (clientX - rect.left) / rect.width)),
          fy: Math.min(1, Math.max(0, (clientY - rect.top) / rect.height)),
          clientX,
          clientY,
        };
      }
    }
    setPreviewZoom(clampZoom(next));
  };

  // zoom 落进 DOM 后做一次滚动补偿：锚点不动，光标下的内容不漂。
  useLayoutEffect(() => {
    const anchor = zoomAnchorRef.current;
    zoomAnchorRef.current = null;
    if (!anchor) return;
    const stage = stageRef.current;
    const img = imgRef.current;
    if (!stage || !img) return;
    const rect = img.getBoundingClientRect();
    stage.scrollLeft += rect.left + anchor.fx * rect.width - anchor.clientX;
    stage.scrollTop += rect.top + anchor.fy * rect.height - anchor.clientY;
  }, [previewZoom]);

  const zoomFromStageCenter = (next: number) => {
    const stage = stageRef.current;
    if (!stage) {
      setPreviewZoom(clampZoom(next));
      return;
    }
    const rect = stage.getBoundingClientRect();
    zoomWithAnchor(next, rect.left + rect.width / 2, rect.top + rect.height / 2);
  };

  // FLIP 展开：wrapper 先贴到触发缩略图的矩形，下一帧回到最终布局。
  useLayoutEffect(() => {
    const wrapper = flipRef.current;
    if (!wrapper || prefersReducedMotion()) return;
    const transform = flipTransform(wrapper, preview.originRect);
    if (!transform) return;
    wrapper.style.transition = "none";
    wrapper.style.transform = transform;
    // 强制 reflow：让「贴着缩略图」的样式先落一帧，下一帧回到 identity 才有过渡。
    void wrapper.getBoundingClientRect();
    const raf = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (cb: () => void) => setTimeout(cb, 0) as unknown as number;
    const id = raf(() => {
      wrapper.style.transition = "";
      wrapper.style.transform = "";
    });
    return () => cancelAnimationFrame(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 用图片自然尺寸 × stage 可用区计算 zoom=1 的适配尺寸，作为缩放基准；
  // 不读当前布局尺寸，避免与「缩放改变布局宽度」形成循环依赖。
  const measureFit = () => {
    const img = imgRef.current;
    const stage = stageRef.current;
    if (!img || !stage || stage.clientWidth <= 0 || stage.clientHeight <= 0) return;
    const naturalW = img.naturalWidth || 1600;
    const naturalH = img.naturalHeight || 1600;
    const scale = Math.min((stage.clientWidth - 40) / naturalW, (stage.clientHeight - 40) / naturalH, 1);
    setFitSize({ width: naturalW * scale, height: naturalH * scale });
  };
  useLayoutEffect(measureFit, []);

  const requestClose = useCallback(() => {
    if (closingRef.current) return;
    closingRef.current = true;
    setClosing(true);
    const reduced = prefersReducedMotion();
    const wrapper = flipRef.current;
    if (!reduced && wrapper) {
      const transform = flipTransform(wrapper, preview.originRect);
      if (transform) {
        wrapper.style.transition = "transform .2s cubic-bezier(.45,0,.7,.2), opacity .2s ease";
        wrapper.style.transform = transform;
        wrapper.style.opacity = "0";
      }
    }
    window.setTimeout(onClose, reduced ? 0 : EXIT_DURATION_MS);
  }, [onClose, preview.originRect]);

  // V02-51B (audit §2.4/§7/U7): the lightbox is a dialog — Esc closes it, ＋/－
  // zoom without reaching for the toolbar, Tab stays inside, and closing hands
  // focus back to the thumbnail that opened it.
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  // 滚轮直接缩放（全屏查看器约定），非 passive 才能压住页面自身滚动。
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (closing) return;
      zoomWithAnchor(previewZoom * Math.exp(-event.deltaY * 0.0016), event.clientX, event.clientY);
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        requestClose();
        return;
      }
      if (event.key === "+" || event.key === "=") {
        zoomFromStageCenter(previewZoom * LIGHTBOX_ZOOM_STEP);
        return;
      }
      if (event.key === "-" || event.key === "_") {
        zoomFromStageCenter(previewZoom / LIGHTBOX_ZOOM_STEP);
        return;
      }
      if (event.key === "Tab" && dialogRef.current) {
        const focusables = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled])"));
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !(active instanceof Node) || !dialogRef.current.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || !(active instanceof Node) || !dialogRef.current.contains(active))) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => document.removeEventListener("keydown", onKeyDown, true);
  });

  // 放大后拖拽平移：stage 滚动条仍在（键盘/触摸板可用），指针拖拽直接改 scroll。
  const onStagePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || previewZoom <= 1 || closing) return;
    const stage = stageRef.current;
    if (!stage) return;
    event.preventDefault();
    const start = { x: event.clientX, y: event.clientY, left: stage.scrollLeft, top: stage.scrollTop };
    setPanning(true);
    const move = (moveEvent: PointerEvent) => {
      stage.scrollLeft = start.left - (moveEvent.clientX - start.x);
      stage.scrollTop = start.top - (moveEvent.clientY - start.y);
    };
    const up = () => {
      setPanning(false);
      window.removeEventListener("pointermove", move);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up, { once: true });
    window.addEventListener("pointercancel", up, { once: true });
  };

  const onStageDoubleClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (closing) return;
    const target = previewZoom > 1.01 ? 1 : 2.2;
    zoomWithAnchor(target, event.clientX, event.clientY);
  };

  const stageClass = [
    "lightbox-stage",
    previewZoom > 1 ? "can-pan" : "",
    panning ? "panning" : "",
  ].filter(Boolean).join(" ");
  const zoomedSize = fitSize && previewZoom !== 1
    ? { width: Math.round(fitSize.width * previewZoom), height: "auto" as const, maxWidth: "none" as const, maxHeight: "none" as const }
    : undefined;

  return <div ref={dialogRef} className={closing ? "image-lightbox leaving" : "image-lightbox"} role="dialog" aria-modal="true" aria-label={preview.label} onClick={requestClose}><button ref={closeRef} type="button" className="lightbox-close" aria-label="关闭大图" onClick={requestClose}><X size={20} /></button><div className="lightbox-shell" onClick={(event) => event.stopPropagation()}><div className="lightbox-toolbar"><strong>{preview.label}</strong><div>{preview.candidate && onLocalEdit && <button type="button" className="lightbox-local-edit" title="进入局部选区编辑器：画 mask 后按 regenerate_region 生成派生候选" onClick={() => onLocalEdit(preview.candidate!)}><Pencil size={15} />局部修改</button>}<button type="button" aria-label="缩小图片" disabled={previewZoom <= LIGHTBOX_ZOOM_MIN} onClick={() => zoomFromStageCenter(previewZoom / LIGHTBOX_ZOOM_STEP)}><ZoomOut size={17} /></button><button type="button" onClick={() => zoomFromStageCenter(1)}>{Math.round(previewZoom * 100)}%</button><button type="button" aria-label="放大图片" disabled={previewZoom >= LIGHTBOX_ZOOM_MAX} onClick={() => zoomFromStageCenter(previewZoom * LIGHTBOX_ZOOM_STEP)}><ZoomIn size={17} /></button></div></div><div ref={stageRef} className={stageClass} onPointerDown={onStagePointerDown} onDoubleClick={onStageDoubleClick}><div ref={flipRef} className="lightbox-flip"><Image ref={imgRef} className={loaded ? "is-loaded" : ""} style={zoomedSize} src={preview.url} alt={preview.label} width={1600} height={1600} unoptimized onLoad={() => { setLoaded(true); measureFit(); }} /></div></div><span>滚轮或双击以光标为锚点缩放（25%–400%），放大后拖拽平移，Esc 或点击背景关闭</span></div></div>;
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
  // 水合安全的持久化（见 lib/local-storage-store.ts）：渲染期直读
  // localStorage 会在隐藏过快捷栏的用户端造成水合不匹配。
  const queueDockHidden = useLocalStorageValue("mangaflow.queue-dock-hidden", "false") === "true";
  const toggleQueueDock = (hidden: boolean) => {
    writeLocalStorage("mangaflow.queue-dock-hidden", String(hidden));
  };

  return queueDockHidden ? <button type="button" className="queue-dock-reveal" aria-label="显示任务中心快捷栏" title="显示任务中心快捷栏" onClick={() => toggleQueueDock(false)}><ListTodo size={16} /><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><ChevronUp size={13} /></button> : <><Link className="queue-dock" href={projectPath("jobs")}><div><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><strong>打开任务中心</strong><small>{latestJob ? `${latestJobLabel} · ${jobStatusLabels[latestJob.status] ?? latestJob.status}` : section === "jobs" || section === "generate" ? "当前没有任务" : "查看生成、解析与检查进度"}</small></div>{(section === "jobs" || section === "generate") && <div><span>并发上限 {concurrency}</span><i /><span>{queueStats.waiting} 等待</span><i /><span>{queueStats.failed} 失败</span></div>}</Link><button type="button" className="queue-dock-hide" aria-label="隐藏任务中心快捷栏" title="隐藏任务中心快捷栏" onClick={() => toggleQueueDock(true)}><ChevronDown size={15} /></button></>;
}
