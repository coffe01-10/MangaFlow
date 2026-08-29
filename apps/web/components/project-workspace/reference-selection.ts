import type { Character, Outfit, StoryboardPanel } from "@/lib/api";

export interface ReferenceSelection {
  character_asset_id: string | null;
  outfit_id: string | null;
  outfit_asset_id: string | null;
}

export type ReferenceSelections = Record<string, ReferenceSelection>;

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
 * reference image.
 */
export function buildDefaultReferenceSelections(
  visibleCharacterIds: string[],
  characters: Character[] | undefined,
  outfits: Outfit[] | undefined,
  panels: StoryboardPanel[],
): ReferenceSelections {
  const next: ReferenceSelections = {};
  for (const characterId of visibleCharacterIds) {
    const character = characters?.find((item) => item.id === characterId);
    const assignedOutfitId = panels.find((panel) => panel.outfits[characterId])?.outfits[characterId] ?? null;
    const outfit = outfits?.find((item) => item.id === assignedOutfitId);
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
 * one selected.
 */
export function isGenerationReferenceReady(
  selections: ReferenceSelections,
  visibleCharacterIds: string[],
  outfits: Outfit[] | undefined,
): boolean {
  return visibleCharacterIds.every((characterId) => {
    const selection = selections[characterId];
    if (!selection?.character_asset_id) return false;
    const outfit = outfits?.find((item) => item.id === selection.outfit_id);
    return !outfit || Boolean(outfit.reference_asset_ids.length && selection.outfit_asset_id);
  });
}
