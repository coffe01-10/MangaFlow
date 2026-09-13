import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  type Character,
  type DirectorCommand,
  type DirectorCommandGroup,
  type MangaPage,
  type ScriptScene,
  type StoryboardPanel,
} from "@/lib/api";

import { useDirectorWorkspace } from "./use-director-workspace";

const proposeApi = vi.spyOn(api, "directorProposeCommandGroup");
const groupsApi = vi.spyOn(api, "directorCommandGroups");

function pageFixture(overrides: Partial<MangaPage> = {}): MangaPage {
  return {
    id: "page-1",
    chapter_id: "chapter-1",
    page_number: 1,
    revision_no: 1,
    page_function: "dialogue",
    panel_count: 2,
    reading_direction: "rtl",
    resolution: "1K",
    status: "PLANNED",
    estimated_text_chars: 40,
    estimated_bubbles: 1,
    source_coverage: { complete: true, layout_mode: "dynamic", ranges: [{ text: "巷口灯还亮着" }] },
    selected_candidate_id: null,
    storyboard_version: 2,
    selected_candidate_ack_version: 1,
    continuity_status: "PASSED",
    scene_ids: ["scene-1"],
    beat_ids: [],
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
    aliases: [],
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

const baseCommand: DirectorCommand = {
  command_id: "cmd-1",
  command_group_id: "group-1",
  operation: "update_panel_shot",
  status: "PREVIEWED",
  target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
  expected_version: { scope: "panel", value: 4 },
  payload: { shot_type: "close_up" },
  source: { user_prompt: "第 1 格改成近景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
  diff: { shot_type: { before: "medium_close_up", after: "close_up" } },
  error: null,
  retry_of_command_id: null,
  inverse_of_command_id: null,
  storyboard_version_after: null,
  version: 1,
};

function groupFixture(overrides: Partial<DirectorCommandGroup> = {}): DirectorCommandGroup {
  const { commands = [baseCommand], ...rest } = overrides;
  return {
    id: "row-1",
    project_id: "project-1",
    command_group_id: "group-1",
    page_id: "page-1",
    status: "PREVIEWED",
    idempotent_replay: false,
    commands,
    version: 1,
    ...rest,
  };
}

function renderDirectorHook() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return renderHook(() => useDirectorWorkspace({
    id: "project-1",
    page: pageFixture(),
    panels: [panelFixture()],
    scenes: [sceneFixture()],
    characters: [characterFixture()],
  }), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
}

describe("useDirectorWorkspace propose 失败回滚解析文案（#648）", () => {
  beforeEach(() => {
    proposeApi.mockReset();
    groupsApi.mockReset().mockResolvedValue([]);
  });

  it("已有预览时 propose 失败：回滚 plan 为 null，预览卡回到旧命令的原文渲染", async () => {
    // 挂起的 propose：先观察在途状态（新 plan 已写入、旧 group 保留）。
    let rejectPropose: ((reason: Error) => void) | null = null;
    proposeApi.mockImplementation(
      () => new Promise<DirectorCommandGroup>((_resolve, reject) => {
        rejectPropose = reject;
      }),
    );
    const { result } = renderDirectorHook();
    // 复现失败场景的前半段：历史里已有 PREVIEWED 命令预览（重开历史组）。
    act(() => {
      result.current.reopenGroup(groupFixture());
    });
    expect(result.current.preview?.command_group_id).toBe("group-1");
    expect(result.current.previewPlan).toBeNull();
    // 输入新指令（整页命令）点「预览」。
    act(() => {
      result.current.setDraft({ utterance: "改成 6 格", retryOfCommandId: null });
    });
    act(() => {
      result.current.submitForPreview();
    });
    await waitFor(() => {
      expect(proposeApi).toHaveBeenCalledTimes(1);
    });
    // 在途：文案已切到新命令的解析（高风险整页），diff/执行按钮仍是旧组——
    // 这正是失败后必须回滚的错位来源。
    expect(result.current.previewPlan?.intentLabel).toBe("整页布局");
    expect(result.current.previewPlan?.risk).toBe("high");
    expect(result.current.preview?.command_group_id).toBe("group-1");
    // propose 失败（网络/服务端）：错误 notice 出现，预览卡不关。
    await act(async () => {
      rejectPropose!(new Error("propose 服务暂不可用"));
    });
    await waitFor(() => {
      expect(result.current.propose.isError).toBe(true);
    });
    expect(result.current.notice).toBe("propose 服务暂不可用");
    // #648 错位修复断言：previewPlan 回滚为 null（渲染回退到 operation 标签 +
    // 命令原文），预览与可执行的命令仍是同一旧命令（cmd-1 / 第 1 格改成近景）。
    expect(result.current.previewPlan).toBeNull();
    expect(result.current.preview?.command_group_id).toBe("group-1");
    expect(result.current.preview?.commands[0].command_id).toBe("cmd-1");
    expect(result.current.preview?.commands[0].source.user_prompt).toBe("第 1 格改成近景");
  });

  it("无既有预览时 propose 失败：只呈现 notice，不产生预览", async () => {
    proposeApi.mockRejectedValue(new Error("网络中断"));
    const { result } = renderDirectorHook();
    act(() => {
      result.current.setDraft({ utterance: "改成 6 格", retryOfCommandId: null });
    });
    act(() => {
      result.current.submitForPreview();
    });
    await waitFor(() => {
      expect(result.current.propose.isError).toBe(true);
    });
    expect(result.current.notice).toBe("网络中断");
    expect(result.current.preview).toBeNull();
    expect(result.current.previewPlan).toBeNull();
    expect(result.current.planState).toBeNull();
  });
});
