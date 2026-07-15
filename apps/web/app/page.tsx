"use client";

import { AppShell } from "@/components/shell";
import { api, type Project, type VertexStatus } from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Check,
  CircleAlert,
  CloudCog,
  Gauge,
  LoaderCircle,
  Plus,
  Settings,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import Link from "next/link";
import { FormEvent, useMemo, useState } from "react";

const modeLabel = { AUTO: "自动", DIRECTOR: "导演", SEMI_AUTO: "半自动" } as const;

function ConnectionBadge({ status }: { status?: VertexStatus }) {
  if (!status) return <span className="status-chip muted"><i />检测中</span>;
  if (!status.configured) return <span className="status-chip danger"><i />未配置</span>;
  if (status.health_state === "HEALTHY") return <span className="status-chip success"><i />Vertex 已验证</span>;
  if (status.health_state === "CHECKING") return <span className="status-chip muted"><i />正在验证</span>;
  if (status.health_state === "DEGRADED") return <span className="status-chip warning"><i />连接降级</span>;
  return <span className="status-chip danger"><i />Vertex 离线</span>;
}

function EmptyProjects({ onCreate }: { onCreate: () => void }) {
  return (
    <button className="empty-project" onClick={onCreate}>
      <span className="empty-project-art" aria-hidden="true">
        <i className="frame one" />
        <i className="frame two" />
        <Plus size={23} />
      </span>
      <strong>建立第一部漫画</strong>
      <small>设置项目、模型与工作模式</small>
    </button>
  );
}

function ProjectCard({ project, index }: { project: Project; index: number }) {
  return (
    <Link href={`/projects/${project.id}`} className="project-card">
      <div className={`project-cover cover-${(index % 3) + 1}`}>
        <span className="cover-kicker">MANGA PROJECT</span>
        <span className="cover-number">{String(index + 1).padStart(2, "0")}</span>
        <div className="cover-lines"><i /><i /><i /></div>
        <strong>{project.name.slice(0, 8)}</strong>
        <span className="cover-stamp">制作中</span>
      </div>
      <div className="project-meta">
        <div>
          <strong>{project.name}</strong>
          <span>{modeLabel[project.workflow_mode]} · {project.default_resolution}</span>
        </div>
        <ArrowRight size={17} />
      </div>
      <div className="project-progress"><i style={{ width: "2%" }} /></div>
      <footer><span>尚未导入章节</span><span>刚刚更新</span></footer>
    </Link>
  );
}

function CreateProjectPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [mode, setMode] = useState("SEMI_AUTO");
  const [resolution, setResolution] = useState("2K");
  const [error, setError] = useState("");
  const createProject = useMutation({
    mutationFn: () => api.createProject({
      name: name.trim(),
      workflow_mode: mode as Project["workflow_mode"],
      default_resolution: resolution as Project["default_resolution"],
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setName("");
      setError("");
      onClose();
    },
    onError: (reason) => setError(reason instanceof Error ? reason.message : "创建失败"),
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) return setError("请先填写项目名称");
    createProject.mutate();
  }

  return (
    <>
      <button className={open ? "drawer-backdrop show" : "drawer-backdrop"} onClick={onClose} aria-label="关闭" />
      <aside className={open ? "create-drawer open" : "create-drawer"} aria-hidden={!open}>
        <header>
          <div><span>NEW PROJECT / 01</span><h2>建立漫画项目</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="关闭创建面板"><X size={19} /></button>
        </header>
        <form onSubmit={submit}>
          <label className="field-label">项目名称</label>
          <input className="text-input title-input" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：雨停之前" autoFocus={open} />

          <label className="field-label">工作方式</label>
          <div className="mode-grid">
            {[
              ["SEMI_AUTO", "半自动", "推荐", "AI 先完成，可随时接管"],
              ["DIRECTOR", "导演", "逐步", "每个阶段等待确认"],
              ["AUTO", "自动", "快速", "自动运行到最终审核"],
            ].map(([value, label, tag, desc]) => (
              <button type="button" key={value} className={mode === value ? "mode-option selected" : "mode-option"} onClick={() => setMode(value)}>
                <span>{label}<small>{tag}</small></span><p>{desc}</p>{mode === value && <Check size={16} />}
              </button>
            ))}
          </div>

          <p className="form-note"><Sparkles size={14} />Nano Banana 2 与 Pro 不设默认主模型；进入工作区后，每次生成前选择。</p>

          <label className="field-label">正式输出清晰度</label>
          <div className="resolution-row">
            {["1K", "2K", "4K"].map((item) => (
              <button type="button" key={item} onClick={() => setResolution(item)} className={resolution === item ? "selected" : ""}>
                {item}{item === "4K" && <small>Preview</small>}
              </button>
            ))}
          </div>
          <p className="form-note"><ShieldCheck size={14} />凭据仅保存在服务端，本项目不会把密钥发往浏览器。</p>
          {error && <p className="form-error"><CircleAlert size={15} />{error}</p>}
          <div className="drawer-actions">
            <button type="button" className="button ghost" onClick={onClose}>取消</button>
            <button className="button ink" disabled={createProject.isPending}>
              {createProject.isPending ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={16} />}创建项目
            </button>
          </div>
        </form>
      </aside>
    </>
  );
}

export default function HomePage() {
  const [creating, setCreating] = useState(false);
  const queryClient = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const vertex = useQuery({ queryKey: ["vertex-status"], queryFn: api.vertexStatus });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const verify = useMutation({
    mutationFn: () => api.verifyVertex("CREDENTIALS"),
    onSuccess: (result) => queryClient.setQueryData(["vertex-status"], result),
  });
  const modelMap = useMemo(() => new Map(models.data?.map((model) => [model.logical_alias, model])), [models.data]);
  const projectCount = projects.data?.length ?? 0;

  return (
    <AppShell>
      <div className="paper-texture" />
      <header className="topbar">
        <div className="topbar-title"><span>MANGAFLOW / PRODUCTION DESK</span><strong>漫画生产台</strong></div>
        <div className="topbar-actions">
          <ConnectionBadge status={vertex.data} />
          <Link className="button ghost compact" href="/settings"><Settings size={16} />系统设置</Link>
          <button className="button ink compact" onClick={() => setCreating(true)}><Plus size={16} />新建项目</button>
        </div>
      </header>

      <div className="dashboard-grid">
        <section className="dashboard-main">
          <div className="hero-row">
            <div><span className="section-index">工作区 / 00</span><h1>从文字开始，<br /><em>把故事画出来。</em></h1></div>
            <p>面向连续漫画生产的结构化工作流。角色、服装、分镜与对白，每一步都可确认、可锁定、可修复。</p>
          </div>

          <div className="metric-strip">
            <div><span>活跃项目</span><strong>{String(projectCount).padStart(2, "0")}</strong><small>PROJECTS</small></div>
            <div><span>漫画页面</span><strong>00</strong><small>尚未生成</small></div>
            <div><span>待审核</span><strong>00</strong><small>NEEDS REVIEW</small></div>
            <div className="metric-accent"><Gauge size={17} /><span>平级生图引擎</span><strong>Nano Banana 2 / Pro</strong><small>{modelMap.size >= 3 ? "每次抽卡独立选择" : "读取模型能力…"}</small></div>
          </div>

          <section className="projects-section">
            <header><div><span className="section-index">项目 / PROJECTS</span><h2>最近创作</h2></div><span className="count-label">{projectCount} 个项目</span></header>
            {projects.isLoading ? (
              <div className="loading-panel"><LoaderCircle className="spin" />正在读取项目…</div>
            ) : projects.isError ? (
              <div className="error-panel"><CircleAlert /><div><strong>无法连接 MangaFlow API</strong><p>请确认 FastAPI 已在 8000 端口启动。</p></div></div>
            ) : (
              <div className="project-grid">
                {projects.data?.map((project, index) => <ProjectCard key={project.id} project={project} index={index} />)}
                <EmptyProjects onCreate={() => setCreating(true)} />
              </div>
            )}
          </section>
        </section>

        <aside className="dashboard-side">
          <section className="side-card vertex-card">
            <header><span><CloudCog size={17} />VERTEX AI</span><ConnectionBadge status={vertex.data} /></header>
            <div className="vertex-signal"><i className={vertex.data?.configured ? "on" : ""} /><i className={vertex.data?.credential_file_present ? "on" : ""} /><i className={vertex.data?.health_state === "HEALTHY" ? "on" : ""} /></div>
            <h3>{vertex.data?.configured ? "服务端凭据已接入" : "等待服务端配置"}</h3>
            <p>{vertex.data?.message ?? "正在检查本地配置…"}</p>
            <dl>
              <div><dt>区域</dt><dd>{vertex.data?.location ?? "—"}</dd></div>
              <div><dt>文本模型</dt><dd>Gemini 3.5 Flash</dd></div>
              <div><dt>生图模型</dt><dd>NB 2 / NB Pro</dd></div>
            </dl>
            <button className="button outline full" disabled={!vertex.data?.configured || verify.isPending} onClick={() => verify.mutate()}>
              {verify.isPending ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />}{vertex.data?.health_state === "HEALTHY" ? "重新验证" : "联网验证凭据"}
            </button>
            {verify.isError && <p className="inline-error">{verify.error.message}</p>}
          </section>

          <section className="side-card flow-card">
            <header><span>生产闭环</span><small>MVP ROUTE</small></header>
            <ol>
              {["导入原作", "建立资产", "剧本改编", "分页分镜", "生成页面", "检查修复", "连续导出"].map((step, index) => (
                <li key={step}><span>{String(index + 1).padStart(2, "0")}</span><p>{step}</p><i className={index === 0 ? "ready" : ""} /></li>
              ))}
            </ol>
            <p className="honesty-note">原作导入、动态分页、逐页抽卡、收藏采用与批次素材库均已接入真实工作流。</p>
          </section>
        </aside>
      </div>
      <CreateProjectPanel open={creating} onClose={() => setCreating(false)} />
    </AppShell>
  );
}
