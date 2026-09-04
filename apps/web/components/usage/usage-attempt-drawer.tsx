"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import type { ModelCallAttempt } from "@/lib/api";
import {
  attemptCostMode,
  COST_MODE_META,
  formatDateTime,
  formatDuration,
  formatQuantity,
  outcomeLabel,
  usageStatusLabel,
} from "./usage-format";

interface UsageAttemptDrawerProps {
  attempt: ModelCallAttempt;
  onClose: () => void;
}

export function UsageAttemptDrawer({ attempt, onClose }: UsageAttemptDrawerProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      // aria-modal dialogs must keep Tab inside (same trap the scene modal uses).
      if (event.key === "Tab" && drawerRef.current) {
        const focusables = Array.from(drawerRef.current.querySelectorAll<HTMLElement>("button:not([disabled]), a[href]"));
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !drawerRef.current.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || !drawerRef.current.contains(active))) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previousFocusRef.current?.focus();
    };
  }, [onClose]);

  const mode = attemptCostMode(attempt);
  const meta = COST_MODE_META[mode];
  const dims = attempt.output_image_dims ?? [];

  return (
    <div className="usage-drawer-backdrop" onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside
        ref={drawerRef}
        className="usage-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="usage-drawer-title"
      >
        <header className="usage-drawer-header">
          <div>
            <span>CALL ATTEMPT</span>
            <h2 id="usage-drawer-title">调用尝试详情</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            className="button ghost compact"
            onClick={onClose}
            aria-label="关闭调用尝试详情"
          >
            <X size={15} />关闭 (Esc)
          </button>
        </header>
        <div className="usage-drawer-body">
          <section className="usage-drawer-card">
            <h3>计费与用量核算</h3>
            <dl>
              <div><dt>成本语义</dt><dd><span className={meta.badge} title={meta.hint}>{meta.label}</span></dd></div>
              <div><dt>计量状态</dt><dd>{usageStatusLabel(attempt.usage_status)}{attempt.usage_source ? ` · ${attempt.usage_source}` : ""}</dd></div>
              <div><dt>计量单位</dt><dd>{attempt.unit_kind ?? "UNKNOWN"}</dd></div>
              <div><dt>单次金额</dt><dd className="usage-cost-none">单次尝试金额不在账本读取接口返回，估算金额仅在汇总层按币种展示</dd></div>
            </dl>
          </section>
          <section className="usage-drawer-card">
            <h3>路由与通道信息</h3>
            <dl>
              <div><dt>供应商</dt><dd>{attempt.provider}</dd></div>
              <div><dt>物理模型 ID</dt><dd className="usage-mono">{attempt.model_id}</dd></div>
              <div><dt>通道</dt><dd>{attempt.channel === "CLI" ? "CLI" : "HTTP API"}</dd></div>
              <div><dt>上游 Request ID</dt><dd className="usage-mono">{attempt.request_id ?? "未返回"}</dd></div>
              <div><dt>派发请求 ID</dt><dd className="usage-mono">{attempt.dispatch_request_id ?? "—"}</dd></div>
              <div><dt>尝试序号</dt><dd>{`调度尝试 ${attempt.job_attempt} · 第 ${attempt.dispatch_no} 次派发`}{attempt.route_switched ? <span className="usage-route-flag" title="该次派发切换了路由/密钥">换路</span> : ""}</dd></div>
            </dl>
          </section>
          <section className="usage-drawer-card">
            <h3>Token 与数据规格</h3>
            <dl>
              <div><dt>输入 Token</dt><dd>{formatQuantity(attempt.input_tokens)}</dd></div>
              <div><dt>输出 Token</dt><dd>{formatQuantity(attempt.output_tokens)}</dd></div>
              <div><dt>缓存命中 Token</dt><dd>{formatQuantity(attempt.cached_input_tokens)}{attempt.cache_hit === null ? "" : attempt.cache_hit ? "（命中）" : "（未命中）"}</dd></div>
              <div><dt>输出图片</dt><dd>{attempt.output_images === null ? "未知" : `${formatQuantity(attempt.output_images)} 张`}</dd></div>
              {dims.length > 0 ? (
                <div><dt>图片规格</dt><dd>{dims.map((dim) => `${dim.width ?? "?"}×${dim.height ?? "?"}`).join(" · ")}</dd></div>
              ) : null}
              <div><dt>耗时</dt><dd>{formatDuration(attempt.duration_ms)}</dd></div>
              <div><dt>触发时间</dt><dd>{formatDateTime(attempt.started_at)} · 结束 {formatDateTime(attempt.finished_at)}</dd></div>
              <div><dt>结果</dt><dd>{outcomeLabel(attempt.outcome)}{attempt.error_code ? ` · ${attempt.error_code}` : ""}</dd></div>
            </dl>
            {attempt.error_message ? <p className="usage-drawer-error">{attempt.error_message}</p> : null}
          </section>
        </div>
        <footer className="usage-drawer-footer">
          <small>数据来自模型调用账本（已脱敏）· 未知 ≠ 0，CLI 通道费用未知 ≠ 免费</small>
        </footer>
      </aside>
    </div>
  );
}
