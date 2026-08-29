"use client";

import dynamic from "next/dynamic";
import { Clapperboard, Sparkles } from "lucide-react";

import type { WorkspaceQueries } from "./use-workspace-queries";

const ScriptEditor = dynamic(() => import("@/components/script-editor").then((mod) => mod.ScriptEditor));

export function ScriptSection({
  chapters,
  script,
  characters,
  outfits,
  activeChapterId,
  setSelectedChapterId,
  parseChapter,
  assignOutfit,
}: {
  chapters: WorkspaceQueries["chapters"];
  script: WorkspaceQueries["script"];
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  activeChapterId: string | null;
  setSelectedChapterId: (chapterId: string | null) => void;
  parseChapter: { isPending: boolean; mutate: () => void };
  assignOutfit: { mutate: (input: { sceneId: string; assignments: Record<string, string> }) => void };
}) {
  return (
    <>
      <header className="canvas-header"><div><span>SCREENPLAY / 漫画剧本</span><h2>先写场景与情节拍，再进入分页</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑剧本的章节" value={activeChapterId ?? ""} onChange={(event) => setSelectedChapterId(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{script.data?.scenes.length ?? 0} 个场景</small></div></header>
      {!activeChapterId ? <div className="asset-empty tall"><Clapperboard size={28} /><strong>请先导入原作</strong></div> : !script.data?.scenes.length ? <div className="script-empty"><Clapperboard size={30} /><strong>本章还没有漫画剧本</strong><p>点击“生成漫画剧本”，默认文字模型会逐段补充可视化动作、场景、对白、旁白、情绪和翻页悬念，不会压缩原文。</p><button className="button ink" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />生成漫画剧本</button></div> : <ScriptEditor chapterId={activeChapterId} script={script.data} characters={characters.data ?? []} outfits={outfits.data ?? []} onAssignOutfit={(sceneId, assignments) => assignOutfit.mutate({ sceneId, assignments })} />}
    </>
  );
}
