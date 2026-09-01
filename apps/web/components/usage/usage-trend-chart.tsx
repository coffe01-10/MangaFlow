"use client";

import { useState } from "react";
import type { UsageSummaryGroup } from "@/lib/api";
import {
  buildDailyTrend,
  currencySymbol,
  formatCurrencyAmount,
  formatQuantity,
  type UsageTrendMetric,
} from "./usage-format";

const METRIC_LABELS: Record<UsageTrendMetric, string> = {
  amount: "按估算金额",
  tokens: "按 Token 数量",
  images: "按生图张数",
};

const SERIES_COLORS = ["#d34a2f", "#3f6d4e", "#3f5e8c", "#a8842c", "#6d4a7e"];

interface UsageTrendChartProps {
  groups: UsageSummaryGroup[];
}

function seriesLabel(metric: UsageTrendMetric, key: string) {
  return metric === "amount" ? `${currencySymbol(key).trim()} ${key}` : key;
}

function formatSeriesValue(metric: UsageTrendMetric, key: string, value: number) {
  if (metric === "amount") return formatCurrencyAmount(value, key);
  if (metric === "images") return `${formatQuantity(value)} 张`;
  return `${formatQuantity(value)} Tokens`;
}

export function UsageTrendChart({ groups }: UsageTrendChartProps) {
  const [metric, setMetric] = useState<UsageTrendMetric>("amount");
  const { days, series, rows } = buildDailyTrend(groups, metric);
  const maxBySeries = new Map<string, number>();
  for (const key of series) {
    const values = rows
      .map((row) => row.series.get(key))
      .filter((value): value is number => value !== null && value !== undefined);
    maxBySeries.set(key, Math.max(1, ...values));
  }
  const chartHeight = 120;
  const daySlot = 100 / Math.max(days.length, 1);
  const segmentSlot = series.length > 0 ? daySlot / series.length : daySlot;

  return (
    <section className="usage-panel usage-trend" aria-label="用量趋势">
      <header>
        <h2>费用与调用趋势</h2>
        <div className="usage-trend-toggle" role="group" aria-label="切换趋势度量">
          {(Object.keys(METRIC_LABELS) as UsageTrendMetric[]).map((key) => (
            <button
              key={key}
              type="button"
              aria-pressed={metric === key}
              className={metric === key ? "active" : ""}
              onClick={() => setMetric(key)}
            >
              {METRIC_LABELS[key]}
            </button>
          ))}
        </div>
      </header>
      {series.length === 0 ? (
        <p className="usage-trend-empty">
          {metric === "amount" ? "所选范围内无估算金额记录" : "所选范围内无用量记录"}
        </p>
      ) : (
        <>
          <div className="usage-trend-legend" aria-label="图例">
            {series.map((key, index) => (
              <span key={key}>
                <i style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
                {seriesLabel(metric, key)}
              </span>
            ))}
          </div>
          <svg
            className="usage-trend-chart"
            viewBox={`0 0 100 ${chartHeight}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`${METRIC_LABELS[metric]}的按日趋势图`}
          >
            {rows.map((row, dayIndex) => {
              const hasKnownValue = [...row.series.values()].some(
                (value) => value !== null && value !== undefined,
              );
              return (
                <g key={row.day}>
                  {series.map((key, seriesIndex) => {
                    const value = row.series.get(key);
                    if (value === null || value === undefined || value <= 0) return null;
                    const max = maxBySeries.get(key) ?? 1;
                    const barHeight = Math.max(2, (value / max) * (chartHeight - 8));
                    return (
                      <rect
                        key={key}
                        x={dayIndex * daySlot + seriesIndex * segmentSlot + segmentSlot * 0.15}
                        y={chartHeight - barHeight}
                        width={segmentSlot * 0.7}
                        height={barHeight}
                        fill={SERIES_COLORS[seriesIndex % SERIES_COLORS.length]}
                      >
                        <title>{`${row.day} · ${seriesLabel(metric, key)} · ${formatSeriesValue(metric, key, value)}`}</title>
                      </rect>
                    );
                  })}
                  {!hasKnownValue && row.callCount > 0 ? (
                    <rect
                      x={dayIndex * daySlot + daySlot * 0.38}
                      y={chartHeight - 8}
                      width={daySlot * 0.24}
                      height={8}
                      fill="var(--line-dark)"
                    >
                      <title>{`${row.day} · 有 ${formatQuantity(row.callCount)} 次调用，但用量未知`}</title>
                    </rect>
                  ) : null}
                </g>
              );
            })}
          </svg>
          <div className="usage-trend-axis" aria-hidden="true">
            {rows.map((row, index) => (
              <span key={row.day} className={index % 3 === 0 ? "" : "usage-axis-skip"}>
                {row.day.slice(5)}
              </span>
            ))}
          </div>
          <details className="usage-trend-table">
            <summary>图表数据表（无障碍）</summary>
            <table>
              <thead>
                <tr>
                  <th scope="col">日期</th>
                  {series.map((key) => (
                    <th scope="col" key={key}>{seriesLabel(metric, key)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.day}>
                    <th scope="row">{row.day}</th>
                    {series.map((key) => {
                      const value = row.series.get(key);
                      return (
                        <td key={key}>
                          {value === null || value === undefined
                            ? row.callCount === 0 ? "0（无调用）" : "未知"
                            : formatSeriesValue(metric, key, value)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
          {metric === "amount" ? (
            <p className="usage-footnote">金额按币种独立分组展示，未做任何汇率换算，不同币种不相加；“未知”表示当日有调用但无估算记录。</p>
          ) : (
            <p className="usage-footnote">“未知”表示当日该系列有调用但未返回可用计量，未知不等于 0。</p>
          )}
        </>
      )}
    </section>
  );
}
