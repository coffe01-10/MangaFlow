"use client";

import type { PageReadiness } from "@/lib/api";
import {
  Check,
  CircleAlert,
  Cpu,
  MessageSquareText,
  Palette,
  ShieldCheck,
  Users,
} from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

const stageRoutes: Record<string, string> = {
  SOURCE: "source",
  SCRIPT: "script",
  STORYBOARD: "storyboard",
  ASSETS: "assets",
  STYLE: "assets",
  SETTINGS: "settings",
  PROVIDER: "settings",
  WORKER: "settings",
};

function routeForStage(projectId: string, stage: string) {
  return `/projects/${projectId}/${stageRoutes[stage.toUpperCase()] ?? "generate"}`;
}

function ReadinessMark({ ok, children }: { ok: boolean; children: ReactNode }) {
  return <span className={ok ? "readiness-mark ready" : "readiness-mark blocked"}>
    {ok ? <Check size={13} /> : <CircleAlert size={13} />}
    {children}
  </span>;
}

export function ProductionReadiness({
  projectId,
  readiness,
  loading,
  error,
  targetDialogues,
}: {
  projectId: string;
  readiness?: PageReadiness;
  loading: boolean;
  error?: Error | null;
  targetDialogues: string[];
}) {
  return <section className="generation-reference-check production-readiness">
    <header>
      <div><span>PRODUCTION CHECK / 页面生产准备</span><strong>服务器统一判断这页能不能正式生成</strong></div>
      <small>{loading ? "检查中" : readiness?.ready ? "准备完成" : "存在阻塞项"}</small>
    </header>

    {loading ? <p className="reference-check-loading"><Cpu className="spin" size={15} />正在核对剧本、参考资产、风格与执行器…</p> : null}
    {error ? <p className="form-error"><CircleAlert size={14} />准备检查失败：{error.message}</p> : null}

    {readiness ? <>
      {readiness.blockers.length ? <div className="workflow-warning readiness-blockers">
        <CircleAlert size={17} />
        <div><strong>{readiness.blockers.length} 项准备工作未完成</strong><ul>{readiness.blockers.map((blocker) => <li key={`${blocker.code}-${blocker.target_id ?? "page"}`}><span>{blocker.message}</span><Link href={routeForStage(projectId, blocker.stage)}>去处理</Link></li>)}</ul></div>
      </div> : <p className="edit-notice"><Check size={13} />页面生产条件已全部满足，可以确认参考图后生成 1 个 1K 彩色候选。</p>}

      <details className="production-diagnostics" open={!readiness.ready}>
        <summary>{readiness.ready ? "查看原文覆盖、供应商目录与执行器诊断" : "展开查看阻塞诊断"}</summary>
        <div className="reference-check-grid readiness-overview">
        <article>
          <div><strong><ShieldCheck size={15} />内容追溯</strong><span>原文与剧本必须同时覆盖</span></div>
          <ReadinessMark ok={readiness.source_complete}>原文{readiness.source_complete ? "完整" : "未覆盖"}</ReadinessMark>
          <ReadinessMark ok={readiness.script_complete}>剧本{readiness.script_complete ? "完整" : "未完成"}</ReadinessMark>
        </article>
        <article>
          <div><strong><Users size={15} />实际出镜</strong><span>只有 VISIBLE 人物要求参考图</span></div>
          <p>{readiness.visible_characters.length ? readiness.visible_characters.map((item) => `${item.primary_name}${item.outfit_name ? ` · ${item.outfit_name}` : ""}`).join("、") : "本页无实际出镜人物"}</p>
          {readiness.mentioned_characters.length ? <small>仅提及：{readiness.mentioned_characters.map((item) => item.primary_name).join("、")}（不要求人物参考）</small> : null}
          {readiness.props.length ? <small>场景道具：{readiness.props.join("、")}</small> : null}
        </article>
        <article>
          <div><strong><Palette size={15} />正式彩色风格</strong><span>{readiness.style.name ?? "尚未选择风格"}</span></div>
          <ReadinessMark ok={readiness.style.color_mode === "color"}>彩色模式</ReadinessMark>
          <ReadinessMark ok={readiness.style.palette_confirmed}>色板已确认</ReadinessMark>
          <ReadinessMark ok={readiness.style.test_image_approved}>测试图已通过</ReadinessMark>
        </article>
        <article>
          <div><strong><Cpu size={15} />真实执行链</strong><span>所选模型 · 1K · 单候选</span></div>
          <ReadinessMark ok>供应商、协议与图片编辑能力在排队前校验</ReadinessMark>
          <ReadinessMark ok={readiness.worker.can_execute}>{readiness.worker.executor} · {readiness.worker.queue_mode}</ReadinessMark>
          <small>{readiness.estimated_cost_note}</small>
        </article>
        </div>

        <div className="draw-context readiness-dialogue-proof">
          <div><span>LETTERING</span><strong><MessageSquareText size={15} />目标中文</strong><small>模型直接绘制 · 采用前人工校对</small></div>
          <p>{targetDialogues.length ? targetDialogues.map((text, index) => `${index + 1}. ${text}`).join("　") : "本页没有对白或旁白。"}</p>
        </div>
      </details>
    </> : null}
  </section>;
}
