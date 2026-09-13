"use client";

import type { ModelCapability, ProviderProfile } from "@/lib/api";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { ConnectionPanel } from "./connection-panel";
import { mapCategory, mapRisk } from "./provider-copy";
import type { CapabilityFilter, ModelTypeFilter } from "./provider-filters";
import { providerModelCount } from "./provider-filters";
import { ProviderLifecycleControls } from "./provider-lifecycle-controls";

export function ProviderCard({
  provider,
  forceExpanded,
  preferExpanded,
  modelType,
  capability,
  verifiedOnly,
  showHidden,
  catalog,
  autoFocusKey,
  onKeyFocused,
  onDirtyChange,
}: {
  provider: ProviderProfile;
  forceExpanded: boolean;
  preferExpanded: boolean;
  modelType: ModelTypeFilter;
  capability: CapabilityFilter;
  verifiedOnly: boolean;
  showHidden: boolean;
  catalog: ModelCapability[];
  autoFocusKey: boolean;
  onKeyFocused: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(
    preferExpanded || provider.connections.some((connection) => connection.configured),
  );
  // #546-5：收起卡片会卸载 ConnectionPanel（API Key / 手工模型 / JSON 草稿
  // 随之丢失）。各连接面板上抛脏标记，收起前确认。
  const [dirtyConnectionIds, setDirtyConnectionIds] = useState<Set<string>>(new Set());
  const cardDirty = dirtyConnectionIds.size > 0;
  useEffect(() => { onDirtyChange?.(cardDirty); }, [cardDirty, onDirtyChange]);

  function handleConnectionDirty(connectionId: string, dirty: boolean) {
    setDirtyConnectionIds((current) => {
      // 必须在无变化时返回原引用：父级每次渲染都传新的 onDirtyChange
      // 闭包，effect 会反复执行；新 Set 会造成 setState → 渲染 → effect
      // 的无限循环。
      if (dirty === current.has(connectionId)) return current;
      const next = new Set(current);
      if (dirty) next.add(connectionId);
      else next.delete(connectionId);
      return next;
    });
  }

  const shown = forceExpanded || preferExpanded || expanded;
  const panelId = `provider-card-${provider.id}`;
  const connectionCount = provider.connections.length;
  const configuredCount = provider.connections.filter((connection) => connection.configured).length;
  const modelCount = providerModelCount(provider);
  const meta = [mapCategory(provider.category), mapRisk(provider.risk_label)].filter(Boolean).join(" · ");

  return (
    <article className={`provider-card ${provider.enabled ? "" : "disabled"}`}>
      <header>
        <button
          id={`provider-card-toggle-${provider.id}`}
          type="button"
          className="provider-card-toggle"
          aria-expanded={shown}
          aria-controls={panelId}
          onClick={() => {
            if (shown && cardDirty && !window.confirm("该供应商连接中有未保存的输入，收起会丢弃这些草稿。仍要收起吗？")) return;
            setExpanded((current) => !current);
          }}
        >
          {shown ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          <span className="provider-card-title">
            <span>{meta}</span>
            <strong>{provider.name}</strong>
            {provider.description ? <small>{provider.description}</small> : null}
          </span>
          <span className="provider-card-counts">
            <small>{configuredCount}/{connectionCount} 连接</small>
            <small>{modelCount} 模型</small>
          </span>
        </button>
        {provider.documentation_url && (
          <a
            className="provider-doc-link"
            href={provider.documentation_url}
            target="_blank"
            rel="noreferrer"
          >
            说明
          </a>
        )}
        <ProviderLifecycleControls provider={provider} />
      </header>
      {shown && (
        <div id={panelId} className="provider-card-body">
          {provider.connections.map((connection) => (
            <ConnectionPanel
              key={connection.id}
              connection={connection}
              modelType={modelType}
              capability={capability}
              verifiedOnly={verifiedOnly}
              showHidden={showHidden}
              catalog={catalog}
              autoFocusKey={autoFocusKey}
              onKeyFocused={onKeyFocused}
              onDirtyChange={(dirty) => handleConnectionDirty(connection.id, dirty)}
            />
          ))}
        </div>
      )}
    </article>
  );
}
