import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { initialEdges, initialNodes, STORAGE_KEY } from "./graph-model";
import type { FlowEdge, FlowNode } from "./types";
import { useWorkflowPersistence } from "./use-workflow-persistence";

function useHarness(options: { resolvedProjectId: string }) {
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodes);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdges);
  const persistence = useWorkflowPersistence({ resolvedProjectId: options.resolvedProjectId, nodes, edges, setNodes, setEdges });
  return { ...persistence, nodes, edges, setNodes, setEdges };
}

describe("useWorkflowPersistence", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("saveFlow 把当前图与项目 id 写入 localStorage 并标记已保存", () => {
    const { result } = renderHook(() => useHarness({ resolvedProjectId: "project-1" }));

    act(() => {
      result.current.setNodes([...initialNodes, createExtraNode()]);
    });
    expect(result.current.nodes).toHaveLength(initialNodes.length + 1);

    act(() => {
      result.current.saveFlow();
    });

    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}");
    expect(stored.projectId).toBe("project-1");
    expect(stored.nodes).toHaveLength(initialNodes.length + 1);
    expect(stored.edges).toEqual(initialEdges);
    expect(result.current.saved).toBe(true);
    expect(result.current.toast).toBe("工作流已保存到本机");
  });

  it("未连接项目时保存的 projectId 为 null", () => {
    const { result } = renderHook(() => useHarness({ resolvedProjectId: "" }));

    act(() => {
      result.current.saveFlow();
    });

    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}").projectId).toBeNull();
  });

  it("保存提示 1800ms 后自动消失", () => {
    vi.useFakeTimers();
    try {
      const { result } = renderHook(() => useHarness({ resolvedProjectId: "" }));

      act(() => {
        result.current.saveFlow();
      });
      expect(result.current.toast).toBe("工作流已保存到本机");

      act(() => {
        vi.advanceTimersByTime(1800);
      });
      expect(result.current.toast).toBe("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("挂载时从 localStorage 恢复节点与连线", async () => {
    const storedNodes = [createExtraNode()];
    const storedEdges = [initialEdges[0]];
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ projectId: "project-2", nodes: storedNodes, edges: storedEdges }));

    const { result } = renderHook(() => useHarness({ resolvedProjectId: "" }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(result.current.nodes).toEqual(storedNodes);
    expect(result.current.edges).toEqual(storedEdges);
  });

  it("恢复的内容损坏时清空存储且不影响初始图", async () => {
    window.localStorage.setItem(STORAGE_KEY, "{not-json");

    const { result } = renderHook(() => useHarness({ resolvedProjectId: "" }));

    await act(async () => {
      await Promise.resolve();
    });

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(result.current.nodes).toEqual(initialNodes);
    expect(result.current.edges).toEqual(initialEdges);
  });
});

function createExtraNode(): FlowNode {
  return {
    ...initialNodes[0],
    id: "probe-1",
    title: "探针节点",
  };
}
