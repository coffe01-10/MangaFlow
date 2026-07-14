"use client";

import { AppShell } from "@/components/shell";
import {
  api,
  publicUrl,
  type ImageModelAlias,
  type MangaPage,
  type Project,
  type Resolution,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  BookOpenText,
  Check,
  ChevronDown,
  CircleAlert,
  Download,
  FileImage,
  Heart,
  ImagePlus,
  LibraryBig,
  LoaderCircle,
  LockKeyhole,
  PanelTop,
  Plus,
  Save,
  Sparkles,
  Star,
  Upload,
  Users,
} from "lucide-react";
import Link from "next/link";
import Image from "next/image";
import { useParams } from "next/navigation";
import { ChangeEvent, useMemo, useState } from "react";

type WorkspaceTab = "assets" | "source" | "pages" | "draw" | "library";

const kinds = [
  ["character", "人物参考"],
  ["outfit", "服装参考"],
  ["style", "漫画风格"],
] as const;

const modelOptions: { alias: ImageModelAlias; name: string; id: string }[] = [
  { alias: "image.nano_banana_2", name: "Nano Banana 2", id: "gemini-3.1-flash-image" },
  { alias: "image.nano_banana_pro", name: "Nano Banana Pro", id: "gemini-3-pro-image" },
];

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function CandidateArtwork({ contentUrl, label }: { contentUrl: string | null; label: string }) {
  const url = publicUrl(contentUrl);
  return url ? (
    <Image className="candidate-image" src={url} alt={label} width={720} height={960} unoptimized />
  ) : (
    <div className="candidate-placeholder"><LoaderCircle size={22} /><span>等待 Worker 生成</span></div>
  );
}

export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<WorkspaceTab>("source");
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [assetKind, setAssetKind] = useState("character");
  const [uploadError, setUploadError] = useState("");
  const [sourceTitle, setSourceTitle] = useState("第一章");
  const [sourceText, setSourceText] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(null);
  const [drawModel, setDrawModel] = useState<ImageModelAlias>("image.nano_banana_2");
  const [drawResolution, setDrawResolution] = useState<Resolution>("1K");
  const [characterName, setCharacterName] = useState("");
  const [characterAliases, setCharacterAliases] = useState("");
  const [bindCharacterId, setBindCharacterId] = useState("");
  const [favoriteOnly, setFavoriteOnly] = useState(false);

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const assets = useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id) });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const chapters = useQuery({ queryKey: ["chapters", id], queryFn: () => api.chapters(id) });
  const characters = useQuery({ queryKey: ["characters", id], queryFn: () => api.characters(id) });
  const library = useQuery({
    queryKey: ["library", id, favoriteOnly],
    queryFn: () => api.library(id, favoriteOnly ? true : undefined),
    refetchInterval: 4000,
  });
  const jobs = useQuery({
    queryKey: ["jobs", id],
    queryFn: () => api.jobs(id),
    refetchInterval: 3000,
  });
  const exportsQuery = useQuery({ queryKey: ["exports", id], queryFn: () => api.exports(id) });
  const activeChapterId = selectedChapterId ?? chapters.data?.[0]?.id ?? null;
  const pages = useQuery({
    queryKey: ["pages", activeChapterId],
    queryFn: () => api.pages(activeChapterId!),
    enabled: Boolean(activeChapterId),
  });
  const selectedPage = pages.data?.find((item) => item.id === selectedPageId) ?? pages.data?.[0] ?? null;
  const batches = useQuery({
    queryKey: ["batches", selectedPage?.id],
    queryFn: () => api.batches(selectedPage!.id),
    enabled: Boolean(selectedPage),
  });
  const currentBatch = batches.data?.find((item) => item.status === "OPEN") ?? batches.data?.[0] ?? null;
  const candidates = useQuery({
    queryKey: ["candidates", currentBatch?.id],
    queryFn: () => api.candidates(currentBatch!.id),
    enabled: Boolean(currentBatch),
    refetchInterval: 3000,
  });

  const draft = localDraft ?? project.data ?? null;
  const queueStats = useMemo(() => {
    const values = jobs.data ?? [];
    return {
      waiting: values.filter((item) => ["WAITING", "QUEUED"].includes(item.status)).length,
      failed: values.filter((item) => item.status === "FAILED").length,
    };
  }, [jobs.data]);

  const save = useMutation({
    mutationFn: () => api.updateProject(id, {
      version: draft!.version,
      default_resolution: draft!.default_resolution,
      draft_resolution: draft!.draft_resolution,
      workflow_mode: draft!.workflow_mode,
      default_concurrency: draft!.default_concurrency,
      ocr_enabled: draft!.ocr_enabled,
      consistency_check_enabled: draft!.consistency_check_enabled,
    }),
    onSuccess: (result) => {
      setDraft(result);
      queryClient.setQueryData(["project", id], result);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const uploaded = await api.uploadAsset(id, assetKind, file);
      if (assetKind === "character" && bindCharacterId) {
        await api.bindCharacterReference(bindCharacterId, uploaded.id);
      }
      return uploaded;
    },
    onSuccess: () => {
      setUploadError("");
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
    onError: (reason) => setUploadError(reason instanceof Error ? reason.message : "上传失败"),
  });

  const createCharacter = useMutation({
    mutationFn: () => api.createCharacter(
      id,
      characterName.trim(),
      characterAliases.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
    ),
    onSuccess: (result) => {
      setCharacterName("");
      setCharacterAliases("");
      setBindCharacterId(result.id);
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const generateCharacterAsset = useMutation({
    mutationFn: async (variant: "FRONT" | "SIDE" | "BACK" | "EXPRESSION") => {
      const batch = await api.startAssetBatch("CHARACTER", bindCharacterId, "CHARACTER");
      return api.generateAssetCandidate(batch.id, drawModel, "1K", variant);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const importSource = useMutation({
    mutationFn: () => api.importSource(id, sourceTitle.trim(), sourceText),
    onSuccess: (result) => {
      setSelectedChapterId(result.chapters[0]?.id ?? null);
      setSourceText("");
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const parseChapter = useMutation({
    mutationFn: () => api.parseChapter(activeChapterId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });

  const planChapter = useMutation({
    mutationFn: () => api.planChapter(activeChapterId!),
    onSuccess: (result) => {
      setSelectedPageId(result.pages[0]?.id ?? null);
      setTab("pages");
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const startBatch = useMutation({
    mutationFn: () => api.startBatch(selectedPage!.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] }),
  });

  const generate = useMutation({
    mutationFn: async () => {
      const batch = currentBatch ?? await api.startBatch(selectedPage!.id);
      return api.generateCandidate(batch.id, drawModel, drawResolution);
    },
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["project", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const favorite = useMutation({
    mutationFn: ({ candidateId, value }: { candidateId: string; value: boolean }) =>
      api.favoriteCandidate(candidateId, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const selectCandidate = useMutation({
    mutationFn: (candidateId: string) => api.selectCandidate(selectedPage!.id, candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const goNext = useMutation({
    mutationFn: () => api.nextPage(selectedPage!.id),
    onSuccess: (next) => {
      setSelectedPageId(next.id);
      queryClient.invalidateQueries({ queryKey: ["batches", next.id] });
    },
  });

  const createExport = useMutation({
    mutationFn: (type: "PNG" | "PDF" | "JSON") => api.createExport(activeChapterId!, type),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", id] }),
  });

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = "";
  }

  function openPage(page: MangaPage) {
    setSelectedPageId(page.id);
    setTab("draw");
  }

  if (project.isLoading || !draft) {
    return <AppShell><div className="full-loading"><LoaderCircle className="spin" />加载项目工作区…</div></AppShell>;
  }
  if (project.isError) {
    return <AppShell><div className="full-loading error"><CircleAlert />项目无法打开</div></AppShell>;
  }

  return (
    <AppShell>
      <header className="workspace-topbar">
        <div className="workspace-crumb"><Link href="/"><ArrowLeft size={17} />项目</Link><i /><span>{draft.name}</span></div>
        <div className="workspace-status"><span><i />MVP 工作流已接通</span><button className="button ink compact" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={15} />}保存设置</button></div>
      </header>

      <div className="workspace-layout">
        <aside className="workspace-left">
          <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{draft.name}</h1><p>{chapters.data?.length ?? 0} 章 · {pages.data?.length ?? 0} 页已规划</p></div>
          <nav className="workspace-steps">
            <button className={tab === "source" ? "active" : ""} onClick={() => setTab("source")}><BookOpenText size={17} /><span>原作导入<small>无损分段</small></span><i>01</i></button>
            <button className={tab === "assets" ? "active" : ""} onClick={() => setTab("assets")}><Users size={17} /><span>角色与素材<small>姓名绑定</small></span><i>02</i></button>
            <button className={tab === "pages" ? "active" : ""} onClick={() => setTab("pages")}><PanelTop size={17} /><span>动态分页<small>按内容扩展</small></span><i>03</i></button>
            <button className={tab === "draw" ? "active" : ""} onClick={() => setTab("draw")}><Sparkles size={17} /><span>单页抽卡<small>逐页选择</small></span><i>04</i></button>
            <button className={tab === "library" ? "active" : ""} onClick={() => setTab("library")}><LibraryBig size={17} /><span>批次素材库<small>收藏与导出</small></span><i>05</i></button>
          </nav>
          <div className="lock-note"><LockKeyhole size={16} /><p><strong>采用版本才影响后续</strong>收藏与采用互相独立，重新抽卡不会覆盖历史候选。</p></div>
        </aside>

        <section className="workspace-canvas">
          {tab === "source" && (
            <>
              <header className="canvas-header"><div><span>SOURCE / 原作</span><h2>完整导入，不压缩故事</h2></div><small>{chapters.data?.length ?? 0} 个章节</small></header>
              <div className="source-compose">
                <input className="text-input" value={sourceTitle} onChange={(event) => setSourceTitle(event.target.value)} placeholder="章节标题" />
                <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder="粘贴完整章节。系统先无损分段，再根据文字和剧本长度动态计算页数。" />
                <div><span>不会限制总页数 · 单页硬上限 180 个中文字符</span><button className="button ink" disabled={!sourceText.trim() || importSource.isPending} onClick={() => importSource.mutate()}>{importSource.isPending ? <LoaderCircle className="spin" size={16} /> : <Upload size={16} />}导入原作</button></div>
                {importSource.isError && <p className="form-error"><CircleAlert size={14} />{importSource.error.message}</p>}
              </div>
              <div className="chapter-register">
                {chapters.data?.map((chapter) => (
                    <button key={chapter.id} className={activeChapterId === chapter.id ? "chapter-row active" : "chapter-row"} onClick={() => setSelectedChapterId(chapter.id)}>
                    <span>{String(chapter.ordinal).padStart(2, "0")}</span><div><strong>{chapter.title}</strong><small>{chapter.source_character_count} 字 · {chapter.segment_count} 段 · {chapter.page_count} 页</small></div><em>{Math.round(chapter.coverage_ratio * 100)}% 覆盖</em>
                  </button>
                ))}
                {!chapters.data?.length && <div className="asset-empty"><BookOpenText size={24} /><strong>尚未导入原作</strong><p>粘贴一个完整章节开始工作。</p></div>}
              </div>
              {activeChapterId && <div className="workflow-actions"><button className="button outline" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />Gemini 解析角色与剧本</button><button className="button ink" disabled={planChapter.isPending} onClick={() => planChapter.mutate()}>{planChapter.isPending ? <LoaderCircle className="spin" size={15} /> : <PanelTop size={15} />}计算动态分页</button></div>}
            </>
          )}

          {tab === "assets" && (
            <>
              <header className="canvas-header"><div><span>CHARACTER BIBLE / 角色资产</span><h2>姓名、绰号与参考图绑定</h2></div><small>{characters.data?.length ?? 0} 个角色</small></header>
              <div className="character-create">
                <input className="text-input" value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="主要姓名（剧本默认使用）" />
                <input className="text-input" value={characterAliases} onChange={(event) => setCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" />
                <button className="button ink compact" disabled={!characterName.trim() || createCharacter.isPending} onClick={() => createCharacter.mutate()}><Plus size={14} />添加角色</button>
              </div>
              <div className="character-strip">
                {characters.data?.map((character) => <button key={character.id} className={bindCharacterId === character.id ? "character-chip active" : "character-chip"} onClick={() => setBindCharacterId(character.id)}><strong>{character.primary_name}</strong><span>{character.aliases.length ? `又名 ${character.aliases.join(" / ")}` : "无绰号"}</span>{character.alias_conflict && <em>称呼冲突待确认</em>}<small>{character.references.length} 张参考图</small></button>)}
              </div>
              {bindCharacterId && <div className="asset-quickgen"><span>为选中角色生成补充角度（使用抽卡区当前模型）</span><div>{(["FRONT", "SIDE", "BACK", "EXPRESSION"] as const).map((variant) => <button key={variant} disabled={generateCharacterAsset.isPending} onClick={() => generateCharacterAsset.mutate(variant)}><Sparkles size={13} />{{ FRONT: "正面", SIDE: "侧面", BACK: "背面", EXPRESSION: "表情" }[variant]}</button>)}</div></div>}
              <div className="intake-toolbar"><div className="kind-switch">{kinds.map(([value, label]) => <button key={value} className={assetKind === value ? "active" : ""} onClick={() => setAssetKind(value)}>{label}</button>)}</div><span>{assetKind === "character" && bindCharacterId ? "将绑定到选中的角色" : "PNG / JPG / WEBP · 最大 20 MB"}</span></div>
              <label className={upload.isPending ? "upload-stage busy" : "upload-stage"}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} disabled={upload.isPending} /><span className="upload-icon">{upload.isPending ? <LoaderCircle className="spin" /> : <Upload />}</span><strong>{upload.isPending ? "正在安全上传…" : `上传${kinds.find(([value]) => value === assetKind)?.[1]}`}</strong><p>人物图会和选中的主要姓名绑定，不会只依赖文件名猜测身份。</p></label>
              {uploadError && <p className="form-error"><CircleAlert size={15} />{uploadError}</p>}
              <div className="asset-list-header"><span>素材登记</span><small>ASSET REGISTER</small></div>
              <div className="asset-grid">
                {assets.data?.map((asset, index) => <article className="asset-card" key={asset.id}><div className={`asset-thumb thumb-${(index % 3) + 1}`}>{asset.content_url ? <Image src={publicUrl(asset.content_url)!} alt={asset.original_name} width={74} height={74} unoptimized /> : <FileImage size={27} />}<span>{asset.width && asset.height ? `${asset.width}×${asset.height}` : asset.mime_type}</span></div><div><strong>{asset.original_name}</strong><p>{asset.kind} · {formatBytes(asset.byte_size)}</p><span className="tiny-status"><Check size={11} />{asset.status}</span></div></article>)}
              </div>
            </>
          )}

          {tab === "pages" && (
            <>
              <header className="canvas-header"><div><span>PAGE CAPACITY / 动态分页</span><h2>内容有多少，页面就有多少</h2></div><small>{pages.data?.length ?? 0} 页</small></header>
              {!pages.data?.length ? <div className="asset-empty tall"><PanelTop size={28} /><strong>尚未计算页面</strong><p>先导入章节，再运行动态分页。</p></div> : <div className="page-plan-grid">{pages.data.map((page) => <button key={page.id} className={page.selected_candidate_id ? "page-plan-card accepted" : "page-plan-card"} onClick={() => openPage(page)}><span className="page-no">P.{String(page.page_number).padStart(3, "0")}</span><div className="mini-panels">{Array.from({ length: Math.min(page.panel_count, 6) }).map((_, index) => <i key={index} />)}</div><strong>{page.panel_count} 格 · {page.estimated_bubbles} 气泡</strong><p>{page.estimated_text_chars} 字 / 上限 180</p><small>{page.source_coverage.complete ? "原文覆盖完整" : "覆盖缺失"}</small>{page.selected_candidate_id && <em><Check size={11} />已采用</em>}</button>)}</div>}
            </>
          )}

          {tab === "draw" && (
            <>
              <header className="canvas-header"><div><span>DRAW / 单页抽卡</span><h2>{selectedPage ? `第 ${selectedPage.page_number} 页候选` : "选择一页开始"}</h2></div><small>每次只生成 1 页</small></header>
              {selectedPage ? <>
                <div className="draw-toolbar"><div className="page-picker">{pages.data?.map((page) => <button key={page.id} className={selectedPage.id === page.id ? "active" : ""} onClick={() => setSelectedPageId(page.id)}>{page.page_number}</button>)}</div><button className="button ghost compact" onClick={() => startBatch.mutate()}><Plus size={14} />新批次</button></div>
                <div className="draw-context"><div><span>PAGE LOAD</span><strong>{selectedPage.estimated_text_chars} 字</strong><small>{selectedPage.panel_count} 格 / {selectedPage.estimated_bubbles} 气泡</small></div><p>{selectedPage.source_coverage.ranges?.map((item) => item.text).join("").slice(0, 180)}</p></div>
                <div className="model-duel">{modelOptions.map((option) => <button key={option.alias} className={drawModel === option.alias ? "model-choice active" : "model-choice"} onClick={() => setDrawModel(option.alias)}><Sparkles size={18} /><span><strong>{option.name}</strong><small>{option.id}</small></span>{drawModel === option.alias && <Check size={15} />}</button>)}</div>
                <div className="generation-bar"><div className="resolution-row small">{(["1K", "2K", "4K"] as Resolution[]).map((value) => <button key={value} className={drawResolution === value ? "selected" : ""} onClick={() => setDrawResolution(value)}>{value}{value === "4K" && <small>P</small>}</button>)}</div><button className="button ink generate-one" disabled={generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? <LoaderCircle className="spin" size={17} /> : <Star size={17} />}生成一个候选</button></div>
                {(generate.isError || startBatch.isError) && <p className="form-error"><CircleAlert size={14} />{(generate.error ?? startBatch.error)?.message}</p>}
                <div className="batch-heading"><div><span>BATCH</span><strong>{currentBatch ? `批次 ${currentBatch.ordinal}` : "尚未开始批次"}</strong></div><small>可跨模型比较 · 收藏不等于采用</small></div>
                <div className="candidate-grid">{candidates.data?.map((candidate) => <article className={candidate.is_selected ? "candidate-card selected" : "candidate-card"} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} label={`候选 ${candidate.ordinal}`} /><div className="candidate-meta"><span>候选 {String(candidate.ordinal).padStart(2, "0")}</span><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><small>{candidate.resolution} · {candidate.status}</small></div><div className="candidate-actions"><button className={candidate.is_favorite ? "favorited" : ""} onClick={() => favorite.mutate({ candidateId: candidate.id, value: !candidate.is_favorite })}><Heart size={14} fill={candidate.is_favorite ? "currentColor" : "none"} />收藏</button><button disabled={!candidate.asset_id || candidate.is_selected} onClick={() => selectCandidate.mutate(candidate.id)}><Check size={14} />{candidate.is_selected ? "已采用" : "采用"}</button></div></article>)}</div>
                {!candidates.data?.length && <div className="asset-empty"><ImagePlus size={25} /><strong>这个批次还没有候选</strong><p>选择任一平级模型，生成一张再决定是否收藏或采用。</p></div>}
                <div className="next-page-row"><span>{selectedPage.selected_candidate_id ? "当前页已有采用版本，可以继续" : "采用一个满意候选后才能进入下一页"}</span><button className="button outline" disabled={!selectedPage.selected_candidate_id || goNext.isPending} onClick={() => goNext.mutate()}>生成下一页 <ArrowRight size={15} /></button></div>
              </> : <div className="asset-empty tall"><Sparkles size={28} /><strong>没有可抽卡页面</strong><p>先完成动态分页。</p></div>}
            </>
          )}

          {tab === "library" && (
            <>
              <header className="canvas-header"><div><span>LIBRARY / 批次素材库</span><h2>保存每一次值得比较的结果</h2></div><small>{library.data?.total_candidates ?? 0} 个候选</small></header>
              <div className="library-toolbar"><button className={favoriteOnly ? "active" : ""} onClick={() => setFavoriteOnly(!favoriteOnly)}><Heart size={14} />只看收藏（{library.data?.favorite_count ?? 0}）</button><span>按章节 → 页面 → 批次排列</span></div>
              <div className="library-groups">{library.data?.groups.map((group) => <section className="library-group" key={group.batch.id}><header><div><span>BATCH {String(group.batch.ordinal).padStart(3, "0")}</span><strong>{group.batch.generation_kind === "REPAIR" ? "修复批次" : "页面抽卡"}</strong></div><small>{new Date(group.batch.created_at).toLocaleString("zh-CN")} · {group.candidates.length} 张</small></header><div className="library-candidates">{group.candidates.map((candidate) => <article key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} label={`批次候选 ${candidate.ordinal}`} /><div><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><span>{candidate.resolution} · {candidate.status}</span>{candidate.is_favorite && <Heart size={13} fill="currentColor" />}{candidate.is_selected && <em>采用中</em>}</div></article>)}</div></section>)}</div>
              {!library.data?.groups.length && <div className="asset-empty tall"><LibraryBig size={28} /><strong>素材库还是空的</strong><p>从单页抽卡开始，所有候选都会按批次保留。</p></div>}
              <div className="export-desk"><div><span>EXPORT / 导出</span><strong>采用全部页面后导出整章</strong></div><div>{(["PNG", "PDF", "JSON"] as const).map((type) => <button key={type} disabled={!activeChapterId || createExport.isPending} onClick={() => createExport.mutate(type)}><Download size={14} />{type}</button>)}</div></div>
              <div className="export-list">{exportsQuery.data?.map((item) => <a key={item.id} href={publicUrl(item.download_url)!}><FileImage size={14} /><span>{item.export_type} · {item.page_count} 页 · {formatBytes(item.byte_size)}</span><Download size={13} /></a>)}</div>
              {createExport.isError && <p className="form-error"><CircleAlert size={14} />{createExport.error.message}</p>}
            </>
          )}
        </section>

        <aside className="workspace-right">
          <header><span>项目控制</span><small>SERVER VALIDATED</small></header>
          <label className="field-label">工作模式</label>
          <div className="select-wrap light"><select value={draft.workflow_mode} onChange={(event) => setDraft({ ...draft, workflow_mode: event.target.value as Project["workflow_mode"] })}><option value="SEMI_AUTO">半自动</option><option value="DIRECTOR">导演模式</option><option value="AUTO">自动模式</option></select><ChevronDown size={15} /></div>
          <label className="field-label">平级生图模型</label>
          <div className="equal-models">{modelOptions.map((item) => <div key={item.alias}><strong>{item.name}</strong><span>{models.data?.find((model) => model.logical_alias === item.alias)?.model_id ?? item.id}</span></div>)}</div>
          <p className="side-note">不设主次。每次生成候选时单独选择，项目仅记录上一次选择。</p>
          <label className="field-label">正式清晰度</label>
          <div className="resolution-row small">{(["1K", "2K", "4K"] as Resolution[]).map((value) => <button key={value} className={draft.default_resolution === value ? "selected" : ""} onClick={() => setDraft({ ...draft, default_resolution: value })}>{value}{value === "4K" && <small>P</small>}</button>)}</div>
          <label className="field-label">并发任务</label>
          <div className="stepper"><button onClick={() => setDraft({ ...draft, default_concurrency: Math.max(1, draft.default_concurrency - 1) })}>−</button><strong>{draft.default_concurrency}</strong><button onClick={() => setDraft({ ...draft, default_concurrency: Math.min(8, draft.default_concurrency + 1) })}>＋</button></div>
          <div className="toggle-row"><div><strong>OCR 文字检查</strong><span>生成后核对目标对白</span></div><button className={draft.ocr_enabled ? "toggle on" : "toggle"} onClick={() => setDraft({ ...draft, ocr_enabled: !draft.ocr_enabled })}><i /></button></div>
          <div className="toggle-row"><div><strong>一致性检查</strong><span>角色、服装与场景连续性</span></div><button className={draft.consistency_check_enabled ? "toggle on" : "toggle"} onClick={() => setDraft({ ...draft, consistency_check_enabled: !draft.consistency_check_enabled })}><i /></button></div>
          <div className="settings-footnote"><LockKeyhole size={15} /><p>模型请求只会从服务端 Worker 发出；浏览器不会接触 Vertex 凭据。</p></div>
          {save.isSuccess && <p className="save-success"><Check size={14} />设置已保存</p>}
          {save.isError && <p className="form-error"><CircleAlert size={14} />{save.error.message}</p>}
        </aside>
      </div>

      <footer className="queue-dock"><div><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><strong>生成队列</strong><small>{jobs.data?.[0] ? `${jobs.data[0].job_type} · ${jobs.data[0].status}` : "当前没有任务"}</small></div><div><span>并发上限 {draft.default_concurrency}</span><i /><span>{queueStats.waiting} WAITING</span><i /><span>{queueStats.failed} FAILED</span></div></footer>
    </AppShell>
  );
}
