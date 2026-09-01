import type {
  ModelCallAttempt,
  UsageCurrencyAmount,
  UsageSummaryGroup,
} from "@/lib/api";

export type UsageCostMode =
  | "BILLED"
  | "ESTIMATED"
  | "USAGE_ONLY"
  | "UNKNOWN"
  | "UNAVAILABLE";

export const COST_MODE_META: Record<
  UsageCostMode,
  { label: string; badge: string; hint: string }
> = {
  BILLED: {
    label: "账单",
    badge: "usage-badge billed",
    hint: "来自运营对账导入的账单事实",
  },
  ESTIMATED: {
    label: "预估",
    badge: "usage-badge estimated",
    hint: "按本地价格表推算，估算值不等于供应商账单",
  },
  USAGE_ONLY: {
    label: "仅计量",
    badge: "usage-badge usage-only",
    hint: "已统计到用量；单次金额需以汇总层估算为准（该范围可能未配置单价）",
  },
  UNKNOWN: {
    label: "成本未知",
    badge: "usage-badge unknown",
    hint: "缺少计量或价格数据，未知不等于 0，也不等于免费",
  },
  UNAVAILABLE: {
    label: "无用量返回",
    badge: "usage-badge unavailable",
    hint: "供应商调用成功但未返回 usage 字段",
  },
};

const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: "¥",
  USD: "$",
  EUR: "€",
  GBP: "£",
  JPY: "JP¥",
  HKD: "HK$",
};

export function currencySymbol(currency: string) {
  return CURRENCY_SYMBOLS[currency] ?? `${currency} `;
}

export function formatCurrencyAmount(
  amount: string | number,
  currency: string,
  decimals = 2,
) {
  const value = typeof amount === "string" ? Number(amount) : amount;
  if (!Number.isFinite(value)) return "—";
  const formatted = new Intl.NumberFormat("zh-CN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
  return `${currencySymbol(currency)}${formatted}`;
}

export function formatQuantity(value: number | null | undefined) {
  if (value === null || value === undefined) return "未知";
  return new Intl.NumberFormat("zh-CN").format(value);
}

export function formatCompactQuantity(value: number | null) {
  if (value === null) return "未知";
  return new Intl.NumberFormat("zh-CN", {
    notation: value >= 1_000_000 ? "compact" : "standard",
    maximumFractionDigits: 2,
  }).format(value);
}

export function formatDateTime(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

export function formatDuration(ms: number | null) {
  if (ms === null) return "—";
  return `${new Intl.NumberFormat("zh-CN").format(ms)} ms`;
}

export function outcomeLabel(outcome: string | null) {
  if (outcome === "SUCCEEDED") return "成功";
  if (outcome === "FAILED") return "失败";
  return "未决";
}

function hasQuantities(values: Array<number | null | undefined>) {
  return values.some((value) => value !== null && value !== undefined);
}

/** Single-attempt cost semantics. The API exposes no per-attempt amount or
 * pricing metadata, so ESTIMATED never applies here — amounts exist only at
 * summary granularity, and "no price configured" cannot be inferred. */
export function attemptCostMode(attempt: ModelCallAttempt): UsageCostMode {
  if (attempt.usage_source === "OPERATOR_BILLED") return "BILLED";
  if (
    hasQuantities([
      attempt.input_tokens,
      attempt.output_tokens,
      attempt.cached_input_tokens,
      attempt.output_images,
    ])
  ) {
    return "USAGE_ONLY";
  }
  // Only a successful call can "omit" usage; failed/pending attempts have no
  // usage because no successful provider response ever existed.
  if (attempt.usage === null && attempt.outcome === "SUCCEEDED") {
    return "UNAVAILABLE";
  }
  return "UNKNOWN";
}

/** Group-level cost semantics driven by summary aggregates only. */
export function groupCostMode(group: UsageSummaryGroup): UsageCostMode {
  if (group.estimated_costs.length > 0) return "ESTIMATED";
  if (
    hasQuantities([
      group.input_tokens,
      group.output_tokens,
      group.cached_input_tokens,
      group.output_images,
    ])
  ) {
    return "USAGE_ONLY";
  }
  return "UNKNOWN";
}

/** Sum only present values; returns null when nothing was measured so the UI
 * can render 未知 instead of a fabricated 0. */
export function sumPresent<K extends keyof UsageSummaryGroup>(
  groups: UsageSummaryGroup[],
  key: K,
): number | null {
  let total = 0;
  let present = false;
  for (const group of groups) {
    const value = group[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      total += value;
      present = true;
    }
  }
  return present ? total : null;
}

/** Per-currency estimated totals. Currencies are never merged or converted. */
export function estimatedTotalsByCurrency(
  groups: UsageSummaryGroup[],
): UsageCurrencyAmount[] {
  const totals = new Map<string, number>();
  for (const group of groups) {
    for (const cost of group.estimated_costs) {
      const value = Number(cost.amount);
      if (!Number.isFinite(value)) continue;
      totals.set(cost.currency, (totals.get(cost.currency) ?? 0) + value);
    }
  }
  return [...totals.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, amount]) => ({ currency, amount: amount.toFixed(2) }));
}

export function billedTotalsByCurrency(
  billed: Array<{ currency: string; billed_amount: string }>,
): UsageCurrencyAmount[] {
  const totals = new Map<string, number>();
  for (const item of billed) {
    const value = Number(item.billed_amount);
    if (!Number.isFinite(value)) continue;
    totals.set(item.currency, (totals.get(item.currency) ?? 0) + value);
  }
  return [...totals.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([currency, amount]) => ({ currency, amount: amount.toFixed(2) }));
}

export interface UsageTrendDay {
  day: string;
  /** Per-series values; null marks "calls happened but no measurable usage". */
  series: Map<string, number | null>;
  callCount: number;
}

export type UsageTrendMetric = "amount" | "tokens" | "images";

export function trendSeriesKeys(
  groups: UsageSummaryGroup[],
  metric: UsageTrendMetric,
): string[] {
  if (metric === "amount") {
    return [
      ...new Set(
        groups.flatMap((group) => group.estimated_costs.map((cost) => cost.currency)),
      ),
    ].sort();
  }
  if (metric === "tokens") {
    return [...new Set(groups.map((group) => group.provider))].sort();
  }
  return ["images"];
}

/** Build per-day buckets. Days with no attempts at all are filled with true
 * zeros (no calls occurred); a day that has attempts but no measurable usage
 * keeps null so the UI never renders unknown usage as 0. */
export function buildDailyTrend(
  groups: UsageSummaryGroup[],
  metric: UsageTrendMetric,
): { days: string[]; series: string[]; rows: UsageTrendDay[] } {
  const byDay = new Map<string, UsageTrendDay>();
  const ensure = (day: string) => {
    let row = byDay.get(day);
    if (!row) {
      row = { day, series: new Map(), callCount: 0 };
      byDay.set(day, row);
    }
    return row;
  };

  for (const group of groups) {
    const row = ensure(group.day);
    row.callCount += group.attempt_count;
    if (metric === "amount") {
      for (const cost of group.estimated_costs) {
        const current = row.series.get(cost.currency);
        row.series.set(
          cost.currency,
          (current ?? 0) + Number(cost.amount),
        );
      }
    } else if (metric === "tokens") {
      const current = row.series.get(group.provider);
      const measured =
        (group.input_tokens ?? 0) + (group.output_tokens ?? 0);
      const hasTokens =
        group.input_tokens !== null || group.output_tokens !== null;
      if (!hasTokens && current === undefined) {
        row.series.set(group.provider, null);
      } else if (hasTokens) {
        row.series.set(group.provider, (current ?? 0) + measured);
      }
    } else {
      const current = row.series.get("images");
      if (group.output_images === null) {
        if (current === undefined) row.series.set("images", null);
      } else {
        row.series.set("images", (current ?? 0) + group.output_images);
      }
    }
  }

  const days = [...byDay.keys()].sort();
  // Fill calendar gaps with true zeros: no attempts recorded those days.
  if (days.length >= 2) {
    const cursor = new Date(`${days[0]}T00:00:00Z`);
    const end = new Date(`${days[days.length - 1]}T00:00:00Z`);
    while (cursor < end) {
      const iso = cursor.toISOString().slice(0, 10);
      ensure(iso);
      cursor.setUTCDate(cursor.getUTCDate() + 1);
    }
  }
  const series = trendSeriesKeys(groups, metric);
  const rows = [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day));
  for (const row of rows) {
    for (const key of series) {
      if (row.series.has(key)) continue;
      if (row.callCount === 0) {
        // Calendar gap: no attempts at all, so zero is a fact.
        row.series.set(key, 0);
      } else if (metric === "amount") {
        // Calls happened but no estimate was recorded for this currency that
        // day — unpriced models are indistinguishable, so keep it unknown.
        row.series.set(key, null);
      } else {
        // Provider absent that day made zero calls → zero tokens/images.
        row.series.set(key, 0);
      }
    }
  }
  return { days: rows.map((row) => row.day), series, rows };
}

/** Neutralize spreadsheet formulas: cells starting with = + - @ (or tab) get a
 * leading apostrophe so opening the CSV cannot execute a formula. */
function csvCell(value: string | number | null) {
  const text = value === null ? "" : String(value);
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

/** CSV of summary groups. One physical row per currency so amounts from
 * different currencies are never added together on a single line. */
export function buildUsageCsv(groups: UsageSummaryGroup[]): string {
  const header = [
    "日期",
    "供应商",
    "模型ID",
    "通道",
    "调用次数",
    "成功",
    "失败",
    "未决",
    "输入Token",
    "输出Token",
    "缓存命中Token",
    "输出图片张数",
    "计量状态分布",
    "估算币种",
    "估算金额（原币种）",
  ];
  const lines: Array<Array<string | number | null>> = [];
  for (const group of groups) {
    const base: Array<string | number | null> = [
      group.day,
      group.provider,
      group.model_id,
      group.channel,
      group.attempt_count,
      group.succeeded_count,
      group.failed_count,
      group.pending_count,
      group.input_tokens,
      group.output_tokens,
      group.cached_input_tokens,
      group.output_images,
      Object.entries(group.usage_status_counts)
        .map(([status, count]) => `${status}:${count}`)
        .join(" ") || null,
    ];
    const costs = group.estimated_costs.length > 0
      ? group.estimated_costs
      : [{ currency: null, amount: null }];
    for (const cost of costs) {
      lines.push([
        ...base,
        cost.currency ?? "无估算数据",
        cost.amount ?? "",
      ]);
    }
  }
  const body = [header, ...lines]
    .map((row) => row.map(csvCell).join(","))
    .join("\r\n");
  return `\uFEFF${body}\r\n`;
}
