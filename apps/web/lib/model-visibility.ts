import type { ModelCapability } from "@/lib/api";

export type CurrentModelReferences = {
  catalogIds?: Iterable<string | null | undefined>;
  logicalAliases?: Iterable<string | null | undefined>;
};

/**
 * Creator-facing selectors hide models by preference without invalidating a
 * value that is already bound to a project, workflow node, or in-progress UI.
 */
export function creatorVisibleModels(
  models: ModelCapability[],
  current: CurrentModelReferences = {},
): ModelCapability[] {
  const catalogIds = new Set([...current.catalogIds ?? []].filter(Boolean));
  const logicalAliases = new Set([...current.logicalAliases ?? []].filter(Boolean));

  return models.filter((model) =>
    (model.enabled && model.display_enabled)
    || catalogIds.has(model.catalog_id)
    || logicalAliases.has(model.logical_alias),
  );
}
