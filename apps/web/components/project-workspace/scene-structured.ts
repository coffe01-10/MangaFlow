import type { SceneAssetStructured } from "@/lib/api";

export const TIME_OF_DAY_OPTIONS = [
  ["", "未指定"],
  ["dawn", "黎明"],
  ["day", "白天"],
  ["dusk", "黄昏"],
  ["night", "夜晚"],
] as const;

export const VARIANT_OVERRIDE_KEYS = ["time_of_day", "weather", "season", "lighting", "palette"] as const;

export function emptyStructured(): SceneAssetStructured {
  return {
    place: "",
    subareas: [],
    interior: null,
    time_of_day: "",
    weather: "",
    season: "",
    lighting: "",
    palette: { dominant: [], mood: "" },
    fixed_props: [],
    spatial_relations: [],
  };
}

export function splitList(value: string) {
  return value.split(/[，,、\n]/).map((item) => item.trim()).filter(Boolean);
}

export function joinList(value: string[] | undefined) {
  return (value ?? []).join("，");
}

export function pickStructured(input?: SceneAssetStructured | Record<string, unknown> | null): SceneAssetStructured {
  const source = input ?? {};
  const palette = source.palette && typeof source.palette === "object" && !Array.isArray(source.palette)
    ? source.palette as { dominant?: string[]; mood?: string }
    : {};
  return {
    place: typeof source.place === "string" ? source.place : "",
    subareas: Array.isArray(source.subareas) ? source.subareas.map(String).filter(Boolean) : [],
    interior: source.interior === true ? true : source.interior === false ? false : null,
    time_of_day: source.time_of_day === "dawn" || source.time_of_day === "day"
      || source.time_of_day === "dusk" || source.time_of_day === "night"
      ? source.time_of_day
      : "",
    weather: typeof source.weather === "string" ? source.weather : "",
    season: typeof source.season === "string" ? source.season : "",
    lighting: typeof source.lighting === "string" ? source.lighting : "",
    palette: {
      dominant: Array.isArray(palette.dominant) ? palette.dominant.map(String).filter(Boolean) : [],
      mood: typeof palette.mood === "string" ? palette.mood : "",
    },
    fixed_props: Array.isArray(source.fixed_props) ? source.fixed_props.map(String).filter(Boolean) : [],
    spatial_relations: Array.isArray(source.spatial_relations)
      ? source.spatial_relations.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        .map((item) => ({
          from: typeof item.from === "string" ? item.from : "",
          to: typeof item.to === "string" ? item.to : "",
          relation: typeof item.relation === "string" ? item.relation : "",
        }))
        .filter((item) => item.from || item.to || item.relation)
      : [],
  };
}

export function pickVariantOverrides(input?: Record<string, unknown> | null): Record<string, unknown> {
  const source = input ?? {};
  const next: Record<string, unknown> = {};
  for (const key of VARIANT_OVERRIDE_KEYS) {
    if (key === "palette") {
      const palette = source.palette;
      if (palette && typeof palette === "object" && !Array.isArray(palette)) {
        const record = palette as { dominant?: unknown; mood?: unknown };
        next.palette = {
          dominant: Array.isArray(record.dominant) ? record.dominant.map(String).filter(Boolean) : [],
          mood: typeof record.mood === "string" ? record.mood : "",
        };
      }
      continue;
    }
    if (typeof source[key] === "string") next[key] = source[key];
  }
  return next;
}

export async function countPersistedSceneBindings(
  projectId: string,
  sceneAssetId: string,
  chapters: () => Promise<{ id: string }[]>,
  scriptOf: (chapterId: string) => Promise<{ scenes: { scene_asset_id: string | null }[] }>,
  isNotFound: (error: unknown) => boolean,
): Promise<number | null> {
  try {
    const listed = await chapters();
    let count = 0;
    for (const chapter of listed) {
      try {
        const script = await scriptOf(chapter.id);
        count += script.scenes.filter((scene) => scene.scene_asset_id === sceneAssetId).length;
      } catch (error) {
        if (isNotFound(error)) continue;
        return null;
      }
    }
    return count;
  } catch {
    return null;
  }
}
