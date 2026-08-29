"use client";

import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";

import { STORAGE_KEY } from "./graph-model";
import type { FlowEdge, FlowNode } from "./types";

type PersistedFlow = { nodes?: FlowNode[]; edges?: FlowEdge[] };

export function useWorkflowPersistence({
  resolvedProjectId,
  nodes,
  edges,
  setNodes,
  setEdges,
}: {
  resolvedProjectId: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
  setNodes: Dispatch<SetStateAction<FlowNode[]>>;
  setEdges: Dispatch<SetStateAction<FlowEdge[]>>;
}) {
  const [saved, setSaved] = useState(true);
  const [toast, setToast] = useState("");

  const showToast = useCallback((message: string) => {
    setToast(message);
    window.setTimeout(() => setToast(""), 1800);
  }, []);

  const saveFlow = useCallback(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ projectId: resolvedProjectId || null, nodes, edges }));
    setSaved(true);
    showToast("工作流已保存到本机");
  }, [edges, nodes, resolvedProjectId, showToast]);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (!stored) return;
      const parsed = JSON.parse(stored) as PersistedFlow;
      window.queueMicrotask(() => {
        if (parsed.nodes?.length) setNodes(parsed.nodes);
        if (parsed.edges) setEdges(parsed.edges);
      });
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, [setEdges, setNodes]);

  return { saved, setSaved, toast, showToast, saveFlow };
}
