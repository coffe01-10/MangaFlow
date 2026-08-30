"use client";

import {
  Archive,
  CircleAlert,
  History,
  ListTodo,
  Maximize2,
  RotateCcw,
  Trash2,
} from "lucide-react";

import { publicUrl, type Job } from "@/lib/api";
import { isActiveTaskStatus } from "@/lib/task-status";

import { jobLabels } from "./labels";
import type { JobsWorkspace } from "./use-jobs-workspace";


function costEstimateLabel(job: Job) {
  const status = job.estimated_cost_status ?? (job.estimated_cost === null ? "UNAVAILABLE" : "AVAILABLE");
  if (job.estimated_cost === null) return status === "PARTIAL" ? "部分费用暂不可估算" : "费用暂不可估算";
  const formatted = job.estimated_cost_currency
    ? new Intl.NumberFormat("zh-CN", {
        style: "currency",
        currency: job.estimated_cost_currency,
        currencyDisplay: "code",
        minimumFractionDigits: 2,
        maximumFractionDigits: 6,
      }).format(job.estimated_cost)
    : job.estimated_cost.toFixed(6);
  return `${status === "PARTIAL" ? "部分估算" : "估算"} ${formatted}`;
}

export function JobsSection({
  jobs,
  workspace,
  modelOptions,
  openPreview,
}: {
  jobs: JobsWorkspace["jobs"];
  workspace: JobsWorkspace;
  modelOptions: { alias: string; name: string }[];
  openPreview: (url: string, label: string) => void;
}) {
  const {
    showArchivedJobs,
    setShowArchivedJobs,
    jobNotice,
    setJobNotice,
    selectedJobIds,
    setSelectedJobIds,
    requestCancel,
    requestRetry,
    isCancelPending,
    isRetryPending,
    archiveJob,
    restoreJob,
    archiveCompletedJobs,
    bulkArchiveJobs,
    deleteJob,
    activeJobs,
    failedJobs,
    completedJobGroups,
  } = workspace;

  function renderJob(job: Job, showProgress: boolean) {
    const terminal = ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(job.status);
    const resultUrl = publicUrl(job.result?.content_url ?? null);
    const showResult = () => {
      if (resultUrl && job.result) openPreview(resultUrl, job.result.label);
    };
    return <article className={`job-row status-${job.status.toLowerCase()} ${resultUrl ? "has-result" : ""}`} key={job.id} onClick={resultUrl ? showResult : undefined}>
      {!showArchivedJobs && terminal && <label className="job-select" onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`选择${jobLabels[job.job_type] ?? job.job_type}`} checked={selectedJobIds.includes(job.id)} onChange={(event) => setSelectedJobIds((values) => event.target.checked ? [...values, job.id] : values.filter((id) => id !== job.id))} /></label>}
      <div className="job-type"><span>{jobLabels[job.job_type] ?? job.job_type}</span><strong>{job.status}</strong></div>
      {showProgress && <div className="job-progress"><i><b style={{ width: `${job.progress}%` }} /></i><span>{job.progress}% · 尝试 {job.attempt_count}/{job.max_attempts}</span></div>}
      <div className="job-detail"><span>{job.workflow_node_id ? `节点 ${job.workflow_node_id}` : job.model_alias ? modelOptions.find((item) => item.alias === job.model_alias)?.name ?? job.model_alias : "系统任务"}</span><small>{job.duration_ms === null ? "尚未完成" : `耗时 ${(job.duration_ms / 1000).toFixed(1)} 秒`} · <span title={job.estimated_cost_note}>{costEstimateLabel(job)}</span>{job.estimated_cost_note ? ` · ${job.estimated_cost_note}` : " · 估算值不等于供应商账单"}</small>{job.error_message && <em>{job.error_code ? `${job.error_code} · ` : ""}{job.error_message}</em>}</div>
      <div className="job-actions" onClick={(event) => event.stopPropagation()}>{resultUrl && <button className="job-result-action" onClick={showResult}><Maximize2 size={12} />查看结果</button>}{!showArchivedJobs && isActiveTaskStatus(job.status) && <button disabled={isCancelPending(job.id)} onClick={() => requestCancel(job.id)}>取消</button>}{!showArchivedJobs && job.status === "FAILED" && <button disabled={isRetryPending(job.id)} onClick={() => requestRetry(job.id)}><RotateCcw size={12} />重试</button>}{!showArchivedJobs && terminal && <button onClick={() => archiveJob.mutate(job.id)}><Archive size={12} />归档</button>}{showArchivedJobs && <button onClick={() => restoreJob.mutate(job.id)}><RotateCcw size={12} />恢复</button>}{showArchivedJobs && ["FAILED", "CANCELLED"].includes(job.status) && <button className="danger-action" onClick={() => { if (window.confirm("仅无候选、生成记录、工作流或任务依赖的失败任务可以彻底删除。继续吗？")) deleteJob.mutate(job.id); }}><Trash2 size={12} />彻底删除</button>}</div>
    </article>;
  }

  return (
    <>
      <header className="canvas-header"><div><span>JOBS / 任务中心</span><h2>每个生成任务都能看懂、取消和重试</h2></div><small>{jobs.data?.length ?? 0} 个任务</small></header>
      <div className="job-toolbar"><div><button className={!showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(false); setJobNotice(""); }}><ListTodo size={13} />近期任务</button><button className={showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(true); setJobNotice(""); }}><History size={13} />历史记录</button></div>{!showArchivedJobs && <div className="job-bulk-actions"><button disabled={!selectedJobIds.length || bulkArchiveJobs.isPending} onClick={() => bulkArchiveJobs.mutate()}><Archive size={13} />归档已选（{selectedJobIds.length}）</button><button disabled={archiveCompletedJobs.isPending} onClick={() => { if (window.confirm("将所有已完成、失败和已取消任务移入历史记录？生成候选与溯源信息不会删除。")) archiveCompletedJobs.mutate(); }}><Archive size={13} />归档全部终态</button></div>}</div>
      {jobNotice && <p className="job-notice"><CircleAlert size={13} />{jobNotice}</p>}
      <div className="job-sections">
        {activeJobs.length > 0 && <section><header><strong>正在运行</strong><small>{activeJobs.length} 条</small></header><div className="job-list">{activeJobs.map((job) => renderJob(job, true))}</div></section>}
        {failedJobs.length > 0 && <details open className="job-group failed"><summary><span>失败任务</span><strong>{failedJobs.length} 条 · 展开查看错误与重试</strong></summary><div className="job-list">{failedJobs.map((job) => renderJob(job, false))}</div></details>}
        {completedJobGroups.map(([date, groupedJobs]) => <details className="job-group" key={date}><summary><span>{date}</span><strong>{groupedJobs.length} 条已结束任务</strong></summary><div className="job-list">{groupedJobs.map((job) => renderJob(job, false))}</div></details>)}
      </div>
      {!jobs.data?.length && <div className="asset-empty tall">{showArchivedJobs ? <History size={28} /> : <ListTodo size={28} />}<strong>{showArchivedJobs ? "还没有历史任务" : "当前没有任务"}</strong><p>{showArchivedJobs ? "归档后的已结束任务会保留在这里，可随时恢复。" : "剧本解析、页面生成、检查和修复都会列在这里。"}</p></div>}
    </>
  );
}
