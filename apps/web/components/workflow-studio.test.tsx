import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type MangaPage, type ModelCapability, type WorkflowDefinition, type WorkflowGraph, type WorkflowNodeRun, type WorkflowNodeType, type WorkflowRun } from "@/lib/api";

import WorkflowStudio, { workflowRunsPollInterval } from "./workflow-studio";

vi.mock("@xyflow/react", () => ({
  ReactFlow: ({ children }: { children?: unknown }) => <div data-testid="react-flow">{children as never}</div>,
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Controls: () => null,
  MiniMap: () => null,
  Handle: () => null,
  Position: { Left: "left", Right: "right" },
  addEdge: (edge: unknown, edges: unknown[]) => [...edges, edge],
  applyEdgeChanges: (_changes: unknown, edges: unknown[]) => edges,
  applyNodeChanges: (_changes: unknown, nodes: unknown[]) => nodes,
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const emptyGraph: WorkflowGraph = { schema_version: 2, nodes: [], edges: [] };

function workflow(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    id: "wf-1",
    project_id: "project-1",
    name: "单页生产流程",
    description: "",
    draft_graph: emptyGraph,
    draft_version: 1,
    published_version_id: null,
    is_active: true,
    created_at: "2026-08-27T00:00:00Z",
    updated_at: "2026-08-27T00:00:00Z",
    version: 1,
    ...overrides,
  };
}

const nodeType: WorkflowNodeType = {
  type: "agent.parse_story",
  label: "解析原作",
  category: "AGENT",
  description: "测试节点",
  inputs: [],
  outputs: [],
  configurable_fields: [],
};

const projectSpy = vi.spyOn(api, "project");
const workflowsSpy = vi.spyOn(api, "workflows");
const catalogSpy = vi.spyOn(api, "workflowNodeTypes");
const modelsSpy = vi.spyOn(api, "models");
const chaptersSpy = vi.spyOn(api, "chapters");
const pagesSpy = vi.spyOn(api, "pages");
const versionsSpy = vi.spyOn(api, "workflowVersions");
const runsSpy = vi.spyOn(api, "workflowRuns");
const startRunSpy = vi.spyOn(api, "startWorkflowRun");
const updateSpy = vi.spyOn(api, "updateWorkflow");
const publishSpy = vi.spyOn(api, "publishWorkflow");

function renderStudio() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <WorkflowStudio projectId="project-1" />
    </QueryClientProvider>,
  );
  return { view, client };
}

describe("WorkflowStudio 草稿保存与发布", () => {
  beforeEach(() => {
    window.localStorage.clear();
    projectSpy.mockReset().mockResolvedValue({
      id: "project-1",
      name: "测试项目",
      language: "zh-CN",
      reading_direction: "rtl",
      page_ratio: "b5_portrait",
      default_resolution: "2K",
      draft_resolution: "1K",
      workflow_mode: "SEMI_AUTO",
      default_concurrency: 1,
      default_style_id: null,
      consistency_check_enabled: true,
      text_model_alias: "text.fast",
      last_image_model_alias: null,
      default_text_model_id: null,
      last_image_model_id: null,
      created_at: "2026-08-27T00:00:00Z",
      updated_at: "2026-08-27T00:00:00Z",
      version: 1,
    });
    workflowsSpy.mockReset().mockResolvedValue([workflow()]);
    catalogSpy.mockReset().mockResolvedValue([nodeType]);
    modelsSpy.mockReset().mockResolvedValue([]);
    chaptersSpy.mockReset().mockResolvedValue([]);
    pagesSpy.mockReset().mockResolvedValue([]);
    versionsSpy.mockReset().mockResolvedValue([]);
    runsSpy.mockReset().mockResolvedValue([]);
    updateSpy.mockReset();
    publishSpy.mockReset().mockResolvedValue({
      id: "ver-1",
      workflow_id: "wf-1",
      revision: 1,
      graph: emptyGraph,
      graph_checksum: "abc",
      validation_report: { valid: true, issues: [], topological_order: [] },
      published_at: "2026-08-27T00:00:00Z",
    });
  });

  it("保存中继续改图会补交最新草稿，已保存与持久化内容一致", async () => {
    const first = deferred<WorkflowDefinition>();
    const second = deferred<WorkflowDefinition>();
    let call = 0;
    updateSpy.mockImplementation(async (_id, _version, payload) => {
      call += 1;
      const graph = payload.draft_graph as WorkflowGraph;
      const saved = workflow({
        version: call + 1,
        draft_version: call + 1,
        draft_graph: graph,
      });
      return call === 1 ? first.promise.then(() => saved) : second.promise.then(() => saved);
    });

    renderStudio();
    await screen.findByText("流程编排");
    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: "保存" }).click();
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(screen.getByText("保存状态").parentElement).toHaveTextContent("保存中");
    expect(updateSpy.mock.calls[0][2].draft_graph?.nodes).toHaveLength(1);

    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    expect(screen.getByText("保存状态").parentElement).toHaveTextContent("待保存");

    await act(async () => {
      first.resolve(workflow({ version: 2, draft_version: 2 }));
      await first.promise;
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(2));
    expect(screen.getByText("保存状态").parentElement).toHaveTextContent("保存中");
    expect(updateSpy.mock.calls[1][2].draft_graph?.nodes).toHaveLength(2);

    await act(async () => {
      second.resolve(workflow({ version: 3, draft_version: 3 }));
      await second.promise;
    });
    await waitFor(() => {
      expect(screen.getByText("保存状态").parentElement).toHaveTextContent("已保存");
    });
    expect(screen.getByText("草稿已保存")).toBeInTheDocument();
  });

  it("发布会等待最新草稿保存成功；保存失败则不发布", async () => {
    const first = deferred<WorkflowDefinition>();
    updateSpy.mockImplementation(async (_id, _version, payload) => {
      const graph = payload.draft_graph as WorkflowGraph;
      return first.promise.then(() => workflow({
        version: 2,
        draft_version: 2,
        draft_graph: graph,
      }));
    });

    renderStudio();
    await screen.findByText("流程编排");
    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: "发布" }).click();
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(publishSpy).not.toHaveBeenCalled();

    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    await act(async () => {
      first.resolve(workflow({ version: 2 }));
      await first.promise;
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(publishSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy.mock.calls[1][2].draft_graph?.nodes).toHaveLength(2);

    updateSpy.mockRejectedValueOnce(new Error("网络中断"));
    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    await act(async () => {
      screen.getByRole("button", { name: "发布" }).click();
    });
    await waitFor(() => expect(screen.getByText("草稿保存失败，未发布")).toBeInTheDocument());
    expect(publishSpy).toHaveBeenCalledTimes(1);

    updateSpy.mockImplementation(async (_id, _version, payload) => workflow({
      version: 4, draft_version: 4, draft_graph: payload.draft_graph as WorkflowGraph,
    }));
    await act(async () => {
      screen.getByRole("button", { name: "发布" }).click();
    });
    await waitFor(() => expect(publishSpy).toHaveBeenCalledTimes(2));
    expect(updateSpy.mock.calls.at(-1)?.[2].draft_graph?.nodes).toHaveLength(3);
    expect(screen.getByText("保存状态").parentElement).toHaveTextContent("已保存");
  });
});

describe("WorkflowStudio 草稿离开边界", () => {
  beforeEach(() => {
    window.localStorage.clear();
    projectSpy.mockReset().mockResolvedValue({
      id: "project-1",
      name: "测试项目",
      language: "zh-CN",
      reading_direction: "rtl",
      page_ratio: "b5_portrait",
      default_resolution: "2K",
      draft_resolution: "1K",
      workflow_mode: "SEMI_AUTO",
      default_concurrency: 1,
      default_style_id: null,
      consistency_check_enabled: true,
      text_model_alias: "text.fast",
      last_image_model_alias: null,
      default_text_model_id: null,
      last_image_model_id: null,
      created_at: "2026-08-27T00:00:00Z",
      updated_at: "2026-08-27T00:00:00Z",
      version: 1,
    });
    workflowsSpy.mockReset().mockResolvedValue([workflow()]);
    catalogSpy.mockReset().mockResolvedValue([nodeType]);
    modelsSpy.mockReset().mockResolvedValue([]);
    chaptersSpy.mockReset().mockResolvedValue([]);
    pagesSpy.mockReset().mockResolvedValue([]);
    versionsSpy.mockReset().mockResolvedValue([]);
    runsSpy.mockReset().mockResolvedValue([]);
    updateSpy.mockReset();
    publishSpy.mockReset();
  });

  it("SPA 卸载时会把防抖窗口内的未保存草稿提交到服务器", async () => {
    updateSpy.mockImplementation(async (_id, _version, payload) => workflow({
      version: 2,
      draft_version: 2,
      draft_graph: payload.draft_graph as WorkflowGraph,
    }));

    const { view } = renderStudio();
    await screen.findByText("流程编排");
    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    expect(updateSpy).not.toHaveBeenCalled();

    // 卸载发生在 800ms 防抖到期之前：清理函数必须补交这次保存。
    view.unmount();
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy.mock.calls[0][2].draft_graph?.nodes).toHaveLength(1);
  });

  it("切换工作流前会先保存当前草稿；保存失败则保持原工作流", async () => {
    workflowsSpy.mockResolvedValue([
      workflow(),
      workflow({ id: "wf-2", name: "整章导出流程" }),
    ]);
    updateSpy.mockImplementation(async (_id, _version, payload) => workflow({
      version: 2,
      draft_version: 2,
      draft_graph: payload.draft_graph as WorkflowGraph,
    }));

    renderStudio();
    await screen.findByText("流程编排");
    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });

    const select = screen.getByLabelText("选择工作流");
    await act(async () => {
      fireEvent.change(select, { target: { value: "wf-2" } });
    });
    // 切换前先把旧工作流的草稿落库。
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy.mock.calls[0][2].draft_graph?.nodes).toHaveLength(1);
    await waitFor(() =>
      expect(screen.getByLabelText("选择工作流")).toHaveProperty("value", "wf-2"),
    );

    // 保存失败：切换被阻止，停留在原工作流并提示。
    await act(async () => {
      screen.getByRole("button", { name: /解析原作/ }).click();
    });
    updateSpy.mockRejectedValueOnce(new Error("网络中断"));
    const back = screen.getByLabelText("选择工作流");
    await act(async () => {
      fireEvent.change(back, { target: { value: "wf-1" } });
    });
    await waitFor(() =>
      expect(
        screen.getByText("当前工作流保存失败，已保持在原工作流，请重试后再切换"),
      ).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("选择工作流")).toHaveProperty("value", "wf-2");
  });

  it("PAGE 运行范围可选择其他章节的页面（不再钉死第一章）", async () => {
    const chapter = (id: string, title: string, ordinal: number) => ({
      id, project_id: "project-1", title, ordinal, status: "READY",
      current_source_revision_id: null, source_character_count: 0, segment_count: 0,
      page_count: 1, coverage_ratio: 1,
      created_at: "2026-08-27T00:00:00Z", updated_at: "2026-08-27T00:00:00Z", version: 1,
    });
    const page = (id: string, chapterId: string, pageNumber: number): MangaPage => ({
      id, chapter_id: chapterId, page_number: pageNumber, revision_no: 1,
      page_function: "dialogue", panel_count: 4, reading_direction: "rtl",
      resolution: "1K", status: "PLANNED", estimated_text_chars: 40,
      estimated_bubbles: 2, source_coverage: { complete: true, ranges: [] },
      selected_candidate_id: null, storyboard_version: 1,
      selected_candidate_ack_version: 1, continuity_status: "PASSED",
      scene_ids: [], beat_ids: [], version: 1,
    });
    chaptersSpy.mockResolvedValue([chapter("ch-1", "第一章", 1), chapter("ch-2", "第二章", 2)]);
    pagesSpy.mockImplementation(async (chapterId: string) =>
      chapterId === "ch-2"
        ? [page("p-2-1", "ch-2", 1), page("p-2-2", "ch-2", 2)]
        : [page("p-1-1", "ch-1", 1)]);

    renderStudio();
    await screen.findByText("流程编排");
    // PAGE 模式默认展示第一章的页面。
    const chapterSelect = screen.getByLabelText("页面所属章节");
    expect(chapterSelect).toHaveValue("ch-1");
    await waitFor(() => expect(screen.getByLabelText("运行目标")).toHaveValue("p-1-1"));

    // 切到第二章：页面列表与运行目标都来自第二章。
    fireEvent.change(chapterSelect, { target: { value: "ch-2" } });
    await waitFor(() => expect(pagesSpy).toHaveBeenCalledWith("ch-2"));
    await waitFor(() => expect(screen.getByLabelText("运行目标")).toHaveValue("p-2-1"));
    expect(screen.getByRole("option", { name: "第 2 页" })).toBeInTheDocument();
  });
});

describe("WorkflowStudio 运行状态显示", () => {
  function nodeRun(overrides: Partial<WorkflowNodeRun> = {}): WorkflowNodeRun {
    return {
      id: "nr-1", workflow_run_id: "run-1", node_id: "node-1", node_type: "control.approval",
      status: "WAITING", job_id: null, input_snapshot: {}, output_refs: {}, attempt_count: 0,
      started_at: null, finished_at: null, error_code: null, error_message: null,
      ...overrides,
    };
  }
  function run(overrides: Partial<WorkflowRun> = {}): WorkflowRun {
    return {
      id: "run-1", workflow_id: "wf-1", workflow_version_id: "ver-1", project_id: "project-1",
      scope_type: "CHAPTER", scope_id: "ch-1", status: "RUNNING", start_node_ids: [], stop_node_ids: [],
      node_runs: [], created_at: "2026-08-27T00:00:00Z", updated_at: "2026-08-27T00:00:00Z", version: 1,
      ...overrides,
    };
  }

  beforeEach(() => {
    window.localStorage.clear();
    projectSpy.mockReset().mockResolvedValue({
      id: "project-1",
      name: "测试项目",
      language: "zh-CN",
      reading_direction: "rtl",
      page_ratio: "b5_portrait",
      default_resolution: "2K",
      draft_resolution: "1K",
      workflow_mode: "SEMI_AUTO",
      default_concurrency: 1,
      default_style_id: null,
      consistency_check_enabled: true,
      text_model_alias: "text.fast",
      last_image_model_alias: null,
      default_text_model_id: null,
      last_image_model_id: null,
      created_at: "2026-08-27T00:00:00Z",
      updated_at: "2026-08-27T00:00:00Z",
      version: 1,
    });
    workflowsSpy.mockReset().mockResolvedValue([workflow()]);
    catalogSpy.mockReset().mockResolvedValue([nodeType]);
    modelsSpy.mockReset().mockResolvedValue([]);
    chaptersSpy.mockReset().mockResolvedValue([{
      id: "ch-1", project_id: "project-1", title: "第一章", ordinal: 1, status: "READY",
      current_source_revision_id: null, source_character_count: 0, segment_count: 0,
      page_count: 1, coverage_ratio: 1,
      created_at: "2026-08-27T00:00:00Z", updated_at: "2026-08-27T00:00:00Z", version: 1,
    }]);
    pagesSpy.mockReset().mockResolvedValue([]);
    versionsSpy.mockReset().mockResolvedValue([]);
    runsSpy.mockReset().mockResolvedValue([]);
    startRunSpy.mockReset();
    updateSpy.mockReset();
    publishSpy.mockReset();
  });

  it("运行启动后，审批行与进度来自轮询列表而非启动快照", async () => {
    // 启动响应快照：两个节点都还在 WAITING（后端 202 立即返回）。
    const snapshot = run({
      node_runs: [
        nodeRun({ node_id: "node-1" }),
        nodeRun({ id: "nr-2", node_id: "node-2" }),
      ],
    });
    // 后端推进后的轮询结果：node-1 已完成，node-2 到达 WAITING_APPROVAL。
    const progressed = run({
      node_runs: [
        nodeRun({
          node_id: "node-1", status: "COMPLETED",
          started_at: "2026-08-27T00:00:01Z", finished_at: "2026-08-27T00:00:02Z",
        }),
        nodeRun({ id: "nr-2", node_id: "node-2", status: "WAITING_APPROVAL" }),
      ],
    });
    const started = deferred<WorkflowRun>();
    startRunSpy.mockReturnValue(started.promise);
    runsSpy.mockResolvedValue([]);

    renderStudio();
    await screen.findByText("流程编排");
    // 运行范围默认 PAGE：切到 CHAPTER 并等章节就位。
    fireEvent.change(screen.getByLabelText("运行范围类型"), { target: { value: "CHAPTER" } });
    await waitFor(() => expect(screen.getByLabelText("运行目标")).toHaveValue("ch-1"));
    await act(async () => {
      screen.getByRole("button", { name: "运行工作流" }).click();
    });
    // 启动成功后，下一次轮询返回同 ID 的更新运行。
    runsSpy.mockResolvedValue([progressed]);
    await act(async () => {
      started.resolve(snapshot);
      await started.promise;
    });

    // 审批行来自轮询数据：快照里没有 WAITING_APPROVAL，冻结在快照上就永远不出现。
    expect(await screen.findByText("采用候选后继续")).toBeInTheDocument();
    // 进度计数 1/2 同样来自轮询数据（快照是 0/2）。
    expect(screen.getByText(/1\/2/)).toBeInTheDocument();
  });

  it("PAUSED（审批栅栏）运行显示中文状态，且取消按钮存在并调用 cancel", async () => {
    const cancelSpy = vi.spyOn(api, "cancelWorkflowRun");
    cancelSpy.mockResolvedValue(run({ status: "CANCELLED" }));
    runsSpy.mockResolvedValue([run({ status: "PAUSED" })]);

    renderStudio();
    await screen.findByText("流程编排");

    // 页脚不再裸显英文 PAUSED。
    expect(await screen.findByText(/运行 暂停中/)).toBeInTheDocument();
    // 取消按钮在 PAUSED 态必须渲染：cancel_run 接受 PAUSED，否则审批
    // 栅栏态的 run 没有任何停止途径（同 scope 再起 run 会被 409 拒绝）。
    const cancelButton = screen.getByRole("button", { name: /取消/ });
    expect(cancelButton).toBeEnabled();
    await act(async () => {
      cancelButton.click();
    });
    await waitFor(() => expect(cancelSpy).toHaveBeenCalledWith("run-1"));
    // PAUSED 不是 FAILED：不渲染重试入口。
    expect(screen.queryByRole("button", { name: /重试/ })).toBeNull();
  });

  it("FAILED 运行在页脚提供重试入口，调用 retryWorkflowRun 复制同一范围", async () => {
    const retrySpy = vi.spyOn(api, "retryWorkflowRun");
    retrySpy.mockResolvedValue(run({ id: "run-2", status: "WAITING" }));
    runsSpy.mockResolvedValue([run({ status: "FAILED" })]);

    renderStudio();
    await screen.findByText("流程编排");

    expect(await screen.findByText(/运行 已失败/)).toBeInTheDocument();
    // PAUSED/终态外不渲染取消；FAILED 只露出重试。
    expect(screen.queryByRole("button", { name: /^取消$/ })).toBeNull();
    await act(async () => {
      screen.getByRole("button", { name: /重试/ }).click();
    });
    await waitFor(() => expect(retrySpy).toHaveBeenCalledWith("run-1"));
  });

  // #380：旧谓词只认 RUNNING——审批栅栏把 run 置为 PAUSED 后轮询停摆，
  // 审批通过/取消后的状态永远不再更新。新契约：RUNNING → 3s；否则
  // PAUSED → 10s 折中降频；全部终态/空列表 → 停止轮询。
  it("轮询间隔契约：RUNNING 3s、PAUSED 10s、混合时 RUNNING 优先、终态停止", () => {
    expect(workflowRunsPollInterval([run({ status: "RUNNING" })])).toBe(3000);
    expect(workflowRunsPollInterval([
      run({ id: "run-a", status: "COMPLETED" }),
      run({ id: "run-b", status: "RUNNING" }),
    ])).toBe(3000);
    // RUNNING 与 PAUSED 混合：保持 3s 快档。
    expect(workflowRunsPollInterval([
      run({ id: "run-a", status: "RUNNING" }),
      run({ id: "run-b", status: "PAUSED" }),
    ])).toBe(3000);
    // PAUSED（审批栅栏）态仍轮询，只是降频到 10s。
    expect(workflowRunsPollInterval([run({ status: "PAUSED" })])).toBe(10000);
    expect(workflowRunsPollInterval([
      run({ id: "run-a", status: "COMPLETED" }),
      run({ id: "run-b", status: "PAUSED" }),
    ])).toBe(10000);
    expect(workflowRunsPollInterval([run({ status: "COMPLETED" })])).toBe(false);
    expect(workflowRunsPollInterval([])).toBe(false);
    expect(workflowRunsPollInterval(undefined)).toBe(false);
  });

  // #381：generator.page 的审批选择器来源 imageModels 为空时，「确认继续」
  // 因 !drawModel 恒真而永久禁用——必须在审批条内给出设置指引与取消出路，
  // 而不是一个空选择器加无提示的死按钮。
  it("无可用图像模型时审批条渲染设置指引（含取消出路），不再是无提示死按钮", async () => {
    runsSpy.mockResolvedValue([run({
      status: "PAUSED",
      node_runs: [nodeRun({ node_id: "gen-1", node_type: "generator.page", status: "WAITING_APPROVAL" })],
    })]);
    modelsSpy.mockResolvedValue([]);

    renderStudio();
    await screen.findByText("流程编排");

    expect(await screen.findByText("单页生成等待选择模型")).toBeInTheDocument();
    expect(await screen.findByText(/未配置可用图像模型/)).toBeInTheDocument();
    // 指引给出系统设置入口（供应商与模型目录在 /settings 管理）。
    expect(screen.getByRole("link", { name: "前往设置" })).toHaveAttribute("href", "/settings");
    // 出路之二：取消按钮已在页脚渲染（PAUSED 态），且可用。
    expect(screen.getByRole("button", { name: /取消/ })).toBeEnabled();
    // 空目录下不再渲染无法选择的模型选择器。
    expect(screen.queryByLabelText("选择图片模型")).toBeNull();
  });

  // #394：models 查询失败且无任何缓存 data 时，imageModels 同为空，但这是
  // 目录读取错误而非未配置——错误态必须给出「读取失败」文案与重试出路，
  // 不能落进 #381 的空目录指引把错误伪装成配置问题。
  it("目录读取失败且无缓存时审批条渲染错误态与重试，不出现未配置指引", async () => {
    runsSpy.mockResolvedValue([run({
      status: "PAUSED",
      node_runs: [nodeRun({ node_id: "gen-1", node_type: "generator.page", status: "WAITING_APPROVAL" })],
    })]);
    modelsSpy.mockRejectedValue(new Error("目录服务暂不可用"));

    renderStudio();
    await screen.findByText("流程编排");

    expect(await screen.findByText(/模型目录读取失败/)).toBeInTheDocument();
    expect(screen.queryByText(/未配置可用图像模型/)).toBeNull();
    // 错误态不渲染模型选择器与设置指引：目录内容未知，指引会误导用户去改配置。
    expect(screen.queryByLabelText("选择图片模型")).toBeNull();
    expect(screen.queryByRole("link", { name: "前往设置" })).toBeNull();
    // 重试按钮走 models.refetch（同一 queryFn），点击后目录被重新请求。
    const callsBefore = modelsSpy.mock.calls.length;
    await act(async () => {
      screen.getByRole("button", { name: "重试" }).click();
    });
    await waitFor(() => expect(modelsSpy.mock.calls.length).toBeGreaterThan(callsBefore));
  });

  // #394 回归：目录请求成功但为空目录时仍走 #381 空目录指引，不误入错误态。
  it("目录成功返回空数组时维持未配置指引，不渲染读取失败错误态", async () => {
    runsSpy.mockResolvedValue([run({
      status: "PAUSED",
      node_runs: [nodeRun({ node_id: "gen-1", node_type: "generator.page", status: "WAITING_APPROVAL" })],
    })]);
    modelsSpy.mockResolvedValue([]);

    renderStudio();
    await screen.findByText("流程编排");

    expect(await screen.findByText(/未配置可用图像模型/)).toBeInTheDocument();
    expect(screen.queryByText(/模型目录读取失败/)).toBeNull();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    // 空目录指引保留设置入口。
    expect(screen.getByRole("link", { name: "前往设置" })).toHaveAttribute("href", "/settings");
  });

  it("有可用图像模型时审批条渲染模型选择器，不渲染空目录指引", async () => {
    const imageModel: ModelCapability = {
      catalog_id: "mi-1",
      connection_id: "conn-1",
      provider: "甲",
      protocol: "vertex",
      model_id: "image-model-a",
      logical_alias: "image.a",
      display_name: "图片模型A",
      model_type: "IMAGE",
      input_modalities: ["IMAGE"],
      output_modalities: ["IMAGE"],
      operations: ["image_edit"],
      resolutions: ["1K", "2K", "4K"],
      preview_resolutions: ["1K"],
      max_reference_images: 4,
      regions: [],
      confidence: "DECLARED",
      enabled: true,
      display_enabled: true,
      auto_eligible: true,
      priority: 1,
    };
    runsSpy.mockResolvedValue([run({
      status: "PAUSED",
      node_runs: [nodeRun({ node_id: "gen-1", node_type: "generator.page", status: "WAITING_APPROVAL" })],
    })]);
    modelsSpy.mockResolvedValue([imageModel]);

    renderStudio();
    await screen.findByText("流程编排");

    expect(await screen.findByText("单页生成等待选择模型")).toBeInTheDocument();
    expect(screen.queryByText(/未配置可用图像模型/)).toBeNull();
    expect(screen.queryByRole("link", { name: "前往设置" })).toBeNull();
    const modelSelect = await screen.findByLabelText("选择图片模型");
    await waitFor(() => {
      expect(within(modelSelect).getByRole("option", { name: "甲 · 图片模型A" })).toBeInTheDocument();
    });
    // 既有行为回归：未选模型前确认继续禁用，选中后解禁。
    const confirm = screen.getByRole("button", { name: "确认继续" });
    expect(confirm).toBeDisabled();
    fireEvent.change(modelSelect, { target: { value: "image.a" } });
    await waitFor(() => expect(confirm).toBeEnabled());
  });

  // round-4 审查（PR #387 后续）：drawModel 只在 select onChange 时写入、无重置
  // 路径——曾选过模型后目录清空会留下残留别名，旧谓词 !drawModel 拦不住，
  // 指引文案旁边出现可点的「确认继续」，点击即向审批接口提交失效别名。
  it("曾选模型后目录清空：指引可见且确认继续禁用，不再提交失效别名", async () => {
    const imageModel: ModelCapability = {
      catalog_id: "mi-1",
      connection_id: "conn-1",
      provider: "甲",
      protocol: "vertex",
      model_id: "image-model-a",
      logical_alias: "image.a",
      display_name: "图片模型A",
      model_type: "IMAGE",
      input_modalities: ["IMAGE"],
      output_modalities: ["IMAGE"],
      operations: ["image_edit"],
      resolutions: ["1K", "2K", "4K"],
      preview_resolutions: ["1K"],
      max_reference_images: 4,
      regions: [],
      confidence: "DECLARED",
      enabled: true,
      display_enabled: true,
      auto_eligible: true,
      priority: 1,
    };
    runsSpy.mockResolvedValue([run({
      status: "PAUSED",
      node_runs: [nodeRun({ node_id: "gen-1", node_type: "generator.page", status: "WAITING_APPROVAL" })],
    })]);
    modelsSpy.mockResolvedValueOnce([imageModel]).mockResolvedValue([]);

    const { client } = renderStudio();
    await screen.findByText("流程编排");

    const modelSelect = await screen.findByLabelText("选择图片模型");
    await waitFor(() => {
      expect(within(modelSelect).getByRole("option", { name: "甲 · 图片模型A" })).toBeInTheDocument();
    });
    const confirm = screen.getByRole("button", { name: "确认继续" });
    fireEvent.change(modelSelect, { target: { value: "image.a" } });
    await waitFor(() => expect(confirm).toBeEnabled());

    // 供应商删除后目录重取为空（例如窗口重新聚焦触发 refetch）。
    await act(async () => { await client.refetchQueries({ queryKey: ["models"] }); });
    expect(await screen.findByText(/未配置可用图像模型/)).toBeInTheDocument();
    expect(confirm).toBeDisabled();
  });

  it("曾选模型被删但目录仍有其他模型：确认继续禁用直到重新选择", async () => {
    const imageModelA: ModelCapability = {
      catalog_id: "mi-1",
      connection_id: "conn-1",
      provider: "甲",
      protocol: "vertex",
      model_id: "image-model-a",
      logical_alias: "image.a",
      display_name: "图片模型A",
      model_type: "IMAGE",
      input_modalities: ["IMAGE"],
      output_modalities: ["IMAGE"],
      operations: ["image_edit"],
      resolutions: ["1K", "2K", "4K"],
      preview_resolutions: ["1K"],
      max_reference_images: 4,
      regions: [],
      confidence: "DECLARED",
      enabled: true,
      display_enabled: true,
      auto_eligible: true,
      priority: 1,
    };
    const imageModelB: ModelCapability = { ...imageModelA, catalog_id: "mi-2", model_id: "image-model-b", logical_alias: "image.b", display_name: "图片模型B" };
    runsSpy.mockResolvedValue([run({
      status: "PAUSED",
      node_runs: [nodeRun({ node_id: "gen-1", node_type: "generator.page", status: "WAITING_APPROVAL" })],
    })]);
    modelsSpy.mockResolvedValueOnce([imageModelA]).mockResolvedValue([imageModelB]);

    const { client } = renderStudio();
    await screen.findByText("流程编排");

    const modelSelect = await screen.findByLabelText("选择图片模型");
    await waitFor(() => {
      expect(within(modelSelect).getByRole("option", { name: "甲 · 图片模型A" })).toBeInTheDocument();
    });
    const confirm = screen.getByRole("button", { name: "确认继续" });
    fireEvent.change(modelSelect, { target: { value: "image.a" } });
    await waitFor(() => expect(confirm).toBeEnabled());

    // image.a 的行从目录消失、只剩 image.b：残留选择不得让按钮保持可点。
    await act(async () => { await client.refetchQueries({ queryKey: ["models"] }); });
    await waitFor(() => {
      expect(within(modelSelect).getByRole("option", { name: "甲 · 图片模型B" })).toBeInTheDocument();
    });
    expect(confirm).toBeDisabled();
    fireEvent.change(modelSelect, { target: { value: "image.b" } });
    await waitFor(() => expect(confirm).toBeEnabled());
  });
});

const createWorkflowSpy = vi.spyOn(api, "createWorkflow");

describe("WorkflowStudio 默认工作流部分失败与错误面（#545-2 / #545-7）", () => {
  beforeEach(() => {
    window.localStorage.clear();
    projectSpy.mockReset().mockResolvedValue({
      id: "project-1",
      name: "测试项目",
      language: "zh-CN",
      reading_direction: "rtl",
      page_ratio: "b5_portrait",
      default_resolution: "2K",
      draft_resolution: "1K",
      workflow_mode: "SEMI_AUTO",
      default_concurrency: 1,
      default_style_id: null,
      consistency_check_enabled: true,
      text_model_alias: "text.fast",
      last_image_model_alias: null,
      default_text_model_id: null,
      last_image_model_id: null,
      created_at: "2026-08-27T00:00:00Z",
      updated_at: "2026-08-27T00:00:00Z",
      version: 1,
    });
    workflowsSpy.mockReset().mockResolvedValue([workflow()]);
    catalogSpy.mockReset().mockResolvedValue([nodeType]);
    modelsSpy.mockReset().mockResolvedValue([]);
    chaptersSpy.mockReset().mockResolvedValue([]);
    pagesSpy.mockReset().mockResolvedValue([]);
    versionsSpy.mockReset().mockResolvedValue([]);
    runsSpy.mockReset().mockResolvedValue([]);
    updateSpy.mockReset();
    publishSpy.mockReset();
    createWorkflowSpy.mockReset();
  });

  it("TEST-WF-CREATE1 部分创建失败可恢复，重试只补建缺失的整章导出流程（#545-2）", async () => {
    let listCalls = 0;
    // 首次列表为空触发自动创建；失败后重试重拉时，服务器上已有建成的单页流程。
    workflowsSpy.mockImplementation(async () => {
      listCalls += 1;
      return listCalls === 1 ? [] : [workflow({ id: "wf-page", name: "单页生产流程" })];
    });
    createWorkflowSpy.mockImplementation(async (_projectId: string, name?: string) => {
      if (name === "整章导出流程") throw new Error("整章创建被拒");
      return workflow({ id: "wf-page", name: "单页生产流程" });
    });

    renderStudio();
    expect(await screen.findByText("默认工作流创建失败")).toBeInTheDocument();
    expect(screen.getByText(/整章创建被拒/)).toBeInTheDocument();

    createWorkflowSpy.mockResolvedValue(workflow({ id: "wf-export", name: "整章导出流程" }));
    fireEvent.click(screen.getByRole("button", { name: "重试创建" }));
    await screen.findByText("流程编排");

    const createdNames = createWorkflowSpy.mock.calls.map((call) => call[1]);
    expect(createdNames).toEqual(["单页生产流程", "整章导出流程", "整章导出流程"]);
  });

  it("TEST-WF-CREATE2 全部创建失败后重试仍会补建两个流程（#545-2）", async () => {
    workflowsSpy.mockResolvedValue([]);
    createWorkflowSpy.mockRejectedValueOnce(new Error("网络中断"));
    renderStudio();
    expect(await screen.findByText("默认工作流创建失败")).toBeInTheDocument();
    createWorkflowSpy.mockResolvedValue(workflow({ id: "wf-page" }));
    fireEvent.click(screen.getByRole("button", { name: "重试创建" }));
    await screen.findByText("流程编排");
    expect(createWorkflowSpy.mock.calls.map((call) => call[1])).toEqual([
      "单页生产流程",
      "整章导出流程",
      "单页生产流程",
      "整章导出流程",
    ]);
  });

  it("TEST-WF-VER1 发布版本读取失败不再谎报「尚未发布」，重试后恢复（#545-7）", async () => {
    versionsSpy.mockRejectedValueOnce(new Error("版本接口 500"));
    renderStudio();
    await screen.findByText("流程编排");
    await waitFor(() => expect(screen.getAllByText("读取失败").length).toBeGreaterThan(0));
    expect(screen.queryByText("尚未发布")).not.toBeInTheDocument();
    versionsSpy.mockResolvedValueOnce([{
      id: "ver-1",
      workflow_id: "wf-1",
      revision: 3,
      graph: emptyGraph,
      graph_checksum: "abc",
      validation_report: { valid: true, issues: [], topological_order: [] },
      published_at: "2026-08-27T00:00:00Z",
    }]);
    fireEvent.click(screen.getAllByRole("button", { name: "重试" })[0]);
    await waitFor(() => expect(versionsSpy).toHaveBeenCalledTimes(2));
    // 状态条与版本列表都应显示 V3（不再是读取失败 / 尚未发布）。
    await waitFor(() => expect(screen.getAllByText("V3").length).toBeGreaterThanOrEqual(2));
  });

  it("TEST-WF-LIB1 节点库读取失败显示错误与重试，恢复后可添加节点（#545-7）", async () => {
    catalogSpy.mockRejectedValueOnce(new Error("目录 503"));
    renderStudio();
    await screen.findByText("流程编排");
    expect(await screen.findByText(/节点库读取失败/)).toBeInTheDocument();
    expect(screen.getByText(/目录 503/)).toBeInTheDocument();
    catalogSpy.mockResolvedValueOnce([nodeType]);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("button", { name: /解析原作/ })).toBeInTheDocument();
  });
});
