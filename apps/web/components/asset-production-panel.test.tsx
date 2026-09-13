import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Character, type StyleProfile } from "@/lib/api";

import { CharacterConceptPanel, StyleProductionPanel } from "./asset-production-panel";

const assetBatches = vi.spyOn(api, "assetBatches");
const candidates = vi.spyOn(api, "candidates");
const jobs = vi.spyOn(api, "jobs");
const approveAssetReference = vi.spyOn(api, "approveAssetReference");
const deleteCandidate = vi.spyOn(api, "deleteCandidate");
const confirmAction = vi.spyOn(window, "confirm");

const character: Character = {
  id: "character-1",
  project_id: "project-1",
  primary_name: "旁白",
  aliases: [],
  alias_conflict: false,
  canonical_description: "这段已有的人物描述不应直接填入表单",
  locked_features: [],
  forbidden_changes: [],
  status: "DRAFT",
  version: 1,
  references: [],
};

describe("CharacterConceptPanel", () => {
  beforeEach(() => {
    window.localStorage.clear();
    assetBatches.mockReset().mockResolvedValue([]);
    candidates.mockReset().mockResolvedValue([]);
    jobs.mockReset().mockResolvedValue([]);
    approveAssetReference.mockReset();
    deleteCandidate.mockReset().mockResolvedValue(undefined);
    confirmAction.mockReset().mockReturnValue(true);
  });

  it("不预填人物或服装提示词，只显示简短的占位提示", () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    render(
      <QueryClientProvider client={queryClient}>
        <CharacterConceptPanel
          projectId="project-1"
          character={character}
          model="image.fast"
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(screen.getByPlaceholderText("简述外貌与气质；可留空")).toHaveValue("");
    expect(screen.getByPlaceholderText("给这套服装起个名称")).toHaveValue("");
    expect(screen.getByPlaceholderText("简述款式、颜色与场景")).toHaveValue("");
    expect(screen.getByPlaceholderText("填写必须保持一致的特征")).toHaveValue("");
    expect(screen.queryByDisplayValue(character.canonical_description)).not.toBeInTheDocument();
  });

  it("按项目和人物记住用户输入，重新挂载后恢复草稿", async () => {
    const firstClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const first = render(
      <QueryClientProvider client={firstClient}>
        <CharacterConceptPanel
          projectId="project-1"
          character={character}
          model="image.fast"
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(window.localStorage.getItem(
        "mangaflow:character-concept-draft:project-1:character-1",
      )).not.toBeNull();
    });

    fireEvent.change(screen.getByPlaceholderText("简述外貌与气质；可留空"), {
      target: { value: "黑发黑瞳，19岁" },
    });
    fireEvent.change(screen.getByPlaceholderText("给这套服装起个名称"), {
      target: { value: "秋季日常" },
    });
    fireEvent.change(screen.getByPlaceholderText("简述款式、颜色与场景"), {
      target: { value: "日常系穿搭" },
    });
    fireEvent.change(screen.getByPlaceholderText("填写必须保持一致的特征"), {
      target: { value: "黑发，学生感" },
    });

    await waitFor(() => {
      expect(window.localStorage.getItem(
        "mangaflow:character-concept-draft:project-1:character-1",
      )).toContain("秋季日常");
    });
    first.unmount();

    const secondClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={secondClient}>
        <CharacterConceptPanel
          projectId="project-1"
          character={character}
          model="image.fast"
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByDisplayValue("黑发黑瞳，19岁")).toBeInTheDocument();
    expect(screen.getByDisplayValue("秋季日常")).toBeInTheDocument();
    expect(screen.getByDisplayValue("日常系穿搭")).toBeInTheDocument();
    expect(screen.getByDisplayValue("黑发，学生感")).toBeInTheDocument();
  });

  it("直接展示失败任务原因，并允许没有成图的失败记录重试", async () => {
    assetBatches.mockResolvedValue([{ id: "batch-1" }] as never);
    candidates.mockResolvedValue([{
      id: "candidate-1",
      batch_id: "batch-1",
      ordinal: 1,
      status: "FAILED",
      resolution: "1K",
      asset_id: null,
      job_id: "job-1",
      prompt_snapshot: {},
    }] as never);
    jobs.mockResolvedValue([{
      id: "job-1",
      status: "FAILED",
      error_message: "角色参考图已失效，请重新绑定后再生成",
    }] as never);

    render(
      <QueryClientProvider client={new QueryClient()}>
        <CharacterConceptPanel
          projectId="project-1"
          character={character}
          model="image.fast"
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );

    expect(await screen.findByText("角色参考图已失效，请重新绑定后再生成"))
      .toBeInTheDocument();
    const deleteButton = screen.getByRole("button", { name: "删除记录" });
    expect(deleteButton).toBeEnabled();
    expect(screen.queryByText("等待 Worker 生成")).not.toBeInTheDocument();

    fireEvent.click(deleteButton);
    await waitFor(() => expect(deleteCandidate).toHaveBeenCalledWith("candidate-1"));
  });

  it("未填写服装时确认人物参考，不提交空字符串触发 422", async () => {
    assetBatches.mockResolvedValue([{ id: "batch-1" }] as never);
    candidates.mockResolvedValue([{
      id: "candidate-1",
      batch_id: "batch-1",
      ordinal: 1,
      status: "READY",
      resolution: "1K",
      asset_id: "asset-1",
      job_id: "job-1",
      prompt_snapshot: {},
    }] as never);
    approveAssetReference.mockResolvedValue({
      candidate_id: "candidate-1",
      asset_id: "asset-1",
      character_id: "character-1",
    });

    render(
      <QueryClientProvider client={new QueryClient()}>
        <CharacterConceptPanel
          projectId="project-1"
          character={character}
          model="image.fast"
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "确认并设为规范参考" }));

    await waitFor(() => {
      expect(approveAssetReference).toHaveBeenCalledWith("candidate-1", expect.objectContaining({
        outfit_name: undefined,
        outfit_description: undefined,
      }));
    });
  });

  it("TEST-CONCEPT-ERR 批次读取失败显示错误与重试，不再伪装成空态（#545-3）", async () => {
    assetBatches.mockRejectedValueOnce(new Error("批次接口 500"));
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <CharacterConceptPanel
          projectId="project-1"
          character={character}
          model="image.fast"
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("设定草稿读取失败");
    expect(alert).toHaveTextContent("批次：批次接口 500");
    // 失败不得被误读为「还没生成」，否则会诱导重复付费生成。
    expect(screen.queryByText("第一张草稿生成后会实时出现在这里；确认前不会进入正式页面提示词。"))
      .not.toBeInTheDocument();

    assetBatches.mockResolvedValueOnce([]);
    fireEvent.click(within(alert).getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(assetBatches).toHaveBeenCalledTimes(2));
    await screen.findByText("第一张草稿生成后会实时出现在这里；确认前不会进入正式页面提示词。");
  });
});

function styleFixture(overrides: Partial<StyleProfile> = {}): StyleProfile {
  return {
    id: "style-1",
    project_id: "project-1",
    name: "雨季水彩",
    color_mode: "color",
    profile: { palette_draft: { "肤色": "#f2e2d5", "发色": "#3a2e2a" } },
    locked_fields: [],
    status: "ACTIVE",
    version: 3,
    ...overrides,
  };
}

describe("StyleProductionPanel 候选错误面与色板草稿守卫（#545-3 / #546-6）", () => {
  beforeEach(() => {
    window.localStorage.clear();
    assetBatches.mockReset().mockResolvedValue([]);
    candidates.mockReset().mockResolvedValue([]);
    jobs.mockReset().mockResolvedValue([]);
    confirmAction.mockReset().mockReturnValue(true);
  });

  function renderPanel(style: StyleProfile) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const view = render(
      <QueryClientProvider client={client}>
        <StyleProductionPanel
          projectId="project-1"
          style={style}
          model="image.fast"
          active={false}
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    );
    return { client, rerender: (next: StyleProfile) => view.rerender(
      <QueryClientProvider client={client}>
        <StyleProductionPanel
          projectId="project-1"
          style={next}
          model="image.fast"
          active={false}
          onOpen={() => undefined}
        />
      </QueryClientProvider>,
    ) };
  }

  it("TEST-STYLE-ERR 测试图候选读取失败显示错误与重试（#545-3）", async () => {
    assetBatches.mockRejectedValueOnce(new Error("候选批次 503"));
    renderPanel(styleFixture());

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("测试图候选读取失败");
    expect(alert).toHaveTextContent("候选批次 503");

    assetBatches.mockResolvedValueOnce([]);
    fireEvent.click(within(alert).getByRole("button", { name: "重试读取" }));
    await waitFor(() => expect(assetBatches).toHaveBeenCalledTimes(2));
  });

  it("TEST-STYLE-GUARD1 version bump 不再重挂载：进行中的色板编辑在轮询刷新后保留（#546-6）", async () => {
    const initial = styleFixture({
      status: "ANALYZING",
      profile: { palette_draft: { "肤色": "#f2e2d5" } },
    });
    const { rerender } = renderPanel(initial);
    const nameInput = await screen.findByLabelText("色板项 1 名称");
    expect(nameInput).toHaveValue("肤色");
    fireEvent.change(nameInput, { target: { value: "主肤色" } });

    // ANALYZING 轮询完成：version bump，服务器还补充了「天空」一项。
    const bumped = styleFixture({
      status: "ACTIVE",
      version: 4,
      profile: { palette_draft: { "肤色": "#f2e2d5", "天空": "#a8c8e8" } },
    });
    rerender(bumped);

    // 脏输入原样保留，没有被服务器色板覆盖。
    expect(screen.getByLabelText("色板项 1 名称")).toHaveValue("主肤色");
    expect(screen.queryByLabelText("色板项 2 名称")).not.toBeInTheDocument();
  });

  it("TEST-STYLE-GUARD2 未编辑时服务器新色板仍会被采纳（非脏重同步）（#546-6）", async () => {
    const initial = styleFixture({ profile: { palette_draft: { "肤色": "#f2e2d5" } } });
    const { rerender } = renderPanel(initial);
    expect(await screen.findByLabelText("色板项 1 名称")).toHaveValue("肤色");

    const updated = styleFixture({ version: 4, profile: { palette_draft: { "肤色": "#f2e2d5", "天空": "#a8c8e8" } } });
    rerender(updated);

    await waitFor(() => expect(screen.getByLabelText("色板项 2 名称")).toHaveValue("天空"));
  });
});
