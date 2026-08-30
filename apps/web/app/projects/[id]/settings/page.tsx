"use client";

import { AppShell } from "@/components/shell";
import { api, type Project, type Resolution, type WorkflowMode } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, CircleAlert, Gauge, LoaderCircle, Save, ShieldCheck, SlidersHorizontal, Trash2 } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { creatorVisibleModels } from "@/lib/model-visibility";

type ProjectDraft = Pick<Project, "workflow_mode" | "draft_resolution" | "default_resolution" | "default_concurrency" | "consistency_check_enabled" | "default_text_model_id" | "text_model_alias">;

export default function ProjectSettingsPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const [localDraft, setLocalDraft] = useState<ProjectDraft | null>(null);
  const [saved, setSaved] = useState(false);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const draft = localDraft ?? (project.data ? {
      workflow_mode: project.data.workflow_mode,
      draft_resolution: project.data.draft_resolution,
      default_resolution: project.data.default_resolution,
      default_concurrency: project.data.default_concurrency,
      consistency_check_enabled: project.data.consistency_check_enabled,
      default_text_model_id: project.data.default_text_model_id,
      text_model_alias: project.data.text_model_alias,
    } : null);
  const textModels = draft ? creatorVisibleModels(
    (models.data ?? []).filter((model) => model.model_type === "TEXT" && model.operations.includes("structured_text") && model.operations.includes("multimodal_analysis")),
    {
      catalogIds: [draft.default_text_model_id],
      logicalAliases: [draft.text_model_alias],
    },
  ) : [];
  const currentTextModelValue = draft?.default_text_model_id ?? draft?.text_model_alias ?? "auto";
  const textModelOptionValue = (catalogId: string, logicalAlias: string) =>
    draft?.default_text_model_id === catalogId ? catalogId : logicalAlias === draft?.text_model_alias ? logicalAlias : catalogId;
  const currentTextModelMissing = currentTextModelValue !== "auto" && !textModels.some((model) =>
    textModelOptionValue(model.catalog_id, model.logical_alias) === currentTextModelValue,
  );

  const save = useMutation({
    mutationFn: () => {
      if (!draft || !project.data) throw new Error("项目设置尚未加载");
      return api.updateProject(id, { ...draft, version: project.data.version });
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["project", id], data);
      setLocalDraft({
        workflow_mode: data.workflow_mode,
        draft_resolution: data.draft_resolution,
        default_resolution: data.default_resolution,
        default_concurrency: data.default_concurrency,
        consistency_check_enabled: data.consistency_check_enabled,
        default_text_model_id: data.default_text_model_id,
        text_model_alias: data.text_model_alias,
      });
      setSaved(true);
    },
  });
  const update = <K extends keyof ProjectDraft>(key: K, value: ProjectDraft[K]) => {
    setLocalDraft((current) => ({ ...(current ?? draft!), [key]: value }));
    setSaved(false);
  };
  const archive = useMutation({
    mutationFn: () => {
      if (!project.data || deleteConfirmation !== project.data.name) throw new Error("请输入完整项目名称");
      return api.deleteProject(id, deleteConfirmation);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.removeQueries({ queryKey: ["project", id] });
      router.replace("/");
    },
  });

  return (
    <AppShell>
      <div className="paper-texture" />
      <header className="topbar settings-topbar">
        <div className="topbar-title"><span>PROJECT / CONTROL</span><strong>{project.data?.name ?? "读取项目…"}</strong></div>
        <div className="topbar-actions"><Link href={`/projects/${id}`} className="button ghost compact"><ArrowLeft size={16} />返回工作区</Link><button className="button ink compact" disabled={!draft || save.isPending} onClick={() => save.mutate()}>{save.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}保存项目设置</button></div>
      </header>
      <main className="project-settings-page">
        <header className="project-settings-hero"><span>PROJECT SETTINGS / 08</span><h1>控制每一次生成，<br />不是控制你的故事。</h1><p>这里仅保存当前项目的制作策略。图片模型仍在每个候选生成前单独选择，不设置主次。</p></header>
        {draft ? <div className="project-settings-grid">
          <section className="project-setting-section">
            <header><SlidersHorizontal size={18} /><div><span>WORKFLOW MODE</span><h2>工作方式</h2></div></header>
            <div className="project-choice-grid">{([
              ["DIRECTOR", "导演模式", "每个关键阶段都等待确认"],
              ["SEMI_AUTO", "半自动", "自动完成准备步骤，保留人工采用"],
              ["AUTO", "自动规划", "自动推进文字环节，图片仍逐页确认"],
            ] as const).map(([value, label, detail]) => <button key={value} className={draft.workflow_mode === value ? "selected" : ""} onClick={() => update("workflow_mode", value as WorkflowMode)}><span><strong>{label}</strong><small>{detail}</small></span>{draft.workflow_mode === value && <Check size={16} />}</button>)}</div>
          </section>

          <section className="project-setting-section">
            <header><Gauge size={18} /><div><span>OUTPUT</span><h2>清晰度与并发</h2></div></header>
            <div className="project-inline-setting"><span><strong>草稿清晰度</strong><small>抽卡和预览使用</small></span><div className="segmented">{(["1K", "2K"] as Resolution[]).map((value) => <button key={value} className={draft.draft_resolution === value ? "selected" : ""} onClick={() => update("draft_resolution", value)}>{value}</button>)}</div></div>
            <div className="project-inline-setting"><span><strong>正式清晰度</strong><small>导出前保持结构升清</small></span><div className="segmented">{(["1K", "2K", "4K"] as Resolution[]).map((value) => <button key={value} className={draft.default_resolution === value ? "selected" : ""} onClick={() => update("default_resolution", value)}>{value}</button>)}</div></div>
            <label className="project-inline-setting"><span><strong>任务并发</strong><small>同一项目最多并行任务数</small></span><input type="number" min={1} max={8} value={draft.default_concurrency} onChange={(event) => update("default_concurrency", Number(event.target.value))} /></label>
          </section>

          <section className="project-setting-section checks-section">
            <header><ShieldCheck size={18} /><div><span>QUALITY GATES</span><h2>检查开关</h2></div></header>
            <button className={`switch-setting ${draft.consistency_check_enabled ? "on" : ""}`} onClick={() => update("consistency_check_enabled", !draft.consistency_check_enabled)}><ShieldCheck size={19} /><span><strong>连续性检查</strong><small>检查角色、服装、道具和场景状态</small></span><i /></button>
            <p className="project-setting-note"><strong>文字由人工校对</strong><span>采用候选前必须明确确认页面文字，不再运行 OCR 或自动文字修复。</span></p>
          </section>

          <aside className="project-setting-note"><span>MODEL POLICY</span><strong>图片模型按任务选择</strong><p>项目不绑定图片“主模型”。每次生成候选都必须明确选择供应商模型，以保持画风一致。</p></aside>
          <section className="project-setting-section">
            <header><Gauge size={18} /><div><span>TEXT MODEL</span><h2>文字任务默认路由</h2></div></header>
            <label className="project-inline-setting"><span><strong>剧本、风格分析与视觉检查</strong><small>自动路由只使用已完成能力测试的模型</small></span><select value={draft.default_text_model_id ?? draft.text_model_alias} onChange={(event) => {
              const value = event.target.value;
              update("default_text_model_id", value === "auto" || value === "text.fast" ? null : value);
              update("text_model_alias", value === "auto" ? "auto" : "text.fast");
            }}><option value="auto">自动路由 · 已验证文字/视觉模型</option>{currentTextModelMissing ? <option value={currentTextModelValue}>当前配置 · {currentTextModelValue}</option> : null}{textModels.map((model) => <option key={model.catalog_id} value={textModelOptionValue(model.catalog_id, model.logical_alias)}>{model.provider} · {model.display_name}{!model.display_enabled ? "（已隐藏）" : ""}</option>)}</select></label>
          </section>
        </div> : <div className="loading-panel"><LoaderCircle className="spin" />读取项目设置…</div>}
        {saved && <p className="save-success floating"><Check size={15} />项目设置已保存</p>}
        {save.isError && <p className="form-error"><CircleAlert size={15} />{save.error.message}</p>}
        {project.data && <section className="project-danger-zone"><header><Trash2 size={18} /><div><span>DANGER ZONE / 项目管理</span><h2>删除当前项目</h2></div></header><div><p>删除后项目将从工作台隐藏。数据库记录和生成文件暂时保留，避免误删；如需恢复可由维护工具处理。</p><label><span>输入项目名称确认</span><input aria-label="输入项目名称确认删除" value={deleteConfirmation} onChange={(event) => setDeleteConfirmation(event.target.value)} placeholder={project.data.name} /></label><button type="button" disabled={deleteConfirmation !== project.data.name || archive.isPending} onClick={() => { if (window.confirm(`确认从工作台删除项目“${project.data.name}”？`)) archive.mutate(); }}>{archive.isPending ? <LoaderCircle className="spin" size={16} /> : <Trash2 size={16} />}删除项目</button></div>{archive.isError && <p className="form-error"><CircleAlert size={15} />{archive.error.message}</p>}</section>}
      </main>
    </AppShell>
  );
}
