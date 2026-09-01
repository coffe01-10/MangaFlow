"use client";

import {
  estimatedTotalsByCurrency,
  billedTotalsByCurrency,
  formatCompactQuantity,
  formatCurrencyAmount,
  formatQuantity,
  sumPresent,
} from "./usage-format";
import type { UsageSummary } from "@/lib/api";

interface UsageKpiGridProps {
  summary: UsageSummary;
}

function UnknownValue({ children }: { children?: string }) {
  return <strong className="usage-kpi-unknown">{children ?? "未知"}</strong>;
}

export function UsageKpiGrid({ summary }: UsageKpiGridProps) {
  const groups = summary.groups;
  const attemptCount = groups.reduce((total, group) => total + group.attempt_count, 0);
  const succeededCount = groups.reduce((total, group) => total + group.succeeded_count, 0);
  const failedCount = groups.reduce((total, group) => total + group.failed_count, 0);
  const pendingCount = groups.reduce((total, group) => total + group.pending_count, 0);
  const successRate = attemptCount > 0 ? succeededCount / attemptCount : null;

  const estimated = estimatedTotalsByCurrency(groups);
  const billed = billedTotalsByCurrency(summary.billed);
  const inputTokens = sumPresent(groups, "input_tokens");
  const outputTokens = sumPresent(groups, "output_tokens");
  const cachedTokens = sumPresent(groups, "cached_input_tokens");
  const images = sumPresent(groups, "output_images");
  const tokenUnknown = inputTokens === null && outputTokens === null && cachedTokens === null;
  const cacheRate =
    inputTokens !== null && inputTokens > 0 && cachedTokens !== null
      ? cachedTokens / inputTokens
      : null;

  return (
    <section className="usage-kpi-grid" aria-label="用量概览指标">
      <article className="usage-kpi-card">
        <h3>调用总览</h3>
        <p className="usage-kpi-value">{formatQuantity(attemptCount)} 次</p>
        <dl>
          <div><dt>成功</dt><dd>{formatQuantity(succeededCount)}</dd></div>
          <div><dt>失败</dt><dd>{formatQuantity(failedCount)}</dd></div>
          <div><dt>未决</dt><dd>{formatQuantity(pendingCount)}</dd></div>
          <div><dt>成功率</dt><dd>{successRate === null ? "未知" : `${(successRate * 100).toFixed(1)}%`}</dd></div>
        </dl>
      </article>
      <article className="usage-kpi-card" aria-describedby="usage-estimated-note">
        <h3>估算支出</h3>
        {estimated.length > 0 ? (
          <ul className="usage-kpi-currencies">
            {estimated.map((cost) => (
              <li key={cost.currency}>
                <span className="usage-amount-prefix" aria-label="估算值">≈</span>
                {formatCurrencyAmount(cost.amount, cost.currency)}
              </li>
            ))}
          </ul>
        ) : (
          <p className="usage-kpi-empty">无估算数据</p>
        )}
        <small id="usage-estimated-note">估算值不等于供应商账单</small>
      </article>
      <article className="usage-kpi-card billed" aria-describedby="usage-billed-note">
        <h3>账单支出</h3>
        {billed.length > 0 ? (
          <ul className="usage-kpi-currencies">
            {billed.map((cost) => (
              <li key={cost.currency}>{formatCurrencyAmount(cost.amount, cost.currency)}</li>
            ))}
          </ul>
        ) : (
          <p className="usage-kpi-empty">暂无对账记录</p>
        )}
        <small id="usage-billed-note">账单事实与估算永不相加</small>
      </article>
      <article className="usage-kpi-card">
        <h3>Token 与生图</h3>
        {tokenUnknown && images === null ? (
          <UnknownValue />
        ) : (
          <dl>
            <div>
              <dt>输入 Token</dt>
              <dd>{formatCompactQuantity(inputTokens)}</dd>
            </div>
            <div>
              <dt>输出 Token</dt>
              <dd>{formatCompactQuantity(outputTokens)}</dd>
            </div>
            <div>
              <dt>缓存命中</dt>
              <dd>{cachedTokens === null ? "未知" : `${formatCompactQuantity(cachedTokens)}${cacheRate === null ? "" : ` · ${(cacheRate * 100).toFixed(1)}%`}`}</dd>
            </div>
            <div>
              <dt>生成图片</dt>
              <dd>{images === null ? "未知" : `${formatQuantity(images)} 张`}</dd>
            </div>
          </dl>
        )}
      </article>
    </section>
  );
}
