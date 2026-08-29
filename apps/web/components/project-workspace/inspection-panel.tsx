"use client";

import { CircleAlert, LoaderCircle, Sparkles } from "lucide-react";

import { inspectionBubbleDiffs, inspectionSummary, recommendedRepairType } from "./display";
import { inspectionLabels, repairTypeLabels } from "./labels";
import type { GenerationWorkspace } from "./use-generation-workspace";

export function InspectionPanel({
  latestInspections,
  reviewJob,
  inspectCandidate,
  repairCandidate,
  upscaleCandidate,
  onClose,
}: {
  latestInspections: GenerationWorkspace["latestInspections"];
  reviewJob: GenerationWorkspace["reviewJob"];
  inspectCandidate: GenerationWorkspace["inspectCandidate"];
  repairCandidate: GenerationWorkspace["repairCandidate"];
  upscaleCandidate: GenerationWorkspace["upscaleCandidate"];
  onClose: () => void;
}) {
  return <section className="inspection-panel">
    <header><div><span>AI QUALITY CHECK</span><strong>候选视觉检查</strong><small>检查说话人归属、角色、服装、道具和连续性；文字由人工校对。</small></div><button onClick={onClose}>关闭</button></header>
    {!latestInspections.length ? <div className="inspection-wait"><LoaderCircle className={reviewJob && !["COMPLETED", "FAILED"].includes(reviewJob.status) ? "spin" : ""} size={18} /><span>{reviewJob ? `检查任务 ${reviewJob.status} · ${reviewJob.progress}%` : "正在读取检查结果"}</span></div> : <div className="inspection-results">{latestInspections.map((inspection) => {
      const passed = ["PASS", "ACCEPTABLE", "MATCH"].includes(inspection.outcome);
      const repairType = recommendedRepairType(inspection.category);
      const bubbleDiffs = inspectionBubbleDiffs(inspection.details);
      return <article className={passed ? "passed" : "failed"} key={inspection.id}>
        <div><span>{inspectionLabels[inspection.category] ?? inspection.category}</span><strong>{inspection.outcome}</strong><em>{inspection.score === null ? "—" : `${Math.round(inspection.score * 100)}%`}</em></div>
        <p>{inspectionSummary(inspection.details)}</p>
        {bubbleDiffs.length > 0 && <div className="bubble-diff-list">{bubbleDiffs.map((diff, index) => <div key={`${inspection.id}:${index}`}><strong>气泡 {String(diff.balloon_index ?? index + 1).padStart(2, "0")}</strong><span>目标：{String(diff.target_text ?? "")}</span><span>识别：{String(diff.recognized_text ?? "")}</span><em>{typeof diff.similarity === "number" ? `${Math.round(diff.similarity * 100)}%` : "—"}</em></div>)}</div>}
        {!passed && inspection.category !== "TEXT" && <button disabled={repairCandidate.isPending} onClick={() => repairCandidate.mutate(inspection)}><Sparkles size={13} />修复{repairTypeLabels[repairType]}</button>}
        {!passed && inspection.category === "TEXT" && <span className="manual-review-hint">请人工校对；确认后可直接采用</span>}
      </article>;
    })}</div>}
    {(inspectCandidate.isError || repairCandidate.isError || upscaleCandidate.isError) && <p className="form-error"><CircleAlert size={14} />{(inspectCandidate.error ?? repairCandidate.error ?? upscaleCandidate.error)?.message}</p>}
  </section>;
}
