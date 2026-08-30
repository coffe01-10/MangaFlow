"use client";

import type { ModelCapability, ProviderProfile } from "@/lib/api";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { ProviderCard } from "./provider-card";
import type { CapabilityFilter, ModelTypeFilter } from "./provider-filters";

export function ProviderGroup({
  id,
  label,
  providers,
  models,
  modelsStatus,
  defaultExpanded,
  forceExpanded,
  forceExpandCards,
  pinnedProviderId,
  modelType,
  capability,
  verifiedOnly,
  focusProviderId,
  onKeyFocused,
  onRetryModels,
}: {
  id: string;
  label: string;
  providers: ProviderProfile[];
  models: ModelCapability[];
  modelsStatus: "loading" | "error" | "ready";
  defaultExpanded: boolean;
  forceExpanded: boolean;
  forceExpandCards: boolean;
  pinnedProviderId: string | null;
  modelType: ModelTypeFilter;
  capability: CapabilityFilter;
  verifiedOnly: boolean;
  focusProviderId: string | null;
  onKeyFocused: () => void;
  onRetryModels: () => void;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const shown = forceExpanded || expanded;
  const panelId = `provider-group-${id}`;
  if (!providers.length) return null;

  return (
    <section className="provider-group">
      <header>
        <button
          type="button"
          className="provider-group-toggle"
          aria-expanded={shown}
          aria-controls={panelId}
          onClick={() => setExpanded((current) => !current)}
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
              models={models}
              modelsStatus={modelsStatus}
              forceExpanded={forceExpandCards}
              preferExpanded={pinnedProviderId === provider.id}
              modelType={modelType}
              capability={capability}
              verifiedOnly={verifiedOnly}
              autoFocusKey={focusProviderId === provider.id}
              onKeyFocused={onKeyFocused}
              onRetryModels={onRetryModels}
            />
          ))}
        </div>
      )}
    </section>
  );
}
