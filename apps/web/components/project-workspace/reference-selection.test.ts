import { describe, expect, it } from "vitest";

import type { Character, Outfit, StoryboardPanel } from "@/lib/api";

import {
  buildDefaultReferenceSelections,
  collectVisibleCharacterIds,
  isGenerationReferenceReady,
  mergeReferenceSelections,
} from "./reference-selection";

function panel(overrides: Partial<StoryboardPanel>): StoryboardPanel {
  return {
    id: "panel-1",
    page_id: "page-1",
    reading_order: 1,
    bounds: {},
    shot_type: "MS",
    camera_angle: "eye",
    camera_height: "normal",
    characters: [],
    character_presence: {},
    props: [],
    outfits: {},
    actions: {},
    expressions: {},
    background: "",
    bubble_regions: [],
    sound_effects: [],
    bleed: false,
    borderless: false,
    locked_fields: [],
    version: 1,
    dialogues: [],
    ...overrides,
  };
}

function character(overrides: Partial<Character>): Character {
  return {
    id: "character-1",
    project_id: "project-1",
    primary_name: "苏清白",
    aliases: [],
    alias_conflict: false,
    canonical_description: "",
    locked_features: [],
    forbidden_changes: [],
    status: "DRAFT",
    version: 1,
    references: [],
    ...overrides,
  };
}

function outfit(overrides: Partial<Outfit>): Outfit {
  return {
    id: "outfit-1",
    project_id: "project-1",
    character_id: "character-1",
    name: "冬季校服",
    components: {},
    state_rules: {},
    locked_fields: [],
    reference_asset_ids: ["outfit-ref-1"],
    status: "ACTIVE",
    version: 1,
    ...overrides,
  };
}

describe("collectVisibleCharacterIds", () => {
  it("按出场顺序去重收集入镜人物", () => {
    const panels = [
      panel({ characters: ["a", "b"] }),
      panel({ characters: ["b", "a", "c"] }),
    ];
    expect(collectVisibleCharacterIds(panels)).toEqual(["a", "b", "c"]);
  });
});

describe("buildDefaultReferenceSelections", () => {
  it("优先规范参考图，继承分镜指定的服装与该服装首张参考", () => {
    const characters = [
      character({
        id: "a",
        references: [
          { id: "cr-2", character_id: "a", angle: "front", is_canonical: false, asset_id: "ref-2" },
          { id: "cr-1", character_id: "a", angle: "front", is_canonical: true, asset_id: "ref-1" },
        ],
      }),
    ];
    const outfits = [outfit({ id: "outfit-a", reference_asset_ids: ["outfit-ref-9", "outfit-ref-8"] })];
    const panels = [panel({ characters: ["a"], outfits: { a: "outfit-a" } })];

    expect(buildDefaultReferenceSelections(["a"], characters, outfits, panels)).toEqual({
      a: { character_asset_id: "ref-1", outfit_id: "outfit-a", outfit_asset_id: "outfit-ref-9" },
    });
  });

  it("没有规范参考时退回第一张，未指定服装时 outfit 为空", () => {
    const characters = [character({ id: "a", references: [{ id: "cr-1", character_id: "a", angle: "front", is_canonical: false, asset_id: "only-ref" }] })];
    expect(buildDefaultReferenceSelections(["a"], characters, [], [panel({ characters: ["a"] })])).toEqual({
      a: { character_asset_id: "only-ref", outfit_id: null, outfit_asset_id: null },
    });
  });

  it("角色档案缺失时人物参考为空", () => {
    expect(buildDefaultReferenceSelections(["ghost"], [], [], [])).toEqual({
      ghost: { character_asset_id: null, outfit_id: null, outfit_asset_id: null },
    });
  });
});

describe("mergeReferenceSelections", () => {
  it("本页手动选择覆盖继承默认值，其余保持默认", () => {
    const defaults = {
      a: { character_asset_id: "ref-a", outfit_id: null, outfit_asset_id: null },
      b: { character_asset_id: "ref-b", outfit_id: null, outfit_asset_id: null },
    };
    const overrides = {
      a: { character_asset_id: "ref-a2", outfit_id: null, outfit_asset_id: null },
    };
    expect(mergeReferenceSelections(defaults, overrides)).toEqual({
      a: { character_asset_id: "ref-a2", outfit_id: null, outfit_asset_id: null },
      b: { character_asset_id: "ref-b", outfit_id: null, outfit_asset_id: null },
    });
  });
});

describe("isGenerationReferenceReady", () => {
  const outfits = [outfit({ id: "outfit-a", reference_asset_ids: ["outfit-ref-1"] })];

  it("所有入镜人物都有参考时视为就绪", () => {
    const selections = {
      a: { character_asset_id: "ref-a", outfit_id: null, outfit_asset_id: null },
    };
    expect(isGenerationReferenceReady(selections, ["a"], outfits)).toBe(true);
  });

  it("缺少人物参考时未就绪", () => {
    const selections = {
      a: { character_asset_id: null, outfit_id: null, outfit_asset_id: null },
    };
    expect(isGenerationReferenceReady(selections, ["a"], outfits)).toBe(false);
  });

  it("分镜指定服装时必须选中该服装的一张参考图", () => {
    const withoutOutfitAsset = {
      a: { character_asset_id: "ref-a", outfit_id: "outfit-a", outfit_asset_id: null },
    };
    expect(isGenerationReferenceReady(withoutOutfitAsset, ["a"], outfits)).toBe(false);

    const withOutfitAsset = {
      a: { character_asset_id: "ref-a", outfit_id: "outfit-a", outfit_asset_id: "outfit-ref-1" },
    };
    expect(isGenerationReferenceReady(withOutfitAsset, ["a"], outfits)).toBe(true);
  });

  it("分镜指定的服装没有任何参考图时无法就绪", () => {
    const emptyOutfitOutfits = [outfit({ id: "outfit-a", reference_asset_ids: [] })];
    const selections = {
      a: { character_asset_id: "ref-a", outfit_id: "outfit-a", outfit_asset_id: null },
    };
    expect(isGenerationReferenceReady(selections, ["a"], emptyOutfitOutfits)).toBe(false);
  });
});
