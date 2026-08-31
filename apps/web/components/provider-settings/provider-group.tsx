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
              forceExpanded={forceExpandCards}
              preferExpanded={pinnedProviderId === provider.id}
              modelType={modelType}
              capability={capability}
              verifiedOnly={verifiedOnly}
              showHidden={showHidden}
              catalog={catalog}
              autoFocusKey={focusProviderId === provider.id}
              onKeyFocused={onKeyFocused}
            />
          ))}
        </div>
      )}
    </section>
  );
}
