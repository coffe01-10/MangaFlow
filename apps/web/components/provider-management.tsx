"use client";

import { api, type ModelCapability } from "@/lib/api";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle, Sparkles } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ProviderCreateForm } from "./provider-settings/provider-create-form";
import {
  groupProviders,
  providerConfigured,
  providerMatchesQuery,
  sortProviders,
  type CapabilityFilter,
  type ModelTypeFilter,
  type ProviderSort,
} from "./provider-settings/provider-filters";
import { ProviderGroup } from "./provider-settings/provider-group";
import { ProviderToolbar } from "./provider-settings/provider-toolbar";

const EMPTY_MODELS: ModelCapability[] = [];

export function ProviderManagement() {
  const queryClient = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<ProviderSort>("RECOMMENDED");
  const [modelType, setModelType] = useState<ModelTypeFilter>("ALL");
  const [capability, setCapability] = useState<CapabilityFilter>("ALL");
  const [verifiedOnly, setVerifiedOnly] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [focusProviderId, setFocusProviderId] = useState<string | null>(null);
  const [pinnedProviderId, setPinnedProviderId] = useState<string | null>(null);

  const catalog = models.data ?? EMPTY_MODELS;
  const grouped = useMemo(() => {
    const visible = sortProviders(
      (providers.data ?? []).filter((provider) => providerMatchesQuery(provider, catalog, filter)),
      sort,
    );
    return groupProviders(visible);
  }, [catalog, filter, providers.data, sort]);

  const visibleCount = grouped.configured.length + grouped.unconfigured.length + grouped.disabled.length;
  const configuredCount = (providers.data ?? []).filter(providerConfigured).length;
  const searching = Boolean(filter.trim());
  const jumpToResults = useCallback(() => {
    const first = grouped.configured[0] ?? grouped.unconfigured[0] ?? grouped.disabled[0];
    if (!first) return;
    document.getElementById(`provider-card-toggle-${first.id}`)?.focus();
  }, [grouped]);

  const clearFocus = useCallback(() => setFocusProviderId(null), []);

  function retryProviders() {
    providers.refetch();
    models.refetch();
  }

  function renderResults() {
    if (providers.isPending) {
      return (
        <div className="loading-panel">
          <LoaderCircle className="spin" />正在读取供应商与模型目录…
        </div>
      );
    }
    if (providers.isError) {
      return (
        <div id="provider-platform-error" className="form-error" role="alert">
          <CircleAlert size={14} />供应商列表读取失败
          <button type="button" onClick={retryProviders}>重试</button>
        </div>
      );
    }
    if (!(providers.data ?? []).length) {
      return <p className="provider-group-empty">还没有供应商。先添加自定义连接，或检查服务端预设。</p>;
    }
    if (!visibleCount) {
      return (
        <p className="provider-group-empty">
          没有符合当前搜索或筛选的供应商
          <button type="button" onClick={() => setFilter("")}>清除筛选</button>
        </p>
      );
    }
    return (
      <div className="provider-list">
        <ProviderGroup
          id="configured"
          label="已配置"
          providers={grouped.configured}
          defaultExpanded
          forceExpanded={searching || grouped.configured.some((provider) => provider.id === pinnedProviderId)}
          forceExpandCards={searching}
          pinnedProviderId={pinnedProviderId}
          modelType={modelType}
          capability={capability}
          verifiedOnly={verifiedOnly}
          showHidden={showHidden}
          catalog={catalog}
          focusProviderId={focusProviderId}
          onKeyFocused={clearFocus}
        />
        <ProviderGroup
          id="unconfigured"
          label="未配置"
          providers={grouped.unconfigured}
          defaultExpanded={false}
          forceExpanded={searching || grouped.unconfigured.some((provider) => provider.id === pinnedProviderId)}
          forceExpandCards={searching}
          pinnedProviderId={pinnedProviderId}
          modelType={modelType}
          capability={capability}
          verifiedOnly={verifiedOnly}
          showHidden={showHidden}
          catalog={catalog}
          focusProviderId={focusProviderId}
          onKeyFocused={clearFocus}
        />
        <ProviderGroup
          id="disabled"
          label="已停用"
          providers={grouped.disabled}
          defaultExpanded={false}
          forceExpanded={searching || grouped.disabled.some((provider) => provider.id === pinnedProviderId)}
          forceExpandCards={searching}
          pinnedProviderId={pinnedProviderId}
          modelType={modelType}
          capability={capability}
          verifiedOnly={verifiedOnly}
          showHidden={showHidden}
          catalog={catalog}
          focusProviderId={focusProviderId}
          onKeyFocused={clearFocus}
        />
      </div>
    );
  }

  return (
    <article className="control-card provider-platform">
      <header>
        <div>
          <Sparkles size={18} />
          <span>AI 供应商与模型</span>
        </div>
        <small>{providers.data?.length ?? 0} 家供应商 · {configuredCount} 已配置</small>
      </header>
      <ProviderToolbar
        filter={filter}
        onFilterChange={setFilter}
        onJump={jumpToResults}
        matchCount={visibleCount}
        modelType={modelType}
        onModelTypeChange={setModelType}
        capability={capability}
        onCapabilityChange={setCapability}
        verifiedOnly={verifiedOnly}
        onVerifiedOnlyChange={setVerifiedOnly}
        showHidden={showHidden}
        onShowHiddenChange={setShowHidden}
        sort={sort}
        onSortChange={setSort}
        createOpen={createOpen}
        onToggleCreate={() => setCreateOpen((current) => !current)}
      />
      <p className="provider-toolbar-hint">可添加兼容连接；账号型凭据由服务端环境管理，CLI 登录由外部工具管理，Key 型连接在各自连接卡内录入。</p>
      <ProviderCreateForm
        open={createOpen}
        onCreated={(provider) => {
          setCreateOpen(false);
          setFocusProviderId(provider.id);
          setPinnedProviderId(provider.id);
          queryClient.invalidateQueries({ queryKey: ["providers"] });
        }}
      />
      {renderResults()}
    </article>
  );
}
