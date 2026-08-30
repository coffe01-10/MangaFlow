"use client";

import type { ProviderProfile } from "@/lib/api";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

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
  autoFocusKey,
  onKeyFocused,
}: {
  provider: ProviderProfile;
  forceExpanded: boolean;
  preferExpanded: boolean;
  modelType: ModelTypeFilter;
  capability: CapabilityFilter;
  verifiedOnly: boolean;
  showHidden: boolean;
  autoFocusKey: boolean;
  onKeyFocused: () => void;
}) {
  const [expanded, setExpanded] = useState(
    preferExpanded || provider.connections.some((connection) => connection.configured),
  );
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
          onClick={() => setExpanded((current) => !current)}
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
              autoFocusKey={autoFocusKey}
              onKeyFocused={onKeyFocused}
            />
          ))}
        </div>
      )}
    </article>
  );
}
