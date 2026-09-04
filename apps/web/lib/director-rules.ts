// V02-41B director rule stub. Compiles "scope selection + short utterance"
// into one whitelisted V02-40 command envelope. This is deliberately NOT a
// model: V02-40 propose only accepts structured commands, no NL parsing or
// LLM call exists, and the UI must label every plan as 规则解析，非模型.
// Pixel-level intents (redraw / mask) are refused here with an explicit
// reason so the UI can never silently POST a whole-page regeneration.

import type {
  Character,
  DirectorCommandEnvelope,
  DirectorOperation,
  MangaPage,
  ScriptScene,
  StoryboardPanel,
} from "@/lib/api";

export const DIRECTOR_PARSER_LABEL = "规则解析，非模型";
export const DIRECTOR_RAW_OUTPUT_ID = "rule_stub_v1";

export type DirectorScopeSelection =
  | { kind: "page" }
  | { kind: "panel"; panelId: string }
  | { kind: "dialogue"; dialogueId: string; panelId: string }
  | { kind: "character"; characterId: string };

export interface DirectorClarifyOption {
  kind: "page" | "panel" | "dialogue" | "character";
  id: string;
  panelId?: string;
  label: string;
}

export type DirectorPlan =
  | {
    kind: "command";
    envelope: DirectorCommandEnvelope;
    intentLabel: string;
    scopeLabel: string;
    summary: string;
    risk: "low" | "medium" | "high";
  }
  | { kind: "clarify"; reason: string; options: DirectorClarifyOption[] }
  | { kind: "blocked"; reason: string }
  | { kind: "unsupported"; reason: string };

export interface DirectorRuleInput {
  projectId: string;
  page: MangaPage;
  panels: StoryboardPanel[];
  scenes: ScriptScene[];
  characters: Character[];
  selection: DirectorScopeSelection | null;
  utterance: string;
  pageGenerationPending?: boolean;
  retryOfCommandId?: string | null;
  newId?: () => string;
  now?: () => string;
}

const CN_DIGITS: Record<string, number> = {
  一: 1, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8,
};

const SHOT_TYPES: [RegExp, string, string][] = [
  [/大特写/, "extreme_close_up", "大特写"],
  [/中近景/, "medium_close_up", "中近景"],
  [/特写|近景/, "close_up", "近景"],
  [/全景/, "wide_action", "全景"],
  [/远景|建立镜头/, "establishing", "远景"],
];

const CAMERA_ANGLES: [RegExp, string, string][] = [
  [/俯拍|俯视|俯角/, "high_angle", "俯拍"],
  [/仰拍|仰视|仰角/, "low_angle", "仰拍"],
  [/倾斜|斜角/, "dutch_angle", "倾斜镜头"],
  [/越肩/, "over_shoulder", "越肩"],
  [/平视/, "eye_level", "平视"],
];

const TIME_LABELS: [RegExp, string][] = [
  [/深夜|半夜/, "深夜"],
  [/夜晚|夜里|入夜|晚上/, "夜晚"],
  [/清晨|黎明/, "清晨"],
  [/正午|中午/, "正午"],
  [/黄昏|傍晚/, "傍晚"],
  [/白天|日间/, "白天"],
];

// Ordered: specific weathers first so 「雨下大」 resolves to 大雨, not 雨.
const WEATHER_LABELS: [RegExp, string][] = [
  [/暴雨/, "暴雨"],
  [/雷雨|打雷|雷电/, "雷雨"],
  [/雨下大|雨大|大雨/, "大雨"],
  [/雨小|小雨|毛毛雨/, "小雨"],
  [/下雪|降雪|雪/, "雪"],
  [/起雾|大雾|雾/, "雾"],
  [/阴天|转阴/, "阴"],
  [/放晴|晴天|晴/, "晴"],
  [/雨/, "雨"],
];

const EXPRESSIONS: [RegExp, string][] = [
  [/微笑|笑了|露出笑容/, "微笑"],
  [/皱眉|皱起眉头/, "皱眉"],
  [/惊讶|吃惊|震惊/, "惊讶"],
  [/愤怒|生气|发怒/, "愤怒"],
  [/哭泣|哭了|落泪/, "哭泣"],
  [/害怕|恐惧|惊恐/, "恐惧"],
  [/悲伤|难过/, "悲伤"],
  [/开心|高兴/, "开心"],
];

function parseCount(raw: string): number | null {
  if (/^[3-8]$/.test(raw)) return Number(raw);
  return CN_DIGITS[raw] ?? null;
}

/** Panel / bubble ordinals run 1–8; panel counts are 3–8. */
function parseOrdinal(raw: string): number | null {
  if (/^[1-8]$/.test(raw)) return Number(raw);
  return CN_DIGITS[raw] ?? null;
}

function parsePanelNumber(text: string): number | null {
  const match = text.match(/第\s*([0-9]|[一二三四五六七八])\s*格/);
  if (!match) return null;
  return parseOrdinal(match[1]);
}

function orderedPanels(panels: StoryboardPanel[]): StoryboardPanel[] {
  return [...panels].sort((left, right) => left.reading_order - right.reading_order);
}

function panelByOrder(panels: StoryboardPanel[], order: number): StoryboardPanel | null {
  return panels.find((panel) => panel.reading_order === order) ?? null;
}

function orderedDialogues(panel: StoryboardPanel) {
  return [...panel.dialogues].sort((left, right) => left.reading_order - right.reading_order);
}

function characterLabel(characters: Character[], characterId: string): string {
  return characters.find((item) => item.id === characterId)?.primary_name ?? "未知角色";
}

function visibleCharacterIds(panels: StoryboardPanel[]): string[] {
  const ids: string[] = [];
  for (const panel of orderedPanels(panels)) {
    for (const characterId of panel.characters ?? []) {
      if (!ids.includes(characterId)) ids.push(characterId);
    }
  }
  return ids;
}

/** Names (primary + aliases) that literally appear in the utterance. */
function mentionedCharacters(utterance: string, characters: Character[]): Character[] {
  return characters.filter((character) =>
    [character.primary_name, ...(character.aliases ?? [])]
      .filter(Boolean)
      .some((name) => utterance.includes(name)),
  );
}

function panelClarifyOptions(panels: StoryboardPanel[]): DirectorClarifyOption[] {
  return orderedPanels(panels).map((panel) => ({
    kind: "panel" as const,
    id: panel.id,
    label: `格 ${panel.reading_order}`,
  }));
}

function dialogueClarifyOptions(panel: StoryboardPanel): DirectorClarifyOption[] {
  return orderedDialogues(panel).map((dialogue, index) => ({
    kind: "dialogue" as const,
    id: dialogue.id,
    panelId: panel.id,
    label: `格 ${panel.reading_order} · 气泡 ${index + 1}`,
  }));
}

function characterClarifyOptions(
  characters: Character[],
  characterIds: string[],
): DirectorClarifyOption[] {
  return characterIds.map((characterId) => ({
    kind: "character" as const,
    id: characterId,
    label: characterLabel(characters, characterId),
  }));
}

function quotedText(utterance: string): string | null {
  const quoted = utterance.match(/[「『“"]([^」』”"]{1,200})[」』”"]/);
  if (quoted?.[1]) return quoted[1].trim();
  const bare = utterance.match(/(?:台词|对白|气泡)(?:内容|文字)?(?:改成|改为|换成)\s*([^，。！？；\s]{1,200})/);
  if (bare?.[1]) return bare[1].trim();
  return null;
}

interface ResolvedTarget {
  panel: StoryboardPanel | null;
  dialogueIndex: number | null;
  clarify?: DirectorClarifyOption[];
  reason?: string;
}

/**
 * Resolves which panel a command should target: the explicit 「第N格」 in the
 * utterance wins, then the current scope selection. Dialogue commands also
 * resolve the bubble index the same way.
 */
function resolvePanelTarget(
  panels: StoryboardPanel[],
  selection: DirectorScopeSelection | null,
  utterance: string,
  needsDialogue: boolean,
): ResolvedTarget {
  const ordered = orderedPanels(panels);
  const mentionedOrder = parsePanelNumber(utterance);
  let panel: StoryboardPanel | null = null;
  if (mentionedOrder != null) {
    panel = panelByOrder(ordered, mentionedOrder);
    if (!panel) {
      return { panel: null, dialogueIndex: null, reason: `本页没有第 ${mentionedOrder} 格` };
    }
  } else if (selection?.kind === "dialogue") {
    panel = panels.find((item) => item.id === selection.panelId) ?? null;
  } else if (selection?.kind === "panel") {
    panel = panels.find((item) => item.id === selection.panelId) ?? null;
  } else if (selection?.kind === "character") {
    const owned = ordered.filter((item) => (item.characters ?? []).includes(selection.characterId));
    if (owned.length === 1) panel = owned[0];
  }
  if (!panel) {
    if (!ordered.length) {
      return { panel: null, dialogueIndex: null, reason: "当前页还没有分镜格，请先完成分镜" };
    }
    return {
      panel: null,
      dialogueIndex: null,
      clarify: panelClarifyOptions(panels),
      reason: needsDialogue
        ? "请选择目标：点击格芯片，或在指令里写明「第 N 格」"
        : "请先点击一个格芯片（或在指令里写明「第 N 格」）再下指令",
    };
  }
  let dialogueIndex: number | null = null;
  if (needsDialogue) {
    const dialogues = orderedDialogues(panel);
    if (!dialogues.length) {
      return { panel, dialogueIndex: null, reason: `格 ${panel.reading_order} 没有气泡` };
    }
    if (selection?.kind === "dialogue") {
      dialogueIndex = dialogues.findIndex((item) => item.id === selection.dialogueId);
    }
    const bubbleMatch = utterance.match(/第\s*([0-9]|[一二三四五六七八])\s*(?:句|气泡|个气泡)/);
    if (dialogueIndex == null && bubbleMatch) {
      const bubbleOrder = parseOrdinal(bubbleMatch[1]);
      if (bubbleOrder != null && bubbleOrder >= 1 && bubbleOrder <= dialogues.length) {
        dialogueIndex = bubbleOrder - 1;
      }
    }
    if (dialogueIndex == null && dialogues.length > 1) {
      return {
        panel,
        dialogueIndex: null,
        clarify: dialogueClarifyOptions(panel),
        reason: `格 ${panel.reading_order} 有 ${dialogues.length} 个气泡，请点击气泡芯片确认目标`,
      };
    }
    if (dialogueIndex == null) dialogueIndex = 0;
  }
  return { panel, dialogueIndex };
}

/**
 * Resolves which character an utterance refers to. A literal name match is
 * unambiguous; pronouns or missing names with several on-page characters
 * must be clarified through the character chips (audit D4).
 */
function resolveCharacter(
  characters: Character[],
  panels: StoryboardPanel[],
  selection: DirectorScopeSelection | null,
  utterance: string,
): { character: Character | null; clarify?: DirectorClarifyOption[]; reason?: string } {
  const mentioned = mentionedCharacters(utterance, characters);
  if (mentioned.length === 1) return { character: mentioned[0] };
  if (mentioned.length > 1) {
    return {
      character: null,
      clarify: characterClarifyOptions(characters, mentioned.map((item) => item.id)),
      reason: "指令中匹配到多名角色，必须点击角色芯片确认目标",
    };
  }
  if (selection?.kind === "character") {
    const selected = characters.find((item) => item.id === selection.characterId);
    if (selected) return { character: selected };
  }
  const visible = visibleCharacterIds(panels);
  if (visible.length === 1) {
    return { character: characters.find((item) => item.id === visible[0]) ?? null };
  }
  if (!visible.length) {
    return { character: null, reason: "当前页分镜没有入镜角色" };
  }
  return {
    character: null,
    clarify: characterClarifyOptions(characters, visible),
    reason: "页上有多名角色，请点击角色芯片确认目标",
  };
}

function toClarify(resolved: { clarify?: DirectorClarifyOption[]; reason?: string }) {
  return resolved.clarify
    ? { kind: "clarify" as const, reason: resolved.reason ?? "请确认目标", options: resolved.clarify }
    : { kind: "unsupported" as const, reason: resolved.reason ?? "无法确定目标" };
}

export function compileDirectorCommand(input: DirectorRuleInput): DirectorPlan {
  const utterance = input.utterance.trim();
  if (!utterance) {
    return { kind: "clarify", reason: "请输入一句导演指令", options: [] };
  }

  // Pixel redraw / mask intents: the command bar only edits storyboard
  // fields — mask strokes cannot ride on a text command. Point at the local
  // edit shell (V02-43B) which compiles the drawn regions into a real
  // regenerate_region command; never silently fall back to a whole page.
  if (/重画|重绘|重新生成|重新抽|重抽|局部|选区|蒙版|mask|涂/.test(utterance)) {
    return {
      kind: "unsupported",
      reason:
        "局部重绘需要在图上画选区，命令栏编不了 mask。请点击下方「在选区编辑（mask 局部重绘）」进入局部编辑器：画好选区并确认预览后，会按 regenerate_region 生成派生候选。导演台不会静默整页重绘。",
    };
  }

  // Whole-page layout: 「改成 6 格」. Explicit verbs avoid matching 「第 3 格」.
  const layoutMatch = utterance.match(/(?:改成|改为|变成|换成|调整为?|分成|划分为?)\s*([3-8]|[一二三四五六七八])\s*格/);
  if (layoutMatch) {
    if (input.pageGenerationPending) {
      return {
        kind: "blocked",
        reason: "当前页已有生成任务进行中，请等当前图完成或取消后，再发整页命令",
      };
    }
    const panelCount = parseCount(layoutMatch[1]);
    if (panelCount == null || panelCount < 3 || panelCount > 8) {
      return { kind: "unsupported", reason: "每页格数只能在 3–8 格之间" };
    }
    return buildPlan(input, {
      operation: "update_page_layout",
      payload: { panel_count: panelCount, layout_mode: "dynamic" },
      target: { project_id: input.projectId, page_id: input.page.id },
      expectedVersion: { scope: "page", value: input.page.version },
      intentLabel: "整页布局",
      scopeLabel: `第 ${input.page.page_number} 页 · 整页`,
      summary: `把第 ${input.page.page_number} 页改为 ${panelCount} 格（动态布局）。整页命令风险高：改动后该页候选将过期。`,
      risk: "high",
    });
  }

  // Dialogue rewrite: 台词/对白/气泡 words or a quoted 「…」 after 改成/说.
  const hasDialogueWord = /台词|对白|气泡/.test(utterance);
  const hasQuote = /[「『“"]/.test(utterance);
  const dialogueIntent = hasDialogueWord
    || (hasQuote && /改成|改为|换成|说/.test(utterance));
  if (dialogueIntent) {
    const newText = quotedText(utterance);
    if (!newText) {
      if (hasDialogueWord) {
        return {
          kind: "clarify",
          reason: "请用引号写明新的台词内容，例如：台词改成「我没事」",
          options: [],
        };
      }
    } else {
      const resolved = resolvePanelTarget(input.panels, input.selection, utterance, true);
      if (resolved.clarify) return toClarify(resolved);
      if (!resolved.panel) return { kind: "unsupported", reason: resolved.reason ?? "无法确定目标" };
      const panel = resolved.panel!;
      const dialogues = orderedDialogues(panel);
      const dialogue = resolved.dialogueIndex != null ? dialogues[resolved.dialogueIndex] : null;
      if (!dialogue) {
        return { kind: "unsupported", reason: `格 ${panel.reading_order} 没有可选气泡` };
      }
      if (dialogue.rewrite_forbidden) {
        return {
          kind: "unsupported",
          reason: `格 ${panel.reading_order} 的气泡被标记为禁止改写，请在分镜编辑器处理`,
        };
      }
      return buildPlan(input, {
        operation: "update_dialogue",
        payload: { target_text: newText },
        target: {
          project_id: input.projectId,
          page_id: input.page.id,
          panel_id: panel.id,
          dialogue_id: dialogue.id,
        },
        expectedVersion: { scope: "panel", value: panel.version },
        intentLabel: "气泡台词",
        scopeLabel: `格 ${panel.reading_order} · 气泡 ${(resolved.dialogueIndex ?? 0) + 1}`,
        summary: `把格 ${panel.reading_order} 气泡${(resolved.dialogueIndex ?? 0) + 1} 的台词改为「${newText}」。`,
        risk: "low",
      });
    }
  }

  // Scene context (weather / time of day), before cast rules so 「去掉雨」
  // resolves as weather. Uses the page's primary scene — the same scene that
  // feeds generation input.
  const weather = WEATHER_LABELS.find(([pattern]) => pattern.test(utterance));
  const timeLabel = TIME_LABELS.find(([pattern]) => pattern.test(utterance));
  if (weather || timeLabel) {
    const sceneId = input.page.scene_ids[0] ?? null;
    const scene = sceneId ? input.scenes.find((item) => item.id === sceneId) ?? null : null;
    if (!scene) {
      return {
        kind: "unsupported",
        reason: "本页没有关联剧本场景，无法修改天气或时间；请先在剧本中绑定场景",
      };
    }
    const payload: Record<string, unknown> = {};
    const wantsGone = /去掉|移除|拿掉|停|不要/.test(utterance);
    if (weather) payload.weather = wantsGone ? "无雨" : weather[1];
    if (timeLabel) payload.time_label = timeLabel[1];
    const changes = [
      weather ? `天气→${wantsGone ? "无雨" : weather[1]}` : null,
      timeLabel ? `时间→${timeLabel[1]}` : null,
    ].filter(Boolean).join("、");
    const panelNote = parsePanelNumber(utterance);
    const scopeNote = panelNote != null ? `（含格 ${panelNote}）` : "";
    return buildPlan(input, {
      operation: "update_scene_context",
      payload,
      target: {
        project_id: input.projectId,
        page_id: input.page.id,
        scene_id: scene.id,
      },
      expectedVersion: { scope: "scene", value: scene.version },
      intentLabel: "场景上下文",
      scopeLabel: `主场景 · ${scene.location || `第 ${scene.ordinal} 场`}${scopeNote}`,
      summary: `把本页主场景的${changes}。天气/时间是场景级字段，会影响本页后续所有抽卡。`,
      risk: "medium",
    });
  }

  // Panel cast: remove / add a character on a panel.
  const removeMatch = utterance.match(/(?:去掉|移除|拿掉|删除|清空)\s*(.+)/);
  const addMatch = utterance.match(/(.+?)(?:出现在|登场|走进|加入|入镜)/);
  if (removeMatch || addMatch) {
    const characterResolution = resolveCharacter(input.characters, input.panels, input.selection, utterance);
    if (!characterResolution.character) {
      return characterResolution.clarify
        ? { kind: "clarify", reason: characterResolution.reason ?? "请确认角色", options: characterResolution.clarify }
        : { kind: "unsupported", reason: characterResolution.reason ?? "无法确定角色" };
    }
    const character = characterResolution.character;
    const resolved = resolvePanelTarget(input.panels, input.selection, utterance, false);
    if (resolved.clarify) return toClarify(resolved);
      if (!resolved.panel) return { kind: "unsupported", reason: resolved.reason ?? "无法确定目标" };
    const panel = resolved.panel!;
    const currentIds = panel.characters ?? [];
    let nextIds: string[];
    let nextPresence: Record<string, string>;
    let intentLabel: string;
    let summary: string;
    if (removeMatch) {
      if (!currentIds.includes(character.id)) {
        return {
          kind: "unsupported",
          reason: `格 ${panel.reading_order} 本来没有 ${character.primary_name}`,
        };
      }
      nextIds = currentIds.filter((id) => id !== character.id);
      nextPresence = Object.fromEntries(
        Object.entries(panel.character_presence ?? {}).filter(([id]) => id !== character.id),
      );
      intentLabel = "移除入镜角色";
      summary = `把 ${character.primary_name} 从格 ${panel.reading_order} 的入镜角色中移除。`;
    } else {
      if (currentIds.includes(character.id)) {
        return {
          kind: "unsupported",
          reason: `${character.primary_name} 已经在格 ${panel.reading_order} 的入镜角色里`,
        };
      }
      nextIds = [...currentIds, character.id];
      nextPresence = {
        ...(panel.character_presence ?? {}),
        [character.id]: "VISIBLE",
      };
      intentLabel = "加入入镜角色";
      summary = `让 ${character.primary_name} 出现在格 ${panel.reading_order}。`;
    }
    return buildPlan(input, {
      operation: "update_panel_cast",
      payload: { characters: nextIds, character_presence: nextPresence },
      target: {
        project_id: input.projectId,
        page_id: input.page.id,
        panel_id: panel.id,
      },
      expectedVersion: { scope: "panel", value: panel.version },
      intentLabel,
      scopeLabel: `格 ${panel.reading_order}`,
      summary,
      risk: "medium",
    });
  }

  // Expression: 让X在格N微笑 / 格N里X皱眉. The panel must already contain the
  // character, otherwise accept would 409 (表情只能指定给本格出现的角色).
  const expression = EXPRESSIONS.find(([pattern]) => pattern.test(utterance));
  if (expression) {
    const characterResolution = resolveCharacter(input.characters, input.panels, input.selection, utterance);
    if (!characterResolution.character) {
      return characterResolution.clarify
        ? { kind: "clarify", reason: characterResolution.reason ?? "请确认角色", options: characterResolution.clarify }
        : { kind: "unsupported", reason: characterResolution.reason ?? "无法确定角色" };
    }
    const character = characterResolution.character;
    const resolved = resolvePanelTarget(input.panels, input.selection, utterance, false);
    if (resolved.clarify) return toClarify(resolved);
      if (!resolved.panel) return { kind: "unsupported", reason: resolved.reason ?? "无法确定目标" };
    const panel = resolved.panel!;
    if (!(panel.characters ?? []).includes(character.id)) {
      return {
        kind: "unsupported",
        reason: `格 ${panel.reading_order} 里没有 ${character.primary_name}。可以先让 ${character.primary_name} 出场，再改表情。`,
      };
    }
    return buildPlan(input, {
      operation: "update_panel_cast",
      payload: {
        expressions: { ...(panel.expressions ?? {}), [character.id]: expression[1] },
      },
      target: {
        project_id: input.projectId,
        page_id: input.page.id,
        panel_id: panel.id,
      },
      expectedVersion: { scope: "panel", value: panel.version },
      intentLabel: "角色表情",
      scopeLabel: `格 ${panel.reading_order} · ${character.primary_name}`,
      summary: `把格 ${panel.reading_order} 中 ${character.primary_name} 的表情改为「${expression[1]}」。`,
      risk: "low",
    });
  }

  // Shot / camera on a panel.
  const shot = SHOT_TYPES.find(([pattern]) => pattern.test(utterance));
  const camera = CAMERA_ANGLES.find(([pattern]) => pattern.test(utterance));
  if (shot || camera) {
    const resolved = resolvePanelTarget(input.panels, input.selection, utterance, false);
    if (resolved.clarify) return toClarify(resolved);
      if (!resolved.panel) return { kind: "unsupported", reason: resolved.reason ?? "无法确定目标" };
    const panel = resolved.panel!;
    const payload: Record<string, unknown> = {};
    if (shot) payload.shot_type = shot[1];
    if (camera) payload.camera_angle = camera[1];
    const changes = [
      shot ? `景别→${shot[2]}` : null,
      camera ? `镜头角度→${camera[2]}` : null,
    ].filter(Boolean).join("、");
    return buildPlan(input, {
      operation: "update_panel_shot",
      payload,
      target: {
        project_id: input.projectId,
        page_id: input.page.id,
        panel_id: panel.id,
      },
      expectedVersion: { scope: "panel", value: panel.version },
      intentLabel: "镜头景别",
      scopeLabel: `格 ${panel.reading_order}`,
      summary: `把格 ${panel.reading_order} 的${changes}。`,
      risk: "medium",
    });
  }

  // Nothing matched: with no scope the clarification layer lists the page and
  // panels (audit D2); with a scope selected, refuse honestly instead of
  // guessing an operation.
  if (!input.selection) {
    return {
      kind: "clarify",
      reason: "这条指令还没有明确目标。请点击下方作用域芯片，或换一种写法（如：第 3 格改成近景、改成 6 格、台词改成「……」）",
      options: [
        { kind: "page", id: "page", label: "整页" },
        ...panelClarifyOptions(input.panels),
      ],
    };
  }
  return {
    kind: "unsupported",
    reason:
      "规则桩无法把这条指令编成白名单命令。当前支持：格的景别/镜头、气泡台词改写、入镜角色增删、角色表情、场景天气/时间、整页格数。分镜字段也可以在分镜编辑器直接修改。",
  };
}

function randomId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // jsdom-safe RFC 4122 v4 fallback for environments without randomUUID.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const random = Math.trunc(Math.random() * 16);
    const value = char === "x" ? random : (random & 0x3) | 0x8;
    return value.toString(16);
  });
}

function buildPlan(
  input: DirectorRuleInput,
  spec: {
    operation: DirectorOperation;
    payload: Record<string, unknown>;
    target: DirectorCommandEnvelope["target"];
    expectedVersion: DirectorCommandEnvelope["expected_version"];
    intentLabel: string;
    scopeLabel: string;
    summary: string;
    risk: "low" | "medium" | "high";
  },
): DirectorPlan {
  const newId = input.newId ?? randomId;
  const now = input.now ?? (() => new Date().toISOString());
  const envelope: DirectorCommandEnvelope = {
    schema_version: 1,
    command_id: newId(),
    command_group_id: newId(),
    created_at: now(),
    target: spec.target,
    expected_version: spec.expectedVersion,
    retry_of_command_id: input.retryOfCommandId ?? null,
    operation: spec.operation,
    payload: spec.payload,
    source: {
      user_prompt: input.utterance.trim(),
      reference_asset_ids: [],
      model: null,
      raw_output_id: DIRECTOR_RAW_OUTPUT_ID,
    },
  };
  return {
    kind: "command",
    envelope,
    intentLabel: spec.intentLabel,
    scopeLabel: spec.scopeLabel,
    summary: spec.summary,
    risk: spec.risk,
  };
}
