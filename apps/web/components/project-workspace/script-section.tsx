"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import dynamic from "next/dynamic";
import { CircleAlert, Clapperboard, LoaderCircle, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";

import { api } from "@/lib/api";

import type { WorkspaceQueries } from "./use-workspace-queries";

const ScriptEditor = dynamic(() => import("@/components/script-editor").then((mod) => mod.ScriptEditor));

export function ScriptSection({
  projectId,
  chapters,
  script,
  characters,
  outfits,
  sceneAssets,
  activeChapterId,
  setSelectedChapterId,
  parseChapter,
  assignOutfit,
}: {
  projectId: string;
  chapters: WorkspaceQueries["chapters"];
  script: WorkspaceQueries["script"];
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  sceneAssets: WorkspaceQueries["sceneAssets"];
  activeChapterId: string | null;
  setSelectedChapterId: (chapterId: string | null) => void;
  parseChapter: { isPending: boolean; isError: boolean; error: Error | null; mutate: () => void };
  assignOutfit: {
    mutate: (input: { sceneId: string; assignments: Record<string, string> }) => void;
    isError: boolean;
    error: Error | null;
    isPending: boolean;
  };
}) {
  const queryClient = useQueryClient();
  const [editorDirty, setEditorDirty] = useState(false);
  const hasPages = (chapters.data?.find((chapter) => chapter.id === activeChapterId)?.page_count ?? 0) > 0;
  const deleteScript = useMutation({
    mutationFn: () => api.deleteScript(activeChapterId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["script", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["chapters"] });
    },
  });
  function switchChapter(nextChapterId: string) {
    if (!nextChapterId || nextChapterId === activeChapterId) return;
    if (editorDirty && !window.confirm("当前场景 / 情节拍的修改尚未保存，切换章节会丢弃这些修改。仍要切换吗？")) return;
    setSelectedChapterId(nextChapterId);
  }
  return (
    <>
      <header className="canvas-header"><div><span>SCREENPLAY / 漫画剧本</span><h2>先写场景与情节拍，再进入分页</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑剧本的章节" value={activeChapterId ?? ""} onChange={(event) => switchChapter(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{script.data?.scenes.length ?? 0} 个场景</small></div></header>
      {!activeChapterId ? chapters.isError ? <div className="asset-empty tall" role="alert"><CircleAlert size={28} /><strong>章节列表读取失败</strong><p>{chapters.error instanceof Error ? chapters.error.message : "请稍后重试"}</p><button type="button" className="button outline compact" onClick={() => chapters.refetch()}>重试</button></div> : <div className="asset-empty tall"><Clapperboard size={28} /><strong>请先导入原作</strong></div>
        : script.isLoading ? <div className="loading-panel"><LoaderCircle className="spin" size={16} />正在读取剧本…</div>
        : script.isError ? <p className="form-error" role="alert"><CircleAlert size={15} />剧本读取失败：{script.error instanceof Error ? script.error.message : "请稍后重试"}</p>
        : !script.data ? <div className="loading-panel"><LoaderCircle className="spin" size={16} />正在读取剧本…</div>
        : !script.data.scenes.length ? <div className="script-empty"><Clapperboard size={30} /><strong>本章还没有漫画剧本</strong><p>{hasPages ? "已有分页时不能重新生成剧本。请先删除分页，再生成剧本并重新计算分页。" : "点击“生成漫画剧本”，默认文字模型会逐段补充可视化动作、场景、对白、旁白、情绪和翻页悬念，不会压缩原文。"}</p>{hasPages ? <button className="button outline" disabled={deleteScript.isPending} onClick={() => { if (window.confirm("删除本章分页、分镜和页面候选？原文会保留，之后可重新生成剧本。")) deleteScript.mutate(); }}>{deleteScript.isPending ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}删除分页</button> : <button className="button ink" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />生成漫画剧本</button>}{(parseChapter.isError || deleteScript.isError) && <p className="form-error" role="alert"><CircleAlert size={14} />{(parseChapter.error ?? deleteScript.error)?.message}</p>}</div>
        : <ScriptEditor chapterId={activeChapterId} projectId={projectId} script={script.data} characters={characters.data ?? []} outfits={outfits.data ?? []} sceneAssets={sceneAssets.data ?? []} onAssignOutfit={(sceneId, assignments) => assignOutfit.mutate({ sceneId, assignments })} assignOutfitError={assignOutfit.isError ? assignOutfit.error : null} assignOutfitPending={assignOutfit.isPending} onDirtyChange={setEditorDirty} />}
    </>
  );
}
