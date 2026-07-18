"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  type ImageModelAlias,
  type Resolution,
  type WorkflowDefinition,
  type WorkflowGraph,
  type WorkflowGraphNode,
  type WorkflowNodeRun,
  type WorkflowNodeType,
  type WorkflowRun,
} from "@/lib/api";
import {
  ArrowLeft,
  BoxSelect,
  Check,
  ChevronDown,
  CircleAlert,
  Copy,
  Download,
  GitBranch,
  History,
  LayoutGrid,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  Send,
  Trash2,
  Undo2,
  Upload,
  X,
} from "lucide-react";
import Link from "next/link";
import { ChangeEvent, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import styles from "./workflow-studio.module.css";

type StudioNodeData = {
  graphNode: WorkflowGraphNode;
  runStatus?: string;
};
type StudioNode = Node<StudioNodeData, "mangaNode">;
type StudioEdge = Edge<{ sourcePort: string; targetPort: string }>;
type Snapshot = { nodes: StudioNode[]; edges: StudioEdge[] };

const EMPTY_CONFIG: WorkflowGraphNode["config"] = {
  model_alias: null,
  prompt_template: "",
  system_instruction: "",
  temperature: 0.2,
  timeout_seconds: 900,
  max_attempts: 3,
  concurrency: 1,
  resolution: null,
  locked: false,
  notes: "",
  condition: {},
  requires_approval: false,
};

const categoryLabel: Record<string, string> = {
  INPUT: "输入",
  AGENT: "智能处理",
  CONTROL: "控制",
  OUTPUT: "生成与输出",
};

const statusLabel: Record<string, string> = {
  WAITING: "等待",
  RUNNING: "运行中",
  COMPLETED: "已完成",
  WAITING_APPROVAL: "等待确认",
  FAILED: "失败",
  SKIPPED: "已跳过",
  CANCELLED: "已取消",
};

function nodeTone(type: string) {
  if (type.startsWith("source.")) return "input";
  if (type.startsWith("control.")) return "control";
  if (type.startsWith("generator.") || type.startsWith("output.")) return "output";
  if (type.startsWith("quality.")) return "quality";
  return "agent";
}

const MangaNode = memo(function MangaNode({ data, selected }: NodeProps<StudioNode>) {
  const node = data.graphNode;
  return (
    <article className={`${styles.node} ${styles[nodeTone(node.type)]} ${selected ? styles.selected : ""}`}>
      <header><span>{node.type}</span><i>{data.runStatus ? statusLabel[data.runStatus] ?? data.runStatus : "DRAFT"}</i></header>
      <strong>{node.name}</strong>
      <div className={styles.ports}>
        <div>
          {node.inputs.map((port, index) => (
            <label key={port.id} style={{ top: 64 + index * 25 }}>
              <Handle type="target" id={port.id} position={Position.Left} className={`${styles.handle} ${styles[port.data_type]}`} />
              <span>{port.label}<small>{port.data_type}</small></span>
            </label>
          ))}
        </div>
        <div>
          {node.outputs.map((port, index) => (
            <label key={port.id} style={{ top: 64 + index * 25 }}>
              <span>{port.label}<small>{port.data_type}</small></span>
              <Handle type="source" id={port.id} position={Position.Right} className={`${styles.handle} ${styles[port.data_type]}`} />
            </label>
          ))}
        </div>
      </div>
    </article>
  );
});

const nodeTypes = { mangaNode: MangaNode };

function graphNodes(graph: WorkflowGraph, runs: WorkflowNodeRun[] = []): StudioNode[] {
  const statuses = new Map(runs.map((run) => [run.node_id, run.status]));
  return graph.nodes.map((node) => ({
    id: node.id,
    type: "mangaNode",
    position: node.position,
    data: { graphNode: node, runStatus: statuses.get(node.id) },
  }));
}

function graphEdges(graph: WorkflowGraph): StudioEdge[] {
  return graph.edges.map((edge) => ({
    id: edge.id,
    source: edge.source_node,
    sourceHandle: edge.source_port,
    target: edge.target_node,
    targetHandle: edge.target_port,
    data: { sourcePort: edge.source_port, targetPort: edge.target_port },
    animated: false,
  }));
}

function downloadJson(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  URL.revokeObjectURL(url);
}

export default function WorkflowStudio({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [nodes, setNodes] = useState<StudioNode[]>([]);
  const [edges, setEdges] = useState<StudioEdge[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [past, setPast] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const [validation, setValidation] = useState<string[]>([]);
  const [currentRun, setCurrentRun] = useState<WorkflowRun | null>(null);
  const [scopeType, setScopeType] = useState<"CHAPTER" | "PAGE">("CHAPTER");
  const [scopeId, setScopeId] = useState("");
  const [drawModel, setDrawModel] = useState<ImageModelAlias | "">("");
  const [drawResolution, setDrawResolution] = useState<Resolution>("1K");
  const [legacyGraph, setLegacyGraph] = useState<WorkflowGraph | null>(null);
  const [notice, setNotice] = useState("");
  const [saveStatus, setSaveStatus] = useState<"已保存" | "待保存" | "保存中" | "保存失败">("已保存");
  const initializedId = useRef<string | null>(null);
  const workflowRef = useRef<WorkflowDefinition | null>(null);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saving = useRef(false);
  const dirty = useRef(false);
  const dragging = useRef(false);
  const creating = useRef(false);
  const flowInstance = useRef<ReactFlowInstance<StudioNode, StudioEdge> | null>(null);

  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId), staleTime: 60_000 });
  const workflows = useQuery({ queryKey: ["workflows", projectId], queryFn: () => api.workflows(projectId), staleTime: 20_000 });
  const catalog = useQuery({ queryKey: ["workflow-node-types"], queryFn: api.workflowNodeTypes, staleTime: 60 * 60_000 });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 30_000 });
  const textModels = (models.data ?? []).filter((model) => model.enabled && model.model_type === "TEXT" && model.operations.includes("structured_text"));
  const imageModels = (models.data ?? []).filter((model) => model.enabled && model.model_type === "IMAGE" && model.operations.includes("image_edit"));
  const chapters = useQuery({ queryKey: ["chapters", projectId], queryFn: () => api.chapters(projectId), staleTime: 20_000 });
  const activeChapter = scopeType === "CHAPTER" ? (scopeId || chapters.data?.[0]?.id || "") : chapters.data?.[0]?.id ?? "";
  const pages = useQuery({ queryKey: ["pages", activeChapter], queryFn: () => api.pages(activeChapter), enabled: Boolean(activeChapter), staleTime: 10_000 });
  const effectiveScopeId = scopeType === "CHAPTER"
    ? chapters.data?.some((chapter) => chapter.id === scopeId) ? scopeId : chapters.data?.[0]?.id ?? ""
    : pages.data?.some((page) => page.id === scopeId) ? scopeId : pages.data?.[0]?.id ?? "";
  const activeWorkflow = workflows.data?.find((item) => item.id === activeId) ?? workflows.data?.[0] ?? null;
  const versions = useQuery({ queryKey: ["workflow-versions", activeWorkflow?.id], queryFn: () => api.workflowVersions(activeWorkflow!.id), enabled: Boolean(activeWorkflow) });
  const runs = useQuery({
    queryKey: ["workflow-runs", activeWorkflow?.id],
    queryFn: () => api.workflowRuns(activeWorkflow!.id),
    enabled: Boolean(activeWorkflow),
    refetchInterval: (query) => (query.state.data ?? []).some((run) => run.status === "RUNNING") ? 3000 : false,
  });

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { workflowRef.current = activeWorkflow; }, [activeWorkflow]);

  useEffect(() => {
    if (!workflows.isSuccess || workflows.data.length || creating.current) return;
    creating.current = true;
    api.createWorkflow(projectId).then((created) => {
      queryClient.setQueryData<WorkflowDefinition[]>(["workflows", projectId], [created]);
      setActiveId(created.id);
    }).finally(() => { creating.current = false; });
  }, [projectId, queryClient, workflows.data, workflows.isSuccess]);

  useEffect(() => {
    if (!activeWorkflow || initializedId.current === activeWorkflow.id) return;
    initializedId.current = activeWorkflow.id;
    workflowRef.current = activeWorkflow;
    setNodes(graphNodes(activeWorkflow.draft_graph));
    setEdges(graphEdges(activeWorkflow.draft_graph));
    setPast([]);
    setFuture([]);
    setValidation([]);
    setSelectedId(null);
    setSaveStatus("已保存");
  }, [activeWorkflow]);

  useEffect(() => {
    const raw = window.localStorage.getItem("mangaflow.workflow.v1");
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as { nodes?: WorkflowGraph["nodes"]; edges?: WorkflowGraph["edges"] };
      if (Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) {
        window.setTimeout(() => setLegacyGraph({ schema_version: 2, nodes: parsed.nodes!, edges: parsed.edges! }), 0);
      }
    } catch { /* damaged legacy drafts stay untouched */ }
  }, []);

  const buildGraph = useCallback((): WorkflowGraph => ({
    schema_version: 2,
    nodes: nodesRef.current.map((node) => ({ ...node.data.graphNode, position: node.position })),
    edges: edgesRef.current.map((edge) => ({
      id: edge.id,
      source_node: edge.source,
      source_port: edge.sourceHandle ?? edge.data?.sourcePort ?? "",
      target_node: edge.target,
      target_port: edge.targetHandle ?? edge.data?.targetPort ?? "",
    })),
  }), []);

  const saveNow = useCallback(async () => {
    const workflow = workflowRef.current;
    if (!workflow || saving.current || !dirty.current) return;
    saving.current = true;
    setSaveStatus("保存中");
    try {
      const updated = await api.updateWorkflow(workflow.id, workflow.version, { draft_graph: buildGraph() });
      workflowRef.current = updated;
      dirty.current = false;
      setSaveStatus("已保存");
      queryClient.setQueryData<WorkflowDefinition[]>(["workflows", projectId], (items = []) => items.map((item) => item.id === updated.id ? updated : item));
      setNotice("草稿已保存");
    } catch (error) {
      setSaveStatus("保存失败");
      setNotice(error instanceof Error ? error.message : "保存失败");
    } finally {
      saving.current = false;
    }
  }, [buildGraph, projectId, queryClient]);

  const scheduleSave = useCallback(() => {
    dirty.current = true;
    setSaveStatus("待保存");
    if (dragging.current) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => { void saveNow(); }, 800);
  }, [saveNow]);

  useEffect(() => {
    const flush = () => { if (dirty.current) void saveNow(); };
    const visibility = () => { if (document.visibilityState === "hidden") flush(); };
    window.addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("beforeunload", flush);
      document.removeEventListener("visibilitychange", visibility);
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, [saveNow]);

  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const selectedTextModels = selected?.data.graphNode.type === "quality.inspect"
    ? textModels.filter((model) => model.operations.includes("multimodal_analysis"))
    : textModels;
  const record = useCallback(() => {
    setPast((items) => [...items.slice(-39), { nodes: nodesRef.current, edges: edgesRef.current }]);
    setFuture([]);
  }, []);

  const onNodesChange = useCallback((changes: NodeChange<StudioNode>[]) => {
    const moving = changes.some((change) => change.type === "position" && change.dragging);
    dragging.current = moving;
    setNodes((items) => applyNodeChanges(changes, items));
    if (!moving && changes.some((change) => change.type === "position" || change.type === "remove")) scheduleSave();
  }, [scheduleSave]);

  const onEdgesChange = useCallback((changes: EdgeChange<StudioEdge>[]) => {
    setEdges((items) => applyEdgeChanges(changes, items));
    if (changes.some((change) => change.type === "remove")) scheduleSave();
  }, [scheduleSave]);

  const validConnection = useCallback((connection: Edge | Connection) => {
    const source = nodesRef.current.find((node) => node.id === connection.source)?.data.graphNode.outputs.find((port) => port.id === connection.sourceHandle);
    const target = nodesRef.current.find((node) => node.id === connection.target)?.data.graphNode.inputs.find((port) => port.id === connection.targetHandle);
    return Boolean(source && target && source.data_type === target.data_type && connection.source !== connection.target);
  }, []);

  const connect = useCallback((connection: Connection) => {
    if (!validConnection(connection) || !connection.sourceHandle || !connection.targetHandle) return;
    record();
    setEdges((items) => addEdge({
      ...connection,
      id: `${connection.source}:${connection.sourceHandle}-${connection.target}:${connection.targetHandle}`,
      data: { sourcePort: connection.sourceHandle!, targetPort: connection.targetHandle! },
    }, items));
    scheduleSave();
  }, [record, scheduleSave, validConnection]);

  function addNode(type: WorkflowNodeType) {
    record();
    const id = `${type.type.replaceAll(".", "-")}-${crypto.randomUUID().slice(0, 8)}`;
    const graphNode: WorkflowGraphNode = {
      id,
      type: type.type,
      name: type.label,
      position: { x: 320 + nodes.length * 24, y: 120 + nodes.length * 18 },
      inputs: type.inputs,
      outputs: type.outputs,
      config: {
        ...EMPTY_CONFIG,
        model_alias: type.type.startsWith("agent.") || type.type.startsWith("quality.") || type.type.startsWith("director.") ? "text.fast" : null,
        resolution: type.type === "generator.page" ? "1K" : null,
        requires_approval: ["generator.page", "control.approval"].includes(type.type),
      },
    };
    setNodes((items) => [...items, { id, type: "mangaNode", position: graphNode.position, data: { graphNode } }]);
    setSelectedId(id);
    scheduleSave();
  }

  function updateSelected(patch: Partial<WorkflowGraphNode>, config?: Partial<WorkflowGraphNode["config"]>) {
    if (!selected) return;
    record();
    setNodes((items) => items.map((node) => node.id === selected.id ? {
      ...node,
      data: { ...node.data, graphNode: { ...node.data.graphNode, ...patch, config: { ...node.data.graphNode.config, ...config } } },
    } : node));
    scheduleSave();
  }

  function deleteSelected() {
    if (!selectedId) return;
    record();
    setNodes((items) => items.filter((node) => node.id !== selectedId));
    setEdges((items) => items.filter((edge) => edge.source !== selectedId && edge.target !== selectedId));
    setSelectedId(null);
    scheduleSave();
  }

  function duplicateSelected() {
    if (!selected) return;
    record();
    const id = `${selected.data.graphNode.type.replaceAll(".", "-")}-${crypto.randomUUID().slice(0, 8)}`;
    const graphNode = { ...selected.data.graphNode, id, name: `${selected.data.graphNode.name} 副本`, position: { x: selected.position.x + 44, y: selected.position.y + 44 } };
    setNodes((items) => [...items, { id, type: "mangaNode", position: graphNode.position, data: { graphNode } }]);
    setSelectedId(id);
    scheduleSave();
  }

  function undo() {
    const snapshot = past.at(-1);
    if (!snapshot) return;
    setFuture((items) => [{ nodes, edges }, ...items]);
    setPast((items) => items.slice(0, -1));
    setNodes(snapshot.nodes);
    setEdges(snapshot.edges);
    scheduleSave();
  }

  function redo() {
    const snapshot = future[0];
    if (!snapshot) return;
    setPast((items) => [...items, { nodes, edges }]);
    setFuture((items) => items.slice(1));
    setNodes(snapshot.nodes);
    setEdges(snapshot.edges);
    scheduleSave();
  }

  function autoLayout() {
    record();
    const lanes = new Map<string, number>();
    setNodes((items) => items.map((node, index) => {
      const tone = nodeTone(node.data.graphNode.type);
      const lane = lanes.get(tone) ?? 0;
      lanes.set(tone, lane + 1);
      return { ...node, position: { x: 80 + index * 285, y: 110 + lane * 170 } };
    }));
    scheduleSave();
  }

  const validate = useMutation({
    mutationFn: async () => {
      await saveNow();
      return api.validateWorkflow(workflowRef.current!.id);
    },
    onSuccess: (result) => setValidation(result.issues.map((issue) => issue.message)),
  });
  const publish = useMutation({
    mutationFn: async () => {
      await saveNow();
      return api.publishWorkflow(workflowRef.current!.id);
    },
    onSuccess: () => { setNotice("已发布不可变版本"); void versions.refetch(); void workflows.refetch(); },
    onError: (error) => setNotice(error.message),
  });
  const startRun = useMutation({
    mutationFn: (range: "FULL" | "NODE" | "FROM") => {
      if (!activeWorkflow || !effectiveScopeId) throw new Error("请先选择章节或页面运行范围");
      const selectedIds = selectedId ? [selectedId] : [];
      return api.startWorkflowRun(activeWorkflow.id, {
        scope_type: scopeType,
        scope_id: effectiveScopeId,
        start_node_ids: range === "FULL" ? [] : selectedIds,
        stop_node_ids: range === "NODE" ? selectedIds : scopeType === "CHAPTER" && range === "FULL" ? ["storyboard"] : [],
      });
    },
    onSuccess: (run) => { setCurrentRun(run); void runs.refetch(); },
    onError: (error) => setNotice(error.message),
  });
  const approveNode = useMutation({
    mutationFn: (run: WorkflowNodeRun) => api.approveWorkflowNode(currentRun!.id, run.node_id, run.node_type === "generator.page" ? {
      image_model_alias: drawModel || null,
      resolution: drawResolution,
    } : {}),
    onSuccess: (run) => { setCurrentRun(run); void runs.refetch(); },
    onError: (error) => setNotice(error.message),
  });

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as { graph?: WorkflowGraph; name?: string; description?: string };
      const created = await api.importWorkflow(projectId, { name: parsed.name ?? file.name.replace(/\.json$/i, ""), description: parsed.description, graph: parsed.graph ?? parsed as unknown as WorkflowGraph });
      await workflows.refetch();
      initializedId.current = null;
      setActiveId(created.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "JSON 导入失败");
    } finally {
      event.target.value = "";
    }
  }

  async function importLegacy() {
    if (!legacyGraph) return;
    try {
      const created = await api.importWorkflow(projectId, { name: "旧版流程导入", graph: legacyGraph });
      window.localStorage.removeItem("mangaflow.workflow.v1");
      setLegacyGraph(null);
      await workflows.refetch();
      initializedId.current = null;
      setActiveId(created.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "旧版工作流导入失败");
    }
  }

  function ignoreLegacy() {
    window.localStorage.removeItem("mangaflow.workflow.v1");
    setLegacyGraph(null);
  }

  const groupedCatalog = useMemo(() => {
    const groups = new Map<string, WorkflowNodeType[]>();
    for (const item of catalog.data ?? []) groups.set(item.category, [...(groups.get(item.category) ?? []), item]);
    return [...groups.entries()];
  }, [catalog.data]);
  const displayedRun = currentRun ?? runs.data?.[0] ?? null;
  const selectedNodeRun = displayedRun?.node_runs.find((item) => item.node_id === selectedId) ?? null;
  const renderedNodes = useMemo(() => {
    if (!displayedRun) return nodes;
    const statuses = new Map(displayedRun.node_runs.map((item) => [item.node_id, item.status]));
    return nodes.map((node) => ({ ...node, data: { ...node.data, runStatus: statuses.get(node.id) } }));
  }, [displayedRun, nodes]);

  useEffect(() => {
    const targetRun = displayedRun?.node_runs.find((item) => item.status === "RUNNING")
      ?? displayedRun?.node_runs.find((item) => !["COMPLETED", "SKIPPED"].includes(item.status));
    const target = nodesRef.current.find((node) => node.id === targetRun?.node_id)
      ?? nodesRef.current[0];
    if (target && flowInstance.current) {
      void flowInstance.current.setCenter(target.position.x + 112, target.position.y + 60, {
        zoom: 0.75,
        duration: 350,
      });
    }
  }, [activeWorkflow?.id, displayedRun]);

  if (project.isLoading || workflows.isLoading || !activeWorkflow) {
    return <div className={styles.loading}><LoaderCircle className={styles.spin} />正在载入项目工作流…</div>;
  }

  return (
    <main className={styles.studio}>
      <header className={styles.topbar}>
        <div className={styles.crumb}><Link href={`/projects/${projectId}/source`}><ArrowLeft size={16} />项目</Link><i /><strong>{project.data?.name}</strong><span>流程编排</span></div>
        <div className={styles.workflowSelect}><GitBranch size={15} /><select value={activeWorkflow.id} onChange={(event) => { initializedId.current = null; setActiveId(event.target.value); }}><option value={activeWorkflow.id}>{activeWorkflow.name}</option>{workflows.data?.filter((item) => item.id !== activeWorkflow.id).map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><ChevronDown size={14} /></div>
        <div className={styles.topActions}>
          <button onClick={() => downloadJson(`${activeWorkflow.name}.json`, { schema: "mangaflow.workflow.v2", name: activeWorkflow.name, description: activeWorkflow.description, graph: buildGraph() })}><Download size={14} />导出</button>
          <label><Upload size={14} />导入<input type="file" accept="application/json,.json" onChange={importFile} /></label>
          <button onClick={() => void saveNow()}><Save size={14} />保存</button>
          <button onClick={() => validate.mutate()}><Check size={14} />校验</button>
          <button className={styles.publish} disabled={publish.isPending} onClick={() => publish.mutate()}><Send size={14} />发布</button>
        </div>
      </header>

      {legacyGraph ? <section className={styles.legacy}><History size={17} /><div><strong>发现升级前保存在当前浏览器的工作流草稿</strong><span>它不影响现在的服务端草稿与已发布版本。需要保留时可另存导入；确认无用可永久忽略。</span></div><button onClick={importLegacy}>另存为新流程</button><button className={styles.ignoreLegacy} onClick={() => window.confirm("永久忽略这份旧版浏览器草稿？不会删除服务端流程。") && ignoreLegacy()}><X size={14} />永久忽略</button></section> : null}
      {notice ? <button className={styles.notice} onClick={() => setNotice("")}>{notice}<X size={12} /></button> : null}
      <section className={styles.workflowStatus} aria-live="polite"><div><span>草稿版本</span><strong>V{activeWorkflow.draft_version}</strong></div><div><span>已发布版本</span><strong>{versions.data?.[0] ? `V${versions.data[0].revision}` : "尚未发布"}</strong></div><div><span>保存状态</span><strong>{saveStatus}</strong></div><div><span>校验问题</span><strong>{validation.length} 项</strong></div><button onClick={() => startRun.mutate("FULL")} disabled={startRun.isPending}><Play size={14} />运行已发布流程</button></section>

      <section className={`${styles.body} ${libraryOpen ? "" : styles.libraryClosed} ${inspectorOpen ? "" : styles.inspectorClosed}`}>
        <aside className={styles.library}>
          <header><div><span>NODE LIBRARY</span><strong>节点库</strong></div><button onClick={() => setLibraryOpen(false)}><X size={14} /></button></header>
          <div className={styles.libraryScroll}>{groupedCatalog.map(([category, items]) => <section key={category}><span>{categoryLabel[category] ?? category}</span>{items.map((item) => <button key={item.type} onClick={() => addNode(item)}><Plus size={13} /><div><strong>{item.label}</strong><small>{item.description}</small></div></button>)}</section>)}</div>
        </aside>

        <section className={styles.canvas}>
          <div className={styles.canvasToolbar}>
            {!libraryOpen ? <button onClick={() => setLibraryOpen(true)}><Plus size={14} />节点库</button> : null}
            <button disabled={!past.length} onClick={undo}><Undo2 size={14} />撤销</button><button disabled={!future.length} onClick={redo}><Redo2 size={14} />重做</button>
            <button onClick={autoLayout}><LayoutGrid size={14} />自动布局</button><button disabled={!selected} onClick={duplicateSelected}><Copy size={14} />复制</button><button disabled={!selected} onClick={deleteSelected}><Trash2 size={14} />删除</button>
            <button onClick={() => void flowInstance.current?.fitView({ padding: 0.15, duration: 300 })}><LayoutGrid size={14} />查看全图</button>
            {!inspectorOpen ? <button onClick={() => setInspectorOpen(true)}><BoxSelect size={14} />属性</button> : null}
          </div>
          <ReactFlow<StudioNode, StudioEdge>
            nodes={renderedNodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            isValidConnection={validConnection}
            onNodeDragStart={record}
            onNodeDragStop={() => { dragging.current = false; scheduleSave(); }}
            onSelectionChange={({ nodes: selectedNodes }) => setSelectedId(selectedNodes.at(-1)?.id ?? null)}
            selectionOnDrag
            multiSelectionKeyCode={["Meta", "Control"]}
            deleteKeyCode={["Backspace", "Delete"]}
            defaultViewport={{ x: 80, y: 70, zoom: 0.75 }}
            onInit={(instance) => { flowInstance.current = instance; }}
            minZoom={0.2}
            maxZoom={1.6}
            onlyRenderVisibleElements={nodes.length > 200}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#4c514e" />
            <MiniMap pannable zoomable className={styles.minimap} nodeColor={(node) => ({ input: "#397b68", control: "#b77c26", output: "#b94735", quality: "#7862a4", agent: "#326b91" })[nodeTone((node.data as StudioNodeData).graphNode.type)]} />
            <Controls showInteractive={false} />
          </ReactFlow>
          {validation.length ? <div className={styles.validation}><CircleAlert size={15} /><div>{validation.map((message) => <span key={message}>{message}</span>)}</div><button onClick={() => setValidation([])}><X size={13} /></button></div> : null}
        </section>

        <aside className={styles.inspector}>
          <header><div><span>INSPECTOR</span><strong>属性面板</strong></div><button onClick={() => setInspectorOpen(false)}><X size={14} /></button></header>
          {selected ? <div className={styles.inspectorForm}>
            <label>节点名称<input value={selected.data.graphNode.name} onChange={(event) => updateSelected({ name: event.target.value })} /></label>
            <label>节点类型<input value={selected.data.graphNode.type} disabled /></label>
            {selected.data.graphNode.type === "generator.page" ? <><label>模型由每次生成选择<input value="必须显式选择供应商图片模型" disabled /></label><label>建议清晰度<select value={selected.data.graphNode.config.resolution ?? "1K"} onChange={(event) => updateSelected({}, { resolution: event.target.value as Resolution })}><option>1K</option><option>2K</option><option>4K</option></select></label></> : null}
            {selected.data.graphNode.config.model_alias ? <><label>文本模型<select value={selected.data.graphNode.config.model_alias} onChange={(event) => updateSelected({}, { model_alias: event.target.value })}>{selectedTextModels.map((model) => <option key={model.catalog_id} value={model.logical_alias}>{model.provider} · {model.display_name}</option>)}</select></label><label>温度<input type="number" min="0" max="2" step="0.1" value={selected.data.graphNode.config.temperature} onChange={(event) => updateSelected({}, { temperature: Number(event.target.value) })} /></label></> : null}
            <label>超时（秒）<input type="number" min="30" max="3600" value={selected.data.graphNode.config.timeout_seconds} onChange={(event) => updateSelected({}, { timeout_seconds: Number(event.target.value) })} /></label>
            <label>重试次数<input type="number" min="1" max="10" value={selected.data.graphNode.config.max_attempts} onChange={(event) => updateSelected({}, { max_attempts: Number(event.target.value) })} /></label>
            <label>提示词<textarea value={selected.data.graphNode.config.prompt_template} onChange={(event) => updateSelected({}, { prompt_template: event.target.value })} placeholder="留空时使用内置业务提示词" /></label>
            {selected.data.graphNode.type === "control.condition" ? <><label>JSON 路径<input value={String(selected.data.graphNode.config.condition.path ?? "$")} onChange={(event) => updateSelected({}, { condition: { ...selected.data.graphNode.config.condition, path: event.target.value } })} /></label><label>比较符<select value={String(selected.data.graphNode.config.condition.operator ?? "exists")} onChange={(event) => updateSelected({}, { condition: { ...selected.data.graphNode.config.condition, operator: event.target.value } })}><option value="exists">存在</option><option value="eq">等于</option><option value="ne">不等于</option><option value="contains">包含</option><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></select></label></> : null}
            <label>备注<textarea value={selected.data.graphNode.config.notes} onChange={(event) => updateSelected({}, { notes: event.target.value })} /></label>
            {selectedNodeRun ? <section className={styles.nodeRuntime}><strong>{statusLabel[selectedNodeRun.status] ?? selectedNodeRun.status}</strong><span>{selectedNodeRun.started_at && selectedNodeRun.finished_at ? `耗时 ${((new Date(selectedNodeRun.finished_at).getTime() - new Date(selectedNodeRun.started_at).getTime()) / 1000).toFixed(1)} 秒` : "尚未产生完整耗时"}</span><pre>{JSON.stringify(selectedNodeRun.output_refs, null, 2)}</pre>{selectedNodeRun.error_message ? <em>{selectedNodeRun.error_code} · {selectedNodeRun.error_message}</em> : null}</section> : null}
          </div> : <div className={styles.noSelection}><GitBranch size={28} /><strong>从这里开始</strong><ol><li>选择节点查看配置</li><li>拖动端口建立连线</li><li>校验草稿并修复问题</li><li>发布不可变版本</li><li>选择范围后运行</li></ol></div>}
          <section className={styles.versionList}><header><span>发布版本</span><strong>{versions.data?.length ?? 0}</strong></header>{versions.data?.slice(0, 4).map((version) => <button key={version.id} onClick={async () => { const restored = await api.restoreWorkflowVersion(version.id, workflowRef.current!.version); workflowRef.current = restored; initializedId.current = null; await workflows.refetch(); }}><RotateCcw size={12} />V{version.revision}<small>{new Date(version.published_at).toLocaleString("zh-CN")}</small></button>)}</section>
        </aside>
      </section>

      <footer className={styles.runner}>
        <div className={styles.runScope}><span>运行范围</span><select value={scopeType} onChange={(event) => { const next = event.target.value as "CHAPTER" | "PAGE"; setScopeType(next); setScopeId(next === "CHAPTER" ? chapters.data?.[0]?.id ?? "" : pages.data?.[0]?.id ?? ""); }}><option value="CHAPTER">章节</option><option value="PAGE">页面</option></select><select value={effectiveScopeId} onChange={(event) => setScopeId(event.target.value)}>{scopeType === "CHAPTER" ? chapters.data?.map((chapter) => <option value={chapter.id} key={chapter.id}>{chapter.title}</option>) : pages.data?.map((page) => <option value={page.id} key={page.id}>第 {page.page_number} 页</option>)}</select></div>
        <div className={styles.runState}><i className={displayedRun?.status === "RUNNING" ? styles.running : ""} /><span>{displayedRun ? `运行 ${displayedRun.status} · ${displayedRun.node_runs.filter((item) => item.status === "COMPLETED").length}/${displayedRun.node_runs.length}` : "尚未运行已发布版本"}</span></div>
        <div className={styles.runActions}><button disabled={!selectedId || startRun.isPending} onClick={() => startRun.mutate("NODE")}><Play size={13} />运行节点</button><button disabled={!selectedId || startRun.isPending} onClick={() => startRun.mutate("FROM")}><Play size={13} />从这里运行</button>{displayedRun?.status === "RUNNING" ? <button onClick={async () => { const run = await api.cancelWorkflowRun(displayedRun.id); setCurrentRun(run); }}><Pause size={13} />取消</button> : null}<button className={styles.runPrimary} disabled={startRun.isPending} onClick={() => startRun.mutate("FULL")}><Play size={14} />运行工作流</button></div>
        {displayedRun?.node_runs.filter((run) => run.status === "WAITING_APPROVAL").map((run) => <div className={styles.approval} key={run.id}><strong>{run.node_type === "generator.page" ? "单页生成等待选择模型" : "采用候选后继续"}</strong>{run.node_type === "generator.page" ? <><select value={drawModel} onChange={(event) => setDrawModel(event.target.value as ImageModelAlias | "")}><option value="">选择图片模型</option>{imageModels.map((model) => <option key={model.catalog_id} value={model.logical_alias}>{model.provider} · {model.display_name}</option>)}</select><select value={drawResolution} onChange={(event) => setDrawResolution(event.target.value as Resolution)}><option>1K</option><option>2K</option><option>4K</option></select></> : <Link href={`/projects/${projectId}/generate`}>前往采用</Link>}<button disabled={approveNode.isPending || (run.node_type === "generator.page" && !drawModel)} onClick={() => approveNode.mutate(run)}>确认继续</button></div>)}
      </footer>
    </main>
  );
}
