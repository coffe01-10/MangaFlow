"use client";

import { Check } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
} from "react";
import { api } from "@/lib/api";
import styles from "./workflow-editor.module.css";
import {
  ACTIVE_PROJECT_KEY,
  NODE_HEIGHT,
  NODE_WIDTH,
  WORLD_HEIGHT,
  WORLD_WIDTH,
  createNode,
  initialEdges,
  initialNodes,
  templateMap,
} from "./workflow-editor/graph-model";
import { clamp } from "./workflow-editor/geometry";
import { NodePalette } from "./workflow-editor/node-palette";
import { FlowCanvas } from "./workflow-editor/flow-canvas";
import { EditorTopbar } from "./workflow-editor/editor-topbar";
import { NodeInspector } from "./workflow-editor/node-inspector";
import { RunMonitor } from "./workflow-editor/run-monitor";
import { useWorkflowPersistence } from "./workflow-editor/use-workflow-persistence";
import { useViewportInteractions } from "./workflow-editor/use-viewport-interactions";
import type { FlowEdge, FlowNode } from "./workflow-editor/types";

export function WorkflowEditor() {
  const viewportRef = useRef<HTMLDivElement>(null);
  const runTokenRef = useRef(0);
  const [activeProjectId, setActiveProjectId] = useState(() => typeof window === "undefined"
    ? ""
    : window.localStorage.getItem(ACTIVE_PROJECT_KEY) ?? "");
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodes);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>("adapter-1");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [logOpen, setLogOpen] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([]);
  const [search, setSearch] = useState("");

  const projectsQuery = useQuery({ queryKey: ["workflow-projects"], queryFn: api.projects });
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: api.models });
  const resolvedProjectId = projectsQuery.data?.some((project) => project.id === activeProjectId)
    ? activeProjectId
    : projectsQuery.data?.[0]?.id ?? "";
  const activeProject = projectsQuery.data?.find((project) => project.id === resolvedProjectId) ?? null;
  const assetsQuery = useQuery({
    queryKey: ["workflow-assets", resolvedProjectId],
    queryFn: () => api.assets(resolvedProjectId),
    enabled: Boolean(resolvedProjectId),
  });
  const chaptersQuery = useQuery({
    queryKey: ["workflow-chapters", resolvedProjectId],
    queryFn: () => api.chapters(resolvedProjectId),
    enabled: Boolean(resolvedProjectId),
  });

  const { saved, setSaved, toast, showToast, saveFlow } = useWorkflowPersistence({
    resolvedProjectId,
    nodes,
    edges,
    setNodes,
    setEdges,
  });

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  const deleteSelection = useCallback(() => {
    if (selectedNodeId) {
      setNodes((current) => current.filter((node) => node.id !== selectedNodeId));
      setEdges((current) => current.filter((item) => item.source !== selectedNodeId && item.target !== selectedNodeId));
      setSelectedNodeId(null);
      setSaved(false);
      return;
    }
    if (selectedEdgeId) {
      setEdges((current) => current.filter((item) => item.id !== selectedEdgeId));
      setSelectedEdgeId(null);
      setSaved(false);
    }
  }, [selectedEdgeId, selectedNodeId, setSaved]);

  const {
    pan,
    setPan,
    zoom,
    draftEnd,
    connectionAnchor,
    worldPoint,
    beginNodeDrag,
    beginOutputConnection,
    beginInputConnection,
    beginPan,
    handleWheel,
    zoomBy,
    fitToView,
  } = useViewportInteractions({
    viewportRef,
    nodes,
    nodeMap,
    edges,
    setNodes,
    setEdges,
    setSaved,
    setSelectedNodeId,
    setSelectedEdgeId,
    saveFlow,
    deleteSelection,
  });

  function addNode(templateKey: string, point?: { x: number; y: number }) {
    const rect = viewportRef.current?.getBoundingClientRect();
    const fallback = {
      x: ((rect?.width ?? 900) / 2 - pan.x) / zoom - NODE_WIDTH / 2,
      y: ((rect?.height ?? 620) / 2 - pan.y) / zoom - NODE_HEIGHT / 2,
    };
    const offsets = [
      { x: 0, y: 0 },
      { x: -310, y: -260 },
      { x: 310, y: -260 },
      { x: 0, y: 260 },
      { x: 310, y: 260 },
      { x: -310, y: 260 },
      { x: 310, y: 0 },
      { x: -310, y: 0 },
      { x: 0, y: -260 },
      { x: 0, y: 520 },
      { x: 620, y: 260 },
      { x: -620, y: 260 },
      { x: 620, y: 0 },
      { x: -620, y: 0 },
    ];
    const target = point ?? offsets
      .map((offset) => ({
        x: clamp(fallback.x + offset.x, 0, WORLD_WIDTH - NODE_WIDTH),
        y: clamp(fallback.y + offset.y, 0, WORLD_HEIGHT - NODE_HEIGHT),
      }))
      .find((candidate) => nodes.every((node) => !(
        candidate.x < node.x + NODE_WIDTH + 22
        && candidate.x + NODE_WIDTH + 22 > node.x
        && candidate.y < node.y + NODE_HEIGHT + 22
        && candidate.y + NODE_HEIGHT + 22 > node.y
      ))) ?? fallback;
    const id = `${templateKey}-${Date.now()}`;
    const next = createNode(templateKey, id, clamp(target.x, 0, WORLD_WIDTH - NODE_WIDTH), clamp(target.y, 0, WORLD_HEIGHT - NODE_HEIGHT));
    setNodes((current) => [...current, next]);
    setSelectedNodeId(id);
    setSelectedEdgeId(null);
    setSaved(false);
  }

  function handleDrop(event: ReactDragEvent<HTMLDivElement>) {
    event.preventDefault();
    const templateKey = event.dataTransfer.getData("application/x-mangaflow-node");
    if (!templateMap.has(templateKey)) return;
    const point = worldPoint(event.clientX, event.clientY);
    addNode(templateKey, { x: point.x - NODE_WIDTH / 2, y: point.y - 30 });
  }

  function updateNode(patch: Partial<FlowNode>) {
    if (!selectedNodeId) return;
    setNodes((current) => current.map((node) => node.id === selectedNodeId ? { ...node, ...patch } : node));
    setSaved(false);
  }

  function updateSettings(patch: Partial<FlowNode["settings"]>) {
    if (!selectedNodeId) return;
    setNodes((current) => current.map((node) => node.id === selectedNodeId ? {
      ...node,
      settings: { ...node.settings, ...patch },
    } : node));
    setSaved(false);
  }

  function chooseProject(projectId: string) {
    setActiveProjectId(projectId);
    window.localStorage.setItem(ACTIVE_PROJECT_KEY, projectId);
    const nextProject = projectsQuery.data?.find((project) => project.id === projectId);
    showToast(nextProject ? `已连接项目：${nextProject.name}` : "项目连接已更新");
  }

  function togglePaletteGroup(label: string) {
    setCollapsedGroups((current) => current.includes(label)
      ? current.filter((item) => item !== label)
      : [...current, label]);
  }

  async function runWorkflow() {
    if (isRunning) return;
    const token = ++runTokenRef.current;
    setIsRunning(true);
    setLogOpen(true);
    const order = ["source-1", "parser-1", "adapter-1", "director-1", "assets-1", "generator-1", "quality-1", "export-1"];
    setNodes((current) => current.map((node) => ({ ...node, status: order.includes(node.id) ? "idle" : node.status })));
    for (const id of order) {
      if (token !== runTokenRef.current) return;
      setNodes((current) => current.map((node) => node.id === id ? { ...node, status: "running" } : node));
      await new Promise((resolve) => window.setTimeout(resolve, 430));
      setNodes((current) => current.map((node) => node.id === id ? { ...node, status: "done" } : node));
    }
    setIsRunning(false);
    showToast("整条工作流运行完成");
  }

  function exportFlow() {
    const blob = new Blob([JSON.stringify({ version: 1, projectId: resolvedProjectId || null, nodes, edges }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "mangaflow-workflow.json";
    anchor.click();
    URL.revokeObjectURL(url);
    showToast("工作流 JSON 已导出");
  }

  const assetCount = assetsQuery.data?.length ?? 0;
  const chapterCount = chaptersQuery.data?.length ?? 0;
  const projectDataLoading = Boolean(resolvedProjectId) && (assetsQuery.isLoading || chaptersQuery.isLoading);
  const projectDataError = projectsQuery.isError || assetsQuery.isError || chaptersQuery.isError;

  const centerSelectedNode = useCallback(() => {
    if (selectedNode) setPan({ x: 360 - selectedNode.x * zoom, y: 260 - selectedNode.y * zoom });
  }, [selectedNode, setPan, zoom]);

  return (
    <div className={styles.editor}>
      <EditorTopbar
        projects={projectsQuery.data ?? []}
        projectsLoading={projectsQuery.isLoading}
        resolvedProjectId={resolvedProjectId}
        chooseProject={chooseProject}
        projectDataError={projectDataError}
        projectDataLoading={projectDataLoading}
        chapterCount={chapterCount}
        assetCount={assetCount}
        saved={saved}
        isRunning={isRunning}
        exportFlow={exportFlow}
        saveFlow={saveFlow}
        runWorkflow={runWorkflow}
      />

      <div className={`${styles.body} ${inspectorOpen ? "" : styles.inspectorClosed}`}>
        <NodePalette
          search={search}
          setSearch={setSearch}
          collapsedGroups={collapsedGroups}
          togglePaletteGroup={togglePaletteGroup}
          addNode={addNode}
        />

        <FlowCanvas
          viewportRef={viewportRef}
          nodes={nodes}
          edges={edges}
          nodeMap={nodeMap}
          selectedNodeId={selectedNodeId}
          selectedEdgeId={selectedEdgeId}
          setSelectedNodeId={setSelectedNodeId}
          setSelectedEdgeId={setSelectedEdgeId}
          activeProject={activeProject}
          projectDataError={projectDataError}
          projectsLoading={projectsQuery.isLoading}
          assetCount={assetCount}
          chapterCount={chapterCount}
          assetsLoading={assetsQuery.isLoading}
          chaptersLoading={chaptersQuery.isLoading}
          pan={pan}
          zoom={zoom}
          draftEnd={draftEnd}
          connectionAnchor={connectionAnchor}
          beginPan={beginPan}
          handleWheel={handleWheel}
          handleDrop={handleDrop}
          beginNodeDrag={beginNodeDrag}
          beginOutputConnection={beginOutputConnection}
          beginInputConnection={beginInputConnection}
          deleteSelection={deleteSelection}
          onCenterSelectedNode={centerSelectedNode}
          zoomBy={zoomBy}
          fitToView={fitToView}
          runMonitor={<RunMonitor nodes={nodes} isRunning={isRunning} logOpen={logOpen} setLogOpen={setLogOpen} />}
        />

        <NodeInspector
          selectedNode={selectedNode}
          activeProject={activeProject}
          projectsLoading={projectsQuery.isLoading}
          projectDataError={projectDataError}
          assetCount={assetCount}
          chapterCount={chapterCount}
          models={modelsQuery.data ?? []}
          modelsLoading={modelsQuery.isLoading}
          inspectorOpen={inspectorOpen}
          setInspectorOpen={setInspectorOpen}
          updateNode={updateNode}
          updateSettings={updateSettings}
          deleteSelection={deleteSelection}
        />
      </div>

      {toast && <div className={styles.toast}><Check size={15} />{toast}</div>}
    </div>
  );
}
