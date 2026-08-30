"use client";

import { AppShell, GlobalNav } from "@/components/shell";
import { api, type DashboardAIOverview, type DashboardProject, type Project } from "@/lib/api";
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
import { FormEvent, useState } from "react";

const modeLabel = { AUTO: "自动", DIRECTOR: "导演", SEMI_AUTO: "半自动" } as const;

function ConnectionBadge({ summary }: { summary?: DashboardAIOverview }) {
  const healthy = summary?.healthy_connection_count ?? 0;
  const configured = summary?.configured_connection_count ?? 0;
  if (healthy) return <span className="status-chip success"><i />{healthy} 个 AI 连接健康</span>;
  if (configured > 0) return <span className="status-chip warning"><i />供应商待验证</span>;
  if (!summary) return <span className="status-chip muted"><i />检测中</span>;
  return <span className="status-chip danger"><i />未配置</span>;
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

function ProjectCard({ item, index }: { item: DashboardProject; index: number }) {
  const { project } = item;
  const progress = item.page_count ? Math.round((item.selected_page_count / item.page_count) * 100) : 0;
  return (
    <Link href={`/projects/${project.id}/${item.next_action.section}`} className="project-card">
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
      <div className="project-progress" aria-label={`制作进度 ${progress}%`}><i style={{ width: `${progress}%` }} /></div>
      <footer><span>{item.chapter_count} 章 · {item.page_count} 页 · {item.selected_page_count} 已采用</span><span>{item.next_action.label}</span></footer>
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
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
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
      {open && <>
      <button className="drawer-backdrop show" onClick={onClose} aria-label="关闭" />
      <aside className="create-drawer open">
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

          <p className="form-note"><Sparkles size={14} />图片模型不设默认主次；进入工作区后必须显式选择，以保持项目画风一致。</p>

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
      </>}
    </>
  );
}

export default function HomePage() {
  const [creating, setCreating] = useState(false);
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard });
  const aiOverview = dashboard.data?.ai_overview;
  const projectCount = dashboard.data?.totals.project_count ?? 0;

  return (
    <AppShell>
      <div className="paper-texture" />
      <header className="topbar">
        <div className="topbar-title"><span>MANGAFLOW / PRODUCTION DESK</span><strong>漫画生产台</strong></div>
        <div className="topbar-actions">
          <GlobalNav />
          <ConnectionBadge summary={aiOverview} />
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
            <div><span>漫画页面</span><strong>{String(dashboard.data?.totals.page_count ?? 0).padStart(2, "0")}</strong><small>{dashboard.data?.totals.selected_page_count ?? 0} 页已采用</small></div>
            <div><span>待复查</span><strong>{String(dashboard.data?.totals.review_page_count ?? 0).padStart(2, "0")}</strong><small>按页面去重</small></div>
            <div className="metric-accent"><Gauge size={17} /><span>AI 模型目录</span><strong>{aiOverview?.enabled_model_count ?? 0} 个可用模型</strong><small>图片显式选择 · 文字可自动路由</small></div>
          </div>

          <section className="projects-section">
            <header><div><span className="section-index">项目 / PROJECTS</span><h2>最近创作</h2></div><span className="count-label">{projectCount} 个项目</span></header>
            {dashboard.isLoading ? (
              <div className="loading-panel"><LoaderCircle className="spin" />正在读取项目…</div>
            ) : dashboard.isError ? (
              <div className="error-panel"><CircleAlert /><div><strong>无法连接 MangaFlow API</strong><p>请确认 FastAPI 已在 8000 端口启动。</p></div></div>
            ) : (
              <div className="project-grid">
                {dashboard.data?.projects.map((item, index) => <ProjectCard key={item.project.id} item={item} index={index} />)}
                <EmptyProjects onCreate={() => setCreating(true)} />
              </div>
            )}
          </section>
        </section>

        <aside className="dashboard-side">
          <section className="side-card connection-card">
            <header><span><CloudCog size={17} />AI 连接 / CONNECTIONS</span><ConnectionBadge summary={aiOverview} /></header>
            <div className="connection-signal"><i className={(aiOverview?.configured_connection_count ?? 0) > 0 ? "on" : ""} /><i className={(aiOverview?.healthy_connection_count ?? 0) > 0 ? "on" : ""} /><i className={(aiOverview?.enabled_model_count ?? 0) > 0 ? "on" : ""} /></div>
            <h3>{(aiOverview?.healthy_connection_count ?? 0) > 0 ? "AI 连接已就绪" : (aiOverview?.configured_connection_count ?? 0) > 0 ? "连接等待验证" : "等待添加供应商"}</h3>
            <p>统一管理账号型凭据与 API Key 连接；模型能力以目录验证结果为准。</p>
            <dl>
              <div><dt>已配置连接</dt><dd>{aiOverview?.configured_connection_count ?? 0}</dd></div>
              <div><dt>健康连接</dt><dd>{aiOverview?.healthy_connection_count ?? 0}</dd></div>
              <div><dt>可用模型</dt><dd>{aiOverview?.enabled_model_count ?? 0}</dd></div>
            </dl>
            <Link className="button outline full" href="/settings"><ShieldCheck size={16} />管理连接与验证</Link>
          </section>

          <section className="side-card flow-card">
            <header><span>生产闭环</span><small>MVP ROUTE</small></header>
            <ol>
              {["导入原作", "建立资产", "剧本改编", "分页分镜", "生成页面", "人工校对与采用", "连续导出"].map((step, index) => (
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
