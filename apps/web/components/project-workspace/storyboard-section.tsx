"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { CircleAlert, LoaderCircle, PanelTop } from "lucide-react";
import { useSyncExternalStore } from "react";

import { getPageStructureIssue } from "@/lib/generation-rules";

import { STRESS_NODE_COUNT } from "@/components/storyboard-editor/stress-fixture";

import type { WorkspaceQueries } from "./use-workspace-queries";

const StoryboardEditor = dynamic(() => import("@/components/storyboard-editor").then((mod) => mod.StoryboardEditor));
const StressStoryboardCanvas = dynamic(() =>
  import("@/components/storyboard-editor/stress-canvas").then((mod) => mod.StressStoryboardCanvas),
);

/** `?stress=100` renders the synthetic client-only stress fixture instead of
 * the editor (V02-32); it never reads or writes storyboard data. */
function readStressParam(): boolean {
  if (typeof window === "undefined") return false;
  return Number(new URLSearchParams(window.location.search).get("stress")) === STRESS_NODE_COUNT;
}

const subscribeNoop = () => () => undefined;

export function StoryboardSection({
  chapters,
  pages,
  characters,
  outfits,
  activeChapterId,
  setSelectedChapterId,
  replanPage,
  projectPath,
  initialPageId,
  focusCharacterId,
}: {
  chapters: WorkspaceQueries["chapters"];
  pages: WorkspaceQueries["pages"];
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  activeChapterId: string | null;
  setSelectedChapterId: (chapterId: string | null) => void;
  replanPage: {
    isPending: boolean;
    error: Error | null;
    mutate: (pageNumber: number) => void;
  };
  projectPath: (target: string) => string;
  initialPageId: string | null;
  focusCharacterId: string | null;
}) {
  // `?stress=100` renders the client-only stress fixture instead of the editor
  // (V02-32). Read through useSyncExternalStore so SSR/hydration agree and no
  // effect-time state set is needed; the param cannot change without a reload.
  const stressMode = useSyncExternalStore(subscribeNoop, readStressParam, () => false);
  const invalidPlannedPageCount = (pages.data ?? []).filter((page) => getPageStructureIssue(page)).length;

  if (stressMode) return <StressStoryboardCanvas />;

  return (
    <>
      <header className="canvas-header"><div><span>PAGE CAPACITY / 动态分页</span><h2>内容有多少，页面就有多少</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑分镜的章节" value={activeChapterId ?? ""} onChange={(event) => setSelectedChapterId(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{pages.data?.length ?? 0} 页</small></div></header>
      {invalidPlannedPageCount > 0 && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>{invalidPlannedPageCount} 页缺少剧本与分镜来源</strong><p>这是旧版分页数据，不能直接生图。请先到漫画剧本页删除分页，再重新生成剧本并计算分页。</p></div><Link className="button outline compact" href={projectPath("script")}>前往漫画剧本</Link></div>}
      {pages.isLoading ? <div className="loading-panel"><LoaderCircle className="spin" size={16} />正在读取页面…</div>
        : pages.isError ? <p className="form-error" role="alert"><CircleAlert size={15} />页面列表读取失败：{pages.error instanceof Error ? pages.error.message : "请稍后重试"}</p>
        : pages.data === undefined ? <div className="loading-panel"><LoaderCircle className="spin" size={16} />正在读取页面…</div>
        : !pages.data.length ? <div className="asset-empty tall"><PanelTop size={28} /><strong>尚未生成分页分镜</strong><p>先完成漫画剧本；系统按场景切换、动作复杂度、对白和气泡容量拆页。</p></div>
        : <StoryboardEditor chapterId={activeChapterId!} pages={pages.data} characters={characters.data ?? []} outfits={outfits.data ?? []} onReplan={(pageNumber) => replanPage.mutate(pageNumber)} replanPending={replanPage.isPending} replanError={replanPage.error} initialPageId={initialPageId} focusCharacterId={focusCharacterId} />}
    </>
  );
}
