import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type SceneAsset, type Script, type ScriptScene } from "@/lib/api";

import { ScriptEditor } from "./script-editor";

// ScenePicker 有自己的测试；占位保持剧本编辑器的脏确认测试独立。
vi.mock("./project-workspace/scene-picker", () => ({
  ScenePicker: () => <div data-testid="scene-picker-stub" />,
}));

const updateSceneApi = vi.spyOn(api, "updateScene");
const updateBeatApi = vi.spyOn(api, "updateBeat");

function beatFixture(overrides: Partial<Script["scenes"][number]["beats"][number]> = {}) {
  return {
    id: "beat-1",
    scene_id: "scene-1",
    ordinal: 1,
    action: "主角推开天台的门",
    speaker_name: "小夏",
    dialogue: "风好大。",
    narration: "",
    subtext: "",
    emotion: "紧张",
    importance: 0.6,
    must_visualize: true,
    mergeable: false,
    page_turn_hook: false,
    source_range: { segment_ids: ["s1"] },
    version: 1,
    ...overrides,
  };
}

function sceneFixture(overrides: Partial<ScriptScene> = {}): ScriptScene {
  return {
    id: "scene-1",
    ordinal: 1,
    location: "学校天台",
    scene_asset_id: null,
    scene_asset_variant_id: null,
    time_label: "黄昏",
    weather: "晴",
    purpose: "初次对峙",
    emotional_arc: "试探到冲突",
    source_range: { segment_ids: ["s1"] },
    outfit_assignments: {},
    locked_fields: [],
    version: 1,
    beats: [beatFixture()],
    ...overrides,
  };
}

function scriptFixture(overrides: Partial<Script> = {}): Script {
  return {
    chapter_id: "chapter-1",
    status: "READY",
    revision_no: 1,
    coverage: { ratio: 1, expected: 1, covered: 1 },
    scenes: [sceneFixture()],
    ...overrides,
  };
}

function renderEditor(script: Script = scriptFixture()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ScriptEditor
        chapterId="chapter-1"
        projectId="project-1"
        script={script}
        characters={[]}
        outfits={[]}
        sceneAssets={[] as SceneAsset[]}
        onAssignOutfit={() => undefined}
      />
    </QueryClientProvider>,
  );
}

describe("ScriptEditor 编辑卡片取消的脏确认", () => {
  beforeEach(() => {
    updateSceneApi.mockReset();
    updateBeatApi.mockReset();
  });

  it("场景草稿已修改时，取消按钮先弹脏确认；拒绝则保留草稿", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /编辑场景/ }));
    const location = screen.getByLabelText(/地点（历史兜底，绑定资产时不会清空）/);
    fireEvent.change(location, { target: { value: "车站前的坡道" } });

    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("尚未保存"));
    // 拒绝丢弃：表单仍在，草稿内容不被清空。
    expect(screen.getByLabelText(/地点（历史兜底/)).toHaveValue("车站前的坡道");
    confirmSpy.mockRestore();
  });

  it("场景草稿确认丢弃后关闭编辑卡片", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /编辑场景/ }));
    fireEvent.change(screen.getByLabelText(/地点（历史兜底/), { target: { value: "车站前的坡道" } });

    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    // 回到只读场景头（编辑卡片消失，编辑入口重新出现）。
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /编辑场景/ })).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "保存场景" })).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("情节拍草稿已修改时，取消按钮先弹脏确认", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    const action = screen.getByLabelText("可视化动作");
    fireEvent.change(action, { target: { value: "主角攥紧了书包带" } });

    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    // 拒绝丢弃：情节拍编辑卡片仍在，修改内容保留。
    expect(screen.getByLabelText("可视化动作")).toHaveValue("主角攥紧了书包带");
    confirmSpy.mockRestore();
  });

  it("情节拍草稿确认丢弃后关闭编辑卡片", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    fireEvent.change(screen.getByLabelText("可视化动作"), { target: { value: "主角攥紧了书包带" } });

    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("草稿未修改时取消不弹确认，直接关闭", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /编辑场景/ }));
    fireEvent.click(screen.getByRole("button", { name: /取消/ }));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /编辑场景/ })).toBeInTheDocument();
    confirmSpy.mockRestore();
  });
});

describe("ScriptEditor 保存在途继续输入不随成功丢弃（#636）", () => {
  beforeEach(() => {
    updateSceneApi.mockReset();
    updateBeatApi.mockReset();
  });

  it("场景保存在途继续输入：成功后表单保留，在途增量不丢", async () => {
    let resolveSave!: (value: unknown) => void;
    updateSceneApi.mockImplementation(() => new Promise((resolve) => {
      resolveSave = resolve;
    }));
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /编辑场景/ }));
    const location = screen.getByLabelText(/地点（历史兜底/);
    fireEvent.change(location, { target: { value: "车站前的坡道" } });
    fireEvent.click(screen.getByRole("button", { name: "保存场景" }));
    await waitFor(() => expect(updateSceneApi).toHaveBeenCalledTimes(1));
    // PUT 在途：用户继续补字（表单在 isPending 期间不锁输入）。
    fireEvent.change(location, { target: { value: "车站前的坡道，雨夜" } });

    // 成功落地：表单不得收起，在途增量仍在输入框里等待下一次保存。
    await act(async () => {
      resolveSave({});
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("场景修改已保存"));
    expect(screen.getByLabelText(/地点（历史兜底/)).toHaveValue("车站前的坡道，雨夜");
    expect(screen.getByRole("button", { name: "保存场景" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /编辑场景/ })).not.toBeInTheDocument();
  });

  it("场景保存成功且无在途变化：表单正常收起", async () => {
    updateSceneApi.mockResolvedValue({} as never);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: /编辑场景/ }));
    fireEvent.change(screen.getByLabelText(/地点（历史兜底/), { target: { value: "车站前的坡道" } });
    fireEvent.click(screen.getByRole("button", { name: "保存场景" }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("场景修改已保存");
    });
    // 回到只读场景头：编辑卡片收起，编辑入口重新出现。
    expect(screen.queryByRole("button", { name: "保存场景" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /编辑场景/ })).toBeInTheDocument();
  });

  it("情节拍保存在途继续输入：成功后表单保留，在途增量不丢", async () => {
    let resolveSave!: (value: unknown) => void;
    updateBeatApi.mockImplementation(() => new Promise((resolve) => {
      resolveSave = resolve;
    }));
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    const action = screen.getByLabelText("可视化动作");
    fireEvent.change(action, { target: { value: "主角攥紧了书包带" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(updateBeatApi).toHaveBeenCalledTimes(1));
    // PUT 在途：用户继续补字。
    fireEvent.change(action, { target: { value: "主角攥紧了书包带，指节发白" } });

    await act(async () => {
      resolveSave({});
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("情节拍修改已保存"));
    expect(screen.getByLabelText("可视化动作")).toHaveValue("主角攥紧了书包带，指节发白");
    expect(screen.getByRole("button", { name: "保存" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改" })).not.toBeInTheDocument();
  });

  it("情节拍保存成功且无在途变化：表单正常收起", async () => {
    updateBeatApi.mockResolvedValue({} as never);
    renderEditor();

    fireEvent.click(screen.getByRole("button", { name: "修改" }));
    fireEvent.change(screen.getByLabelText("可视化动作"), { target: { value: "主角攥紧了书包带" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => {
      expect(screen.getByRole("status")).toHaveTextContent("情节拍修改已保存");
    });
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改" })).toBeInTheDocument();
  });
});
