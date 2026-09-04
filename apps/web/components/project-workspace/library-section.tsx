"use client";

import type { CSSProperties } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleAlert,
  Download,
  FileImage,
  Heart,
  LibraryBig,
  RotateCcw,
  Trash2,
} from "lucide-react";

import { publicUrl, type ImageModelAlias } from "@/lib/api";

import { formatBytes } from "./display";
import { candidateStatusLabels, generationKindLabels } from "./labels";
import { CandidateArtwork } from "./shared";
import type { GenerationWorkspace } from "./use-generation-workspace";
import type { LibraryWorkspace } from "./use-library-workspace";
import type { WorkspaceQueries } from "./use-workspace-queries";

export function LibrarySection({
  pages,
  chapters,
  characters,
  modelOptions,
  openPreview,
  router,
  projectPath,
  rememberWorkspaceScroll,
  setSelectedPageId,
  libraryWorkspace,
  generation,
}: {
  pages: WorkspaceQueries["pages"];
  chapters: WorkspaceQueries["chapters"];
  characters: WorkspaceQueries["characters"];
  modelOptions: { alias: ImageModelAlias; name: string; id: string; provider: string }[];
  openPreview: (url: string, label: string) => void;
  router: { push: (href: string) => void };
  projectPath: (target: string) => string;
  rememberWorkspaceScroll: () => void;
  setSelectedPageId: (pageId: string | null) => void;
  libraryWorkspace: LibraryWorkspace;
  generation: Pick<GenerationWorkspace, "deleteCandidate" | "retractSelectedCandidate" | "actionError">;
}) {
  const {
    favoriteOnly,
    setFavoriteOnly,
    libraryChapter,
    setLibraryChapter,
    libraryCharacter,
    setLibraryCharacter,
    libraryKind,
    setLibraryKind,
    libraryModel,
    setLibraryModel,
    libraryResolution,
    setLibraryResolution,
    libraryDateFrom,
    setLibraryDateFrom,
    libraryDateTo,
    setLibraryDateTo,
    libraryCursor,
    setLibraryCursor,
    libraryHistory,
    setLibraryHistory,
    library,
    exportsQuery,
    chapterProduction,
    createExport,
  } = libraryWorkspace;
  const { deleteCandidate, retractSelectedCandidate, actionError } = generation;

  return (
    <>
      <header className="canvas-header"><div><span>LIBRARY / 批次素材库</span><h2>保存每一次值得比较的结果</h2></div><small>{library.data?.total_candidates ?? 0} 个候选</small></header>
      <div className="library-toolbar">
        <div className="library-filter-grid">
          <select className={libraryChapter ? "filter-active" : ""} aria-label="按章节筛选素材" value={libraryChapter} onChange={(event) => { setLibraryChapter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}>
            <option value="">全部章节</option>
            {chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>第 {chapter.ordinal} 章 · {chapter.title}</option>)}
          </select>
          <button className={favoriteOnly ? "active" : ""} onClick={() => { setFavoriteOnly(!favoriteOnly); setLibraryCursor(""); setLibraryHistory([]); }}><Heart size={14} />只看收藏（{library.data?.favorite_count ?? 0}）</button>
          <select aria-label="按角色筛选素材" value={libraryCharacter} onChange={(event) => { setLibraryCharacter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部角色</option>{characters.data?.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select>
          <select aria-label="按生成类型筛选素材" value={libraryKind} onChange={(event) => { setLibraryKind(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部类型</option>{Object.entries(generationKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
          <select aria-label="按模型筛选素材" value={libraryModel} onChange={(event) => { setLibraryModel(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部模型</option>{modelOptions.map((model) => <option key={model.alias} value={model.alias}>{model.name}</option>)}</select>
          <select aria-label="按分辨率筛选素材" value={libraryResolution} onChange={(event) => { setLibraryResolution(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部清晰度</option>{(["1K", "2K", "4K"] as const).map((value) => <option key={value} value={value}>{value}</option>)}</select>
          <label>从<input aria-label="素材开始日期" type="date" value={libraryDateFrom} onChange={(event) => { setLibraryDateFrom(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label>
          <label>至<input aria-label="素材结束日期" type="date" value={libraryDateTo} onChange={(event) => { setLibraryDateTo(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label>
          <button onClick={() => { setLibraryChapter(""); setFavoriteOnly(false); setLibraryCharacter(""); setLibraryKind(""); setLibraryModel(""); setLibraryResolution(""); setLibraryDateFrom(""); setLibraryDateTo(""); setLibraryCursor(""); setLibraryHistory([]); }}><RotateCcw size={13} />重置</button>
        </div>
      </div>
      <div className="library-groups">{library.data?.groups.map((group, groupIndex) => { const columns = Math.min(Math.max(group.candidates.length, 1), 3); return <section className="library-group" style={{ "--batch-columns": columns } as CSSProperties} key={group.batch.id}><header><div><span>BATCH {String(group.batch.ordinal).padStart(3, "0")}</span><strong>{generationKindLabels[group.batch.generation_kind] ?? group.batch.generation_kind}</strong></div><small>{new Date(group.batch.created_at).toLocaleString("zh-CN")} · {group.candidates.length} 张</small></header><div className="library-candidates">{group.candidates.map((candidate, candidateIndex) => <article className={candidate.is_selected ? "is-selected" : undefined} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`批次候选 ${candidate.ordinal}`} eager={groupIndex === 0 && candidateIndex === 0} onOpen={(url, label) => openPreview(url, label)} /><div><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><span>{candidate.resolution} · {candidateStatusLabels[candidate.status] ?? candidate.status}</span>{candidate.is_favorite && <Heart size={13} fill="currentColor" />}{candidate.is_selected && <div className="library-selection-row"><em><Check size={12} />已暂选</em><button className="library-retract" disabled={!candidate.page_id || retractSelectedCandidate.isPending} onClick={() => { if (candidate.page_id && window.confirm("撤回暂选后，候选图片和生成记录仍会保留，后续页面将标记为待复查。是否继续？")) retractSelectedCandidate.mutate(candidate.page_id); }}><RotateCcw size={13} />撤回</button></div>}{!candidate.is_selected && <button className="library-delete" title="从素材库软删除" aria-label={`从素材库软删除候选 ${candidate.ordinal}`} disabled={deleteCandidate.isPending} onClick={() => { if (window.confirm("从素材库隐藏这个候选？生成文件和任务记录会保留。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={12} /></button>}</div></article>)}</div></section>; })}</div>
      {library.isLoading && <div className="loading-panel"><LibraryBig size={16} />正在读取素材库…</div>}
      {library.isError && <p className="form-error" role="alert"><CircleAlert size={15} />素材库读取失败：{library.error instanceof Error ? library.error.message : "请稍后重试"}</p>}
      {!library.isLoading && !library.isError && !library.data?.groups.length && <div className="asset-empty tall"><LibraryBig size={28} /><strong>素材库还是空的</strong><p>从单页抽卡开始，所有候选都会按批次保留。</p></div>}
      {(libraryHistory.length > 0 || library.data?.next_cursor) && <div className="library-pagination"><button disabled={!libraryHistory.length} onClick={() => { const previous = libraryHistory.at(-1) ?? ""; setLibraryHistory((items) => items.slice(0, -1)); setLibraryCursor(previous); }}><ArrowLeft size={13} />上一页</button><span>每页最多 {library.data?.limit ?? 30} 个批次</span><button disabled={!library.data?.next_cursor} onClick={() => { setLibraryHistory((items) => [...items, libraryCursor]); setLibraryCursor(library.data?.next_cursor ?? ""); }}>下一页<ArrowRight size={13} /></button></div>}
      <div className={`export-desk ${chapterProduction.data?.ready ? "ready" : "blocked"}`}><div><span>EXPORT / 整章导出门禁</span><strong>{chapterProduction.isError ? "章节生产状态读取失败" : chapterProduction.data ? `${chapterProduction.data.ready_pages}/${chapterProduction.data.total_pages} 页生产通过` : "正在核对章节生产状态"}</strong><small>{chapterProduction.isError ? <button type="button" className="chapter-production-retry" onClick={() => chapterProduction.refetch()}>重试读取</button> : chapterProduction.data?.ready ? "全部页面已完成校对、版本确认和视觉检查" : chapterProduction.data?.pages.find((page) => !page.ready)?.blockers[0]?.message ?? "章节没有可导出的页面"}</small></div><div>{(["PNG", "PDF", "JSON"] as const).map((type) => <button key={type} disabled={!chapterProduction.data?.ready || createExport.isPending} onClick={() => createExport.mutate(type)}><Download size={14} />{type}</button>)}</div></div>
      {chapterProduction.data && !chapterProduction.data.ready && <div className="chapter-production-blockers">{chapterProduction.data.pages.filter((page) => !page.ready).slice(0, 4).map((page) => { const pageNumber = pages.data?.find((item) => item.id === page.page_id)?.page_number; return <button key={page.page_id} onClick={() => { setSelectedPageId(page.page_id); rememberWorkspaceScroll(); router.push(projectPath("generate")); }}><span>第 {pageNumber ?? "—"} 页</span><strong>{page.blockers[0]?.message ?? "尚未通过"}</strong><ArrowRight size={14} /></button>; })}</div>}
      <div className="export-list">{exportsQuery.data?.map((item) => <a key={item.id} href={publicUrl(item.download_url)!}><FileImage size={14} /><span>{item.export_type} · {item.page_count} 页 · {formatBytes(item.byte_size)}</span><Download size={13} /></a>)}{exportsQuery.isSuccess && !exportsQuery.data?.length && <p className="export-list-empty">还没有导出文件；通过上方门禁后即可导出整章 PNG / PDF / JSON。</p>}</div>
      {(createExport.isError || actionError) && <p className="form-error" role="alert"><CircleAlert size={14} />{(createExport.error ?? actionError)?.message}</p>}
    </>
  );
}
