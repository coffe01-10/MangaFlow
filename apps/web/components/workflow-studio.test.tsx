import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type WorkflowDefinition, type WorkflowGraph, type WorkflowNodeType } from "@/lib/api";

import WorkflowStudio from "./workflow-studio";

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
const updateSpy = vi.spyOn(api, "updateWorkflow");
const publishSpy = vi.spyOn(api, "publishWorkflow");

function renderStudio() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WorkflowStudio projectId="project-1" />
    </QueryClientProvider>,
  );
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
