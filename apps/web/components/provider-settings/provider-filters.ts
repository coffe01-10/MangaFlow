import type { ModelCapability, ProviderProfile } from "@/lib/api";

export type ProviderSort = "RECOMMENDED" | "NAME" | "HEALTH" | "MODELS" | "LATENCY";
export type ModelTypeFilter = "ALL" | "TEXT" | "IMAGE";
export type CapabilityFilter =
  | "ALL"
  | "structured_text"
  | "multimodal_analysis"
  | "image_generate"
  | "image_edit";

const healthOrder: Record<string, number> = {
  HEALTHY: 0,
  DEGRADED: 1,
  UNKNOWN: 2,
  UNCONFIGURED: 3,
  OFFLINE: 4,
};

export function providerModelCount(provider: ProviderProfile) {
  return provider.connections.reduce((total, connection) => total + connection.model_count, 0);
}

export function providerConfigured(provider: ProviderProfile) {
  return provider.connections.some((connection) => connection.configured);
}

function providerLatency(provider: ProviderProfile) {
  const measured = provider.connections
    .map((connection) => connection.latency_ms)
    .filter((latency): latency is number => latency !== null);
  return measured.length ? Math.min(...measured) : Number.POSITIVE_INFINITY;
}

function providerHealthRank(provider: ProviderProfile) {
  if (!provider.connections.length) return 5;
  return Math.min(
    ...provider.connections.map((connection) => healthOrder[connection.health_state] ?? 5),
  );
}

export function sortProviders(items: ProviderProfile[], sort: ProviderSort) {
  return [...items].sort((left, right) => {
    if (sort === "NAME") return left.name.localeCompare(right.name, "zh-CN");
    if (sort === "HEALTH") {
      return providerHealthRank(left) - providerHealthRank(right)
        || left.name.localeCompare(right.name, "zh-CN");
    }
    if (sort === "MODELS") {
      return providerModelCount(right) - providerModelCount(left)
        || left.name.localeCompare(right.name, "zh-CN");
    }
    if (sort === "LATENCY") {
      return providerLatency(left) - providerLatency(right)
        || left.name.localeCompare(right.name, "zh-CN");
    }
    const leftScore = Number(providerConfigured(left)) * 4
      + Number(providerHealthRank(left) === 0) * 2
      + Number(providerModelCount(left) > 0);
    const rightScore = Number(providerConfigured(right)) * 4
      + Number(providerHealthRank(right) === 0) * 2
      + Number(providerModelCount(right) > 0);
    return rightScore - leftScore || left.name.localeCompare(right.name, "zh-CN");
  });
}

export function providerMatchesQuery(
  provider: ProviderProfile,
  models: ModelCapability[],
  query: string,
) {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return true;
  if (provider.name.toLowerCase().includes(normalized)) return true;
  if ((provider.preset_key ?? "").toLowerCase().includes(normalized)) return true;
  if (provider.connections.some((connection) => connection.protocol.toLowerCase().includes(normalized))) {
    return true;
  }
  const connectionIds = new Set(provider.connections.map((connection) => connection.id));
  return models.some((model) => (
    connectionIds.has(model.connection_id)
    && (
      model.model_id.toLowerCase().includes(normalized)
      || model.catalog_id.toLowerCase().includes(normalized)
    )
  ));
}

export function filterModels<T extends Pick<
  ModelCapability,
  "model_type" | "operations" | "confidence" | "display_enabled"
>>(
  models: T[],
  options: {
    modelType: ModelTypeFilter;
    capability: CapabilityFilter;
    verifiedOnly: boolean;
    showHidden?: boolean;
  },
) {
  return models.filter((model) => {
    if (!options.showHidden && !model.display_enabled) return false;
    if (options.modelType !== "ALL" && model.model_type !== options.modelType) return false;
    if (options.capability !== "ALL" && !model.operations.includes(options.capability)) return false;
    if (options.verifiedOnly && model.confidence !== "VERIFIED") return false;
    return true;
  });
}

export type ProviderGroupKey = "configured" | "unconfigured" | "disabled";

export function groupProviders(items: ProviderProfile[]) {
  const configured: ProviderProfile[] = [];
  const unconfigured: ProviderProfile[] = [];
  const disabled: ProviderProfile[] = [];
  for (const provider of items) {
    if (!provider.enabled) disabled.push(provider);
    else if (providerConfigured(provider)) configured.push(provider);
    else unconfigured.push(provider);
  }
  return { configured, unconfigured, disabled };
}
