import { describe, expect, it } from "vitest";

import type { Character, MangaPage, ScriptScene, StoryboardPanel } from "@/lib/api";

import {
  DIRECTOR_RAW_OUTPUT_ID,
  compileDirectorCommand,
  type DirectorRuleInput,
  type DirectorScopeSelection,
} from "./director-rules";

function pageFixture(overrides: Partial<MangaPage> = {}): MangaPage {
  return {
    id: "page-1",
    chapter_id: "chapter-1",
    page_number: 1,
    revision_no: 1,
    page_function: "dialogue",
    panel_count: 3,
    reading_direction: "rtl",
    resolution: "1K",
    status: "PLANNED",
    estimated_text_chars: 40,
    estimated_bubbles: 2,
    source_coverage: { complete: true, layout_mode: "dynamic", ranges: [{ text: "巷口灯还亮着" }] },
    selected_candidate_id: null,
    storyboard_version: 2,
    selected_candidate_ack_version: 1,
    continuity_status: "PASSED",
    scene_ids: ["scene-1"],
    beat_ids: ["beat-1"],
    version: 1,
    ...overrides,
  };
}

function panelFixture(overrides: Partial<StoryboardPanel> = {}): StoryboardPanel {
  return {
    id: "panel-1",
    page_id: "page-1",
    reading_order: 1,
    bounds: {},
    shot_type: "medium_close_up",
    camera_angle: "eye_level",
    camera_height: "normal",
    characters: ["character-1"],
    character_presence: { "character-1": "VISIBLE" },
    props: [],
    outfits: {},
    actions: {},
    expressions: {},
    background: "巷口",
    bubble_regions: [],
    sound_effects: [],
    bleed: false,
    borderless: false,
    locked_fields: [],
    version: 4,
    dialogues: [{
      id: "dialogue-1",
      panel_id: "panel-1",
      speaker_character_id: "character-1",
      target_text: "你好",
      reading_order: 1,
      text_direction: "horizontal",
      region: {},
      rewrite_forbidden: false,
      bubble: null,
    }],
    ...overrides,
  };
}

function sceneFixture(overrides: Partial<ScriptScene> = {}): ScriptScene {
  return {
    id: "scene-1",
    ordinal: 1,
    location: "巷口",
    scene_asset_id: null,
    scene_asset_variant_id: null,
    time_label: "傍晚",
    weather: "小雨",
    purpose: "",
    emotional_arc: "",
    source_range: {},
    outfit_assignments: {},
    locked_fields: [],
    version: 3,
    beats: [],
    ...overrides,
  };
}

function characterFixture(overrides: Partial<Character> = {}): Character {
  return {
    id: "character-1",
    project_id: "project-1",
    primary_name: "林澈",
    aliases: ["阿澈"],
    alias_conflict: false,
    canonical_description: "",
    locked_features: [],
    forbidden_changes: [],
    status: "ACTIVE",
    version: 1,
    references: [],
    ...overrides,
  };
}

let idCounter = 0;

function baseInput(overrides: Partial<DirectorRuleInput> = {}): DirectorRuleInput {
  idCounter = 0;
  return {
    projectId: "project-1",
    page: pageFixture(),
    panels: [panelFixture()],
    scenes: [sceneFixture()],
    characters: [characterFixture()],
    selection: null,
    utterance: "",
    newId: () => `00000000-0000-4000-8000-${String(++idCounter).padStart(12, "0")}`,
    now: () => "2026-09-03T00:00:00Z",
    ...overrides,
  };
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe("director rules 规则桩（V02-41B）", () => {
  it("D2 无选区输入模糊口令时进入澄清层，不产出命令", () => {
    const plan = compileDirectorCommand(baseInput({ utterance: "改一下" }));
    expect(plan.kind).toBe("clarify");
    if (plan.kind !== "clarify") return;
    expect(plan.options.map((option) => option.label)).toContain("整页");
    expect(plan.options.map((option) => option.label)).toContain("格 1");
  });

  it("D3 已选格 03 + 「雨下大一点」编成场景上下文命令，版本取场景 version", () => {
    const panel3 = panelFixture({ id: "panel-3", reading_order: 3, version: 7 });
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture(), panelFixture({ id: "panel-2", reading_order: 2 }), panel3],
      selection: { kind: "panel", panelId: "panel-3" },
      utterance: "雨下大一点",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_scene_context");
    expect(plan.envelope.payload).toEqual({ weather: "大雨" });
    expect(plan.envelope.target.scene_id).toBe("scene-1");
    expect(plan.envelope.target.page_id).toBe("page-1");
    expect(plan.envelope.expected_version).toEqual({ scope: "scene", value: 3 });
    expect(plan.scopeLabel).toContain("主场景");
  });

  it("移除类天气口令按类型映射:雾→无雾、雪→无雪、裸雨→无雨", () => {
    const fog = compileDirectorCommand(baseInput({ utterance: "把雾去掉" }));
    expect(fog.kind).toBe("command");
    if (fog.kind !== "command") return;
    expect(fog.envelope.operation).toBe("update_scene_context");
    expect(fog.envelope.payload).toEqual({ weather: "无雾" });

    const snow = compileDirectorCommand(baseInput({ utterance: "雪停了" }));
    expect(snow.kind).toBe("command");
    if (snow.kind !== "command") return;
    expect(snow.envelope.payload).toEqual({ weather: "无雪" });

    // 裸「雨」走 WEATHER_LABELS 的兜底条目,移除映射漏掉它会把雨写回去。
    const rain = compileDirectorCommand(baseInput({ utterance: "把雨去掉" }));
    expect(rain.kind).toBe("command");
    if (rain.kind !== "command") return;
    expect(rain.envelope.payload).toEqual({ weather: "无雨" });

    const rainStop = compileDirectorCommand(baseInput({ utterance: "雨停了" }));
    expect(rainStop.kind).toBe("command");
    if (rainStop.kind !== "command") return;
    expect(rainStop.envelope.payload).toEqual({ weather: "无雨" });
  });

  it("并列连词 、 不拆子句:枚举式移除不反向写成肯定值", () => {
    // 「去掉雾、雪」的 、 是并列:若按子句边界切分,首个匹配词会脱离否定
    // 子句,把用户要移除的天气写成肯定值。
    const plan = compileDirectorCommand(baseInput({ utterance: "去掉雾、雪" }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.payload).toEqual({ weather: "无雪" });

    const mixed = compileDirectorCommand(baseInput({ utterance: "去掉雨、雾" }));
    expect(mixed.kind).toBe("command");
    if (mixed.kind !== "command") return;
    expect(mixed.envelope.payload).toEqual({ weather: "无雾" });
  });

  it("复合口令按子句判定否定:「去掉雾,改成晚上」移除雾且仍设定时间", () => {
    const plan = compileDirectorCommand(baseInput({ utterance: "去掉雾，改成晚上" }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_scene_context");
    // TIME_LABELS 把「晚上」归一为规范标签「夜晚」。
    expect(plan.envelope.payload).toEqual({ weather: "无雾", time_label: "夜晚" });
  });

  it("否定时间口令进入澄清而不是把否定值写进 time_label", () => {
    // 「不要夜晚」旧逻辑会写 time_label:"夜晚",与用户意图正好相反。
    const plan = compileDirectorCommand(baseInput({ utterance: "不要夜晚" }));
    expect(plan.kind === "clarify" || plan.kind === "unsupported").toBe(true);
    if (plan.kind === "command") return;
    expect(plan.reason).toContain("时间");
  });

  it("天气存在 + 否定时间:「不要下雨,不要夜晚」进澄清,时间不静默丢弃", () => {
    // 旧逻辑只在「否定时间且无天气」时澄清;天气子句可解析时,时间子句被
    // 静默丢进只改天气的 payload,用户的时间意图无声丢失。
    const plan = compileDirectorCommand(baseInput({ utterance: "不要下雨，不要夜晚" }));
    expect(plan.kind).toBe("clarify");
    if (plan.kind !== "clarify") return;
    expect(plan.reason).toContain("时间");
    // 天气子句的意向一并告知:用户改写口令后两部分都会生效。
    expect(plan.reason).toContain("无雨");

    // 正面天气 + 否定时间同样不产出只改天气的命令。
    const mixed = compileDirectorCommand(baseInput({ utterance: "下雨，不要夜晚" }));
    expect(mixed.kind).toBe("clarify");
    if (mixed.kind !== "clarify") return;
    expect(mixed.reason).toContain("时间");
  });

  it("角色名含天气字样（小雨）时不得把角色指令劫持为场景天气修改", () => {
    const rain = characterFixture({ id: "character-rain", primary_name: "小雨" });
    const panels = [panelFixture({
      characters: ["character-1", "character-rain"],
      character_presence: { "character-1": "VISIBLE", "character-rain": "VISIBLE" },
    })];
    const characters = [characterFixture(), rain];
    // 「去掉小雨」是移除角色，旧代码会因 /小雨/ 命中天气表而改成场景级
    // 「无雨」——影响后续所有抽卡，且用户要的角色编辑静默丢失。
    const remove = compileDirectorCommand(baseInput({
      panels, characters, selection: { kind: "character", characterId: "character-rain" },
      utterance: "去掉小雨",
    }));
    expect(remove.kind).toBe("command");
    if (remove.kind !== "command") return;
    expect(remove.envelope.operation).toBe("update_panel_cast");
    // 同一角色在场时，真正的天气口令仍然解析为场景上下文（剥离不误伤）。
    const weather = compileDirectorCommand(baseInput({
      panels, characters, selection: { kind: "panel", panelId: "panel-1" },
      utterance: "雨下大一点",
    }));
    expect(weather.kind).toBe("command");
    if (weather.kind !== "command") return;
    expect(weather.envelope.operation).toBe("update_scene_context");
    expect(weather.envelope.payload).toEqual({ weather: "大雨" });
  });

  it("D4 「让她微笑」且页上两名角色时必须先点角色芯片，不产出命令", () => {
    const panels = [
      panelFixture({ characters: ["character-1", "character-2"], character_presence: { "character-1": "VISIBLE", "character-2": "VISIBLE" } }),
    ];
    const plan = compileDirectorCommand(baseInput({
      panels,
      characters: [characterFixture(), characterFixture({ id: "character-2", primary_name: "苏离" })],
      utterance: "让她微笑",
    }));
    expect(plan.kind).toBe("clarify");
    if (plan.kind !== "clarify") return;
    expect(plan.options.map((option) => option.kind)).toEqual(["character", "character"]);
    expect(plan.options.map((option) => option.label)).toEqual(["林澈", "苏离"]);
  });

  it("D4 点击角色芯片后同一口令可预览为表情命令", () => {
    const panels = [
      panelFixture({ characters: ["character-1", "character-2"], character_presence: { "character-1": "VISIBLE", "character-2": "VISIBLE" } }),
    ];
    const plan = compileDirectorCommand(baseInput({
      panels,
      characters: [characterFixture(), characterFixture({ id: "character-2", primary_name: "苏离" })],
      selection: { kind: "character", characterId: "character-2" },
      utterance: "让她微笑",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_panel_cast");
    expect(plan.envelope.payload).toEqual({ expressions: { "character-2": "微笑" } });
    expect(plan.envelope.expected_version).toEqual({ scope: "panel", value: 4 });
  });

  it("选中气泡 + 台词引号改写编成 update_dialogue，目标含气泡 id", () => {
    const plan = compileDirectorCommand(baseInput({
      selection: { kind: "dialogue", dialogueId: "dialogue-1", panelId: "panel-1" },
      utterance: "台词改成「我没事」",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_dialogue");
    expect(plan.envelope.payload).toEqual({ target_text: "我没事" });
    expect(plan.envelope.target).toMatchObject({
      project_id: "project-1",
      page_id: "page-1",
      panel_id: "panel-1",
      dialogue_id: "dialogue-1",
    });
    expect(plan.scopeLabel).toContain("气泡 1");
  });

  it("口令里写明「第 N 格」时无需选区也能定位格", () => {
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture(), panelFixture({ id: "panel-2", reading_order: 2 })],
      utterance: "第 2 格改成近景",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_panel_shot");
    expect(plan.envelope.payload).toEqual({ shot_type: "close_up" });
    expect(plan.envelope.target.panel_id).toBe("panel-2");
  });

  it("「改成 6 格」是高风险整页命令，版本取 page.version", () => {
    const plan = compileDirectorCommand(baseInput({ utterance: "改成 6 格" }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_page_layout");
    expect(plan.envelope.payload).toEqual({ panel_count: 6, layout_mode: "dynamic" });
    expect(plan.envelope.expected_version).toEqual({ scope: "page", value: 1 });
    expect(plan.risk).toBe("high");
  });

  it("D11 页面已有生成任务进行中时整页命令被拦下且不产出命令", () => {
    const plan = compileDirectorCommand(baseInput({
      utterance: "改成 6 格",
      pageGenerationPending: true,
    }));
    expect(plan.kind).toBe("blocked");
    if (plan.kind !== "blocked") return;
    expect(plan.reason).toContain("生成任务进行中");
  });

  it("D6/D13 重绘与 mask 意图指向局部编辑器，不产出任何命令", () => {
    for (const utterance of ["重画这一格", "把第三格局部重绘", "我想用蒙版涂一块"]) {
      const plan = compileDirectorCommand(baseInput({ utterance }));
      expect(plan.kind).toBe("unsupported");
      if (plan.kind !== "unsupported") continue;
      expect(plan.reason).toContain("在选区编辑");
      expect(plan.reason).toContain("regenerate_region");
      expect(plan.reason).toContain("不会静默整页重绘");
    }
  });

  it("加入入镜角色时保留格内已有 VISIBLE 角色", () => {
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture({
        characters: ["character-2"],
        character_presence: { "character-2": "VISIBLE" },
      })],
      characters: [characterFixture(), characterFixture({ id: "character-2", primary_name: "苏离" })],
      utterance: "让林澈出现在第 1 格",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.payload).toEqual({
      characters: ["character-2", "character-1"],
      character_presence: { "character-2": "VISIBLE", "character-1": "VISIBLE" },
    });
  });

  it("改单人表情时合并而不是覆盖整格 expressions", () => {
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture({
        characters: ["character-1", "character-2"],
        character_presence: { "character-1": "VISIBLE", "character-2": "VISIBLE" },
        expressions: { "character-1": "皱眉", "character-2": "微笑" },
      })],
      characters: [characterFixture(), characterFixture({ id: "character-2", primary_name: "苏离" })],
      selection: { kind: "character", characterId: "character-1" },
      utterance: "让他惊讶",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.payload).toEqual({
      expressions: { "character-1": "惊讶", "character-2": "微笑" },
    });
  });

  it("让角色加入第 2 格编成 update_panel_cast（characters + presence）", () => {
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture(), panelFixture({ id: "panel-2", reading_order: 2, characters: [], character_presence: {} })],
      utterance: "让林澈出现在第 2 格",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.operation).toBe("update_panel_cast");
    expect(plan.envelope.payload).toEqual({
      characters: ["character-1"],
      character_presence: { "character-1": "VISIBLE" },
    });
    expect(plan.envelope.target.panel_id).toBe("panel-2");
  });

  it("本页没有剧本场景时天气口令明确拒绝", () => {
    const plan = compileDirectorCommand(baseInput({
      page: pageFixture({ scene_ids: [] }),
      utterance: "雨下大一点",
    }));
    expect(plan.kind).toBe("unsupported");
    if (plan.kind !== "unsupported") return;
    expect(plan.reason).toContain("没有关联剧本场景");
  });

  it("envelope 完整：schema_version 1、uuid id、规则桩来源标记", () => {
    const plan = compileDirectorCommand(baseInput({ utterance: "改成 6 格", retryOfCommandId: "cmd-origin" }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    const envelope = plan.envelope;
    expect(envelope.schema_version).toBe(1);
    expect(envelope.command_id).toMatch(UUID_PATTERN);
    expect(envelope.command_group_id).toMatch(UUID_PATTERN);
    expect(envelope.command_id).not.toBe(envelope.command_group_id);
    expect(envelope.created_at).toBe("2026-09-03T00:00:00Z");
    expect(envelope.retry_of_command_id).toBe("cmd-origin");
    expect(envelope.source.user_prompt).toBe("改成 6 格");
    expect(envelope.source.model).toBeNull();
    expect(envelope.source.raw_output_id).toBe(DIRECTOR_RAW_OUTPUT_ID);
  });

  it("选区存在但规则无法识别时明确拒绝，而不是猜一个操作", () => {
    const plan = compileDirectorCommand(baseInput({
      selection: { kind: "panel", panelId: "panel-1" },
      utterance: "让她转身",
    }));
    expect(plan.kind).toBe("unsupported");
    if (plan.kind !== "unsupported") return;
    expect(plan.reason).toContain("规则桩无法");
  });

  it("禁止改写的气泡给出明确拒绝理由", () => {
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture({
        dialogues: [{
          id: "dialogue-1",
          panel_id: "panel-1",
          speaker_character_id: "character-1",
          target_text: "你好",
          reading_order: 1,
          text_direction: "horizontal",
          region: {},
          rewrite_forbidden: true,
          bubble: null,
        }],
      })],
      selection: { kind: "dialogue", dialogueId: "dialogue-1", panelId: "panel-1" },
      utterance: "台词改成「我没事」",
    }));
    expect(plan.kind).toBe("unsupported");
    if (plan.kind !== "unsupported") return;
    expect(plan.reason).toContain("禁止改写");
  });

  it("没有口令时提示输入且无命令", () => {
    const plan = compileDirectorCommand(baseInput({ utterance: "   " }));
    expect(plan.kind).toBe("clarify");
  });

  it("选中的气泡被改写命令锁定，即使格内还有其他气泡", () => {
    const first = panelFixture().dialogues[0];
    const second = { ...first, id: "dialogue-2", reading_order: 2, target_text: "第二句" };
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture({ dialogues: [first, second] })],
      selection: { kind: "dialogue", dialogueId: "dialogue-2", panelId: "panel-1" },
      utterance: "台词改成「换一句」",
    }));
    expect(plan.kind).toBe("command");
    if (plan.kind !== "command") return;
    expect(plan.envelope.target.dialogue_id).toBe("dialogue-2");
  });

  it("已选格但格内两个气泡且未指明句序时列出气泡选项", () => {
    const first = panelFixture().dialogues[0];
    const second = { ...first, id: "dialogue-2", reading_order: 2, target_text: "第二句" };
    const plan = compileDirectorCommand(baseInput({
      panels: [panelFixture({ dialogues: [first, second] })],
      selection: { kind: "panel", panelId: "panel-1" },
      utterance: "台词改成「换一句」",
    }));
    expect(plan.kind).toBe("clarify");
    if (plan.kind !== "clarify") return;
    expect(plan.options.map((option) => option.id)).toEqual(["dialogue-1", "dialogue-2"]);
  });
});

describe("selection helpers", () => {
  it("selectionFromTarget 还原格与气泡选区", async () => {
    const { selectionFromTarget } = await import("@/components/project-workspace/use-director-workspace");
    expect(selectionFromTarget({ panel_id: "panel-1", dialogue_id: "dialogue-1" })).toEqual({
      kind: "dialogue",
      dialogueId: "dialogue-1",
      panelId: "panel-1",
    });
    expect(selectionFromTarget({ panel_id: "panel-1" })).toEqual({ kind: "panel", panelId: "panel-1" });
    expect(selectionFromTarget({})).toBeNull();
  });
});

describe("DirectorScopeSelection 形状", () => {
  it("四种作用域类型可构造", () => {
    const selections: DirectorScopeSelection[] = [
      { kind: "page" },
      { kind: "panel", panelId: "p" },
      { kind: "dialogue", dialogueId: "d", panelId: "p" },
      { kind: "character", characterId: "c" },
    ];
    expect(selections).toHaveLength(4);
  });
});
