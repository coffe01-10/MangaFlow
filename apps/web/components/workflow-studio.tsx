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
  type NodeChange,
  type NodeProps,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  ApiError,
  type ImageModelAlias,
  type Resolution,
  type WorkflowDefinition,
  type WorkflowGraph,
  type WorkflowGraphNode,
  type WorkflowGroup,
  type WorkflowNodeRun,
  type WorkflowNodeType,
  type WorkflowRun,
} from "@/lib/api";
import { createWorkflowDraftSaver, type WorkflowDraftSaver, type WorkflowSaveStatus } from "@/lib/workflow-draft-save";
import { creatorVisibleModels } from "@/lib/model-visibility";
import { workflowRunStatusLabels } from "./project-workspace/labels";
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
import { ChangeEvent, createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import styles from "./workflow-studio.module.css";
import { ClampedNumberInput } from "./clamped-number-input";
import { WorkflowDialog } from "./workflow-dialog";
import { COLLECT_COMMANDS, OPEN_COMMANDS, type StudioCommand } from "./command-palette";
import {
  arrangeNodes, cleanGroups, duplicateNodes, elapsed, groupBounds, insertNode, matchingPorts,
  projectGroups, readLocalList, resolveHandle,
  type Arrangement, type InsertContext, type SavedTemplate, type StudioNode, type StudioNodeData, type StudioEdge,
} from "@/lib/workflow-editor";

type Snapshot = { nodes: StudioNode[]; edges: StudioEdge[]; groups: WorkflowGroup[] };

const categoryLabel: Record<string, string> = {
  INPUT: "输入",
  AGENT: "智能处理",
  CONTROL: "控制",
  OUTPUT: "生成与输出",
};

const statusLabel = workflowRunStatusLabels;

// 空项目的默认工作流对（契约 §4.1）：按名称识别「缺失」，供首次自动创建
// 与失败后的「重试创建」补建共用（#545 item 2：部分成功后只补缺失项）。
const DEFAULT_WORKFLOW_TEMPLATES: { name: string; template: "manga_default" | "chapter_export" }[] = [
  { name: "单页生产流程", template: "manga_default" },
  { name: "整章导出流程", template: "chapter_export" },
];

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
    <article data-run-status={data.runStatus} className={`${styles.node} ${styles[nodeTone(node.type)]} ${selected ? styles.selected : ""}`}>
      <header><span>{node.type}</span><i>{data.runStatus ? statusLabel[data.runStatus] ?? data.runStatus : "DRAFT"}</i></header>
      <strong>{node.name}</strong>
      <div className={styles.ports}>
        <div>
          {node.inputs.map((port, index) => (
            <label key={port.id} style={{ top: 64 + index * 25 }}>
              <Handle type="target" id={port.id} position={Position.Left} style={{ top: 80 + index * 38 }} className={`${styles.handle} ${styles[port.data_type]}`} />
              <span>{port.label}<small>{port.data_type}</small></span>
            </label>
          ))}
        </div>
        <div>
          {node.outputs.map((port, index) => (
            <label key={port.id} style={{ top: 64 + index * 25 }}>
              <span>{port.label}<small>{port.data_type}</small></span>
              <Handle type="source" id={port.id} position={Position.Right} style={{ top: 80 + index * 38 }} className={`${styles.handle} ${styles[port.data_type]}`} />
            </label>
          ))}
        </div>
      </div>
      {data.run && <div className={styles.nodeMetrics}><span>{elapsed(data.run)}</span><span>{data.run.total_tokens == null ? "Token —" : `${data.run.total_tokens.toLocaleString()} Token`}</span><small>{Object.keys(data.run.output_refs).filter((key) => !["job_id", "node_type"].includes(key)).length} 项结果 · 点击查看</small></div>}
    </article>
  );
});

const GroupToggleContext = createContext<(id: string) => void>(() => {});
const FlowGroup = memo(function FlowGroup({ data, selected }: NodeProps<StudioNode>) {
  const group = data.group!;
  const toggle = useContext(GroupToggleContext);
  return <section className={`${styles.flowGroup} ${selected || data.membersSelected ? styles.selected : ""}`} style={{ borderColor: group.color }} data-collapsed={group.collapsed} data-run-status={data.runStatus}>
    <header style={{ borderColor: group.color }}><strong>{group.name}</strong><button className="nodrag nopan" onClick={() => toggle(group.id)} aria-label={`${group.collapsed ? "展开" : "折叠"}分组 ${group.name}`}>{group.collapsed ? "+" : "−"}</button></header>
    <p>{group.node_ids.length} 个节点{data.runStatus ? ` · ${statusLabel[data.runStatus] ?? data.runStatus}` : ""}</p>
    {group.collapsed && <div className={styles.groupPorts}>{(["inputs", "outputs"] as const).map((direction) => <div key={direction}>{data.graphNode[direction].map((port, index) => <label key={port.id}>
      <Handle type={direction === "inputs" ? "target" : "source"} id={port.id} position={direction === "inputs" ? Position.Left : Position.Right} style={{ top: 100 + index * 40 }} />
      <span title={port.label}>{port.label}</span>
    </label>)}</div>)}</div>}
  </section>;
});
const nodeTypes = { mangaNode: MangaNode, flowGroup: FlowGroup };

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

// 后端按 JSON 类型比较（布尔/数字与字符串永不相等），发布校验也允许布尔值
// 条件：true/false/数字必须解析为类型字面量提交，否则 $.ready eq "true"
// 恒为假分支。仅显式比较符做字面量化；"exists" 不读比较值。
function parseInvariantDecimal(trimmed: string): number | undefined {
  // Number() accepts 0x10 / 0b10 / 1e2; native NumberStyles.Float +
  // InvariantCulture accepts only decimal/exponent. A graph saved in one
  // UI and edited in the other must not flip $.x eq 16 vs $.x eq "0x10"
  // (#821). Reject hex/binary/octal; keep decimal and scientific.
  if (!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/.test(trimmed)) {
    return undefined;
  }
  const numeric = Number(trimmed);
  return Number.isFinite(numeric) ? numeric : undefined;
}

export function parseConditionValue(raw: string, operator: string): string | number | boolean | null {
  const trimmed = raw.trim();
  if (["gt", "gte", "lt", "lte", "eq", "ne", "contains"].includes(operator)) {
    if (trimmed === "true") return true;
    if (trimmed === "false") return false;
    if (trimmed === "null") return null;
    const numeric = parseInvariantDecimal(trimmed);
    if (numeric !== undefined) return numeric;
  }
  return raw;
}

// #380：PAUSED（审批栅栏）也是活跃态。旧谓词只认 RUNNING——审批通过或取消后，
// run 已不是 RUNNING，轮询彻底停摆，页脚状态与节点徽标冻结在旧数据上直到手动
// 刷新。契约：任一 run RUNNING → 3s；否则任一 run PAUSED → 10s 折中降频；
// 全部终态（或无运行）→ false 停止轮询。导出供测试直接钉住该词汇表。
export function workflowRunsPollInterval(runs: ReadonlyArray<WorkflowRun> | undefined): number | false {
  const list = runs ?? [];
  if (list.some((run) => run.status === "RUNNING")) return 3000;
  if (list.some((run) => run.status === "PAUSED")) return 10000;
  return false;
}

export default function WorkflowStudio({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [activeId, setActiveId] = useState<string | null>(null);
  const [nodes, setNodes] = useState<StudioNode[]>([]);
  const [edges, setEdges] = useState<StudioEdge[]>([]);
  const [groups, setGroups] = useState<WorkflowGroup[]>([]);
  const groupsRef = useRef(groups);
  const [runMode, setRunMode] = useState<"single" | "batch">("single");
  const runModeRef = useRef(runMode);
  const [picker, setPicker] = useState<InsertContext | null>(null);
  const [nodeSearch, setNodeSearch] = useState("");
  const [category, setCategory] = useState("ALL");
  const [favorites, setFavorites] = useState<string[]>([]);
  const [recent, setRecent] = useState<string[]>([]);
  const [templateDialog, setTemplateDialog] = useState(false);
  const [templateName, setTemplateName] = useState("");
  const [templates, setTemplates] = useState<SavedTemplate[]>([]);
  const [templateBusy, setTemplateBusy] = useState(false);
  const [batchPageIds, setBatchPageIds] = useState<string[]>([]);
  const [batchBusy, setBatchBusy] = useState(false);
  const [followRun, setFollowRun] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [past, setPast] = useState<Snapshot[]>([]);
  const [future, setFuture] = useState<Snapshot[]>([]);
  const [validation, setValidation] = useState<string[]>([]);
  const [currentRun, setCurrentRun] = useState<WorkflowRun | null>(null);
  const [scopeType, setScopeType] = useState<"CHAPTER" | "PAGE">("PAGE");
  const [scopeId, setScopeId] = useState("");
  const [pageChapterId, setPageChapterId] = useState("");
  const [drawModel, setDrawModel] = useState<ImageModelAlias | "">("");
  const [drawResolution, setDrawResolution] = useState<Resolution>("1K");
  const [legacyGraph, setLegacyGraph] = useState<WorkflowGraph | null>(null);
  const [notice, setNotice] = useState("");
  const [createFailed, setCreateFailed] = useState("");
  const [retryCreatePending, setRetryCreatePending] = useState(false);
  const [saveStatus, setSaveStatus] = useState<WorkflowSaveStatus>("已保存");
  const initializedId = useRef<string | null>(null);
  const workflowRef = useRef<WorkflowDefinition | null>(null);
  const nodesRef = useRef(nodes);
  const edgesRef = useRef(edges);
  const dragging = useRef(false);
  const creating = useRef(false);
  const flowInstance = useRef<ReactFlowInstance<StudioNode, StudioEdge> | null>(null);
  const draftSaver = useRef<WorkflowDraftSaver | null>(null);

  const project = useQuery({ queryKey: ["project", projectId], queryFn: () => api.project(projectId), staleTime: 60_000 });
  const workflows = useQuery({ queryKey: ["workflows", projectId], queryFn: () => api.workflows(projectId), staleTime: 20_000 });
  const catalog = useQuery({ queryKey: ["workflow-node-types"], queryFn: api.workflowNodeTypes, staleTime: 60 * 60_000 });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 30_000 });
  const chapters = useQuery({ queryKey: ["chapters", projectId], queryFn: () => api.chapters(projectId), staleTime: 20_000 });
  // PAGE 运行范围的页面来自显式选择的章节（默认第一章）：此前 PAGE 模式恒取
  // chapters[0]，多章项目永远无法从编排页对第 2+ 章的页面发起单页流程。
  const activeChapter = scopeType === "CHAPTER" ? (scopeId || chapters.data?.[0]?.id || "") : (pageChapterId || chapters.data?.[0]?.id) ?? "";
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
    // Error state previously returned false forever (the interval keys on
    // cached data, which is undefined while erroring): a transient API
    // outage froze the run list with no self-heal. Back off to a slow retry
    // instead; the footer also surfaces the error instead of masquerading
    // as an empty history.
    refetchInterval: (query) => (query.state.status === "error" ? 10_000 : workflowRunsPollInterval(query.state.data)),
  });

  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);
  useEffect(() => { groupsRef.current = groups; }, [groups]);
  useEffect(() => { runModeRef.current = runMode; }, [runMode]);
  useEffect(() => { workflowRef.current = activeWorkflow; }, [activeWorkflow]);

  // #545 item 2：Promise.all 在「单页成功、整章失败」时整体拒绝——已建成的
  // 流程既不进缓存，重试又被 data.length 守卫挡住，缺失的整章导出流程从此
  // 无法补建。allSettled 保留部分成功；失败留给「重试创建」按名称补建缺失
  // 项（createFailed 期间禁用自动创建，避免重试路径与 effect 双重建）。
  const createDefaultWorkflows = useCallback(async (missing: typeof DEFAULT_WORKFLOW_TEMPLATES) => {
    if (!missing.length) {
      setCreateFailed("");
      return;
    }
    creating.current = true;
    const results = await Promise.allSettled(
      missing.map((tpl) => api.createWorkflow(projectId, tpl.name, tpl.template)),
    );
    creating.current = false;
    const created = results
      .filter((result): result is PromiseFulfilledResult<WorkflowDefinition> => result.status === "fulfilled")
      .map((result) => result.value);
    if (created.length) {
      queryClient.setQueryData<WorkflowDefinition[]>(["workflows", projectId], (items = []) => {
        const known = new Set(items.map((item) => item.id));
        return [...items, ...created.filter((item) => !known.has(item.id))];
      });
      const preferred = created.find((item) => item.name === DEFAULT_WORKFLOW_TEMPLATES[0].name) ?? created[0];
      setActiveId((current) => current ?? preferred.id);
    }
    const failures = results
      .filter((result): result is PromiseRejectedResult => result.status === "rejected")
      .map((result) => (result.reason instanceof Error ? result.reason.message : String(result.reason)));
    if (failures.length) {
      setCreateFailed(`部分默认工作流创建失败：${failures.join("；")}。点击「重试创建」只会补建缺失的流程。`);
    } else {
      setCreateFailed("");
    }
  }, [projectId, queryClient]);

  useEffect(() => {
    if (createFailed || !workflows.isSuccess || workflows.data.length || creating.current) return;
    void createDefaultWorkflows(DEFAULT_WORKFLOW_TEMPLATES);
  }, [createDefaultWorkflows, createFailed, workflows.data, workflows.isSuccess]);

  async function retryCreateMissingWorkflows() {
    // 重入守卫：重试是「重拉列表 → 补建」两段异步，双击会并发跑两份，
    // 各自都看到同样的缺失集合，把每个模板创建两次。
    if (retryCreatePending) return;
    setRetryCreatePending(true);
    try {
      // 先重拉再补建：失败后缓存里可能已有部分成功未落缓存（或另一端建过），
      // 按最新列表的名字集合判断缺失，避免重复创建同模板工作流。
      const latest = await workflows.refetch();
      // 重拉失败（或把已有数据翻空）时列表状态未知：按名称判缺失可能重复
      // 创建，不在未知状态上补建，转列表错误面让用户再次重试。
      if (latest.isError || !latest.data) {
        setCreateFailed(
          `工作流列表刷新失败：${latest.error instanceof Error ? latest.error.message : "请稍后重试"}。请重试。`,
        );
        return;
      }
      const existing = new Set(latest.data.map((item) => item.name));
      const missing = DEFAULT_WORKFLOW_TEMPLATES.filter((tpl) => !existing.has(tpl.name));
      if (!missing.length) {
        setCreateFailed("");
        return;
      }
      await createDefaultWorkflows(missing);
    } finally {
      setRetryCreatePending(false);
    }
  }

  useEffect(() => {
    if (!activeWorkflow || initializedId.current === activeWorkflow.id) return;
    initializedId.current = activeWorkflow.id;
    workflowRef.current = activeWorkflow;
    setNodes(graphNodes(activeWorkflow.draft_graph));
    setEdges(graphEdges(activeWorkflow.draft_graph));
    setGroups(activeWorkflow.draft_graph.groups ?? []);
    setRunMode(activeWorkflow.draft_graph.run_mode ?? "single");
    setBatchPageIds([]);
    setPicker(null);
    setPast([]);
    setFuture([]);
    setValidation([]);
    setSelectedId(null);
    // 上一个工作流的运行（含 WAITING_APPROVAL 审批行）不得跟随切换残留：
    // displayedRun 会把旧 run 的节点状态与审批按钮画到新画布上。
    setCurrentRun(null);
    draftSaver.current?.reset();
    setSaveStatus("已保存");
    const chapterOnly = activeWorkflow.draft_graph.nodes.some((node) => node.type === "source.approved_pages")
      && !activeWorkflow.draft_graph.nodes.some((node) => node.type === "generator.page");
    setScopeType(chapterOnly ? "CHAPTER" : "PAGE");
    setScopeId("");
    // 页面所属章节也随初始化复位：App Router 在项目间切换时保留组件状态，
    // 残留的章节 id 会把上一个项目的页面列表拉进本项目的运行目标下拉。
    setPageChapterId("");
    // Templates can place nodes outside the default viewport; fit the whole
    // graph on first paint instead of an empty-looking canvas.
    const fitTimer = window.setTimeout(() => { void flowInstance.current?.fitView({ padding: 0.15, duration: 250 }); }, 60);
    return () => window.clearTimeout(fitTimer);
  }, [activeWorkflow]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setFavorites(readLocalList<string>("mangaflow.node-favorites").filter((value) => typeof value === "string"));
      setRecent(readLocalList<string>("mangaflow.node-recent").filter((value) => typeof value === "string"));
      setTemplates(readLocalList<SavedTemplate>("mangaflow.workflow-templates").filter((value) => value && typeof value.name === "string" && value.graph?.schema_version === 2 && Array.isArray(value.graph.nodes) && Array.isArray(value.graph.edges)));
      const requested = new URLSearchParams(window.location.search).get("workflow");
      if (requested) setActiveId(requested);
    }, 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    const raw = window.localStorage.getItem("mangaflow.workflow.v1");
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw) as { nodes?: WorkflowGraph["nodes"]; edges?: WorkflowGraph["edges"] };
      if (Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) {
        const legacyTimer = window.setTimeout(() => setLegacyGraph({ schema_version: 2, nodes: parsed.nodes!, edges: parsed.edges! }), 0);
        return () => window.clearTimeout(legacyTimer);
      }
    } catch { /* damaged legacy drafts stay untouched */ }
  }, []);

  const buildGraph = useCallback((): WorkflowGraph => ({
    schema_version: 2,
    groups: cleanGroups(groupsRef.current, nodesRef.current),
    run_mode: runModeRef.current,
    entry_node_ids: (workflowRef.current?.draft_graph.entry_node_ids ?? []).filter((id) => nodesRef.current.some((node) => node.id === id)),
    nodes: nodesRef.current.map((node) => ({ ...node.data.graphNode, position: node.position })),
    edges: edgesRef.current.map((edge) => ({
      id: edge.id,
      source_node: edge.source,
      source_port: edge.sourceHandle ?? edge.data?.sourcePort ?? "",
      target_node: edge.target,
      target_port: edge.targetHandle ?? edge.data?.targetPort ?? "",
    })),
  }), []);

  const saveNow = useCallback(async () => draftSaver.current?.saveNow() ?? false, []);

  const scheduleSave = useCallback(() => {
    const saver = draftSaver.current;
    if (!saver) return;
    saver.markDirty();
    if (dragging.current) return;
    saver.schedule();
  }, []);

  useEffect(() => {
    const saver = createWorkflowDraftSaver<WorkflowGraph, WorkflowDefinition>({
      debounceMs: 800,
      getSnapshot: () => {
        const workflow = workflowRef.current;
        if (!workflow) return null;
        return { workflowId: workflow.id, version: workflow.version, graph: buildGraph() };
      },
      persist: ({ workflowId, version, graph }) =>
        api.updateWorkflow(workflowId, version, { draft_graph: graph }),
      onStatusChange: setSaveStatus,
      onPersisted: (updated) => {
        if (workflowRef.current?.id === updated.id) workflowRef.current = updated;
        queryClient.setQueryData<WorkflowDefinition[]>(["workflows", projectId], (items = []) =>
          items.map((item) => item.id === updated.id ? updated : item),
        );
        setNotice("草稿已保存");
      },
      onError: (error) => {
        setNotice(error instanceof Error ? error.message : "保存失败");
        // A 409 means the local version fell behind (another tab published a
        // revision, or a flushed save raced navigation). Refresh the list so
        // the next save uses the server's current version instead of failing
        // forever until a manual reload.
        if (error instanceof ApiError && error.status === 409) {
          void queryClient.invalidateQueries({ queryKey: ["workflows", projectId] });
        }
      },
    });
    draftSaver.current = saver;
    const flush = () => { if (saver.isDirty()) void saver.saveNow(); };
    const visibility = () => { if (document.visibilityState === "hidden") flush(); };
    window.addEventListener("beforeunload", flush);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("beforeunload", flush);
      window.removeEventListener("visibilitychange", visibility);
      // SPA route unmount: beforeunload/visibility do not fire. Fire the
      // save, keep the saver alive until it settles (dispose() would cancel
      // the in-flight result and drop the cache update), then dispose.
      if (saver.isDirty()) {
        void saver.saveNow().finally(() => saver.dispose());
      } else {
        saver.dispose();
      }
      if (draftSaver.current === saver) draftSaver.current = null;
    };
  }, [buildGraph, projectId, queryClient]);

  const selected = nodes.find((node) => node.id === selectedId) ?? null;
  const textModels = creatorVisibleModels(
    (models.data ?? []).filter((model) => model.model_type === "TEXT" && model.operations.includes("structured_text")),
    { logicalAliases: [selected?.data.graphNode.config.model_alias] },
  );
  const imageModels = creatorVisibleModels(
    (models.data ?? []).filter((model) => model.model_type === "IMAGE" && model.operations.includes("image_edit")),
    { logicalAliases: [drawModel] },
  );
  const selectedTextModels = selected?.data.graphNode.type === "quality.inspect"
    ? textModels.filter((model) => model.operations.includes("multimodal_analysis"))
    : textModels;
  const record = useCallback(() => {
    const snapshot = { nodes: nodesRef.current, edges: edgesRef.current, groups: groupsRef.current };
    setPast((items) => {
      // deleteKeyCode 删除会先后触发 onNodesChange/onEdgesChange 的 remove，
      // 两次回调之间 refs 尚未随渲染更新，快照引用相同：跳过重复项，避免
      // 撤销栈出现需要按两次才生效的空步。
      const last = items[items.length - 1];
      if (last && last.nodes === snapshot.nodes && last.edges === snapshot.edges && last.groups === snapshot.groups) return items;
      return [...items.slice(-39), snapshot];
    });
    setFuture([]);
  }, []);

  const onNodesChange = useCallback((changes: NodeChange<StudioNode>[]) => {
    const moving = changes.some((change) => change.type === "position" && change.dragging);
    dragging.current = moving;
    // Backspace/Delete 删除走 deleteKeyCode 而非 deleteSelected：先记录删除前
    // 快照（refs 此刻仍是旧状态），否则键盘删除不可撤销，且残留的 future 会在
    // 重做时用删除前的整图覆盖当前 nodes/edges。
    if (changes.some((change) => change.type === "remove")) record();
    const groupChanges = changes.filter((change) => "id" in change && groupsRef.current.some((group) => group.id === change.id));
    const movedMembers = new Set(groupChanges.flatMap((change) => change.type === "position" && change.position
      ? groupsRef.current.find((group) => group.id === change.id)!.node_ids : []));
    const plainChanges = changes.filter((change) => !groupChanges.includes(change) && !(change.type === "position" && movedMembers.has(change.id)));
    let next = applyNodeChanges(plainChanges, nodesRef.current);
    for (const change of groupChanges) {
      if (!("id" in change)) continue;
      const group = groupsRef.current.find((item) => item.id === change.id)!;
      if (change.type === "position" && change.position) {
        const bounds = groupBounds(group, nodesRef.current);
        const dx = change.position.x - bounds.x, dy = change.position.y - bounds.y;
        next = next.map((node) => group.node_ids.includes(node.id) ? { ...node, position: { x: node.position.x + dx, y: node.position.y + dy } } : node);
      }
      if (change.type === "select") next = next.map((node) => group.node_ids.includes(node.id) ? { ...node, selected: change.selected } : node);
      if (change.type === "remove") next = next.filter((node) => !group.node_ids.includes(node.id));
    }
    nodesRef.current = next;
    setNodes(next);
    if (changes.some((change) => change.type === "remove")) {
      const ids = new Set(next.map((node) => node.id));
      setEdges((items) => items.filter((edge) => ids.has(edge.source) && ids.has(edge.target)));
      setGroups((items) => cleanGroups(items, next));
    }
    if (!moving && changes.some((change) => change.type === "position" || change.type === "remove")) scheduleSave();
  }, [record, scheduleSave]);

  const onEdgesChange = useCallback((changes: EdgeChange<StudioEdge>[]) => {
    if (changes.some((change) => change.type === "remove")) record();
    setEdges((items) => applyEdgeChanges(changes, items));
    if (changes.some((change) => change.type === "remove")) scheduleSave();
  }, [record, scheduleSave]);

  const validConnection = useCallback((connection: Edge | Connection) => {
    const from = resolveHandle(connection.source, connection.sourceHandle, groupsRef.current);
    const to = resolveHandle(connection.target, connection.targetHandle, groupsRef.current);
    const source = nodesRef.current.find((node) => node.id === from.nodeId)?.data.graphNode.outputs.find((port) => port.id === from.handle);
    const target = nodesRef.current.find((node) => node.id === to.nodeId)?.data.graphNode.inputs.find((port) => port.id === to.handle);
    return Boolean(source && target && source.data_type === target.data_type && from.nodeId !== to.nodeId);
  }, []);

  const connect = useCallback((raw: Connection) => {
    const source = resolveHandle(raw.source, raw.sourceHandle, groupsRef.current);
    const target = resolveHandle(raw.target, raw.targetHandle, groupsRef.current);
    const connection = { source: source.nodeId, sourceHandle: source.handle, target: target.nodeId, targetHandle: target.handle };
    if (!validConnection(connection) || !connection.sourceHandle || !connection.targetHandle) return;
    // 确定性边 id 意味着同一端口对连两次会产生两条同 id 的边:React key
    // 冲突,且按 id 删除会一次移除两条。连接前先按端口对判重。
    if (edgesRef.current.some((edge) => edge.source === connection.source
      && edge.sourceHandle === connection.sourceHandle
      && edge.target === connection.target
      && edge.targetHandle === connection.targetHandle)) return;
    record();
    setEdges((items) => addEdge({
      ...connection,
      id: `${connection.source}:${connection.sourceHandle}-${connection.target}:${connection.targetHandle}`,
      data: { sourcePort: connection.sourceHandle!, targetPort: connection.targetHandle! },
    }, items));
    scheduleSave();
  }, [record, scheduleSave, validConnection]);

  function addNode(type: WorkflowNodeType) {
    const context = picker ?? { position: flowInstance.current?.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }) ?? { x: 320, y: 120 } };
    const result = insertNode(type, context, nodesRef.current, edgesRef.current);
    if (!result) { setNotice("端口类型不兼容或原连线已被删除，请重新选择"); return; }
    record();
    setNodes(result.nodes); setEdges(result.edges); setSelectedId(result.node.id);
    const next = [type.type, ...recent.filter((item) => item !== type.type)].slice(0, 8);
    setRecent(next); storePreference("mangaflow.node-recent", next);
    setPicker(null); setInspectorOpen(true); scheduleSave();
  }

  function storePreference(key: string, value: unknown) {
    try { localStorage.setItem(key, JSON.stringify(value)); return true; }
    catch { setNotice("浏览器存储不可用，设置仅在本次会话生效"); return false; }
  }

  function openPicker(context?: InsertContext) {
    setNodeSearch(""); setCategory("ALL");
    setPicker(context ?? { position: flowInstance.current?.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 }) ?? { x: 320, y: 120 } });
  }

  const selectedIds = new Set(nodes.filter((node) => node.selected).map((node) => node.id));
  if (!selectedIds.size && selectedId && nodes.some((node) => node.id === selectedId)) selectedIds.add(selectedId);
  const selectedGroup = groups.find((group) => group.id === selectedId);

  function updateGroup(id: string, patch: Partial<WorkflowGroup>) {
    record();
    setGroups((items) => items.map((group) => group.id === id ? { ...group, ...patch } : group));
    scheduleSave();
  }

  function createGroup() {
    if (!selectedIds.size) return;
    record();
    const id = `group-${crypto.randomUUID()}`;
    setGroups((items) => [...items.map((group) => ({ ...group, node_ids: group.node_ids.filter((member) => !selectedIds.has(member)) })).filter((group) => group.node_ids.length),
      { id, name: "新分组", color: "#397b68", notes: "", node_ids: [...selectedIds], collapsed: false }]);
    setSelectedId(id); setInspectorOpen(true); scheduleSave();
  }

  function arrange(mode: Arrangement) {
    record(); setNodes((items) => arrangeNodes(items, selectedIds, mode)); scheduleSave();
  }

  async function switchWorkflow(id: string) {
    if (batchBusy) { setNotice("批量提交中，请稍候切换"); return false; }
    if (draftSaver.current?.isDirty() && !await saveNow()) { setNotice("当前工作流保存失败，请重试后切换"); return false; }
    initializedId.current = null; setActiveId(id); return true;
  }

  async function createFromTemplate(kind: "manga_default" | "chapter_export" | "blank" | "check" | "batch" | SavedTemplate) {
    if (templateBusy) return;
    const name = templateName.trim();
    if (!name) { setNotice("请填写新工作流名称"); return; }
    setTemplateBusy(true);
    try {
      if (draftSaver.current?.isDirty() && !await saveNow()) throw new Error("请先保存当前工作流");
      let created: WorkflowDefinition;
      if (typeof kind === "object") created = await api.importWorkflow(projectId, { name, graph: kind.graph });
      else if (kind === "check" || kind === "batch") {
        const graph = await api.workflowTemplate(kind);
        created = await api.importWorkflow(projectId, { name, graph });
      } else created = await api.createWorkflow(projectId, name, kind);
      queryClient.setQueryData<WorkflowDefinition[]>(["workflows", projectId], (items = []) => [...items, created]);
      initializedId.current = null; setActiveId(created.id); setTemplateDialog(false);
    } catch (error) { setNotice(error instanceof Error ? error.message : "创建失败"); }
    finally { setTemplateBusy(false); }
  }

  function saveTemplate() {
    const name = templateName.trim();
    if (!name) { setNotice("请填写模板名称"); return; }
    const template = { id: crypto.randomUUID(), name, graph: structuredClone(buildGraph()) };
    const next = [...templates, template];
    if (storePreference("mangaflow.workflow-templates", next)) { setTemplates(next); setNotice("模板已保存到当前浏览器，可跨项目使用"); }
  }

  async function startBatch() {
    if (!activeWorkflow || batchBusy) return;
    const ids = batchPageIds.filter((id) => pages.data?.some((page) => page.id === id));
    if (!ids.length) { setNotice("请勾选本次批量运行的页面"); return; }
    setBatchBusy(true);
    const failures: string[] = [];
    const failedIds: string[] = [];
    let count = 0;
    for (const id of ids) {
      try {
        const published = versions.data?.find((version) => version.id === activeWorkflow.published_version_id);
        if (!published) throw new Error("请先发布并载入版本");
        const run = await api.startWorkflowRun(activeWorkflow.id, { scope_type: "PAGE", scope_id: id, start_node_ids: published.graph.entry_node_ids ?? [], stop_node_ids: [] });
        setCurrentRun(run); count++;
      } catch (error) { failedIds.push(id); failures.push(`${id}: ${error instanceof Error ? error.message : "失败"}`); }
    }
    await runs.refetch(); setBatchBusy(false);
    setNotice(`已启动 ${count} 个页面流程${failures.length ? `；${failures.length} 个失败：${failures.join("；")}` : "，请在运行列表中逐页确认模型与结果"}`);
    setBatchPageIds(failedIds);
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
    if (!selectedIds.size) return;
    record();
    const next = nodes.filter((node) => !selectedIds.has(node.id));
    setNodes(next); setGroups(cleanGroups(groups, next));
    setEdges((items) => items.filter((edge) => !selectedIds.has(edge.source) && !selectedIds.has(edge.target)));
    setSelectedId(null);
    scheduleSave();
  }

  function duplicateSelected() {
    if (!selectedIds.size) return;
    record();
    const result = duplicateNodes(nodes, edges, selectedIds);
    setNodes(result.nodes); setEdges(result.edges);
    setGroups((items) => [...items, ...items.filter((group) => group.node_ids.every((id) => selectedIds.has(id))).map((group) => ({ ...group, id: `group-${crypto.randomUUID()}`, name: `${group.name} 副本`, node_ids: group.node_ids.map((id) => result.mapping.get(id)!) }))]);
    setSelectedId(result.copies[0]?.id ?? null);
    scheduleSave();
  }

  function undo() {
    const snapshot = past.at(-1);
    if (!snapshot) return;
    setFuture((items) => [{ nodes, edges, groups }, ...items]);
    setPast((items) => items.slice(0, -1));
    setNodes(snapshot.nodes);
    setEdges(snapshot.edges);
    setGroups(snapshot.groups);
    scheduleSave();
  }

  function redo() {
    const snapshot = future[0];
    if (!snapshot) return;
    setPast((items) => [...items, { nodes, edges, groups }]);
    setFuture((items) => items.slice(1));
    setNodes(snapshot.nodes);
    setEdges(snapshot.edges);
    setGroups(snapshot.groups);
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
      const saved = await saveNow();
      if (!saved) throw new Error("草稿保存失败，未校验");
      const workflow = workflowRef.current;
      if (!workflow) throw new Error("工作流尚未载入");
      return api.validateWorkflow(workflow.id);
    },
    onSuccess: (result) => setValidation(result.issues.map((issue) => issue.message)),
    onError: (error) => setNotice(error instanceof Error ? error.message : "校验失败"),
  });
  const publish = useMutation({
    mutationFn: async () => {
      const saved = await saveNow();
      if (!saved) throw new Error("草稿保存失败，未发布");
      const workflow = workflowRef.current;
      if (!workflow) throw new Error("工作流尚未载入");
      return api.publishWorkflow(workflow.id);
    },
    onSuccess: () => { setNotice("已发布不可变版本"); void versions.refetch(); void workflows.refetch(); },
    onError: (error) => setNotice(error instanceof Error ? error.message : "发布失败"),
  });
  const startRun = useMutation({
    mutationFn: (range: "FULL" | "NODE" | "FROM") => {
      if (!activeWorkflow || !effectiveScopeId) throw new Error("请先选择章节或页面运行范围");
      const rangeIds = selectedGroup ? selectedGroup.node_ids : selectedId ? [selectedId] : [];
      const published = versions.data?.find((version) => version.id === activeWorkflow.published_version_id);
      return api.startWorkflowRun(activeWorkflow.id, {
        scope_type: scopeType,
        scope_id: effectiveScopeId,
        start_node_ids: range === "FULL" ? published?.graph.entry_node_ids ?? [] : rangeIds,
        stop_node_ids: range === "NODE" ? rangeIds : [],
      });
    },
    onSuccess: (run) => { setCurrentRun(run); void runs.refetch(); },
    onError: (error) => setNotice(error.message),
  });
  const approveNode = useMutation({
    // Node runs carry their own workflow_run_id; currentRun can be null when
    // the approval row renders from the runs list before a run is selected.
    mutationFn: (run: WorkflowNodeRun) => api.approveWorkflowNode(run.workflow_run_id, run.node_id, run.node_type === "generator.page" ? {
      image_model_alias: drawModel || null,
      resolution: drawResolution,
    } : {}),
    onSuccess: (run) => { setCurrentRun(run); void runs.refetch(); },
    onError: (error) => setNotice(error.message),
  });

  const restoreVersion = useMutation({
    // Restoring overwrites the current draft graph with a published version;
    // it is destructive enough to confirm, and failures must surface.
    mutationFn: (versionId: string) => {
      const current = workflowRef.current;
      if (!current) throw new Error("当前工作流尚未加载，无法恢复版本");
      return api.restoreWorkflowVersion(versionId, current.version);
    },
    onSuccess: (restored) => {
      workflowRef.current = restored;
      // 恢复是破坏性覆盖：立即把画布重置为恢复后的图（与初始化 effect
      // 同款），并丢弃未落的防抖草稿。此前画布只靠 refetch 落地后新的
      // activeWorkflow 对象身份重载：refetch 失败（retry 后仍错）时缓存
      // 身份不变、画布永远停在恢复前的图而 notice 已宣称成功，此后任意
      // 一次编辑的防抖保存会用「旧节点图 + 恢复后的新版本号」PATCH，把
      // 恢复静默回滚；即使 refetch 成功，落地前的防抖窗口内一次编辑也
      // 一样。先落画布，refetch 只负责刷新侧栏版本等衍生数据。
      setNodes(graphNodes(restored.draft_graph));
      setEdges(graphEdges(restored.draft_graph));
      setGroups(restored.draft_graph.groups ?? []);
      setRunMode(restored.draft_graph.run_mode ?? "single");
      setPast([]);
      setFuture([]);
      setValidation([]);
      setSelectedId(null);
      setCurrentRun(null);
      draftSaver.current?.reset();
      initializedId.current = null;
      setNotice(`已恢复发布版本到草稿（V${restored.version}）`);
      void workflows.refetch().then((result) => {
        if (result.isError) {
          setNotice(`已恢复发布版本到草稿（V${restored.version}）；工作流列表刷新失败：${result.error instanceof Error ? result.error.message : "请手动刷新"}`);
        }
      });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : "恢复版本失败"),
  });

  const cancelRun = useMutation({
    mutationFn: (runId: string) => api.cancelWorkflowRun(runId),
    onSuccess: (run) => { setCurrentRun(run); void runs.refetch(); },
    onError: (error) => setNotice(error instanceof Error ? error.message : "取消运行失败"),
  });
  const retryRun = useMutation({
    // retry_run clones the FAILED run against the version it actually
    // executed (pinned_version_id), keeping scope and node range — the footer
    // 运行工作流 button always re-runs the currently published version.
    mutationFn: (runId: string) => api.retryWorkflowRun(runId),
    onSuccess: (run) => { setCurrentRun(run); void runs.refetch(); },
    onError: (error) => setNotice(error instanceof Error ? error.message : "重试运行失败"),
  });

  async function importFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text()) as { graph?: WorkflowGraph; name?: string; description?: string };
      const graph = parsed.graph && parsed.graph.schema_version === 2 && Array.isArray(parsed.graph.nodes) && Array.isArray(parsed.graph.edges)
        ? parsed.graph
        : null;
      // The backend only accepts schema_version 2 graphs; fail early with a
      // readable message instead of a raw FastAPI 422 from a blind cast.
      if (!graph) {
        setNotice("导入失败：文件不是 schema_version 2 的工作流图谱（缺少 nodes / edges 或版本不符）。请先在流程编排页导出正确格式。");
        return;
      }
      const created = await api.importWorkflow(projectId, { name: parsed.name ?? file.name.replace(/\.json$/i, ""), description: parsed.description, graph });
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
  // currentRun is the optimistic snapshot from a start/approve/cancel
  // response and never updates afterwards; the polled runs list is the live
  // source of truth. Prefer the list entry for the SAME run id once the poll
  // knows about it, fall back to the snapshot only until the first poll
  // catches up (and to the list head after a reload when nothing is running).
  // Freezing on the snapshot kept node badges and WAITING_APPROVAL rows on
  // the start-time state for the whole run, so later approval barriers never
  // rendered and the workflow looked hung.
  const listedRun = (currentRun && runs.data?.find((run) => run.id === currentRun.id))
    ?? currentRun
    ?? runs.data?.[0]
    ?? null;
  // list_runs is a pure read; GET /workflow-runs/{id} is the path that
  // reconciles completed jobs into node/run status. Poll the active run
  // so the footer cannot freeze on RUNNING after the worker finished.
  const liveRunId = listedRun
    && listedRun.status !== "COMPLETED"
    && listedRun.status !== "FAILED"
    && listedRun.status !== "CANCELLED"
    ? listedRun.id
    : null;
  const liveRun = useQuery({
    queryKey: ["workflow-run", liveRunId],
    queryFn: () => api.workflowRun(liveRunId!),
    enabled: Boolean(liveRunId),
    refetchInterval: listedRun?.status === "RUNNING" ? 3000 : listedRun?.status === "PAUSED" ? 10000 : false,
  });
  const displayedRun = (liveRun.data && liveRun.data.id === listedRun?.id)
    ? liveRun.data
    : listedRun;
  // 错误可见性（与 versions/models 的错误面纪律一致）：runs 后台重试失败
  // 且无缓存时不得用「尚未运行」空态冒充事实——那会让用户以为运行丢失而
  // 重复发起（同 scope 活跃 run 守卫随即 409）。liveRun 失败但列表快照仍在
  // 时保留快照展示并标注来源，不回退到空态。
  const runsUnavailable = runs.isError && runs.data === undefined;
  const liveRunStale = Boolean(liveRunId) && liveRun.isError;
  const selectedNodeRun = displayedRun?.node_runs.find((item) => item.node_id === selectedId) ?? null;
  const renderedNodes = useMemo(() => {
    if (!displayedRun) return nodes;
    const nodeRuns = new Map(displayedRun.node_runs.map((item) => [item.node_id, item]));
    return nodes.map((node) => ({ ...node, data: { ...node.data, runStatus: nodeRuns.get(node.id)?.status, run: nodeRuns.get(node.id) } }));
  }, [displayedRun, nodes]);
  const projected = useMemo(() => {
    const running = new Set(displayedRun?.node_runs.filter((item) => item.status === "RUNNING").map((item) => item.node_id));
    const result = projectGroups(renderedNodes, edges.map((edge) => ({ ...edge, animated: running.has(edge.target), className: running.has(edge.target) ? styles.activeEdge : undefined })), groups);
    return result;
  }, [renderedNodes, edges, groups, displayedRun]);
  const toggleGroup = useCallback((id: string) => {
    record(); setGroups((items) => items.map((group) => group.id === id ? { ...group, collapsed: !group.collapsed } : group)); scheduleSave();
  }, [record, scheduleSave]);
  const pickerItems = (catalog.data ?? []).filter((item) =>
    `${item.label} ${item.type} ${item.description}`.toLowerCase().includes(nodeSearch.toLowerCase())
    && (category === "ALL" || category === item.category || (category === "FAVORITES" && favorites.includes(item.type)) || (category === "RECENT" && recent.includes(item.type)))
    && (!picker || matchingPorts(item, picker, nodes).compatible))
    .sort((a, b) => category === "RECENT" ? recent.indexOf(a.type) - recent.indexOf(b.type) : 0);

  useEffect(() => {
    const focusNode = (id: string) => {
      setGroups((items) => items.map((group) => group.node_ids.includes(id) ? { ...group, collapsed: false } : group));
      setNodes((items) => items.map((node) => ({ ...node, selected: node.id === id })));
      setSelectedId(id); setInspectorOpen(true); scheduleSave();
      const node = nodes.find((item) => item.id === id);
      if (node) void flowInstance.current?.setCenter(node.position.x + 112, node.position.y + 80, { zoom: 1, duration: 250 });
    };
    const collect = (event: Event) => {
      const commands = (event as CustomEvent<StudioCommand[]>).detail;
      commands.push(
        { id: "add-node", label: "添加节点", section: "操作", run: () => openPicker() },
        { id: "run-flow", label: "运行已发布流程", section: "操作", disabled: startRun.isPending || !effectiveScopeId, run: () => startRun.mutate("FULL") },
        { id: "publish-flow", label: "发布工作流", section: "操作", disabled: publish.isPending, run: () => publish.mutate() },
        { id: "versions", label: "查看版本", section: "操作", run: () => { setInspectorOpen(true); window.setTimeout(() => document.getElementById("workflow-versions")?.scrollIntoView({ block: "nearest" }), 0); } },
        { id: "templates", label: "新建流程 / 保存为模板", section: "操作", run: () => { setTemplateName(activeWorkflow?.name ?? "新流程"); setTemplateDialog(true); } },
        ...nodes.map((node) => ({ id: `node-${node.id}`, label: node.data.graphNode.name, section: "画布节点", run: () => focusNode(node.id) })),
        ...(workflows.data ?? []).map((workflow) => ({ id: `workflow-${workflow.id}`, label: workflow.name, section: "工作流", run: () => { void switchWorkflow(workflow.id); } })),
      );
    };
    const key = (event: KeyboardEvent) => {
      if ((event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable=true], dialog")) || document.querySelector("dialog[open]")) return;
      if (!(event.ctrlKey || event.metaKey)) return;
      const action = event.key.toLowerCase();
      if (!["a", "d", "g", "z", "y"].includes(action)) return;
      event.preventDefault();
      if (action === "a") setNodes((items) => items.map((node) => ({ ...node, selected: true })));
      if (action === "d") duplicateSelected();
      if (action === "g") createGroup();
      if (action === "z") { if (event.shiftKey) redo(); else undo(); }
      if (action === "y") redo();
    };
    window.addEventListener(COLLECT_COMMANDS, collect);
    window.addEventListener("keydown", key);
    return () => { window.removeEventListener(COLLECT_COMMANDS, collect); window.removeEventListener("keydown", key); };
  });

  // Follow-the-run camera: re-center only when the run or focus node actually
  // changes. runs.data gets a new identity on every 3s poll, so keying the
  // effect on it yanked the viewport away from the user's pan/zoom for the
  // whole run; dragging must also freeze the camera.
  const cameraTargetRef = useRef<string | null>(null);
  useEffect(() => {
    if (!followRun) return;
    const targetRun = displayedRun?.node_runs.find((item) => item.status === "RUNNING")
      ?? displayedRun?.node_runs.find((item) => !["COMPLETED", "SKIPPED"].includes(item.status));
    const cameraKey = `${displayedRun?.id ?? "none"}:${targetRun?.node_id ?? "none"}`;
    if (cameraKey === cameraTargetRef.current) return;
    cameraTargetRef.current = cameraKey;
    const group = groups.find((item) => item.collapsed && item.node_ids.includes(targetRun?.node_id ?? ""));
    const target = (group ? { position: groupBounds(group, nodesRef.current) } : nodesRef.current.find((node) => node.id === targetRun?.node_id))
      ?? nodesRef.current[0];
    if (!target || !flowInstance.current || dragging.current) return;
    void flowInstance.current.setCenter(target.position.x + 112, target.position.y + 60, {
      zoom: 0.75,
      duration: 350,
    });
  }, [activeWorkflow?.id, displayedRun, followRun, groups]);

  if (workflows.isError) {
    return (
      <div className={styles.loading}>
        <strong>无法载入项目工作流</strong>
        <span>{workflows.error instanceof Error ? workflows.error.message : "请求失败，请确认 API 可用后重试。"}</span>
        <button type="button" onClick={() => void workflows.refetch()}>重试</button>
      </div>
    );
  }
  if (createFailed) {
    return (
      <div className={styles.loading}>
        <strong>默认工作流创建失败</strong>
        <span>{createFailed}</span>
        <button type="button" disabled={retryCreatePending} onClick={() => { void retryCreateMissingWorkflows(); }}>重试创建</button>
      </div>
    );
  }
  if (project.isLoading || workflows.isLoading || !activeWorkflow) {
    return <div className={styles.loading}><LoaderCircle className={styles.spin} />正在载入项目工作流…</div>;
  }

  return (
    <main className={styles.studio}>
      <header className={styles.topbar}>
        <div className={styles.crumb}><Link href={`/projects/${projectId}/source`}><ArrowLeft size={16} />项目</Link><i /><strong>{project.data?.name}</strong><span>流程编排</span></div>
        <div className={styles.workflowSelect}><GitBranch size={15} /><select aria-label="选择工作流" value={activeWorkflow.id} onChange={(event) => {
          const next = event.target.value;
          if (next === activeWorkflow.id) return;
          const saver = draftSaver.current;
          // Switching resets the saver and reloads from cache; flush the
          // current draft first so edits are persisted AND the cache update
          // lands before the next workflow is loaded from it. On failure,
          // stay on the current workflow instead of dropping the edits.
          if (saver?.isDirty()) {
            void saver.saveNow().then((saved) => {
              if (!saved) {
                setNotice("当前工作流保存失败，已保持在原工作流，请重试后再切换");
                return;
              }
              initializedId.current = null;
              setActiveId(next);
            });
            return;
          }
          initializedId.current = null;
          setActiveId(next);
        }}><option value={activeWorkflow.id}>{activeWorkflow.name}</option>{workflows.data?.filter((item) => item.id !== activeWorkflow.id).map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><ChevronDown size={14} /></div>
        <div className={styles.topActions}>
          <button onClick={() => window.dispatchEvent(new Event(OPEN_COMMANDS))} title="搜索页面、节点和操作">命令 <kbd>Ctrl K</kbd></button>
          <button onClick={() => { setTemplateName(`${activeWorkflow.name} 模板`); setTemplateDialog(true); }}><Plus size={14} />新建 / 模板</button>
          <button onClick={() => downloadJson(`${activeWorkflow.name}.json`, { schema: "mangaflow.workflow.v2", name: activeWorkflow.name, description: activeWorkflow.description, graph: buildGraph() })}><Download size={14} />导出</button>
          <label><Upload size={14} />导入<input type="file" accept="application/json,.json" onChange={importFile} /></label>
          <button onClick={() => void saveNow()}><Save size={14} />保存</button>
          <button disabled={validate.isPending} onClick={() => validate.mutate()}>{validate.isPending ? "校验中…" : <><Check size={14} />校验</>}</button>
          <button className={styles.publish} disabled={publish.isPending} onClick={() => publish.mutate()}><Send size={14} />发布</button>
        </div>
      </header>

      {legacyGraph ? <section className={styles.legacy}><History size={17} /><div><strong>发现升级前保存在当前浏览器的工作流草稿</strong><span>它不影响现在的服务端草稿与已发布版本。需要保留时可另存导入；确认无用可永久忽略。</span></div><button onClick={importLegacy}>另存为新流程</button><button className={styles.ignoreLegacy} onClick={() => window.confirm("永久忽略这份旧版浏览器草稿？不会删除服务端流程。") && ignoreLegacy()}><X size={14} />永久忽略</button></section> : null}
      {notice ? <button className={styles.notice} role="status" aria-live="polite" onClick={() => setNotice("")}>{notice}<X size={12} /></button> : null}
      <section className={styles.workflowStatus} aria-live="polite"><div><span>草稿版本</span><strong>V{activeWorkflow.draft_version}</strong></div><div><span>已发布版本</span>{versions.isError ? <><strong>读取失败</strong><button type="button" onClick={() => void versions.refetch()}>重试</button></> : <strong>{versions.data?.[0] ? `V${versions.data[0].revision}` : "尚未发布"}</strong>}</div><div><span>保存状态</span><strong>{saveStatus}</strong></div><div><span>校验问题</span><strong>{validation.length} 项</strong></div><button onClick={() => startRun.mutate("FULL")} disabled={startRun.isPending}><Play size={14} />运行已发布流程</button></section>

      <section className={`${styles.body} ${libraryOpen ? "" : styles.libraryClosed} ${inspectorOpen ? "" : styles.inspectorClosed}`}>
        <aside className={styles.library}>
          <header><div><span>NODE LIBRARY</span><strong>节点库</strong></div><button aria-label="关闭节点库" onClick={() => setLibraryOpen(false)}><X size={14} /></button></header>
          {catalog.isError ? (
            // #545-7：节点目录读取失败不能静默清空节点库——给出原因与重试。
            <div className={styles.libraryScroll} role="alert">
              <strong>节点库读取失败</strong>
              <span>{catalog.error instanceof Error ? catalog.error.message : "请稍后重试"}</span>
              <button type="button" onClick={() => void catalog.refetch()}>重试</button>
            </div>
          ) : (
            <div className={styles.libraryScroll}><button onClick={() => openPicker()} className={styles.librarySearch}>搜索节点 · 最近 / 收藏</button>{groupedCatalog.map(([category, items]) => <section key={category}><span>{categoryLabel[category] ?? category}</span>{items.map((item) => <button key={item.type} onClick={() => addNode(item)}><Plus size={13} /><div><strong>{item.label}</strong><small>{item.description}</small></div></button>)}</section>)}</div>
          )}
        </aside>

        <section className={styles.canvas}>
          <div className={styles.canvasToolbar}>
            {!libraryOpen ? <button onClick={() => setLibraryOpen(true)}><Plus size={14} />节点库</button> : null}
            <button disabled={!past.length} onClick={undo}><Undo2 size={14} />撤销</button><button disabled={!future.length} onClick={redo}><Redo2 size={14} />重做</button>
            <button onClick={autoLayout}><LayoutGrid size={14} />自动布局</button><button disabled={!selectedIds.size} onClick={duplicateSelected}><Copy size={14} />复制</button><button disabled={!selectedIds.size} onClick={deleteSelected}><Trash2 size={14} />删除</button>
            <button disabled={!selectedIds.size} onClick={createGroup}>分组</button>
            <button onClick={() => openPicker()}><Plus size={14} />添加</button>
            <button onClick={() => void flowInstance.current?.fitView({ padding: 0.15, duration: 300 })}><LayoutGrid size={14} />查看全图</button>
            {!inspectorOpen ? <button onClick={() => setInspectorOpen(true)}><BoxSelect size={14} />属性</button> : null}
          </div>
          {selectedIds.size > 1 && <div className={styles.selectionBar} aria-label="批量工具"><strong>已选 {selectedIds.size}</strong>{([['left', '左对齐'], ['right', '右对齐'], ['top', '上对齐'], ['bottom', '下对齐'], ['horizontal', '水平等距'], ['vertical', '垂直等距']] as const).map(([mode, label]) => <button key={mode} disabled={['horizontal', 'vertical'].includes(mode) && selectedIds.size < 3} onClick={() => arrange(mode)}>{label}</button>)}</div>}
          <div className={styles.canvasHint}>拖拽框选 · Shift / Ctrl 多选 · 双击连线插入节点</div>
          <GroupToggleContext.Provider value={toggleGroup}><ReactFlow<StudioNode, StudioEdge>
            nodes={projected.nodes}
            edges={projected.edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            onConnectEnd={(event, state) => {
              if (state.isValid || !state.fromNode || !state.fromHandle || state.toNode) return;
              const target = event.target as HTMLElement;
              if (!target.closest?.(".react-flow__pane")) return;
              const point = "changedTouches" in event ? event.changedTouches[0] : event;
              const port = resolveHandle(state.fromNode.id, state.fromHandle.id, groupsRef.current);
              const position = flowInstance.current?.screenToFlowPosition({ x: point.clientX, y: point.clientY });
              if (position) openPicker({ position, [state.fromHandle.type === "source" ? "source" : "target"]: port });
            }}
            onEdgeDoubleClick={(event, edge) => {
              event.preventDefault();
              const actual = edgesRef.current.find((item) => item.id === edge.id);
              if (!actual) return;
              openPicker({ position: flowInstance.current?.screenToFlowPosition({ x: event.clientX, y: event.clientY }) ?? { x: 0, y: 0 }, edgeId: actual.id,
                source: { nodeId: actual.source, handle: actual.sourceHandle! }, target: { nodeId: actual.target, handle: actual.targetHandle! } });
            }}
            isValidConnection={validConnection}
            onNodeDragStart={record}
            onSelectionDragStart={record}
            onNodeDragStop={() => { dragging.current = false; scheduleSave(); }}
            onSelectionDragStop={() => { dragging.current = false; scheduleSave(); }}
            onNodeClick={(_, node) => { setSelectedId(node.id); setInspectorOpen(true); }}
            onSelectionChange={({ nodes: selectedNodes }) => setSelectedId(selectedNodes.at(-1)?.id ?? null)}
            selectionOnDrag
            multiSelectionKeyCode={["Meta", "Control", "Shift"]}
            panOnDrag={[1, 2]}
            selectionKeyCode={null}
            deleteKeyCode={["Backspace", "Delete"]}
            defaultViewport={{ x: 80, y: 70, zoom: 0.75 }}
            onInit={(instance) => { flowInstance.current = instance; }}
            minZoom={0.2}
            maxZoom={1.6}
            onlyRenderVisibleElements={nodes.length > 200}
            ariaLabelConfig={{
              "controls.ariaLabel": "画布控制",
              "controls.zoomIn.ariaLabel": "放大",
              "controls.zoomOut.ariaLabel": "缩小",
              "controls.fitView.ariaLabel": "适应画布",
              "minimap.ariaLabel": "工作流小地图",
            }}
          >
            <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#4c514e" />
            <MiniMap
              ariaLabel="工作流小地图"
              pannable
              zoomable
              className={styles.minimap}
              bgColor="#202421"
              maskColor="rgba(8, 13, 11, 0.38)"
              maskStrokeColor="#f4a78c"
              maskStrokeWidth={2}
              nodeStrokeColor="#d6dfd8"
              nodeStrokeWidth={8}
              nodeBorderRadius={2}
              nodeColor={(node) => ({ input: "#397b68", control: "#b77c26", output: "#b94735", quality: "#7862a4", agent: "#326b91" })[nodeTone((node.data as StudioNodeData).graphNode.type)]}
            />
            <Controls showInteractive={false} />
          </ReactFlow></GroupToggleContext.Provider>
          {validation.length ? <div className={styles.validation}><CircleAlert size={15} /><div>{validation.map((message, index) => <span key={`${index}-${message}`}>{message}</span>)}</div><button aria-label="清除校验提示" onClick={() => setValidation([])}><X size={13} /></button></div> : null}
        </section>

        <aside className={styles.inspector}>
          <header><div><span>INSPECTOR</span><strong>属性面板</strong></div><button aria-label="关闭属性面板" onClick={() => setInspectorOpen(false)}><X size={14} /></button></header>
          {selectedGroup ? <div className={styles.inspectorForm}>
            <label>分组名称<input maxLength={160} value={selectedGroup.name} onChange={(event) => updateGroup(selectedGroup.id, { name: event.target.value || "新分组" })} /></label>
            <label>分组颜色<input type="color" value={selectedGroup.color} onChange={(event) => updateGroup(selectedGroup.id, { color: event.target.value })} /></label>
            <label>分组备注<textarea value={selectedGroup.notes} onChange={(event) => updateGroup(selectedGroup.id, { notes: event.target.value })} /></label>
            <button className={styles.panelButton} onClick={() => updateGroup(selectedGroup.id, { collapsed: !selectedGroup.collapsed })}>{selectedGroup.collapsed ? "展开分组" : "折叠分组"}</button>
            <button className={styles.panelButton} onClick={() => { record(); setGroups((items) => items.filter((group) => group.id !== selectedGroup.id)); setSelectedId(null); scheduleSave(); }}>解散分组 · 保留节点</button>
          </div> : selected ? <div key={selected.id} className={styles.inspectorForm}>
            {selectedNodeRun && <section className={styles.nodeRuntime} aria-label="本次运行结果">
              <strong>{statusLabel[selectedNodeRun.status] ?? selectedNodeRun.status} · 本次运行</strong>
              <span>{elapsed(selectedNodeRun)} · {selectedNodeRun.total_tokens == null ? "Token 尚未完整上报" : `${selectedNodeRun.total_tokens.toLocaleString()} Token`}</span>
              <Link href={`/projects/${projectId}/generate`}>查看产物 / 检查与修复 →</Link>
              <details><summary>结果数据</summary><pre>{JSON.stringify(selectedNodeRun.output_refs, null, 2)}</pre></details>
              {selectedNodeRun.error_message && <em>{selectedNodeRun.error_message}</em>}
            </section>}
            <label>节点名称<input value={selected.data.graphNode.name} onChange={(event) => updateSelected({ name: event.target.value })} /></label>
            <label>节点类型<input value={selected.data.graphNode.type} disabled /></label>
            {selected.data.graphNode.type === "generator.page" ? <><label>模型由每次生成选择<input value="必须显式选择供应商图片模型" disabled /></label><label>建议清晰度<select value={selected.data.graphNode.config.resolution ?? "1K"} onChange={(event) => updateSelected({}, { resolution: event.target.value as Resolution })}><option>1K</option><option>2K</option><option>4K</option></select></label></> : null}
            {selected.data.graphNode.config.model_alias ? <><label>文本模型<select value={selected.data.graphNode.config.model_alias} onChange={(event) => updateSelected({}, { model_alias: event.target.value })}><option value="auto">自动路由 · 按节点类型选择已验证模型</option>{selectedTextModels.map((model) => <option key={model.catalog_id} value={model.logical_alias}>{model.provider} · {model.display_name}</option>)}</select></label><label>温度<ClampedNumberInput min={0} max={2} step={0.1} value={selected.data.graphNode.config.temperature} onCommit={(temperature) => updateSelected({}, { temperature })} /></label></> : null}
            <label>超时（秒）<ClampedNumberInput min={30} max={3600} value={selected.data.graphNode.config.timeout_seconds} onCommit={(timeout_seconds) => updateSelected({}, { timeout_seconds })} /></label>
            <label>重试次数<ClampedNumberInput min={1} max={10} value={selected.data.graphNode.config.max_attempts} onCommit={(max_attempts) => updateSelected({}, { max_attempts })} /></label>
            <label>提示词<textarea value={selected.data.graphNode.config.prompt_template} onChange={(event) => updateSelected({}, { prompt_template: event.target.value })} placeholder="留空时使用内置业务提示词" /></label>
            {selected.data.graphNode.type === "control.condition" ? <><label>JSON 路径<input value={String(selected.data.graphNode.config.condition.path ?? "$")} onChange={(event) => updateSelected({}, { condition: { ...selected.data.graphNode.config.condition, path: event.target.value } })} /></label><label>比较符<select value={String(selected.data.graphNode.config.condition.operator ?? "exists")} onChange={(event) => updateSelected({}, { condition: { ...selected.data.graphNode.config.condition, operator: event.target.value } })}><option value="exists">存在</option><option value="eq">等于</option><option value="ne">不等于</option><option value="contains">包含</option><option value="gt">大于</option><option value="gte">大于等于</option><option value="lt">小于</option><option value="lte">小于等于</option></select></label><label>比较值<input value={selected.data.graphNode.config.condition.value === undefined || selected.data.graphNode.config.condition.value === null ? "" : String(selected.data.graphNode.config.condition.value)} onChange={(event) => {
                const raw = event.target.value;
                const operator = String(selected.data.graphNode.config.condition.operator ?? "exists");
                updateSelected({}, { condition: { ...selected.data.graphNode.config.condition, value: parseConditionValue(raw, operator) } });
              }} placeholder="除“存在”外必须填写" /></label></> : null}
            <label>备注<textarea value={selected.data.graphNode.config.notes} onChange={(event) => updateSelected({}, { notes: event.target.value })} /></label>
          </div> : <div className={styles.noSelection}><GitBranch size={28} /><strong>从这里开始</strong><ol><li>选择节点查看配置</li><li>拖动端口建立连线</li><li>校验草稿并修复问题</li><li>发布不可变版本</li><li>选择范围后运行</li></ol></div>}
          <section id="workflow-versions" className={styles.versionList}><header><span>发布版本</span><strong>{versions.isError ? "读取失败" : versions.data?.length ?? 0}</strong></header>{versions.isError ? <div role="alert"><span>发布版本列表读取失败：{versions.error instanceof Error ? versions.error.message : "请稍后重试"}</span><button type="button" onClick={() => void versions.refetch()}>重试</button></div> : versions.data?.map((version) => <button key={version.id} disabled={restoreVersion.isPending} onClick={() => { if (window.confirm(`用发布版本 V${version.revision} 覆盖当前草稿？未保存的草稿修改会丢失。`)) restoreVersion.mutate(version.id); }}><RotateCcw size={12} />V{version.revision}<small>{new Date(version.published_at).toLocaleString("zh-CN")}</small></button>)}</section>
        </aside>
      </section>

      <footer className={styles.runner}>
        <div className={styles.runHistory}>
          <label>查看运行<select aria-label="查看运行记录" value={displayedRun?.id ?? ""} onChange={(event) => setCurrentRun(runs.data?.find((run) => run.id === event.target.value) ?? null)}><option value="" disabled>尚未运行</option>{runs.data?.map((run) => <option key={run.id} value={run.id}>{pages.data?.find((page) => page.id === run.scope_id)?.page_number ? `第 ${pages.data.find((page) => page.id === run.scope_id)!.page_number} 页 · ` : ""}{new Date(run.created_at).toLocaleTimeString("zh-CN")} · {statusLabel[run.status] ?? run.status}</option>)}</select></label>
          <label><input type="checkbox" checked={followRun} onChange={(event) => { cameraTargetRef.current = null; setFollowRun(event.target.checked); }} />跟随运行</label>
          <button onClick={() => { setRunMode((mode) => mode === "batch" ? "single" : "batch"); setScopeType("PAGE"); scheduleSave(); }}>{runMode === "batch" ? "收起批量" : "批量运行"}</button>
          {runMode === "batch" && <details className={styles.batchPicker}><summary>选择页面（{batchPageIds.length}）</summary><div><p>使用已有分镜，图片生成仍逐页确认模型。</p>{pages.isError ? <button onClick={() => void pages.refetch()}>页面加载失败，重试</button> : !pages.data?.length ? <p>当前章节没有可运行页面</p> : pages.data.map((page) => <label key={page.id}><input type="checkbox" checked={batchPageIds.includes(page.id)} onChange={(event) => setBatchPageIds((ids) => event.target.checked ? [...ids, page.id] : ids.filter((id) => id !== page.id))} />第 {page.page_number} 页</label>)}<button disabled={batchBusy || !batchPageIds.length || !activeWorkflow.published_version_id} onClick={() => void startBatch()}>{batchBusy ? "逐页提交中…" : "启动所选页面"}</button></div></details>}
        </div>
        <div className={styles.runScope}><span>运行范围</span><select aria-label="运行范围类型" value={scopeType} onChange={(event) => { const next = event.target.value as "CHAPTER" | "PAGE"; setScopeType(next); setScopeId(next === "CHAPTER" ? chapters.data?.[0]?.id ?? "" : ""); }}><option value="CHAPTER">章节</option><option value="PAGE">页面</option></select>{scopeType === "PAGE" ? <select aria-label="页面所属章节" value={activeChapter} onChange={(event) => { setPageChapterId(event.target.value); setScopeId(""); }}>{chapters.data?.map((chapter) => <option value={chapter.id} key={chapter.id}>{chapter.title}</option>)}</select> : null}<select aria-label="运行目标" value={effectiveScopeId} onChange={(event) => setScopeId(event.target.value)}>{scopeType === "CHAPTER" ? chapters.data?.map((chapter) => <option value={chapter.id} key={chapter.id}>{chapter.title}</option>) : pages.data?.map((page) => <option value={page.id} key={page.id}>第 {page.page_number} 页</option>)}</select></div>
        <div className={styles.runState}>{runsUnavailable ? <><i /><span role="alert">运行列表读取失败：{runs.error instanceof Error ? runs.error.message : "请稍后重试"}</span><button type="button" onClick={() => void runs.refetch()}>重试</button></> : <><i className={displayedRun?.status === "RUNNING" ? styles.running : ""} /><span>{displayedRun ? `运行 ${statusLabel[displayedRun.status] ?? displayedRun.status} · ${displayedRun.node_runs.filter((item) => item.status === "COMPLETED").length}/${displayedRun.node_runs.length}${liveRunStale ? " · 实时状态读取失败，显示快照" : ""}` : "尚未运行已发布版本"}</span>{liveRunStale ? <button type="button" onClick={() => void liveRun.refetch()}>重试</button> : null}</>}</div>
        {/* 取消按钮必须覆盖 PAUSED（审批栅栏态）：cancel_run 接受 PAUSED，
            而同 scope 的重复运行守卫会把 PAUSED 当活跃 run 拒绝（409 文案
            指示“先取消”）——不在这里露出按钮，用户就没有任何停止途径。 */}
        <div className={styles.runActions}><button disabled={!selectedId || startRun.isPending} onClick={() => startRun.mutate("NODE")}><Play size={13} />运行节点</button><button disabled={!selectedId || startRun.isPending} onClick={() => startRun.mutate("FROM")}><Play size={13} />从这里运行</button>{displayedRun && (displayedRun.status === "RUNNING" || displayedRun.status === "PAUSED") ? <button disabled={cancelRun.isPending} onClick={() => cancelRun.mutate(displayedRun.id)}><Pause size={13} />取消</button> : null}{displayedRun?.status === "FAILED" ? <button disabled={retryRun.isPending} onClick={() => retryRun.mutate(displayedRun.id)}><RotateCcw size={13} />重试</button> : null}<button className={styles.runPrimary} disabled={startRun.isPending} onClick={() => startRun.mutate("FULL")}><Play size={14} />运行工作流</button></div>
        {/* #381：generator.page 的审批选择器来源是 imageModels——目录为空时
            选择器里没有可选项，!drawModel 恒真，「确认继续」永久禁用且没有任何
            出路提示。空目录时改渲染明确指引（设置入口 + 取消按钮已在页脚），
            而不是一个空选择器。 */}
        {/* #387-round-4：drawModel 只在 select onChange 时写入、无重置路径——
            曾选过模型后目录清空（供应商被删/目录重验）会留下残留别名，此时
            指引与可点的「确认继续」自相矛盾，点击会把失效别名提交给后端。按钮
            谓词因此要求选中别名仍存在于当前 imageModels（含别名豁免后的行）。 */}
        {/* #394：models 查询后台重试失败且无任何缓存 data 时，imageModels 同为
            空，但这是目录读取错误而非未配置——先于 #381 判定错误态，渲染「读取
            失败」文案与重试入口；react-query 保留 stale 缓存（data 非 undefined）
            时目录内容仍可信，维持 #381 空目录指引。 */}
        {displayedRun?.node_runs.filter((run) => run.status === "WAITING_APPROVAL").map((run) => <div className={styles.approval} key={run.id}><strong>{run.node_type === "generator.page" ? "单页生成等待选择模型" : "采用候选后继续"}</strong>{run.node_type === "generator.page" ? models.isError && models.data === undefined ? <><span>模型目录读取失败：请重试重新获取目录，或取消本次运行。</span><button type="button" onClick={() => void models.refetch()}>重试</button></> : imageModels.length === 0 ? <><span>未配置可用图像模型：请先在设置中启用图像模型，或取消本次运行。</span><Link href="/settings">前往设置</Link></> : <><select aria-label="选择图片模型" value={drawModel} onChange={(event) => setDrawModel(event.target.value as ImageModelAlias | "")}><option value="">选择图片模型</option>{imageModels.map((model) => <option key={model.catalog_id} value={model.logical_alias}>{model.provider} · {model.display_name}</option>)}</select><select aria-label="选择图片清晰度" value={drawResolution} onChange={(event) => setDrawResolution(event.target.value as Resolution)}><option>1K</option><option>2K</option><option>4K</option></select></> : <Link href={`/projects/${projectId}/generate`}>前往采用</Link>}<button disabled={approveNode.isPending || (run.node_type === "generator.page" && (drawModel === "" || !imageModels.some((model) => model.logical_alias === drawModel)))} onClick={() => approveNode.mutate(run)}>确认继续</button></div>)}
      </footer>
      {picker && <WorkflowDialog title={picker.edgeId ? "插入节点 · 自动接线" : "添加节点"} onClose={() => setPicker(null)}>
        <input autoFocus className={styles.searchInput} aria-label="搜索节点" placeholder="搜索节点名称或用途…" value={nodeSearch} onChange={(event) => setNodeSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && pickerItems[0]) addNode(pickerItems[0]); }} />
        <div className={styles.filterTabs}>{[["ALL", "全部"], ["RECENT", "最近使用"], ["FAVORITES", "收藏"], ...Object.entries(categoryLabel)].map(([id, label]) => <button key={id} aria-pressed={category === id} onClick={() => setCategory(id)}>{label}</button>)}</div>
        <div className={styles.searchResults}>{pickerItems.map((item) => <div className={styles.pickerRow} key={item.type}><button onClick={() => addNode(item)}><span>{item.label}<small>{item.description}</small></span><Plus size={16} /></button><button aria-label={`${favorites.includes(item.type) ? "取消收藏" : "收藏"}${item.label}`} aria-pressed={favorites.includes(item.type)} onClick={() => { const next = favorites.includes(item.type) ? favorites.filter((id) => id !== item.type) : [...favorites, item.type]; setFavorites(next); storePreference("mangaflow.node-favorites", next); }}>{favorites.includes(item.type) ? "★" : "☆"}</button></div>)}
          {catalog.isError ? <button onClick={() => void catalog.refetch()}>节点目录加载失败，重试</button> : !pickerItems.length && <p>没有匹配节点{picker.source || picker.target ? "，仅显示端口类型兼容的节点" : "，试试其他分类或关键词"}。</p>}
        </div><footer>{picker.source || picker.target ? "选择后自动连接兼容端口 · Esc 取消并保留原连线" : "Enter 添加首个结果 · 星标收藏 · Esc 关闭"}</footer>
      </WorkflowDialog>}
      {templateDialog && <WorkflowDialog title="流程模板" onClose={() => { if (!templateBusy) setTemplateDialog(false); }}>
        <label className={styles.templateName}>工作流 / 模板名称<input autoFocus maxLength={160} value={templateName} onChange={(event) => setTemplateName(event.target.value)} /></label>
        <div className={styles.templateGrid}>{([
          ["manga_default", "单页生成", "从原作到分镜、生成、采用与质量检查。"],
          ["check", "检查修复", "从已有候选开始确认与检查；发现问题后前往修复页。"],
          ["batch", "批量出图", "从已有分镜开始，为所选页面创建独立流程，逐页确认生成。"],
          ["chapter_export", "整章导出", "汇总已通过检查的成品，导出整章。"],
          ["blank", "空白流程", "从零开始编排你的创作流程。"],
        ] as const).map(([kind, title, description]) => <button key={kind} disabled={templateBusy || !templateName.trim()} onClick={() => void createFromTemplate(kind)}><GitBranch size={18} /><strong>{title}</strong><small>{description}</small></button>)}</div>
        <div className={styles.savedTemplates}><strong>我的模板 · 当前浏览器</strong><button disabled={!templateName.trim()} onClick={saveTemplate}>保存当前流程为模板</button>{templates.map((template) => <div key={template.id}><button disabled={templateBusy} onClick={() => void createFromTemplate(template)}>{template.name} · {template.graph.nodes.length} 个节点</button><button aria-label={`删除模板 ${template.name}`} onClick={() => { const next = templates.filter((item) => item.id !== template.id); if (storePreference("mangaflow.workflow-templates", next)) setTemplates(next); }}>×</button></div>)}</div>
        <footer>套用会创建新流程。自定义模板保存在当前浏览器，也可用顶部导出迁移。</footer>
      </WorkflowDialog>}
    </main>
  );
}
