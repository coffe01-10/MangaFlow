"use client";

import { BookOpenText, CircleAlert, FileImage, LoaderCircle, PanelTop, Pencil, RotateCcw, Save, Sparkles, Trash2, Upload } from "lucide-react";

import type { WorkspaceQueries } from "./use-workspace-queries";
import type { SourceWorkspace } from "./use-source-workspace";

export function SourceSection({
  chapters,
  script,
  activeChapterId,
  setSelectedChapterId,
  source,
}: {
  chapters: WorkspaceQueries["chapters"];
  script: WorkspaceQueries["script"];
  activeChapterId: string | null;
  setSelectedChapterId: (chapterId: string | null) => void;
  source: SourceWorkspace;
}) {
  const {
    sourceTitle,
    setSourceTitle,
    sourceText,
    revisionLoadError,
    revisionLoading,
    setSourceText,
    editingChapterId,
    setEditingChapterId,
    deletedChapterId,
    importSource,
    importSourceFile,
    deleteChapter,
    restoreChapter,
    parseChapter,
    planChapter,
    chooseSourceFile,
    beginEditChapter,
  } = source;

  return (
    <>
      <header className="canvas-header"><div><span>SOURCE / 原作</span><h2>完整导入，不压缩故事</h2></div><small>{chapters.data?.length ?? 0} 个章节</small></header>
      <div className="source-compose">
        <input className="text-input" value={sourceTitle} onChange={(event) => setSourceTitle(event.target.value)} placeholder="章节标题" />
        <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} disabled={revisionLoading} placeholder={revisionLoading ? "正在载入章节原文，请稍候…" : "粘贴完整章节。系统先无损分段，再根据文字和剧本长度动态计算页数。"} />
        {revisionLoadError && <p className="form-error" role="alert">原文修订加载失败：{revisionLoadError}</p>}
        <div><span>{editingChapterId ? "保存后生成新修订，旧版本仍保留" : "不会限制总页数 · 单页硬上限 180 个中文字符"}</span><span className="compose-actions">{!editingChapterId && <label className={importSourceFile.isPending ? "button outline compact source-file-button pending" : "button outline compact source-file-button"}><input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={chooseSourceFile} disabled={importSourceFile.isPending} />{importSourceFile.isPending ? <LoaderCircle className="spin" size={15} /> : <FileImage size={15} />}{importSourceFile.isPending ? "正在导入…" : "选择 TXT / MD"}</label>}{editingChapterId && <button className="button ghost compact" onClick={() => { setEditingChapterId(null); setSourceText(""); }}>取消修改</button>}<button className="button ink" disabled={!sourceText.trim() || importSource.isPending} onClick={() => importSource.mutate()}>{importSource.isPending ? <LoaderCircle className="spin" size={16} /> : editingChapterId ? <Save size={16} /> : <Upload size={16} />}{editingChapterId ? "保存新修订" : "导入粘贴原文"}</button></span></div>
        {importSource.isError && <p className="form-error"><CircleAlert size={14} />{importSource.error.message}</p>}
        {importSourceFile.isError && <p className="form-error"><CircleAlert size={14} />{importSourceFile.error.message}</p>}
      </div>
      <div className="chapter-register">
        {chapters.data?.map((chapter) => (
          <div key={chapter.id} className={activeChapterId === chapter.id ? "chapter-row active" : "chapter-row"} onClick={() => setSelectedChapterId(chapter.id)}>
            <span>{String(chapter.ordinal).padStart(2, "0")}</span><div><strong>{chapter.title}</strong><small>{chapter.source_character_count} 字 · {chapter.segment_count} 段 · {chapter.page_count} 页 · {chapter.status}</small></div><em>{Math.round(chapter.coverage_ratio * 100)}% 覆盖</em><div className="row-actions"><button title="修改原文" onClick={(event) => { event.stopPropagation(); beginEditChapter(chapter.id, chapter.title); }}><Pencil size={13} /></button><button title="删除章节" onClick={(event) => { event.stopPropagation(); if (window.confirm("删除后会暂时隐藏该章节，可立即撤回。继续吗？")) deleteChapter.mutate(chapter.id); }}><Trash2 size={13} /></button></div>
          </div>
        ))}
        {!chapters.data?.length && <div className="asset-empty"><BookOpenText size={24} /><strong>尚未导入原作</strong><p>粘贴一个完整章节开始工作。</p></div>}
      </div>
      {deletedChapterId && <div className="undo-banner"><span>章节已移入回收状态</span><button onClick={() => restoreChapter.mutate(deletedChapterId)}><RotateCcw size={13} />撤回删除</button></div>}
      {activeChapterId && <div className="workflow-actions"><button className="button outline" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />生成漫画剧本</button><button className="button ink" disabled={planChapter.isPending || script.data?.status !== "READY"} onClick={() => planChapter.mutate()}>{planChapter.isPending ? <LoaderCircle className="spin" size={15} /> : <PanelTop size={15} />}从剧本计算分页</button></div>}
      {planChapter.isError && <p className="form-error"><CircleAlert size={14} />{planChapter.error.message}</p>}
    </>
  );
}
