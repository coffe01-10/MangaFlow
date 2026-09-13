"use client";

import { AppShell } from "@/components/shell";
import { ProviderManagement } from "@/components/provider-management";
import { ClampedNumberInput } from "@/components/clamped-number-input";
import {
  api,
  ApiError,
  type DiagnosticCheck,
  type RuntimeSettings,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Database,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  Save,
  ServerCog,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

const diagnosticIcons: Record<DiagnosticCheck["status"], typeof CheckCircle2> = {
  OK: CheckCircle2, WARNING: TriangleAlert, FAILED: CircleAlert, NOT_CHECKED: RefreshCw,
};

function timeLabel(value: string | null) {
  if (!value) return "尚未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间格式异常";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
}

export default function SystemSettingsPage() {
  const queryClient = useQueryClient();
  const runtime = useQuery({ queryKey: ["runtime-settings"], queryFn: api.runtimeSettings });
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });
  const diagnostics = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics });
  const [localDraft, setLocalDraft] = useState<RuntimeSettings | null>(null);
  const [notice, setNotice] = useState("");
  // #546-5：供应商连接面板的半成品草稿（API Key / 手工模型 / JSON）上抛；
  // 与运行设置草稿合成页面级 beforeunload 判据。
  const [providerDirty, setProviderDirty] = useState(false);
  const draft = localDraft ?? runtime.data ?? null;

  const save = useMutation({
    // RuntimeSettingsUpdate is extra="forbid" server-side; only send the editable keys.
    mutationFn: () => {
      if (!draft) throw new Error("运行设置尚未加载");
      return api.updateRuntimeSettings({
        // 版本跟随缓存里的最新快照而非 localDraft：409 失效重拉后
        // runtime.data.version 已是服务器当前值，localDraft 仍停在编辑时
        // 观察到的旧版本，重发它会永远 409。
        version: runtime.data?.version ?? draft.version,
        queue_mode: draft.queue_mode,
        job_timeout_seconds: draft.job_timeout_seconds,
        job_lease_seconds: draft.job_lease_seconds,
        max_auto_repairs: draft.max_auto_repairs,
        default_concurrency: draft.default_concurrency,
        health_check_interval_seconds: draft.health_check_interval_seconds,
        ui_poll_interval_seconds: draft.ui_poll_interval_seconds,
      });
    },
    onSuccess: (data) => { queryClient.setQueryData(["runtime-settings"], data); setLocalDraft(data); setNotice("运行设置已保存并应用到后续任务"); diagnostics.refetch(); },
    onError: (error) => {
      // 409 = 版本落后（桌面 + web 同开时另一端先保存）。失效缓存触发
      // 重拉，下一次保存带上服务器当前版本，而不是在旧 version 上循环
      // 409 直到窗口聚焦才恢复（与 workflow-studio 草稿保存的 409 处理
      // 同一范式）。
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey: ["runtime-settings"] });
      }
    },
  });
  const update = <K extends keyof RuntimeSettings>(key: K, value: RuntimeSettings[K]) => { setLocalDraft((current) => ({ ...(current ?? draft!), [key]: value })); setNotice(""); };
  // 数字钳制统一走 ClampedNumberInput:输入期间不夹值,失焦才提交区间内结果。

  const runtimeDraftDirty = Boolean(localDraft && runtime.data && (
    localDraft.queue_mode !== runtime.data.queue_mode
    || localDraft.job_timeout_seconds !== runtime.data.job_timeout_seconds
    || localDraft.job_lease_seconds !== runtime.data.job_lease_seconds
    || localDraft.max_auto_repairs !== runtime.data.max_auto_repairs
    || localDraft.default_concurrency !== runtime.data.default_concurrency
    || localDraft.health_check_interval_seconds !== runtime.data.health_check_interval_seconds
    || localDraft.ui_poll_interval_seconds !== runtime.data.ui_poll_interval_seconds
  ));
  const pageDirty = runtimeDraftDirty || providerDirty;
  useEffect(() => {
    if (!pageDirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [pageDirty]);

  return (
    <AppShell>
      <div className="paper-texture" />
      <header className="topbar settings-topbar">
        <div className="topbar-title"><span>SYSTEM / CONTROL ROOM</span><strong>系统设置与运行诊断</strong></div>
        <div className="topbar-actions">
          <Link className="button ghost compact" href="/settings/usage">用量与成本看板</Link>
          <Link className="button ghost compact" href="/"><ArrowLeft size={16} />返回项目</Link>
          <button className="button ink compact" disabled={!draft || save.isPending} onClick={() => save.mutate()}>{save.isPending ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}保存运行设置</button>
        </div>
      </header>
      <main className="settings-page">
        <section className="system-status-strip" aria-label="当前运行状态">
          <div><span>AI 连接</span><strong>{providers.isError ? "读取失败" : providers.data ? `${providers.data.flatMap((provider) => provider.connections).filter((connection) => connection.health_state === "HEALTHY").length} 健康` : "读取中"}</strong></div>
          <div><span>执行器</span><strong>{diagnostics.isError ? "检测失败" : diagnostics.data?.queue.actual_executor ?? "检测中"}</strong></div>
          <div><span>数据库</span><strong>{draft?.database_backend ?? "读取中"}</strong></div>
          <div><span>存储</span><strong>{draft?.storage_root ?? "读取中"}</strong></div>
          <div><span>最近检查</span><strong>{timeLabel(diagnostics.data?.checked_at ?? null)}</strong></div>
        </section>
        <div className="settings-board">
          <section className="settings-primary">
            <ProviderManagement onDirtyChange={setProviderDirty} />
            <article className="control-card">
              <header><div><ServerCog size={18} /><span>WORKER / RUNTIME</span></div><small>非敏感动态设置</small></header>
              {draft ? <div className="runtime-form">
                <label><span>队列模式<small>自动、本地同步或强制 Redis</small></span><select value={draft.queue_mode} onChange={(event) => update("queue_mode", event.target.value as RuntimeSettings["queue_mode"])}><option value="AUTO">自动回退</option><option value="LOCAL">本地同步</option><option value="REDIS">Redis 队列</option></select></label>
                <label><span>任务超时<small>30–3600 秒</small></span><ClampedNumberInput value={draft.job_timeout_seconds} min={30} max={3600} onCommit={(value) => update("job_timeout_seconds", value)} /></label>
                <label><span>任务租约<small>30–3600 秒 · 需不超过任务超时</small></span><ClampedNumberInput value={draft.job_lease_seconds} min={30} max={3600} onCommit={(value) => update("job_lease_seconds", value)} /></label>
                <label><span>默认并发<small>1–8 路</small></span><ClampedNumberInput value={draft.default_concurrency} min={1} max={8} onCommit={(value) => update("default_concurrency", value)} /></label>
                <label><span>视觉修复重试<small>不含文字校对 · 0–10 次</small></span><ClampedNumberInput value={draft.max_auto_repairs} min={0} max={10} onCommit={(value) => update("max_auto_repairs", value)} /></label>
                <label><span>状态检查周期<small>秒</small></span><ClampedNumberInput value={draft.health_check_interval_seconds} min={60} max={3600} onCommit={(value) => update("health_check_interval_seconds", value)} /></label>
                {/* #367：ui_poll_interval_seconds 在 web 端零消费——所有轮询
                    间隔按视图硬编码（lib/task-status.ts 及各 use-*-workspace），
                    只有桌面客户端接入该设置（PollInterval.cs）。标签必须如实
                    标注生效范围，避免“可保存即生效”的误导。 */}
                <label><span>界面轮询周期<small>毫秒 · 仅桌面客户端生效，Web 使用内置固定间隔</small></span><ClampedNumberInput value={draft.ui_poll_interval_seconds} min={1000} max={60000} onCommit={(value) => update("ui_poll_interval_seconds", value)} /></label>
              </div> : <div className="loading-panel"><LoaderCircle className="spin" />读取设置…</div>}
              {notice && <p className="save-success"><CheckCircle2 size={15} />{notice}</p>}{save.isError && <p className="form-error"><CircleAlert size={15} />{save.error.message}</p>}
            </article>
          </section>
          <aside className="settings-secondary">
            <article className="diagnostic-card"><header><div><Database size={17} /><span>分层诊断</span></div><button onClick={() => diagnostics.refetch()} disabled={diagnostics.isFetching}><RefreshCw className={diagnostics.isFetching ? "spin" : ""} size={15} />重新检测</button></header>{diagnostics.isError ? <div className="diagnostic-list"><div className="diagnostic-row failed"><CircleAlert size={16} /><span><strong>诊断读取失败</strong><small>{diagnostics.error instanceof Error ? diagnostics.error.message : "请稍后重试"}</small></span></div></div> : <div className="diagnostic-list">{diagnostics.data?.checks.map((check) => { const Icon = diagnosticIcons[check.status]; return <div key={check.id} className={`diagnostic-row ${check.status.toLowerCase()}`}><Icon size={16} /><span><strong>{check.label}</strong><small>{check.message}</small></span><em>{check.latency_ms ?? "—"} ms</em></div>; }) ?? <div className="loading-panel"><LoaderCircle className="spin" />正在检测…</div>}</div>}</article>
            <article className="storage-card"><header><HardDrive size={17} /><span>本地存储</span></header><dl><div><dt>数据库</dt><dd>{draft?.database_backend ?? "—"}</dd></div><div><dt>生成内容</dt><dd>{draft?.storage_root ?? "—"}</dd></div><div><dt>用户上传</dt><dd>{draft?.upload_root ?? "—"}</dd></div></dl><p>凭据路径、私钥、令牌和 Redis 地址不会通过此接口返回。</p></article>
          </aside>
        </div>
      </main>
    </AppShell>
  );
}
