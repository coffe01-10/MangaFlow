import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  api,
  ApiError,
  type Character,
  type DirectorCommand,
  type DirectorCommandGroup,
  type MangaPage,
  type PageCandidate,
  type ScriptScene,
  type StoryboardPanel,
} from "@/lib/api";

import { DirectorWorkspace } from "./director-workspace";

const proposeApi = vi.spyOn(api, "directorProposeCommandGroup");
const groupsApi = vi.spyOn(api, "directorCommandGroups");
const acceptApi = vi.spyOn(api, "directorAcceptCommand");
const rejectApi = vi.spyOn(api, "directorRejectCommand");
const discardApi = vi.spyOn(api, "directorDiscardCommandGroup");
const undoApi = vi.spyOn(api, "directorUndoCommand");
const redoApi = vi.spyOn(api, "directorRedoCommand");

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

function commandFixture(overrides: Partial<DirectorCommand> = {}): DirectorCommand {
  return { ...baseCommand, ...overrides };
}

function candidateFixture(overrides: Partial<PageCandidate> = {}): PageCandidate {
  return {
    id: "candidate-1",
    batch_id: "batch-1",
    page_id: "page-1",
    ordinal: 3,
    model_alias: "gemini_image",
    resolution: "1K",
    status: "COMPLETED",
    asset_id: "asset-1",
    job_id: null,
    is_favorite: false,
    is_selected: true,
    based_on_storyboard_version: 2,
    version_state: "CURRENT",
    staleness_reasons: [],
    created_at: "2026-09-03T00:00:00Z",
    variant: null,
    prompt_snapshot: {},
    content_url: "/api/v1/assets/asset-1/content",
    thumbnail_url: "/api/v1/assets/asset-1/thumbnail/640",
    ...overrides,
  };
}

type DirectorProps = Parameters<typeof DirectorWorkspace>[0];

function renderDirector(overrides: Partial<DirectorProps> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const props: DirectorProps = {
    id: "project-1",
    page: pageFixture(),
    panels: [panelFixture()],
    scenes: [sceneFixture()],
    characters: [characterFixture()],
    activeDrawModelName: "Nano Banana 2",
    pageGenerationPending: false,
    onExecutingChange: vi.fn(),
    localEditCandidate: candidateFixture(),
    onOpenLocalEdit: vi.fn(),
    ...overrides,
  };
  const view = render(
    <QueryClientProvider client={client}>
      <DirectorWorkspace {...props} />
    </QueryClientProvider>,
  );
  return { client, ...view };
}

async function previewUtterance(utterance: string) {
  const input = screen.getByLabelText("导演指令");
  fireEvent.change(input, { target: { value: utterance } });
  fireEvent.click(screen.getByRole("button", { name: "预览" }));
  await waitFor(() => {
    expect(proposeApi).toHaveBeenCalled();
  });
  const envelope = proposeApi.mock.calls[proposeApi.mock.calls.length - 1][1].commands[0];
  return envelope;
}

describe("DirectorWorkspace 导演台（V02-41B）", () => {
  beforeEach(() => {
    proposeApi.mockReset();
    groupsApi.mockReset().mockResolvedValue([]);
    acceptApi.mockReset();
    rejectApi.mockReset();
    discardApi.mockReset();
    undoApi.mockReset();
    redoApi.mockReset();
  });

  it("D10 命令历史按页从服务端读取，原文与状态可见", async () => {
    groupsApi.mockResolvedValue([groupFixture({
      status: "PREVIEWED",
      commands: [{
        command_id: "cmd-1",
        command_group_id: "group-1",
        operation: "update_panel_shot",
        status: "PREVIEWED",
        target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
        expected_version: { scope: "panel", value: 4 },
        payload: {},
        source: { user_prompt: "第 1 格改成近景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        diff: null,
        error: null,
        retry_of_command_id: null,
        inverse_of_command_id: null,
        storyboard_version_after: null,
        version: 1,
      }],
    })]);
    renderDirector();
    await waitFor(() => {
      expect(groupsApi).toHaveBeenCalledWith("project-1", "page-1");
      expect(screen.getByText("第 1 格改成近景")).toBeInTheDocument();
    });
    expect(screen.getByText("待确认")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续预览" })).toBeInTheDocument();
  });

  it("D13 有采用候选时「在选区编辑」可进入局部编辑器，无采用候选则禁用并说明", () => {
    const onOpenLocalEdit = vi.fn();
    const adopted = candidateFixture();
    const { unmount } = renderDirector({ localEditCandidate: adopted, onOpenLocalEdit });
    const button = screen.getByRole("button", { name: "在选区编辑（mask 局部重绘）" });
    expect(button).toBeEnabled();
    fireEvent.click(button);
    expect(onOpenLocalEdit).toHaveBeenCalledWith(adopted);
    expect(screen.getByText(/V02-42B 派生链/)).toBeInTheDocument();
    unmount();
    renderDirector({ localEditCandidate: null });
    expect(screen.getByRole("button", { name: "在选区编辑（mask 局部重绘）" })).toBeDisabled();
    expect(screen.getByTitle(/当前页还没有采用候选/)).toBeInTheDocument();
  });

  it("规则解析标签可见，且不出现「模型解析」承诺", () => {
    renderDirector();
    expect(screen.getAllByText("规则解析，非模型").length).toBeGreaterThan(0);
  });

  it("D2 缺作用域的口令进入澄清层，不发起 propose；点击格芯片后可重新预览", async () => {
    renderDirector();
    fireEvent.change(screen.getByLabelText("导演指令"), { target: { value: "改成近景" } });
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    const dialog = await screen.findByRole("dialog", { name: "请确认命令目标" });
    expect(proposeApi).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "格 1" }));
    expect(screen.queryByRole("dialog", { name: "请确认命令目标" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await waitFor(() => {
      expect(proposeApi).toHaveBeenCalledTimes(1);
    });
    const payload = proposeApi.mock.calls[0][1];
    expect(payload.command_group_id).toBeTruthy();
    expect(payload.commands[0].operation).toBe("update_panel_shot");
  });

  it("D5 预览与执行分离：先 propose 出预览卡，确认才 accept", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    acceptApi.mockResolvedValue(groupFixture({
      status: "COMMITTED",
      commands: [{
        command_id: "cmd-1",
        command_group_id: "group-1",
        operation: "update_panel_shot",
        status: "EXECUTED",
        target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
        expected_version: { scope: "panel", value: 4 },
        payload: { shot_type: "close_up" },
        source: { user_prompt: "第 1 格改成近景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        diff: { shot_type: { before: "medium_close_up", after: "close_up" } },
        error: null,
        retry_of_command_id: null,
        inverse_of_command_id: null,
        storyboard_version_after: 3,
        version: 2,
      }],
    }));
    renderDirector();
    await previewUtterance("第 1 格改成近景");
    expect(acceptApi).not.toHaveBeenCalled();
    const region = await screen.findByRole("region", { name: "命令预览" });
    expect(within(region).getByText("格 1")).toBeInTheDocument();
    expect(within(region).getByText("close_up")).toBeInTheDocument();
    fireEvent.click(within(region).getByRole("button", { name: "确认执行" }));
    await waitFor(() => {
      expect(acceptApi).toHaveBeenCalledWith("project-1", "cmd-1");
    });
    await waitFor(() => {
      expect(screen.getByText("已执行 · 分镜已更新，可在历史里撤销。")).toBeInTheDocument();
    });
  });

  it("D7 执行期间画布 busy：状态行出现且回调为 true，结束后恢复", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    let resolveAccept: ((group: DirectorCommandGroup) => void) | null = null;
    acceptApi.mockImplementation(() => new Promise<DirectorCommandGroup>((resolve) => {
      resolveAccept = resolve;
    }));
    const onExecutingChange = vi.fn();
    renderDirector({ onExecutingChange });
    await previewUtterance("第 1 格改成近景");
    fireEvent.click(await screen.findByRole("button", { name: "确认执行" }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("命令执行中");
    });
    expect(onExecutingChange).toHaveBeenCalledWith(true);
    resolveAccept!(groupFixture({
      status: "COMMITTED",
      commands: [{
        command_id: "cmd-1",
        command_group_id: "group-1",
        operation: "update_panel_shot",
        status: "EXECUTED",
        target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
        expected_version: { scope: "panel", value: 4 },
        payload: {},
        source: { user_prompt: "第 1 格改成近景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        diff: null,
        error: null,
        retry_of_command_id: null,
        inverse_of_command_id: null,
        storyboard_version_after: 3,
        version: 2,
      }],
    }));
    await waitFor(() => {
      expect(onExecutingChange).toHaveBeenLastCalledWith(false);
    });
  });

  it("D16 费用不可估算时显示明确文案，不显示 0", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    renderDirector();
    await previewUtterance("第 1 格改成近景");
    expect(await screen.findByText(/费用暂不可估算/)).toBeInTheDocument();
    expect(screen.queryByText(/费用[：:]\s*0/)).not.toBeInTheDocument();
    expect(screen.getByText(/规则解析，非模型调用 · 抽卡模型：Nano Banana 2/)).toBeInTheDocument();
  });

  it("D12 accept 409 版本冲突时保留预览与草稿，不覆盖输入", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    acceptApi.mockRejectedValue(new ApiError("目标版本已过期，请刷新后重试", 409));
    renderDirector();
    const input = screen.getByLabelText("导演指令");
    fireEvent.change(input, { target: { value: "第 1 格改成近景" } });
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认执行" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("目标版本已过期");
    });
    expect((input as HTMLTextAreaElement).value).toBe("第 1 格改成近景");
    expect(screen.getByRole("region", { name: "命令预览" })).toBeInTheDocument();
  });

  it("D8 执行失败后可改口令重发：草稿恢复原文并携带 retry_of_command_id", async () => {
    proposeApi.mockResolvedValue(groupFixture({
      status: "REJECTED",
      commands: [{
        command_id: "cmd-1",
        command_group_id: "group-1",
        operation: "update_panel_shot",
        status: "FAILED",
        target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
        expected_version: { scope: "panel", value: 4 },
        payload: {},
        source: { user_prompt: "第 1 格改成近景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        diff: null,
        error: { code: "EXECUTION", message: "分镜格已被锁定", status: 409 },
        retry_of_command_id: null,
        inverse_of_command_id: null,
        storyboard_version_after: null,
        version: 1,
      }],
    }));
    renderDirector();
    await previewUtterance("第 1 格改成近景");
    await screen.findByRole("region", { name: "命令预览" });
    fireEvent.click(screen.getByRole("button", { name: "改口令重发" }));
    const input = screen.getByLabelText("导演指令") as HTMLTextAreaElement;
    await waitFor(() => {
      expect(input.value).toBe("第 1 格改成近景");
    });
    proposeApi.mockResolvedValue(groupFixture());
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await waitFor(() => {
      expect(proposeApi).toHaveBeenCalledTimes(2);
    });
    expect(proposeApi.mock.calls[1][1].commands[0].retry_of_command_id).toBe("cmd-1");
  });

  it("已执行命令可撤销；撤销命令显示为重做", async () => {
    groupsApi.mockResolvedValue([
      groupFixture({
        id: "row-undo",
        command_group_id: "group-undo",
        status: "PARTIALLY_ACCEPTED",
        commands: [{
          command_id: "cmd-undo",
          command_group_id: "group-undo",
          operation: "update_panel_shot",
          status: "EXECUTED",
          target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
          expected_version: { scope: "panel", value: 4 },
          payload: {},
          source: { user_prompt: "第 1 格改成近景（撤销）", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
          diff: null,
          error: null,
          retry_of_command_id: null,
          inverse_of_command_id: "cmd-elsewhere",
          storyboard_version_after: 5,
          version: 3,
        }],
      }),
      groupFixture({
        id: "row-origin",
        command_group_id: "group-origin",
        status: "COMMITTED",
        commands: [{
          command_id: "cmd-origin",
          command_group_id: "group-origin",
          operation: "update_panel_shot",
          status: "EXECUTED",
          target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
          expected_version: { scope: "panel", value: 4 },
          payload: {},
          source: { user_prompt: "第 1 格改成远景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
          diff: null,
          error: null,
          retry_of_command_id: null,
          inverse_of_command_id: null,
          storyboard_version_after: 4,
          version: 2,
        }],
      }),
    ]);
    undoApi.mockResolvedValue(groupFixture({ status: "COMMITTED" }));
    redoApi.mockResolvedValue(groupFixture({ status: "COMMITTED" }));
    renderDirector();
    await screen.findByText("第 1 格改成远景");
    expect(screen.getByRole("button", { name: /撤销/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重做/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }));
    await waitFor(() => {
      expect(undoApi).toHaveBeenCalledWith("project-1", "cmd-origin");
    });
    fireEvent.click(screen.getByRole("button", { name: /重做/ }));
    await waitFor(() => {
      expect(redoApi).toHaveBeenCalledWith("project-1", "cmd-undo");
    });
  });

  it("同一命令组追加撤销后对最后一条 inverse 命令重做", async () => {
    groupsApi.mockResolvedValue([
      groupFixture({
        id: "row-same-group",
        command_group_id: "group-same",
        status: "PARTIALLY_ACCEPTED",
        commands: [
          {
            command_id: "cmd-origin",
            command_group_id: "group-same",
            operation: "update_panel_shot",
            status: "EXECUTED",
            target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
            expected_version: { scope: "panel", value: 4 },
            payload: {},
            source: { user_prompt: "第 1 格改成远景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
            diff: null,
            error: null,
            retry_of_command_id: null,
            inverse_of_command_id: null,
            storyboard_version_after: 4,
            version: 2,
          },
          {
            command_id: "cmd-undo",
            command_group_id: "group-same",
            operation: "update_panel_shot",
            status: "EXECUTED",
            target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
            expected_version: { scope: "panel", value: 5 },
            payload: {},
            source: { user_prompt: "第 1 格改成远景（撤销）", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
            diff: null,
            error: null,
            retry_of_command_id: null,
            inverse_of_command_id: "cmd-origin",
            storyboard_version_after: 5,
            version: 3,
          },
        ],
      }),
    ]);
    redoApi.mockResolvedValue(groupFixture({ status: "COMMITTED" }));
    renderDirector();
    await screen.findByText("第 1 格改成远景");
    expect(screen.queryByRole("button", { name: /撤销/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /重做/ }));
    await waitFor(() => {
      expect(redoApi).toHaveBeenCalledWith("project-1", "cmd-undo");
    });
  });

  it("同一命令组重做之后对末条 inverse 显示撤销而不是再点重做", async () => {
    groupsApi.mockResolvedValue([
      groupFixture({
        id: "row-redo-chain",
        command_group_id: "group-redo",
        status: "COMMITTED",
        commands: [
          {
            command_id: "cmd-origin",
            command_group_id: "group-redo",
            operation: "update_panel_shot",
            status: "EXECUTED",
            target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
            expected_version: { scope: "panel", value: 4 },
            payload: {},
            source: { user_prompt: "第 1 格改成远景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
            diff: null,
            error: null,
            retry_of_command_id: null,
            inverse_of_command_id: null,
            storyboard_version_after: 4,
            version: 2,
          },
          {
            command_id: "cmd-undo",
            command_group_id: "group-redo",
            operation: "update_panel_shot",
            status: "EXECUTED",
            target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
            expected_version: { scope: "panel", value: 5 },
            payload: {},
            source: { user_prompt: "第 1 格改成远景（撤销）", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
            diff: null,
            error: null,
            retry_of_command_id: null,
            inverse_of_command_id: "cmd-origin",
            storyboard_version_after: 5,
            version: 3,
          },
          {
            command_id: "cmd-redo",
            command_group_id: "group-redo",
            operation: "update_panel_shot",
            status: "EXECUTED",
            target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1" },
            expected_version: { scope: "panel", value: 6 },
            payload: {},
            source: { user_prompt: "第 1 格改成远景（重做）", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
            diff: null,
            error: null,
            retry_of_command_id: null,
            inverse_of_command_id: "cmd-undo",
            storyboard_version_after: 6,
            version: 4,
          },
        ],
      }),
    ]);
    undoApi.mockResolvedValue(groupFixture({ status: "COMMITTED" }));
    renderDirector();
    await screen.findByText("第 1 格改成远景");
    expect(screen.queryByRole("button", { name: /重做/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }));
    await waitFor(() => {
      expect(undoApi).toHaveBeenCalledWith("project-1", "cmd-redo");
    });
  });

  it("D14 Ctrl+K 聚焦命令框，Esc 关闭预览", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    renderDirector();
    const input = screen.getByLabelText("导演指令");
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: "第 1 格改成近景" } });
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await screen.findByRole("region", { name: "命令预览" });
    fireEvent.keyDown(window, { key: "Escape", keyCode: 27 });
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "命令预览" })).not.toBeInTheDocument();
    });
  });

  it("拒绝与丢弃走对应端点", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    rejectApi.mockResolvedValue(groupFixture({ status: "REJECTED" }));
    discardApi.mockResolvedValue(groupFixture({ status: "DISCARDED" }));
    renderDirector();
    await previewUtterance("第 1 格改成近景");
    fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));
    await waitFor(() => {
      expect(rejectApi).toHaveBeenCalledWith("project-1", "cmd-1");
    });
    fireEvent.click(screen.getByRole("button", { name: "丢弃" }));
    await waitFor(() => {
      expect(discardApi).toHaveBeenCalledWith("project-1", "group-1");
    });
  });

  it("D4 多名角色歧义时澄清层只列角色选项，不发起 propose", async () => {
    renderDirector({
      panels: [panelFixture({
        characters: ["character-1", "character-2"],
        character_presence: { "character-1": "VISIBLE", "character-2": "VISIBLE" },
      })],
      characters: [characterFixture(), characterFixture({ id: "character-2", primary_name: "苏离" })],
    });
    fireEvent.change(screen.getByLabelText("导演指令"), { target: { value: "让她微笑" } });
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    const dialog = await screen.findByRole("dialog", { name: "请确认命令目标" });
    expect(within(dialog).getByRole("button", { name: "苏离" })).toBeInTheDocument();
    expect(proposeApi).not.toHaveBeenCalled();
  });

  it("重绘口令指向局部编辑器且不发请求（不静默整页重绘）", async () => {
    renderDirector();
    fireEvent.change(screen.getByLabelText("导演指令"), { target: { value: "重画这一格" } });
    fireEvent.click(screen.getByRole("button", { name: "预览" }));
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("局部重绘");
    });
    expect(screen.getByRole("alert")).toHaveTextContent("在选区编辑");
    expect(proposeApi).not.toHaveBeenCalled();
  });

  it("D17 五路 journal 操作任一在途时，预览与历史的执行按钮全部禁用（#165）", async () => {
    groupsApi.mockResolvedValue([
      groupFixture({
        id: "row-exec",
        command_group_id: "group-exec",
        status: "COMMITTED",
        commands: [commandFixture({
          command_id: "cmd-exec-origin",
          command_group_id: "group-exec",
          status: "EXECUTED",
          source: { user_prompt: "第 1 格改成远景", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        })],
      }),
      groupFixture({
        id: "row-prev",
        command_group_id: "group-prev",
        status: "PREVIEWED",
        commands: [commandFixture({
          command_id: "cmd-prev",
          command_group_id: "group-prev",
          status: "PREVIEWED",
        })],
      }),
    ]);
    let resolveAccept: ((group: DirectorCommandGroup) => void) | null = null;
    acceptApi.mockImplementation(() => new Promise<DirectorCommandGroup>((resolve) => {
      resolveAccept = resolve;
    }));
    renderDirector();
    // 从历史打开待确认组，点「确认执行」让 accept 挂起。
    fireEvent.click(await screen.findByRole("button", { name: "继续预览" }));
    fireEvent.click(await screen.findByRole("button", { name: "确认执行" }));
    await waitFor(() => {
      // 历史区的撤销/丢弃与预览卡上的拒绝/丢弃都必须被共享 executing 门禁
      // 覆盖——accept 在途时历史撤销/丢弃仍可点会造成并发 journal 写入。
      expect(screen.getByRole("button", { name: /撤销/ })).toBeDisabled();
      screen.getAllByRole("button", { name: "丢弃" }).forEach((button) => expect(button).toBeDisabled());
      expect(screen.getByRole("button", { name: "拒绝" })).toBeDisabled();
    });
    resolveAccept!(groupFixture({
      status: "COMMITTED",
      commands: [commandFixture({ status: "EXECUTED", storyboard_version_after: 3 })],
    }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /撤销/ })).toBeEnabled();
    });
    screen.getAllByRole("button", { name: "丢弃" }).forEach((button) => expect(button).toBeEnabled());
  });

  it("D18 从历史丢弃无关组不关闭当前预览组（#165）", async () => {
    groupsApi.mockResolvedValue([
      groupFixture({
        id: "row-a",
        command_group_id: "group-a",
        status: "PREVIEWED",
        commands: [commandFixture({
          command_id: "cmd-a",
          command_group_id: "group-a",
          status: "PREVIEWED",
          source: { user_prompt: "格 1 改成近景（组 A）", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        })],
      }),
      groupFixture({
        id: "row-b",
        command_group_id: "group-b",
        status: "PREVIEWED",
        commands: [commandFixture({
          command_id: "cmd-b",
          command_group_id: "group-b",
          status: "PREVIEWED",
          source: { user_prompt: "台词改成「我没事」（组 B）", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        })],
      }),
    ]);
    discardApi.mockResolvedValue(groupFixture({ status: "DISCARDED", command_group_id: "group-b" }));
    renderDirector();
    await screen.findByText("格 1 改成近景（组 A）");
    const openButtons = screen.getAllByRole("button", { name: "继续预览" });
    fireEvent.click(openButtons[0]);
    expect(await screen.findByRole("region", { name: "命令预览" })).toHaveTextContent("格 1 改成近景（组 A）");
    // 丢弃历史里的组 B：预览中的组 A 必须保持打开。
    const discardButtons = screen.getAllByRole("button", { name: "丢弃" });
    fireEvent.click(discardButtons[discardButtons.length - 1]);
    await waitFor(() => {
      expect(discardApi).toHaveBeenCalledWith("project-1", "group-b");
    });
    const region = await screen.findByRole("region", { name: "命令预览" });
    expect(region).toHaveTextContent("格 1 改成近景（组 A）");
  });

  it("D19 撤销其他组后预览不再沿用旧命令的解析文案（#165 previewPlan 陈旧）", async () => {
    proposeApi.mockResolvedValue(groupFixture());
    groupsApi.mockResolvedValue([
      groupFixture({
        id: "row-dialogue",
        command_group_id: "group-dialogue",
        status: "COMMITTED",
        commands: [commandFixture({
          command_id: "cmd-dialogue",
          command_group_id: "group-dialogue",
          operation: "update_dialogue",
          status: "EXECUTED",
          target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1", dialogue_id: "dialogue-1" },
          source: { user_prompt: "台词改成「我没事」", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
        })],
      }),
    ]);
    undoApi.mockResolvedValue(groupFixture({
      id: "row-dialogue",
      command_group_id: "group-dialogue",
      status: "PARTIALLY_ACCEPTED",
      commands: [commandFixture({
        command_id: "cmd-dialogue",
        command_group_id: "group-dialogue",
        operation: "update_dialogue",
        status: "EXECUTED",
        target: { project_id: "project-1", page_id: "page-1", panel_id: "panel-1", dialogue_id: "dialogue-1" },
        source: { user_prompt: "台词改成「我没事」", reference_asset_ids: [], model: null, raw_output_id: "rule_stub_v1" },
      })],
    }));
    renderDirector();
    // 先 propose 一个镜头景别命令，previewPlan 携带旧命令的解析文案。
    await previewUtterance("第 1 格改成近景");
    const region = await screen.findByRole("region", { name: "命令预览" });
    await waitFor(() => {
      expect(within(region).getByText("镜头景别")).toBeInTheDocument();
    });
    // 撤销历史里的台词命令：预览被替换为该组，表头/摘要必须回退到新命令的
    // operation 标签与原文，而不是旧命令的解析文案。
    fireEvent.click(screen.getByRole("button", { name: /撤销/ }));
    await waitFor(() => {
      expect(undoApi).toHaveBeenCalledWith("project-1", "cmd-dialogue");
    });
    await waitFor(() => {
      expect(within(screen.getByRole("region", { name: "命令预览" })).getByText("气泡台词")).toBeInTheDocument();
    });
    const nextRegion = screen.getByRole("region", { name: "命令预览" });
    expect(within(nextRegion).getByText("台词改成「我没事」")).toBeInTheDocument();
    expect(within(nextRegion).queryByText("镜头景别")).not.toBeInTheDocument();
  });

  it("D20 页面切换后预览、草稿与作用域全部重置，不携带旧页状态漂移（#165）", async () => {
    groupsApi.mockImplementation((_projectId: string, pageId?: string | null) =>
      Promise.resolve(pageId === "page-1"
        ? [groupFixture({
            id: "row-prev",
            command_group_id: "group-prev",
            status: "PREVIEWED",
            commands: [commandFixture({ command_id: "cmd-prev", command_group_id: "group-prev", status: "PREVIEWED" })],
          })]
        : []));
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const baseProps = {
      id: "project-1",
      panels: [panelFixture()],
      scenes: [sceneFixture()],
      characters: [characterFixture()],
      activeDrawModelName: "Nano Banana 2",
      pageGenerationPending: false,
      onExecutingChange: vi.fn(),
      localEditCandidate: candidateFixture(),
      onOpenLocalEdit: vi.fn(),
    };
    const view = render(
      <QueryClientProvider client={client}>
        <DirectorWorkspace {...baseProps} page={pageFixture()} />
      </QueryClientProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "继续预览" }));
    expect(await screen.findByRole("region", { name: "命令预览" })).toBeInTheDocument();
    const input = screen.getByLabelText("导演指令") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "第 1 格改成近景" } });
    fireEvent.click(screen.getByRole("button", { name: "格 1" }));
    expect(screen.getByRole("button", { name: "格 1" })).toHaveAttribute("aria-pressed", "true");

    // mid-session 换页(refetch 后 selectedPage 指针变化):旧页的预览/草稿/作用域
    // 不能继续挂在新的 DirectorWorkspace 实例上。
    view.rerender(
      <QueryClientProvider client={client}>
        <DirectorWorkspace {...baseProps} page={pageFixture({ id: "page-2", page_number: 2 })} />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.queryByRole("region", { name: "命令预览" })).not.toBeInTheDocument();
    });
    expect((screen.getByLabelText("导演指令") as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByRole("button", { name: "格 1" })).toHaveAttribute("aria-pressed", "false");
    await waitFor(() => {
      expect(groupsApi).toHaveBeenCalledWith("project-1", "page-2");
    });
  });
});
