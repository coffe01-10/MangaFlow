"use client";

import { AppShell } from "@/components/shell";
import { api, type Project } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Boxes,
  Check,
  ChevronDown,
  CircleAlert,
  FileImage,
  ImagePlus,
  Layers3,
  LoaderCircle,
  LockKeyhole,
  PanelTop,
  Save,
  Sparkles,
  Upload,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ChangeEvent, useState } from "react";

const kinds = [
  ["character", "人物参考"],
  ["outfit", "服装参考"],
  ["style", "漫画风格"],
] as const;

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export default function ProjectWorkspace() {
  const { id } = useParams<{ id: string }>();
  const queryClient = useQueryClient();
  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const assets = useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id) });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [assetKind, setAssetKind] = useState("character");
  const [uploadError, setUploadError] = useState("");

  const draft = localDraft ?? project.data ?? null;

  const save = useMutation({
    mutationFn: () => api.updateProject(id, {
      version: draft!.version,
      default_resolution: draft!.default_resolution,
      draft_resolution: draft!.draft_resolution,
      workflow_mode: draft!.workflow_mode,
      default_concurrency: draft!.default_concurrency,
      ocr_enabled: draft!.ocr_enabled,
      consistency_check_enabled: draft!.consistency_check_enabled,
      image_model_alias: draft!.image_model_alias,
    }),
    onSuccess: (result) => {
      setDraft(result);
      queryClient.setQueryData(["project", id], result);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadAsset(id, assetKind, file),
    onSuccess: () => {
      setUploadError("");
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
    },
    onError: (reason) => setUploadError(reason instanceof Error ? reason.message : "上传失败"),
  });

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = "";
  }

  if (project.isLoading || !draft) {
    return <AppShell><div className="full-loading"><LoaderCircle className="spin" />加载项目工作区…</div></AppShell>;
  }
  if (project.isError) {
    return <AppShell><div className="full-loading error"><CircleAlert />项目无法打开</div></AppShell>;
  }

  const selectedModel = models.data?.find((item) => item.logical_alias === draft.image_model_alias);

  return (
    <AppShell>
      <header className="workspace-topbar">
        <div className="workspace-crumb"><Link href="/"><ArrowLeft size={17} />项目</Link><i /> <span>{draft.name}</span></div>
        <div className="workspace-status"><span><i />基础设置</span><button className="button ink compact" disabled={save.isPending} onClick={() => save.mutate()}>{save.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={15} />}保存设置</button></div>
      </header>

      <div className="workspace-layout">
        <aside className="workspace-left">
          <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{draft.name}</h1><p>尚未导入章节</p></div>
          <nav className="workspace-steps">
            <button className="active"><Boxes size={17} /><span>素材接入<small>当前阶段</small></span><i>01</i></button>
            <button disabled><Layers3 size={17} /><span>原作与剧本<small>里程碑 2–3</small></span><i>02</i></button>
            <button disabled><PanelTop size={17} /><span>页面与分镜<small>里程碑 3</small></span><i>03</i></button>
            <button disabled><Sparkles size={17} /><span>生成与修复<small>里程碑 4–5</small></span><i>04</i></button>
          </nav>
          <div className="lock-note"><LockKeyhole size={16} /><p><strong>锁定机制已启用</strong>后续修改会由服务端检查锁定字段。</p></div>
        </aside>

        <section className="workspace-canvas">
          <header className="canvas-header"><div><span>ASSET INTAKE / 素材接入</span><h2>建立故事的视觉基准</h2></div><small>{assets.data?.length ?? 0} 个素材</small></header>
          <div className="intake-toolbar">
            <div className="kind-switch">
              {kinds.map(([value, label]) => <button key={value} className={assetKind === value ? "active" : ""} onClick={() => setAssetKind(value)}>{label}</button>)}
            </div>
            <span>PNG / JPG / WEBP · 最大 20 MB</span>
          </div>
          <label className={upload.isPending ? "upload-stage busy" : "upload-stage"}>
            <input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} disabled={upload.isPending} />
            <span className="upload-icon">{upload.isPending ? <LoaderCircle className="spin" /> : <Upload />}</span>
            <strong>{upload.isPending ? "正在安全上传…" : `上传${kinds.find(([value]) => value === assetKind)?.[1]}`}</strong>
            <p>点击选择文件。文件仅进入本地私有存储，不会直接发送给模型。</p>
          </label>
          {uploadError && <p className="form-error"><CircleAlert size={15} />{uploadError}</p>}

          <div className="asset-list-header"><span>已接入素材</span><small>ASSET REGISTER</small></div>
          {assets.isLoading ? <div className="asset-empty"><LoaderCircle className="spin" />读取素材…</div> : assets.data?.length ? (
            <div className="asset-grid">
              {assets.data.map((asset, index) => (
                <article className="asset-card" key={asset.id}>
                  <div className={`asset-thumb thumb-${(index % 3) + 1}`}><FileImage size={27} /><span>{asset.width && asset.height ? `${asset.width}×${asset.height}` : asset.mime_type}</span></div>
                  <div><strong>{asset.original_name}</strong><p>{kinds.find(([value]) => value === asset.kind)?.[1] ?? asset.kind} · {formatBytes(asset.byte_size)}</p><span className="tiny-status"><Check size={11} />已上传，待分析</span></div>
                </article>
              ))}
            </div>
          ) : (
            <div className="asset-empty"><ImagePlus size={24} /><strong>还没有参考素材</strong><p>人物、服装和风格图会在这里形成可追踪的资产库。</p></div>
          )}

          <section className="future-canvas"><span>NEXT / STORYBOARD</span><div className="blank-page"><i /><i /><i /><i /></div><h3>漫画页面尚未生成</h3><p>完成原作导入、资产确认和页面规划后，中央区域将显示可读的单页画布。</p></section>
        </section>

        <aside className="workspace-right">
          <header><span>项目设置</span><small>SERVER VALIDATED</small></header>
          <label className="field-label">工作模式</label>
          <div className="select-wrap light"><select value={draft.workflow_mode} onChange={(event) => setDraft({ ...draft, workflow_mode: event.target.value as Project["workflow_mode"] })}><option value="SEMI_AUTO">半自动（推荐）</option><option value="DIRECTOR">导演模式</option><option value="AUTO">自动模式</option></select><ChevronDown size={15} /></div>

          <label className="field-label">生图引擎</label>
          <div className="select-wrap light"><select value={draft.image_model_alias} onChange={(event) => setDraft({ ...draft, image_model_alias: event.target.value })}><option value="image.fast">Nano Banana 2</option><option value="image.quality">Nano Banana Pro</option></select><ChevronDown size={15} /></div>
          <div className="model-detail"><span>{selectedModel?.model_id ?? "读取模型能力…"}</span><p>1K / 2K / 4K <small>4K Preview</small></p></div>

          <label className="field-label">正式清晰度</label>
          <div className="resolution-row small">{(["1K", "2K", "4K"] as const).map((value) => <button key={value} className={draft.default_resolution === value ? "selected" : ""} onClick={() => setDraft({ ...draft, default_resolution: value })}>{value}{value === "4K" && <small>P</small>}</button>)}</div>

          <label className="field-label">并发任务</label>
          <div className="stepper"><button onClick={() => setDraft({ ...draft, default_concurrency: Math.max(1, draft.default_concurrency - 1) })}>−</button><strong>{draft.default_concurrency}</strong><button onClick={() => setDraft({ ...draft, default_concurrency: Math.min(8, draft.default_concurrency + 1) })}>＋</button></div>

          <div className="toggle-row"><div><strong>OCR 文字检查</strong><span>生成后核对目标对白</span></div><button className={draft.ocr_enabled ? "toggle on" : "toggle"} onClick={() => setDraft({ ...draft, ocr_enabled: !draft.ocr_enabled })}><i /></button></div>
          <div className="toggle-row"><div><strong>一致性检查</strong><span>角色、服装与场景连续性</span></div><button className={draft.consistency_check_enabled ? "toggle on" : "toggle"} onClick={() => setDraft({ ...draft, consistency_check_enabled: !draft.consistency_check_enabled })}><i /></button></div>

          <div className="settings-footnote"><LockKeyhole size={15} /><p>模型请求只会从服务端 Worker 发出；浏览器不会接触 Vertex 凭据。</p></div>
          {save.isSuccess && <p className="save-success"><Check size={14} />设置已保存</p>}
          {save.isError && <p className="form-error"><CircleAlert size={14} />{save.error.message}</p>}
        </aside>
      </div>

      <footer className="queue-dock"><div><span className="queue-light" /><strong>生成队列</strong><small>里程碑 4 开放 · 当前没有任务</small></div><div><span>并发上限 {draft.default_concurrency}</span><i /><span>0 WAITING</span><i /><span>0 FAILED</span></div></footer>
    </AppShell>
  );
}
