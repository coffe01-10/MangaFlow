import type { Character, Outfit, StoryboardPanel } from "@/lib/api";

export interface ReferenceSelection {
  character_asset_id: string | null;
  outfit_id: string | null;
  outfit_asset_id: string | null;
  /**
   * Explicit package version override (contract §8.1). `null` marks "package
   * mode with default inheritance" (the backend resolves the latest published
   * version); `undefined` means the key is not sent at all. A character whose
   * package has a published version enters package mode server-side even
   * without this key, so the default builder pre-marks those characters.
   */
  package_version_id?: string | null;
}

export type ReferenceSelections = Record<string, ReferenceSelection>;

/** character_id -> published package version id, from the package summaries. */
export type PublishedPackageVersions = Record<string, string>;

export interface CharacterReferenceLike {
  is_canonical: boolean;
  asset_id: string;
}

/** Distinct character ids in panel drawing order, as rendered on the page. */
export function collectVisibleCharacterIds(panels: StoryboardPanel[]): string[] {
  return Array.from(new Set(panels.flatMap((panel) => panel.characters)));
}

/**
 * Default per-character reference inheritance: canonical character reference
 * (or first), the outfit assigned by the storyboard, and that outfit's first
 * reference image. Characters with a published package version stay in
 * package mode: the backend picks the reference (front → cover → first) from
 * the version matrix, so no legacy asset id is sent for them.
 */
export function buildDefaultReferenceSelections(
  visibleCharacterIds: string[],
  characters: Character[] | undefined,
  outfits: Outfit[] | undefined,
  panels: StoryboardPanel[],
  publishedVersions: PublishedPackageVersions = {},
): ReferenceSelections {
  const next: ReferenceSelections = {};
  for (const characterId of visibleCharacterIds) {
    const character = characters?.find((item) => item.id === characterId);
    const assignedOutfitId = panels.find((panel) => panel.outfits[characterId])?.outfits[characterId] ?? null;
    const outfit = outfits?.find((item) => item.id === assignedOutfitId);
    if (publishedVersions[characterId]) {
      next[characterId] = {
        character_asset_id: null,
        outfit_id: assignedOutfitId,
        outfit_asset_id: null,
        package_version_id: null,
      };
      continue;
    }
    next[characterId] = {
      character_asset_id: character?.references.find((item) => item.is_canonical)?.asset_id ?? character?.references[0]?.asset_id ?? null,
      outfit_id: assignedOutfitId,
      outfit_asset_id: outfit?.reference_asset_ids[0] ?? null,
    };
  }
  return next;
}

/** Page-level manual picks win over the inherited defaults. */
export function mergeReferenceSelections(
  defaults: ReferenceSelections,
  overrides: ReferenceSelections,
): ReferenceSelections {
  return { ...defaults, ...overrides };
}

/**
 * Every visible character needs a character reference, and when the
 * storyboard assigns an outfit that outfit must have reference images with
 * one selected. Package-mode characters (explicit version or published
 * default inheritance) get their reference image resolved server-side from
 * the version matrix, so only the outfit still needs live references here.
 */
export function isGenerationReferenceReady(
  selections: ReferenceSelections,
  visibleCharacterIds: string[],
  outfits: Outfit[] | undefined,
  publishedVersions: PublishedPackageVersions = {},
): boolean {
  return visibleCharacterIds.every((characterId) => {
    const selection = selections[characterId];
    if (!selection) return false;
    const outfit = outfits?.find((item) => item.id === selection.outfit_id);
    if (selection.package_version_id != null || publishedVersions[characterId]) {
      return !outfit || Boolean(outfit.reference_asset_ids.length);
    }
    if (!selection.character_asset_id) return false;
    return !outfit || Boolean(outfit.reference_asset_ids.length && selection.outfit_asset_id);
  });
}

/** True when this character's generate input is resolved by a package version. */
export function isPackageModeSelection(
  characterId: string,
  selections: ReferenceSelections,
  publishedVersions: PublishedPackageVersions = {},
): boolean {
  return selections[characterId]?.package_version_id != null
    || Boolean(publishedVersions[characterId]);
}
