"use client";

import type { ModelCapability, ProviderProfile } from "@/lib/api";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";

import { ProviderCard } from "./provider-card";
import type { CapabilityFilter, ModelTypeFilter } from "./provider-filters";

export function ProviderGroup({
  id,
  label,
  providers,
  defaultExpanded,
  forceExpanded,
  forceExpandCards,
  pinnedProviderId,
  modelType,
  capability,
  verifiedOnly,
  showHidden,
  catalog,
  focusProviderId,
  onKeyFocused,
  onDirtyChange,
}: {
  id: string;
  label: string;
  providers: ProviderProfile[];
  defaultExpanded: boolean;
  forceExpanded: boolean;
  forceExpandCards: boolean;
  pinnedProviderId: string | null;
  modelType: ModelTypeFilter;
  capability: CapabilityFilter;
  verifiedOnly: boolean;
  showHidden: boolean;
  catalog: ModelCapability[];
  focusProviderId: string | null;
  onKeyFocused: () => void;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  // #546-5：收起分组会卸载其中所有连接面板（连同半成品草稿）。各卡片把
  // 脏标记上抛，收起前确认；分组级脏态再上抛给 ProviderManagement。
  const [dirtyProviderIds, setDirtyProviderIds] = useState<Set<string>>(new Set());
  const groupDirty = dirtyProviderIds.size > 0;
  useEffect(() => { onDirtyChange?.(groupDirty); }, [groupDirty, onDirtyChange]);
  const shown = forceExpanded || expanded;
  const panelId = `provider-group-${id}`;
  if (!providers.length) return null;

  function handleProviderDirty(providerId: string, dirty: boolean) {
    setDirtyProviderIds((current) => {
      if (dirty === current.has(providerId)) return current;
      const next = new Set(current);
      if (dirty) next.add(providerId);
      else next.delete(providerId);
      return next;
    });
  }

  return (
    <section className="provider-group">
      <header>
        <button
          type="button"
          className="provider-group-toggle"
          aria-expanded={shown}
          aria-controls={panelId}
          onClick={() => {
            if (shown && groupDirty && !window.confirm("该分组中有未保存的连接输入，收起会丢弃这些草稿。仍要收起吗？")) return;
            setExpanded((current) => !current);
          }}
        >
          {shown ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>{label}</span>
          <strong>{providers.length}</strong>
        </button>
      </header>
      {shown && (
        <div id={panelId}>
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              forceExpanded={forceExpandCards}
              preferExpanded={pinnedProviderId === provider.id}
              modelType={modelType}
              capability={capability}
              verifiedOnly={verifiedOnly}
              showHidden={showHidden}
              catalog={catalog}
              autoFocusKey={focusProviderId === provider.id}
              onKeyFocused={onKeyFocused}
              onDirtyChange={(dirty) => handleProviderDirty(provider.id, dirty)}
            />
          ))}
        </div>
      )}
    </section>
  );
}
