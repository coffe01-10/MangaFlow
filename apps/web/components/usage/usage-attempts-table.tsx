"use client";

import { Fragment } from "react";
import type { ModelCallAttempt } from "@/lib/api";
import {
  attemptCostMode,
  COST_MODE_META,
  formatDateTime,
  formatQuantity,
  outcomeLabel,
} from "./usage-format";

interface UsageAttemptsTableProps {
  items: ModelCallAttempt[];
  loadedCount: number;
  hasMore: boolean;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  onLoadMore: () => void;
  onOpenAttempt: (attempt: ModelCallAttempt) => void;
}

export function UsageAttemptsTable({
  items,
  loadedCount,
  hasMore,
  loading,
  loadingMore,
  error,
  onLoadMore,
  onOpenAttempt,
}: UsageAttemptsTableProps) {
  return (
    <section className="usage-panel" aria-label="调用明细">
      <header>
        <h2>调用明细</h2>
        <small>已加载 {formatQuantity(loadedCount)} 条 · 按开始时间倒序 keyset 分页</small>
      </header>
      {error ? (
        <div className="usage-state error" role="alert">
          <p>调用明细加载失败：{error}</p>
        </div>
      ) : null}
      <div className="usage-table-scroll">
        <table className="usage-table attempts">
          <thead>
            <tr>
              <th scope="col">开始时间</th>
              <th scope="col">通道</th>
              <th scope="col">供应商 / 模型</th>
              <th scope="col">派发</th>
              <th scope="col">状态</th>
              <th scope="col">输入 / 输出 Token</th>
              <th scope="col">图片</th>
              <th scope="col">计量</th>
              <th scope="col" aria-label="操作" />
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && !error ? (
              <tr className="usage-attempts-empty">
                <td colSpan={9}>该范围暂无调用尝试记录</td>
              </tr>
            ) : null}
            {items.map((attempt) => {
              const mode = attemptCostMode(attempt);
              const meta = COST_MODE_META[mode];
              return (
                <tr
                  key={attempt.id}
                  tabIndex={0}
                  data-attempt-id={attempt.id}
                  aria-label={`查看 ${attempt.provider} ${attempt.model_id} 的调用尝试详情`}
                  onClick={() => onOpenAttempt(attempt)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      onOpenAttempt(attempt);
                    }
                  }}
                >
                  <th scope="row">{formatDateTime(attempt.started_at)}</th>
                  <td>{attempt.channel === "CLI" ? "CLI" : "HTTP API"}</td>
                  <td>
                    <strong>{attempt.provider}</strong>
                    <small>{attempt.model_id}</small>
                  </td>
                  <td>
                    {`第 ${attempt.dispatch_no} 次`}
                    {attempt.route_switched ? <span className="usage-route-flag" title="该次派发切换了路由/密钥">换路</span> : null}
                  </td>
                  <td>
                    <span className={`usage-outcome ${attempt.outcome === "SUCCEEDED" ? "ok" : attempt.outcome === "FAILED" ? "fail" : "pending"}`}>
                      {outcomeLabel(attempt.outcome)}
                    </span>
                  </td>
                  <td>
                    {attempt.input_tokens === null && attempt.output_tokens === null
                      ? "未知"
                      : `${formatQuantity(attempt.input_tokens)} / ${formatQuantity(attempt.output_tokens)}`}
                  </td>
                  <td>{attempt.output_images === null ? "未知" : formatQuantity(attempt.output_images)}</td>
                  <td>
                    <span className={meta.badge} title={meta.hint}>{meta.label}</span>
                  </td>
                  <td>
                    <button
                      type="button"
                      className="button ghost compact"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenAttempt(attempt);
                      }}
                    >
                      详情
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <footer className="usage-attempts-footer">
        {items.length === 0 && !loading ? null : (
          <Fragment>
            {hasMore ? (
              <button type="button" className="button ghost compact" disabled={loadingMore} onClick={onLoadMore}>
                {loadingMore ? "加载中…" : "加载更多"}
              </button>
            ) : (
              <small>已加载全部匹配记录</small>
            )}
          </Fragment>
        )}
      </footer>
    </section>
  );
}
