"use client";

import { useMemo, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { api, type UsageChannel, type UsageFilters, type ModelCallAttempt } from "@/lib/api";
import { UsageAttemptDrawer } from "./usage-attempt-drawer";
import { UsageAttemptsTable } from "./usage-attempts-table";
import { UsageBreakdownTable } from "./usage-breakdown-table";
import { UsageKpiGrid } from "./usage-kpi-grid";
import { UsageTrendChart } from "./usage-trend-chart";
import { buildUsageCsv } from "./usage-format";

type RangePreset = "7d" | "30d" | "month" | "custom";

const RANGE_LABELS: Record<Exclude<RangePreset, "custom">, string> = {
  "7d": "近 7 天",
  "30d": "近 30 天",
  month: "本月",
};

function toIsoLocalMidnight(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).toISOString();
}

export function UsageDashboard() {
  const [preset, setPreset] = useState<RangePreset>("30d");
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [projectId, setProjectId] = useState("");
  const [provider, setProvider] = useState("");
  const [modelId, setModelId] = useState("");
  const [channel, setChannel] = useState<UsageChannel | "">("");
  const [selectedAttempt, setSelectedAttempt] = useState<ModelCallAttempt | null>(null);

  const sinceUntil = useMemo((): { since?: string; until?: string } => {
    const now = new Date();
    if (preset === "7d") {
      return { since: new Date(now.getTime() - 7 * 86_400_000).toISOString() };
    }
    if (preset === "30d") {
      return { since: new Date(now.getTime() - 30 * 86_400_000).toISOString() };
    }
    if (preset === "month") {
      return { since: toIsoLocalMidnight(new Date(now.getFullYear(), now.getMonth(), 1)) };
    }
    if (!customFrom) return {};
    const untilDate = customTo || customFrom;
    const until = new Date(untilDate);
    until.setDate(until.getDate() + 1);
    return { since: toIsoLocalMidnight(new Date(customFrom)), until: toIsoLocalMidnight(until) };
  }, [preset, customFrom, customTo]);

  const summaryFilters: UsageFilters = useMemo(
    () => ({
      project_id: projectId || undefined,
      provider: provider || undefined,
      model_id: modelId || undefined,
      ...sinceUntil,
    }),
    [projectId, provider, modelId, sinceUntil],
  );
  const attemptsFilters: UsageFilters = useMemo(
    () => ({ ...summaryFilters, channel: channel || undefined }),
    [summaryFilters, channel],
  );
  const filterKey = JSON.stringify({ summaryFilters, channel });

  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  // Facet source: unfiltered summary keeps provider/model options stable while
  // other filters change. Acceptable for this single-user local product.
  const facets = useQuery({ queryKey: ["usage-facets"], queryFn: () => api.usageSummary({}) });
  const summary = useQuery({
    queryKey: ["usage-summary", filterKey],
    queryFn: () => api.usageSummary(summaryFilters),
  });
  const attempts = useInfiniteQuery({
    queryKey: ["usage-attempts", filterKey],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      api.usageAttempts(attemptsFilters, pageParam, 50),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const providerOptions = useMemo(() => {
    const facetGroups = facets.data?.groups ?? [];
    return [...new Set(facetGroups.map((group) => group.provider))].sort();
  }, [facets.data]);
  const modelOptions = useMemo(() => {
    const facetGroups = (facets.data?.groups ?? []).filter(
      (group) => !provider || group.provider === provider,
    );
    return [...new Set(facetGroups.map((group) => group.model_id))].sort();
  }, [facets.data, provider]);

  const attemptItems = attempts.data?.pages.flatMap((page) => page.items) ?? [];
  const summaryData = summary.data;
  const hasEverBeenCalled = (facets.data?.groups.length ?? 0) > 0;

  const resetFilters = () => {
    setPreset("30d");
    setCustomFrom("");
    setCustomTo("");
    setProjectId("");
    setProvider("");
    setModelId("");
    setChannel("");
  };

  const refresh = () => {
    facets.refetch();
    summary.refetch();
    attempts.refetch();
  };

  const exportCsv = () => {
    if (!summaryData || summaryData.groups.length === 0) return;
    const blob = new Blob([buildUsageCsv(summaryData.groups)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `usage-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  const isLoading = summary.isLoading || attempts.isLoading || facets.isLoading;
  const errorMessage = summary.error?.message ?? attempts.error?.message ?? null;
  const showDashboard = Boolean(summaryData && summaryData.groups.length > 0);

  return (
    <div className="usage-dashboard">
      <section className="usage-filters" aria-label="用量筛选">
        <label>
          <span>时间范围</span>
          <select
            aria-label="选择用量统计时间范围"
            value={preset}
            onChange={(event) => setPreset(event.target.value as RangePreset)}
          >
            {(Object.keys(RANGE_LABELS) as Exclude<RangePreset, "custom">[]).map((key) => (
              <option key={key} value={key}>{RANGE_LABELS[key]}</option>
            ))}
            <option value="custom">自定义</option>
          </select>
        </label>
        {preset === "custom" ? (
          <span className="usage-custom-range">
            <label><span>开始日期</span><input type="date" aria-label="自定义开始日期" value={customFrom} onChange={(event) => setCustomFrom(event.target.value)} /></label>
            <label><span>结束日期</span><input type="date" aria-label="自定义结束日期" value={customTo} onChange={(event) => setCustomTo(event.target.value)} /></label>
          </span>
        ) : null}
        <label>
          <span>项目</span>
          <select aria-label="按项目筛选" value={projectId} onChange={(event) => setProjectId(event.target.value)}>
            <option value="">全部</option>
            {(projects.data ?? []).map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>供应商</span>
          <select aria-label="按供应商筛选" value={provider} onChange={(event) => { setProvider(event.target.value); setModelId(""); }}>
            <option value="">全部</option>
            {providerOptions.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label>
          <span>模型</span>
          <select aria-label="按模型筛选" value={modelId} onChange={(event) => setModelId(event.target.value)}>
            <option value="">全部</option>
            {modelOptions.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label>
          <span>通道</span>
          <select aria-label="按通道筛选" value={channel} onChange={(event) => setChannel(event.target.value as UsageChannel | "")}>
            <option value="">全部</option>
            <option value="HTTP_API">HTTP API</option>
            <option value="CLI">CLI</option>
          </select>
        </label>
        <div className="usage-filter-actions">
          <button type="button" className="button ghost compact" onClick={refresh} disabled={isLoading}>
            <RefreshCw className={isLoading ? "spin" : ""} size={15} />刷新
          </button>
          <button
            type="button"
            className="button ghost compact"
            onClick={exportCsv}
            disabled={!summaryData || summaryData.groups.length === 0}
          >
            <Download size={15} />导出 CSV
          </button>
        </div>
      </section>

      {isLoading ? (
        <div className="usage-state loading" role="status">
          <p>正在汇总 API 用量与成本数据…</p>
        </div>
      ) : errorMessage ? (
        <div className="usage-state error" role="alert">
          <p>用量数据加载失败：{errorMessage}</p>
          <button type="button" className="button ink compact" onClick={refresh}>重试</button>
        </div>
      ) : !hasEverBeenCalled ? (
        <div className="usage-state empty">
          <p>暂无调用记录</p>
          <small>发起剧本分析或单页生成后即可在此查看用量统计</small>
        </div>
      ) : !showDashboard ? (
        <div className="usage-state empty">
          <p>未找到匹配的调用记录</p>
          <small>请尝试调整时间范围或清除筛选条件</small>
          <button type="button" className="button ghost compact" onClick={resetFilters}>重置筛选</button>
        </div>
      ) : summaryData ? (
        <>
          <UsageKpiGrid summary={summaryData} />
          <UsageTrendChart groups={summaryData.groups} />
          <UsageBreakdownTable groups={summaryData.groups} />
          <UsageAttemptsTable
            items={attemptItems}
            loadedCount={attemptItems.length}
            hasMore={attempts.hasNextPage}
            loading={attempts.isLoading}
            loadingMore={attempts.isFetchingNextPage}
            error={attempts.error?.message ?? null}
            onLoadMore={() => attempts.fetchNextPage()}
            onOpenAttempt={setSelectedAttempt}
          />
          <section className="usage-panel usage-billed" aria-label="账单对账记录">
            <header>
              <h2>账单对账记录</h2>
              <small>运营导入的对账事实，独立于估算展示</small>
            </header>
            {summaryData.billed.length === 0 ? (
              <p className="usage-trend-empty">所选范围内暂无对账记录</p>
            ) : (
              <div className="usage-table-scroll">
                <table className="usage-table">
                  <thead>
                    <tr>
                      <th scope="col">账期</th>
                      <th scope="col">供应商 / 模型</th>
                      <th scope="col">账单账户</th>
                      <th scope="col">金额（原币种）</th>
                      <th scope="col">录入人</th>
                      <th scope="col">摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    {summaryData.billed.map((item) => (
                      <tr key={item.id}>
                        <th scope="row">{`${item.period_start.slice(0, 10)} ~ ${item.period_end.slice(0, 10)}`}</th>
                        <td><strong>{item.provider}</strong><small>{item.model_id}</small></td>
                        <td className="usage-mono">{item.billing_account_id}</td>
                        <td><span className="usage-badge billed">账单</span> {item.currency} {item.billed_amount}</td>
                        <td>{item.entered_by}</td>
                        <td>{item.source_note || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      ) : null}

      {selectedAttempt ? (
        <UsageAttemptDrawer attempt={selectedAttempt} onClose={() => setSelectedAttempt(null)} />
      ) : null}

      <footer className="usage-disclaimer">
        <p>
          计量语义：账单（对账导入）与估算（价格表推算）永不相加；不同币种不做隐式换算；未知 ≠ 0；CLI 通道费用未知 ≠ 免费。
          通道筛选作用于调用明细；汇总接口按时间/项目/供应商/模型聚合。
        </p>
      </footer>
    </div>
  );
}
