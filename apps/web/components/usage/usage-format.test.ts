import { describe, expect, it } from "vitest";

import type { ModelCallAttempt, UsageSummaryGroup } from "@/lib/api";

import {
  attemptCostMode,
  buildDailyTrend,
  buildUsageCsv,
  estimatedTotalsByCurrency,
  formatCurrencyAmount,
  groupCostMode,
  sumPresent,
} from "./usage-format";

export function makeGroup(overrides: Partial<UsageSummaryGroup> = {}): UsageSummaryGroup {
  return {
    day: "2026-09-01",
    provider: "usage-provider",
    model_id: "imagen-3.0-generate-002",
    channel: "HTTP_API",
    attempt_count: 2,
    succeeded_count: 1,
    failed_count: 1,
    pending_count: 0,
    input_tokens: null,
    output_tokens: null,
    cached_input_tokens: null,
    output_images: null,
    usage_status_counts: { UNKNOWN: 1, PARTIAL: 0, COMPLETE: 1 },
    estimated_costs: [],
    ...overrides,
  };
}

function makeAttempt(overrides: Partial<ModelCallAttempt> = {}): ModelCallAttempt {
  return {
    id: "attempt-1",
    job_id: "job-1",
    project_id: "project-1",
    job_attempt: 1,
    dispatch_no: 1,
    dispatch_request_id: "dispatch-1",
    route_switched: false,
    outcome: "SUCCEEDED",
    channel: "HTTP_API",
    provider: "usage-provider",
    model_id: "imagen-3.0-generate-002",
    catalog_model_id: null,
    connection_id: null,
    selected_key_id: null,
    request_id: "req-1",
    probe_id: null,
    chapter_id: null,
    page_id: null,
    panel_id: null,
    candidate_id: null,
    started_at: "2026-09-01T10:00:00Z",
    finished_at: null,
    duration_ms: 1200,
    usage: null,
    usage_status: null,
    usage_source: null,
    unit_kind: null,
    input_tokens: null,
    output_tokens: null,
    cached_input_tokens: null,
    cache_hit: null,
    output_images: null,
    output_image_dims: null,
    output_asset_ids: null,
    route_reason: null,
    route_score: null,
    error_code: null,
    error_message: null,
    ...overrides,
  };
}

describe("cost mode derivation", () => {
  it("maps attempts with usage to USAGE_ONLY and successful calls without usage to UNAVAILABLE", () => {
    expect(attemptCostMode(makeAttempt({ usage_status: "COMPLETE", input_tokens: 10 }))).toBe("USAGE_ONLY");
    expect(
      attemptCostMode(makeAttempt({ usage: null, usage_status: "UNKNOWN", outcome: "SUCCEEDED" })),
    ).toBe("UNAVAILABLE");
    expect(attemptCostMode(makeAttempt({ usage: { odd_key: 3 }, usage_status: "PARTIAL" }))).toBe("UNKNOWN");
    expect(attemptCostMode(makeAttempt({ usage_source: "OPERATOR_BILLED" }))).toBe("BILLED");
  });

  it("keeps failed and pending attempts out of UNAVAILABLE (no usage exists yet, none omitted)", () => {
    expect(attemptCostMode(makeAttempt({ usage: null, outcome: "FAILED" }))).toBe("UNKNOWN");
    expect(attemptCostMode(makeAttempt({ usage: null, outcome: null }))).toBe("UNKNOWN");
  });

  it("never applies ESTIMATED at attempt granularity (no per-attempt amounts in API)", () => {
    const attempt = makeAttempt({
      usage_status: "COMPLETE",
      input_tokens: 100,
      output_tokens: 20,
    });
    expect(attemptCostMode(attempt)).not.toBe("ESTIMATED");
  });

  it("derives group modes from aggregates", () => {
    expect(groupCostMode(makeGroup({ estimated_costs: [{ currency: "CNY", amount: "1.00" }] }))).toBe("ESTIMATED");
    expect(groupCostMode(makeGroup({ input_tokens: 10 }))).toBe("USAGE_ONLY");
    expect(groupCostMode(makeGroup())).toBe("UNKNOWN");
  });
});

describe("currency handling", () => {
  it("keeps currencies separate instead of converting or merging them", () => {
    const groups = [
      makeGroup({ day: "2026-09-01", estimated_costs: [{ currency: "CNY", amount: "8.00" }] }),
      makeGroup({ day: "2026-09-02", estimated_costs: [{ currency: "USD", amount: "1.50" }] }),
      makeGroup({ day: "2026-09-02", estimated_costs: [{ currency: "CNY", amount: "2.00" }] }),
    ];
    const totals = estimatedTotalsByCurrency(groups);
    expect(totals).toEqual([
      { currency: "CNY", amount: "10.00" },
      { currency: "USD", amount: "1.50" },
    ]);
    expect(totals).toHaveLength(2);
  });

  it("formats per-currency amounts with the right symbol", () => {
    expect(formatCurrencyAmount("12.5", "CNY")).toBe("¥12.50");
    expect(formatCurrencyAmount("12.5", "USD")).toBe("$12.50");
    expect(formatCurrencyAmount("12.5", "XYZ")).toBe("XYZ 12.50");
  });
});

describe("sumPresent", () => {
  it("returns null when nothing was measured instead of a fabricated 0", () => {
    expect(sumPresent([makeGroup({ input_tokens: null })], "input_tokens")).toBeNull();
    expect(sumPresent([], "output_images")).toBeNull();
    expect(sumPresent([makeGroup({ input_tokens: 5 }), makeGroup({ input_tokens: null })], "input_tokens")).toBe(5);
  });
});

describe("buildDailyTrend", () => {
  it("fills calendar gaps with true zeros and keeps unmeasured usage unknown", () => {
    const groups = [
      makeGroup({
        day: "2026-09-01",
        provider: "usage-provider",
        input_tokens: 100,
        output_tokens: 20,
      }),
      makeGroup({
        day: "2026-09-03",
        provider: "usage-provider",
        attempt_count: 1,
        succeeded_count: 1,
        failed_count: 0,
        input_tokens: null,
        output_tokens: null,
      }),
    ];
    const { rows } = buildDailyTrend(groups, "tokens");
    expect(rows.map((row) => row.day)).toEqual(["2026-09-01", "2026-09-02", "2026-09-03"]);
    expect(rows[0].series.get("usage-provider")).toBe(120);
    expect(rows[1].series.get("usage-provider")).toBe(0);
    expect(rows[2].series.get("usage-provider")).toBeNull();
  });

  it("keeps amount series unknown on days without estimates", () => {
    const groups = [
      makeGroup({
        day: "2026-09-01",
        estimated_costs: [{ currency: "CNY", amount: "3.00" }],
      }),
      makeGroup({
        day: "2026-09-02",
        provider: "usage-provider",
        model_id: "imagen-3.0-generate-002",
        channel: "HTTP_API",
      }),
    ];
    const { rows, series } = buildDailyTrend(groups, "amount");
    expect(series).toEqual(["CNY"]);
    expect(rows[0].series.get("CNY")).toBe(3);
    expect(rows[1].series.get("CNY")).toBeNull();
  });
});

describe("buildUsageCsv", () => {
  it("emits one row per currency and never merges currencies into one amount", () => {
    const csv = buildUsageCsv([
      makeGroup({
        day: "2026-09-01",
        provider: "usage-provider",
        model_id: "imagen-3.0-generate-002",
        channel: "HTTP_API",
        attempt_count: 2,
        input_tokens: 10,
        estimated_costs: [
          { currency: "CNY", amount: "1.5" },
          { currency: "USD", amount: "0.25" },
        ],
      }),
    ]);
    expect(csv.startsWith("\uFEFF")).toBe(true);
    const lines = csv.trim().split("\r\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toContain("估算金额（原币种）");
    expect(lines[1]).toContain("CNY,1.5");
    expect(lines[2]).toContain("USD,0.25");
    expect(csv).not.toContain("1.75");
  });

  it("marks groups without estimates instead of writing 0 amounts", () => {
    const csv = buildUsageCsv([makeGroup({ estimated_costs: [] })]);
    expect(csv).toContain("无估算数据");
  });

  it("neutralizes spreadsheet formulas in text cells", () => {
    const csv = buildUsageCsv([
      makeGroup({ provider: "=SUM(A1:A2)", model_id: "+cmd|powershell" }),
      makeGroup({ provider: "@x", model_id: "-y" }),
    ]);
    expect(csv).toContain("'=SUM(A1:A2)");
    expect(csv).toContain("'+cmd|powershell");
    expect(csv).toContain("'@x");
    expect(csv).toContain("'-y");
    // Values are unchanged apart from the leading apostrophe.
    expect(csv).not.toContain(",=SUM");
    // Regular cells (dates, channels) stay untouched.
    expect(csv).toContain("2026-09-01,'=SUM");
    expect(csv).toContain("HTTP_API,");
  });
});
