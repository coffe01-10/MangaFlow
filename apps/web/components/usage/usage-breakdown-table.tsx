"use client";

import type { UsageSummaryGroup } from "@/lib/api";
import {
  COST_MODE_META,
  formatCurrencyAmount,
  formatQuantity,
  groupCostMode,
} from "./usage-format";

interface UsageBreakdownTableProps {
  groups: UsageSummaryGroup[];
}

interface BreakdownRow {
  provider: string;
  model_id: string;
  channel: string;
  groups: UsageSummaryGroup[];
}

function buildRows(groups: UsageSummaryGroup[]): BreakdownRow[] {
  const byKey = new Map<string, BreakdownRow>();
  for (const group of groups) {
    const key = `${group.provider}\u0000${group.model_id}\u0000${group.channel}`;
    let row = byKey.get(key);
    if (!row) {
      row = { provider: group.provider, model_id: group.model_id, channel: group.channel, groups: [] };
      byKey.set(key, row);
    }
    row.groups.push(group);
  }
  return [...byKey.values()].sort(
    (a, b) =>
      a.provider.localeCompare(b.provider) ||
      a.model_id.localeCompare(b.model_id) ||
      a.channel.localeCompare(b.channel),
  );
}

export function UsageBreakdownTable({ groups }: UsageBreakdownTableProps) {
  const rows = buildRows(groups);
  if (rows.length === 0) return null;

  return (
    <section className="usage-panel" aria-label="供应商与模型分解">
      <header>
        <h2>供应商与模型分解</h2>
        <small>聚合到供应商 / 模型 / 通道粒度</small>
      </header>
      <div className="usage-table-scroll">
        <table className="usage-table">
          <thead>
            <tr>
              <th scope="col">供应商 / 模型</th>
              <th scope="col">通道</th>
              <th scope="col">调用</th>
              <th scope="col">成功 / 失败 / 未决</th>
              <th scope="col">输入 / 输出 Token</th>
              <th scope="col">缓存命中</th>
              <th scope="col">图片</th>
              <th scope="col">估算金额（原币种）</th>
              <th scope="col">成本语义</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const attemptCount = row.groups.reduce((total, group) => total + group.attempt_count, 0);
              const succeeded = row.groups.reduce((total, group) => total + group.succeeded_count, 0);
              const failed = row.groups.reduce((total, group) => total + group.failed_count, 0);
              const pending = row.groups.reduce((total, group) => total + group.pending_count, 0);
              const inputTokens = row.groups.some((group) => group.input_tokens !== null)
                ? row.groups.reduce((total, group) => total + (group.input_tokens ?? 0), 0)
                : null;
              const outputTokens = row.groups.some((group) => group.output_tokens !== null)
                ? row.groups.reduce((total, group) => total + (group.output_tokens ?? 0), 0)
                : null;
              const cachedTokens = row.groups.some((group) => group.cached_input_tokens !== null)
                ? row.groups.reduce((total, group) => total + (group.cached_input_tokens ?? 0), 0)
                : null;
              const images = row.groups.some((group) => group.output_images !== null)
                ? row.groups.reduce((total, group) => total + (group.output_images ?? 0), 0)
                : null;
              const currencies = [
                ...new Set(row.groups.flatMap((group) => group.estimated_costs.map((cost) => cost.currency))),
              ].sort();
              const totals = currencies.map((currency) => ({
                currency,
                amount: row.groups
                  .flatMap((group) => group.estimated_costs)
                  .filter((cost) => cost.currency === currency)
                  .reduce((sum, cost) => sum + Number(cost.amount), 0),
              }));
              const mixedModes = new Set(row.groups.map((group) => groupCostMode(group)));
              const mode = mixedModes.size === 1 ? [...mixedModes][0] : null;
              const meta = mode ? COST_MODE_META[mode] : null;
              return (
                <tr key={`${row.provider}-${row.model_id}-${row.channel}`}>
                  <th scope="row">
                    <strong>{row.provider}</strong>
                    <small>{row.model_id}</small>
                  </th>
                  <td>{row.channel === "CLI" ? "CLI" : "HTTP API"}</td>
                  <td>{formatQuantity(attemptCount)}</td>
                  <td>{`${formatQuantity(succeeded)} / ${formatQuantity(failed)} / ${formatQuantity(pending)}`}</td>
                  <td>
                    {inputTokens === null && outputTokens === null
                      ? "未知"
                      : `${formatQuantity(inputTokens)} / ${formatQuantity(outputTokens)}`}
                  </td>
                  <td>{formatQuantity(cachedTokens)}</td>
                  <td>{formatQuantity(images)}</td>
                  <td>
                    {totals.length > 0 ? (
                      totals.map((total) => (
                        <span key={total.currency} className="usage-cost-cell">
                          ≈ {formatCurrencyAmount(total.amount, total.currency, 4)}
                        </span>
                      ))
                    ) : (
                      <span className="usage-cost-none">无估算数据</span>
                    )}
                  </td>
                  <td>
                    {meta ? (
                      <span className={meta.badge} title={meta.hint}>{meta.label}</span>
                    ) : (
                      <span className="usage-badge mixed" title="不同日期/模型状态混合">混合</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="usage-footnote">估算金额按币种分行展示，不同币种永不相加；估算值不等于供应商账单。</p>
    </section>
  );
}
