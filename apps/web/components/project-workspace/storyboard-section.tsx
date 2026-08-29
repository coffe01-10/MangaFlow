"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { CircleAlert, PanelTop } from "lucide-react";

import { getPageStructureIssue } from "@/lib/generation-rules";

import type { WorkspaceQueries } from "./use-workspace-queries";

const StoryboardEditor = dynamic(() => import("@/components/storyboard-editor").then((mod) => mod.StoryboardEditor));

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
  const invalidPlannedPageCount = (pages.data ?? []).filter((page) => getPageStructureIssue(page)).length;

  return (
    <>
      <header className="canvas-header"><div><span>PAGE CAPACITY / 动态分页</span><h2>内容有多少，页面就有多少</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑分镜的章节" value={activeChapterId ?? ""} onChange={(event) => setSelectedChapterId(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{pages.data?.length ?? 0} 页</small></div></header>
      {invalidPlannedPageCount > 0 && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>{invalidPlannedPageCount} 页缺少剧本与分镜来源</strong><p>这是旧版分页数据，不能直接生图。请先生成漫画剧本，再从第 1 页重新计算分页。</p></div><Link className="button outline compact" href={projectPath("script")}>前往漫画剧本</Link></div>}
      {!pages.data?.length ? <div className="asset-empty tall"><PanelTop size={28} /><strong>尚未生成分页分镜</strong><p>先完成漫画剧本；系统按场景切换、动作复杂度、对白和气泡容量拆页。</p></div> : <StoryboardEditor chapterId={activeChapterId!} pages={pages.data} characters={characters.data ?? []} outfits={outfits.data ?? []} onReplan={(pageNumber) => replanPage.mutate(pageNumber)} replanPending={replanPage.isPending} replanError={replanPage.error} initialPageId={initialPageId} focusCharacterId={focusCharacterId} />}
    </>
  );
}
