// Synthetic 100-node storyboard fixture (V02-32). Purely in-memory: the
// product contract caps real pages at 3–8 panels with at most 8 bubbles per
// page (audit §4), so a 100-object page can never be persisted — this builder
// only feeds tests and the client-side `?stress=100` render harness, and never
// touches the API.
//
// Node accounting: 20 panel outlines + 20 panels × 4 bubbles = 20 + 80 = 100
// hittable objects (1 node = 1 hittable panel or bubble).
import type {
  BubbleGeometryShape,
  MangaPage,
  NormalizedRect,
  Storyboard,
  StoryboardPanel,
} from "@/lib/api";

export const STRESS_NODE_COUNT = 100;
export const STRESS_PANEL_COUNT = 20;
export const STRESS_BUBBLES_PER_PANEL = 4;

const COLUMNS = 4;
const ROWS = 5;
const MARGIN = 0.03;
const GAP = 0.02;
const PANEL_WIDTH = (1 - MARGIN * 2 - GAP * (COLUMNS - 1)) / COLUMNS;
const PANEL_HEIGHT = (1 - MARGIN * 2 - GAP * (ROWS - 1)) / ROWS;

/** Panel-relative bubble rects: a 2×2 placement inside each panel. */
const BUBBLE_SLOTS = [
  { x: 0.05, y: 0.06 },
  { x: 0.55, y: 0.06 },
  { x: 0.05, y: 0.62 },
  { x: 0.55, y: 0.62 },
];
const BUBBLE_WIDTH = 0.4;
const BUBBLE_HEIGHT = 0.28;

export const stressPage: MangaPage = {
  id: "stress-page",
  chapter_id: "stress-chapter",
  page_number: 1,
  revision_no: 1,
  page_function: "narrative",
  panel_count: STRESS_PANEL_COUNT,
  reading_direction: "rtl",
  resolution: "1K",
  status: "READY",
  estimated_text_chars: 0,
  estimated_bubbles: STRESS_PANEL_COUNT * STRESS_BUBBLES_PER_PANEL,
  source_coverage: { layout_mode: "dynamic" },
  selected_candidate_id: null,
  storyboard_version: 1,
  selected_candidate_ack_version: null,
  continuity_status: "READY",
  scene_ids: [],
  beat_ids: [],
  version: 1,
  canvas: { width_mm: 182, height_mm: 257, bleed_mm: 3, safe_mm: 5, unit: "mm" },
};

export function stressPanelRect(index: number): NormalizedRect {
  const column = index % COLUMNS;
  const row = Math.floor(index / COLUMNS);
  return {
    x: MARGIN + column * (PANEL_WIDTH + GAP),
    y: MARGIN + row * (PANEL_HEIGHT + GAP),
    width: PANEL_WIDTH,
    height: PANEL_HEIGHT,
  };
}

export function buildStressStoryboard(): Storyboard {
  const panels: StoryboardPanel[] = [];
  for (let index = 0; index < STRESS_PANEL_COUNT; index++) {
    const bounds = stressPanelRect(index);
    // Fresh literal: StoryboardPanel.bounds is Record<string, number>, and the
    // NormalizedRect interface carries no implicit index signature.
    const boundsRecord = { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
    const readingOrder = index + 1;
    const dialogues = Array.from({ length: STRESS_BUBBLES_PER_PANEL }, (_, slot) => {
      const origin = BUBBLE_SLOTS[slot];
      const rect: NormalizedRect = {
        x: bounds.x + origin.x * bounds.width,
        y: bounds.y + origin.y * bounds.height,
        width: BUBBLE_WIDTH * bounds.width,
        height: BUBBLE_HEIGHT * bounds.height,
      };
      const bubble: BubbleGeometryShape = { type: "rect", rect, rotation: 0 };
      return {
        id: `stress-dlg-${index}-${slot}`,
        panel_id: `stress-panel-${index}`,
        speaker_character_id: null,
        target_text: `压测气泡 ${index}-${slot}`,
        reading_order: slot + 1,
        text_direction: "vertical" as const,
        region: { preferred: "upper_inner" },
        rewrite_forbidden: false,
        bubble,
      };
    });
    panels.push({
      id: `stress-panel-${index}`,
      page_id: stressPage.id,
      reading_order: readingOrder,
      bounds: boundsRecord,
      shot_type: "establishing",
      camera_angle: "eye_level",
      camera_height: "eye_level",
      characters: [],
      character_presence: {},
      props: [],
      outfits: {},
      actions: { script_action: `压测格 ${readingOrder}` },
      expressions: {},
      background: "",
      bubble_regions: [],
      sound_effects: [],
      bleed: false,
      borderless: false,
      locked_fields: [],
      version: 1,
      geometry: { type: "rect", rect: bounds, rotation: 0, z_order: readingOrder },
      dialogues,
    });
  }
  return { page: stressPage, panels, candidate_count: 0 };
}
