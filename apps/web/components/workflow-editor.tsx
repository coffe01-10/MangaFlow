"use client";

import {
  Activity,
  AlignCenter,
  BookOpenText,
  Box,
  Braces,
  Check,
  ChevronDown,
  ChevronUp,
  CircleDot,
  Download,
  FileOutput,
  Focus,
  ImageIcon,
  LibraryBig,
  Link2,
  LockKeyhole,
  Maximize2,
  Minus,
  MoreHorizontal,
  MousePointer2,
  PanelRightClose,
  Play,
  Plus,
  Redo2,
  RotateCcw,
  Save,
  ScanSearch,
  Search,
  Settings2,
  Sparkles,
  Trash2,
  Undo2,
  Unplug,
  WandSparkles,
  Workflow,
  ZoomIn,
  ZoomOut,
  type LucideIcon,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type DragEvent as ReactDragEvent,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";
import { api } from "@/lib/api";
import styles from "./workflow-editor.module.css";

type NodeKind = "input" | "agent" | "director" | "generator" | "quality" | "output";
type NodeStatus = "idle" | "ready" | "running" | "done" | "warning";

type PortDefinition = {
  id: string;
  label: string;
  dataType: "text" | "json" | "image" | "asset" | "report";
};

type FlowNode = {
  id: string;
  kind: NodeKind;
  title: string;
  eyebrow: string;
  description: string;
  x: number;
  y: number;
  status: NodeStatus;
  inputs: PortDefinition[];
  outputs: PortDefinition[];
  settings: {
    model: string;
    resolution: string;
    concurrency: number;
    locked: boolean;
    notes: string;
  };
};

type FlowEdge = {
  id: string;
  source: string;
  sourcePort: string;
  target: string;
  targetPort: string;
};

type PaletteTemplate = {
  key: string;
  title: string;
  description: string;
  kind: NodeKind;
  icon: LucideIcon;
  inputs: PortDefinition[];
  outputs: PortDefinition[];
};

type ConnectionAnchor =
  | { side: "output"; nodeId: string; portId: string }
  | { side: "input"; nodeId: string; portId: string };

type Gesture =
  | {
      type: "node";
      nodeId: string;
      startClientX: number;
      startClientY: number;
      startX: number;
      startY: number;
    }
  | {
      type: "pan";
      startClientX: number;
      startClientY: number;
      startX: number;
      startY: number;
    }
  | {
      type: "connect";
      anchor: ConnectionAnchor;
    };

const NODE_WIDTH = 264;
const NODE_HEIGHT = 178;
const PORT_BASE_Y = 112;
const PORT_GAP = 29;
const WORLD_WIDTH = 2600;
const WORLD_HEIGHT = 1500;
const MIN_ZOOM = 0.18;
const MAX_ZOOM = 1.45;
const STORAGE_KEY = "mangaflow.workflow.v1";
const ACTIVE_PROJECT_KEY = "mangaflow.workflow.activeProject";

const port = (id: string, label: string, dataType: PortDefinition["dataType"]): PortDefinition => ({ id, label, dataType });

const paletteGroups: { label: string; items: PaletteTemplate[] }[] = [
  {
    label: "输入 / INPUT",
    items: [
      { key: "source", title: "原作章节", description: "章节原文与修订版本", kind: "input", icon: BookOpenText, inputs: [], outputs: [port("source", "原始文本", "text")] },
      { key: "assets", title: "参考资产", description: "角色、服装与风格参考", kind: "input", icon: LibraryBig, inputs: [], outputs: [port("assets", "资产包", "asset")] },
    ],
  },
  {
    label: "智能体 / AGENTS",
    items: [
      { key: "parser", title: "剧情解析", description: "识别场景、角色和事实", kind: "agent", icon: Braces, inputs: [port("source", "原始文本", "text")], outputs: [port("story", "结构化剧情", "json")] },
      { key: "adapter", title: "漫画改编", description: "压缩对白并生成情节拍", kind: "agent", icon: WandSparkles, inputs: [port("story", "结构化剧情", "json")], outputs: [port("script", "漫画剧本", "json")] },
      { key: "director", title: "分镜导演", description: "规划景别、机位和阅读顺序", kind: "director", icon: Focus, inputs: [port("script", "漫画剧本", "json")], outputs: [port("panels", "分镜数据", "json")] },
    ],
  },
  {
    label: "生成 / OUTPUT",
    items: [
      { key: "generator", title: "漫画页生成", description: "组装提示词并生成候选", kind: "generator", icon: ImageIcon, inputs: [port("panels", "分镜数据", "json"), port("assets", "参考资产", "asset")], outputs: [port("page", "漫画页面", "image")] },
      { key: "quality", title: "质量检查", description: "说话人、角色、服装、道具与连续性", kind: "quality", icon: ScanSearch, inputs: [port("page", "漫画页面", "image")], outputs: [port("report", "检查报告", "report"), port("approved", "通过页面", "image")] },
      { key: "export", title: "连续导出", description: "输出 PNG、PDF 与项目数据", kind: "output", icon: FileOutput, inputs: [port("page", "通过页面", "image")], outputs: [port("files", "导出文件", "asset")] },
    ],
  },
];

const templateMap = new Map(paletteGroups.flatMap((group) => group.items).map((item) => [item.key, item]));

const kindLabel: Record<NodeKind, string> = {
  input: "INPUT",
  agent: "AGENT",
  director: "DIRECTOR",
  generator: "GENERATOR",
  quality: "INSPECTOR",
  output: "OUTPUT",
};

const initialNodes: FlowNode[] = [
  createNode("source", "source-1", 90, 238, { status: "done" }),
  createNode("parser", "parser-1", 410, 188, { status: "done" }),
  createNode("adapter", "adapter-1", 730, 188, { status: "ready" }),
  createNode("director", "director-1", 1050, 188),
  createNode("assets", "assets-1", 730, 470, { status: "done" }),
  createNode("generator", "generator-1", 1370, 268),
  createNode("quality", "quality-1", 1690, 268),
  createNode("export", "export-1", 2050, 268),
];

const initialEdges: FlowEdge[] = [
  edge("source-1", "source", "parser-1", "source"),
  edge("parser-1", "story", "adapter-1", "story"),
  edge("adapter-1", "script", "director-1", "script"),
  edge("director-1", "panels", "generator-1", "panels"),
  edge("assets-1", "assets", "generator-1", "assets"),
  edge("generator-1", "page", "quality-1", "page"),
  edge("quality-1", "approved", "export-1", "page"),
];

const kindIcon: Record<NodeKind, LucideIcon> = {
  input: BookOpenText,
  agent: Sparkles,
  director: Focus,
  generator: ImageIcon,
  quality: ScanSearch,
  output: FileOutput,
};

const statusLabel: Record<NodeStatus, string> = {
  idle: "等待",
  ready: "就绪",
  running: "运行中",
  done: "已完成",
  warning: "需检查",
};

function edge(source: string, sourcePort: string, target: string, targetPort: string): FlowEdge {
  return { id: `${source}:${sourcePort}-${target}:${targetPort}`, source, sourcePort, target, targetPort };
}

function createNode(
  templateKey: string,
  id: string,
  x: number,
  y: number,
  overrides: Partial<FlowNode> = {},
): FlowNode {
  const template = templateMap.get(templateKey)!;
  return {
    id,
    kind: template.kind,
    title: template.title,
    eyebrow: kindLabel[template.kind],
    description: template.description,
    x,
    y,
    status: "idle",
    inputs: template.inputs,
    outputs: template.outputs,
    settings: {
      model: template.kind === "generator" ? "Nano Banana 2" : "Gemini 3.5 Flash",
      resolution: "1K 草稿",
      concurrency: 2,
      locked: false,
      notes: "",
    },
    ...overrides,
  };
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

function getPortPoint(node: FlowNode, portId: string, side: "input" | "output") {
  const ports = side === "input" ? node.inputs : node.outputs;
  const index = Math.max(0, ports.findIndex((item) => item.id === portId));
  return {
    x: side === "input" ? node.x : node.x + NODE_WIDTH,
    y: node.y + PORT_BASE_Y + index * PORT_GAP,
  };
}

function pathBetween(start: { x: number; y: number }, end: { x: number; y: number }) {
  const distance = Math.max(72, Math.abs(end.x - start.x) * 0.48);
  return `M ${start.x} ${start.y} C ${start.x + distance} ${start.y}, ${end.x - distance} ${end.y}, ${end.x} ${end.y}`;
}

function nodeTypeClass(kind: NodeKind) {
  return `${styles.node} ${styles[`node_${kind}`]}`;
}

function portTypeClass(dataType: PortDefinition["dataType"]) {
  return `${styles.portHandle} ${styles[`port_${dataType}`]}`;
}

export function WorkflowEditor() {
  const viewportRef = useRef<HTMLDivElement>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const runTokenRef = useRef(0);
  const [activeProjectId, setActiveProjectId] = useState(() => typeof window === "undefined"
    ? ""
    : window.localStorage.getItem(ACTIVE_PROJECT_KEY) ?? "");
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodes);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>("adapter-1");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [pan, setPan] = useState({ x: 44, y: 92 });
  const [zoom, setZoom] = useState(0.72);
  const [draftEnd, setDraftEnd] = useState<{ x: number; y: number } | null>(null);
  const [connectionAnchor, setConnectionAnchor] = useState<ConnectionAnchor | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [saved, setSaved] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [logOpen, setLogOpen] = useState(true);
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [toast, setToast] = useState("");

  const projectsQuery = useQuery({ queryKey: ["workflow-projects"], queryFn: api.projects });
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

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) ?? null;
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);

  const worldPoint = useCallback((clientX: number, clientY: number) => {
    const rect = viewportRef.current?.getBoundingClientRect();
    return {
      x: ((clientX - (rect?.left ?? 0)) - pan.x) / zoom,
      y: ((clientY - (rect?.top ?? 0)) - pan.y) / zoom,
    };
  }, [pan.x, pan.y, zoom]);

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
      const parsed = JSON.parse(stored) as { nodes?: FlowNode[]; edges?: FlowEdge[] };
      window.queueMicrotask(() => {
        if (parsed.nodes?.length) setNodes(parsed.nodes);
        if (parsed.edges) setEdges(parsed.edges);
      });
    } catch {
      window.localStorage.removeItem(STORAGE_KEY);
    }
  }, []);

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
  }, [selectedEdgeId, selectedNodeId]);

  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveFlow();
      } else if (!editing && (event.key === "Delete" || event.key === "Backspace")) {
        deleteSelection();
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [deleteSelection, saveFlow]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      const gesture = gestureRef.current;
      if (!gesture) return;
      if (gesture.type === "node") {
        const dx = (event.clientX - gesture.startClientX) / zoom;
        const dy = (event.clientY - gesture.startClientY) / zoom;
        setNodes((current) => current.map((node) => node.id === gesture.nodeId ? {
          ...node,
          x: clamp(Math.round(gesture.startX + dx), 0, WORLD_WIDTH - NODE_WIDTH),
          y: clamp(Math.round(gesture.startY + dy), 0, WORLD_HEIGHT - NODE_HEIGHT),
        } : node));
        setSaved(false);
      } else if (gesture.type === "pan") {
        setPan({
          x: gesture.startX + event.clientX - gesture.startClientX,
          y: gesture.startY + event.clientY - gesture.startClientY,
        });
      } else {
        setDraftEnd(worldPoint(event.clientX, event.clientY));
      }
    };

    const up = (event: PointerEvent) => {
      const gesture = gestureRef.current;
      if (gesture?.type === "connect") {
        const dropTarget = document.elementFromPoint(event.clientX, event.clientY);
        if (gesture.anchor.side === "output") {
          const target = dropTarget?.closest<HTMLElement>("[data-input-port]");
          const targetId = target?.dataset.nodeId;
          const targetPort = target?.dataset.inputPort;
          if (targetId && targetPort && targetId !== gesture.anchor.nodeId) {
            const next = edge(gesture.anchor.nodeId, gesture.anchor.portId, targetId, targetPort);
            setEdges((current) => [
              ...current.filter((item) => !(item.target === targetId && item.targetPort === targetPort)),
              next,
            ]);
            setSaved(false);
          }
        } else {
          const source = dropTarget?.closest<HTMLElement>("[data-output-port]");
          const sourceId = source?.dataset.nodeId;
          const sourcePort = source?.dataset.outputPort;
          if (sourceId && sourcePort && sourceId !== gesture.anchor.nodeId) {
            const next = edge(sourceId, sourcePort, gesture.anchor.nodeId, gesture.anchor.portId);
            setEdges((current) => [
              ...current.filter((item) => !(item.target === gesture.anchor.nodeId && item.targetPort === gesture.anchor.portId)),
              next,
            ]);
            setSaved(false);
          }
        }
      }
      gestureRef.current = null;
      setDraftEnd(null);
      setConnectionAnchor(null);
      document.body.classList.remove(styles.draggingBody);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, [worldPoint, zoom]);

  function beginNodeDrag(event: ReactPointerEvent, node: FlowNode) {
    if (event.button !== 0) return;
    event.stopPropagation();
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    gestureRef.current = {
      type: "node",
      nodeId: node.id,
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: node.x,
      startY: node.y,
    };
    document.body.classList.add(styles.draggingBody);
  }

  function beginOutputConnection(event: ReactPointerEvent, nodeId: string, portId: string) {
    if (event.button !== 0) return;
    event.stopPropagation();
    const node = nodeMap.get(nodeId);
    if (!node) return;
    const anchor: ConnectionAnchor = { side: "output", nodeId, portId };
    gestureRef.current = { type: "connect", anchor };
    setConnectionAnchor(anchor);
    setDraftEnd(getPortPoint(node, portId, "output"));
    document.body.classList.add(styles.draggingBody);
  }

  function beginInputConnection(event: ReactPointerEvent, nodeId: string, portId: string) {
    if (event.button !== 0) return;
    event.stopPropagation();
    const node = nodeMap.get(nodeId);
    if (!node) return;
    const anchor: ConnectionAnchor = { side: "input", nodeId, portId };
    const wasConnected = edges.some((item) => item.target === nodeId && item.targetPort === portId);
    gestureRef.current = { type: "connect", anchor };
    setConnectionAnchor(anchor);
    setDraftEnd(getPortPoint(node, portId, "input"));
    if (wasConnected) {
      setEdges((current) => current.filter((item) => !(item.target === nodeId && item.targetPort === portId)));
      setSaved(false);
    }
    document.body.classList.add(styles.draggingBody);
  }

  function beginPan(event: ReactPointerEvent<HTMLDivElement>) {
    const target = event.target as HTMLElement;
    if (target.closest("button, [data-flow-node], [data-flow-edge]")) return;
    if (event.button !== 0 && event.button !== 1) return;
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    gestureRef.current = {
      type: "pan",
      startClientX: event.clientX,
      startClientY: event.clientY,
      startX: pan.x,
      startY: pan.y,
    };
    document.body.classList.add(styles.draggingBody);
  }

  function handleWheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return;
    const pointerX = event.clientX - rect.left;
    const pointerY = event.clientY - rect.top;
    const worldX = (pointerX - pan.x) / zoom;
    const worldY = (pointerY - pan.y) / zoom;
    const nextZoom = clamp(zoom * (event.deltaY > 0 ? 0.9 : 1.1), MIN_ZOOM, MAX_ZOOM);
    setZoom(nextZoom);
    setPan({ x: pointerX - worldX * nextZoom, y: pointerY - worldY * nextZoom });
  }

  function zoomBy(factor: number) {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return;
    const cx = rect.width / 2;
    const cy = rect.height / 2;
    const worldX = (cx - pan.x) / zoom;
    const worldY = (cy - pan.y) / zoom;
    const nextZoom = clamp(zoom * factor, MIN_ZOOM, MAX_ZOOM);
    setZoom(nextZoom);
    setPan({ x: cx - worldX * nextZoom, y: cy - worldY * nextZoom });
  }

  function fitToView() {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect || nodes.length === 0) return;
    const bounds = nodes.reduce((acc, node) => ({
      minX: Math.min(acc.minX, node.x),
      minY: Math.min(acc.minY, node.y),
      maxX: Math.max(acc.maxX, node.x + NODE_WIDTH),
      maxY: Math.max(acc.maxY, node.y + NODE_HEIGHT),
    }), { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
    const padding = 52;
    const nextZoom = clamp(Math.min((rect.width - padding * 2) / (bounds.maxX - bounds.minX), (rect.height - padding * 2) / (bounds.maxY - bounds.minY)), MIN_ZOOM, 1.05);
    setZoom(nextZoom);
    setPan({ x: padding - bounds.minX * nextZoom, y: padding - bounds.minY * nextZoom });
  }

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

  const visibleGroups = paletteGroups.map((group) => ({
    ...group,
    items: group.items.filter((item) => `${item.title}${item.description}`.toLowerCase().includes(search.toLowerCase())),
  })).filter((group) => group.items.length > 0);

  const completedCount = nodes.filter((node) => node.status === "done").length;
  const runningNode = nodes.find((node) => node.status === "running");
  const assetCount = assetsQuery.data?.length ?? 0;
  const chapterCount = chaptersQuery.data?.length ?? 0;
  const projectDataLoading = Boolean(resolvedProjectId) && (assetsQuery.isLoading || chaptersQuery.isLoading);
  const projectDataError = projectsQuery.isError || assetsQuery.isError || chaptersQuery.isError;
  const selectedIsAssetSource = selectedNode?.outputs.some((item) => item.id === "assets" && item.dataType === "asset") ?? false;
  const selectedIsChapterSource = selectedNode?.outputs.some((item) => item.id === "source" && item.dataType === "text") ?? false;
  const selectedBindingCopy = !activeProject
    ? "尚未连接项目；该节点只能保留本地流程配置。"
    : selectedIsAssetSource
      ? `${assetCount} 项项目资产可从“资产包”端口传给下游节点。`
      : selectedIsChapterSource
        ? `${chapterCount} 章项目原作可从“原始文本”端口传给下游节点。`
        : "继承当前项目范围，但只处理端口实际连入的数据，不会自动读取全部资产。";

  return (
    <div className={styles.editor}>
      <header className={styles.topbar}>
        <div className={styles.breadcrumb}>
          <span className={styles.workspaceMark}><Workflow size={15} /> FLOW / 01</span>
          <i />
          <div>
            <label className={styles.projectPicker} title="选择这张画布绑定的项目">
              <Link2 size={12} />
              <select aria-label="当前工作流项目" value={resolvedProjectId} onChange={(event) => chooseProject(event.target.value)} disabled={!projectsQuery.data?.length}>
                <option value="" disabled>{projectsQuery.isLoading ? "正在读取项目…" : "选择项目"}</option>
                {projectsQuery.data?.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
              <ChevronDown size={12} />
            </label>
            <span>{projectDataError ? "项目服务未连接" : projectDataLoading ? "正在同步项目上下文…" : `${chapterCount} 章 · ${assetCount} 项资产`}</span>
          </div>
        </div>
        <div className={styles.topActions}>
          <div className={saved ? styles.saveState : `${styles.saveState} ${styles.unsaved}`}><span />{saved ? "所有更改已保存" : "有未保存更改"}</div>
          <button className={styles.iconButton} title="撤销（即将支持）" disabled><Undo2 size={16} /></button>
          <button className={styles.iconButton} title="重做（即将支持）" disabled><Redo2 size={16} /></button>
          <button className={styles.secondaryButton} onClick={exportFlow}><Download size={15} />导出</button>
          <button className={styles.secondaryButton} onClick={saveFlow}><Save size={15} />保存</button>
          <button className={styles.runButton} onClick={runWorkflow} disabled={isRunning}>
            {isRunning ? <Activity className={styles.pulseIcon} size={15} /> : <Play size={15} fill="currentColor" />}
            {isRunning ? "运行中" : "运行工作流"}
          </button>
        </div>
      </header>

      <div className={`${styles.body} ${inspectorOpen ? "" : styles.inspectorClosed}`}>
        <aside className={styles.palette}>
          <div className={styles.paletteHeader}>
            <div><span>NODE LIBRARY</span><strong>节点库</strong></div>
            <button className={styles.iconButton} onClick={() => addNode("parser")} title="添加节点"><Plus size={16} /></button>
          </div>
          <label className={styles.searchBox}>
            <Search size={14} />
            <input aria-label="搜索节点" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索节点…" />
            <kbd>/</kbd>
          </label>
          <div className={styles.paletteScroll}>
            {visibleGroups.map((group) => {
              const isCollapsed = collapsedGroups.includes(group.label) && !search.trim();
              return (
                <section className={styles.paletteGroup} key={group.label}>
                  <button className={styles.paletteGroupHeader} aria-expanded={!isCollapsed} onClick={() => togglePaletteGroup(group.label)}>
                    <span>{group.label}</span><ChevronDown className={isCollapsed ? styles.chevronCollapsed : ""} size={13} />
                  </button>
                  {!isCollapsed && <div className={styles.paletteItems}>
                  {group.items.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        key={item.key}
                        draggable
                        className={`${styles.paletteItem} ${styles[`palette_${item.kind}`]}`}
                        onClick={() => addNode(item.key)}
                        onDragStart={(event) => {
                          event.dataTransfer.setData("application/x-mangaflow-node", item.key);
                          event.dataTransfer.effectAllowed = "copy";
                        }}
                      >
                        <span><Icon size={15} /></span>
                        <div><strong>{item.title}</strong><small>{item.description}</small></div>
                        <Plus size={13} />
                      </button>
                    );
                  })}
                  </div>}
                </section>
              );
            })}
          </div>
          <div className={styles.paletteHint}><MousePointer2 size={14} /><span>拖到画布添加节点<br /><small>或单击快速添加</small></span></div>
        </aside>

        <main className={styles.canvasShell}>
          <div className={styles.canvasToolbar}>
            <div className={styles.toolGroup}>
              <button className={`${styles.canvasTool} ${styles.active}`} title="选择"><MousePointer2 size={15} /></button>
              <button className={styles.canvasTool} title="居中选中节点" onClick={() => selectedNode && setPan({ x: 360 - selectedNode.x * zoom, y: 260 - selectedNode.y * zoom })}><AlignCenter size={15} /></button>
              <i />
              <button className={styles.canvasTool} title="断开选中连线" disabled={!selectedEdgeId} onClick={deleteSelection}><Unplug size={15} /></button>
              <button className={styles.canvasTool} title="删除选中项" disabled={!selectedNodeId && !selectedEdgeId} onClick={deleteSelection}><Trash2 size={15} /></button>
            </div>
            <div className={styles.canvasMeta} title={activeProject ? `已连接 ${activeProject.name}：${chapterCount} 章，${assetCount} 项资产` : "尚未连接项目"}>
              <span className={`${styles.liveDot} ${projectDataError || !activeProject ? styles.projectErrorDot : ""}`} />
              <span className={styles.projectMetaText}>{activeProject ? `${activeProject.name} · ${assetCount} 资产` : projectsQuery.isLoading ? "连接项目…" : "未连接项目"}</span>
              <i />{nodes.length} 节点<i />{edges.length} 连线
            </div>
          </div>

          <div
            ref={viewportRef}
            className={`${styles.viewport} ${connectionAnchor?.side === "output" ? styles.connectingFromOutput : ""} ${connectionAnchor?.side === "input" ? styles.connectingFromInput : ""}`}
            style={{
              backgroundPosition: `${pan.x}px ${pan.y}px`,
              backgroundSize: `${24 * zoom}px ${24 * zoom}px`,
            }}
            onPointerDown={beginPan}
            onWheel={handleWheel}
            onDragOver={(event) => event.preventDefault()}
            onDrop={handleDrop}
          >
            <div className={styles.world} style={{ width: WORLD_WIDTH, height: WORLD_HEIGHT, transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}>
              <svg className={styles.edges} width={WORLD_WIDTH} height={WORLD_HEIGHT} aria-hidden="true">
                {edges.map((item) => {
                  const source = nodeMap.get(item.source);
                  const target = nodeMap.get(item.target);
                  if (!source || !target) return null;
                  const start = getPortPoint(source, item.sourcePort, "output");
                  const end = getPortPoint(target, item.targetPort, "input");
                  const selected = selectedEdgeId === item.id;
                  return (
                    <g
                      key={item.id}
                      data-flow-edge="true"
                      data-edge-id={item.id}
                      data-source-node={item.source}
                      data-source-port={item.sourcePort}
                      data-target-node={item.target}
                      data-target-port={item.targetPort}
                      onPointerDown={(event) => {
                      event.stopPropagation();
                      setSelectedEdgeId(item.id);
                      setSelectedNodeId(null);
                    }}
                    >
                      <path className={styles.edgeHit} d={pathBetween(start, end)} />
                      <path className={selected ? styles.edgeSelected : styles.edgeLine} d={pathBetween(start, end)} />
                    </g>
                  );
                })}
                {draftEnd && connectionAnchor && (() => {
                  const anchorNode = nodeMap.get(connectionAnchor.nodeId);
                  if (!anchorNode) return null;
                  const anchorPoint = getPortPoint(anchorNode, connectionAnchor.portId, connectionAnchor.side);
                  return <path className={styles.edgeDraft} d={connectionAnchor.side === "output" ? pathBetween(anchorPoint, draftEnd) : pathBetween(draftEnd, anchorPoint)} />;
                })()}
              </svg>

              {nodes.map((node) => {
                const Icon = kindIcon[node.kind];
                const selected = node.id === selectedNodeId;
                const isAssetSource = node.outputs.some((item) => item.id === "assets" && item.dataType === "asset");
                const isChapterSource = node.outputs.some((item) => item.id === "source" && item.dataType === "text");
                return (
                  <article
                    key={node.id}
                    data-flow-node="true"
                    className={`${nodeTypeClass(node.kind)} ${selected ? styles.selectedNode : ""}`}
                    style={{ transform: `translate(${node.x}px, ${node.y}px)` }}
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      setSelectedNodeId(node.id);
                      setSelectedEdgeId(null);
                    }}
                  >
                    <header className={styles.nodeHeader} onPointerDown={(event) => beginNodeDrag(event, node)}>
                      <span className={styles.nodeIcon}><Icon size={16} /></span>
                      <div><small>{node.eyebrow}</small><strong>{node.title}</strong></div>
                      <button title="更多操作" onPointerDown={(event) => event.stopPropagation()}><MoreHorizontal size={15} /></button>
                    </header>
                    <p className={styles.nodeDescription}>{node.description}</p>
                    <div className={styles.nodeStatus}><span className={styles[`status_${node.status}`]} />{statusLabel[node.status]}</div>

                    <div className={styles.inputPorts}>
                      {node.inputs.map((item) => (
                        <div className={styles.portRow} key={item.id}>
                          <button
                            aria-label={`${node.title} 输入 ${item.label}`}
                            className={portTypeClass(item.dataType)}
                            data-node-id={node.id}
                            data-input-port={item.id}
                            data-connected={edges.some((edgeItem) => edgeItem.target === node.id && edgeItem.targetPort === item.id) ? "true" : "false"}
                            onPointerDown={(event) => beginInputConnection(event, node.id, item.id)}
                            title={edges.some((edgeItem) => edgeItem.target === node.id && edgeItem.targetPort === item.id) ? "拖动以更换来源；释放到空白处断开" : "拖到任意输出端口建立连接"}
                          />
                          <span>{item.label}</span>
                        </div>
                      ))}
                    </div>
                    <div className={styles.outputPorts}>
                      {node.outputs.map((item) => (
                        <div className={styles.portRow} key={item.id}>
                          <span>{isAssetSource && item.id === "assets"
                            ? `${item.label} · ${assetsQuery.isLoading ? "…" : assetCount}`
                            : isChapterSource && item.id === "source"
                              ? `${item.label} · ${chaptersQuery.isLoading ? "…" : chapterCount}章`
                              : item.label}</span>
                          <button
                            aria-label={`${node.title} 输出 ${item.label}`}
                            className={portTypeClass(item.dataType)}
                            data-node-id={node.id}
                            data-output-port={item.id}
                            onPointerDown={(event) => beginOutputConnection(event, node.id, item.id)}
                            title={`拖到任意输入端口连接 ${item.label}`}
                          />
                        </div>
                      ))}
                    </div>
                    <footer className={styles.nodeFooter}>
                      <span className={styles.nodeModel}>{node.settings.model}</span>
                      <span className={`${styles.nodeProjectBadge} ${activeProject && !projectDataError ? "" : styles.nodeProjectBadgeOff}`} title={activeProject ? `已绑定项目：${activeProject.name}；数据仍需通过端口传递` : "未绑定项目"}>
                        <Link2 size={9} />{activeProject ? "项目" : "未绑定"}
                      </span>
                      {node.settings.locked && <LockKeyhole size={11} />}
                      <small>#{node.id.split("-").at(-1)}</small>
                    </footer>
                  </article>
                );
              })}
            </div>

            <div className={connectionAnchor ? `${styles.canvasHint} ${styles.canvasHintActive}` : styles.canvasHint}>
              <MousePointer2 size={13} />
              {connectionAnchor?.side === "output" && "正在连线：释放到任意输入端口"}
              {connectionAnchor?.side === "input" && "正在换源：释放到任意输出端口；放到空白处断开"}
              {!connectionAnchor && "自由连线：拖动任意端口 · 选中连线后按 Delete 删除"}
            </div>
            <div className={styles.zoomControls}>
              <button onClick={() => zoomBy(1.15)} title="放大"><ZoomIn size={15} /></button>
              <span>{Math.round(zoom * 100)}%</span>
              <button onClick={() => zoomBy(0.87)} title="缩小"><ZoomOut size={15} /></button>
              <i />
              <button onClick={fitToView} title="适应画布"><Maximize2 size={15} /></button>
            </div>

            <div className={styles.minimap} aria-label="工作流缩略图">
              <div className={styles.minimapViewport} />
              {nodes.map((node) => (
                <i key={node.id} className={styles[`mini_${node.kind}`]} style={{ left: `${node.x / WORLD_WIDTH * 100}%`, top: `${node.y / WORLD_HEIGHT * 100}%` }} />
              ))}
            </div>

            <section className={logOpen ? styles.runLog : `${styles.runLog} ${styles.runLogCollapsed}`}>
                <header>
                  <div><Activity size={14} /><strong>运行监视器</strong><span>{isRunning ? "LIVE" : "IDLE"}</span></div>
                  <button aria-expanded={logOpen} onClick={() => setLogOpen((current) => !current)} title={logOpen ? "收起运行监视器" : "展开运行监视器"}>
                    {logOpen ? <Minus size={14} /> : <ChevronUp size={14} />}
                  </button>
                </header>
                {logOpen && <div className={styles.logBody}>
                  <div className={styles.progressRing} style={{ "--progress": `${Math.round(completedCount / Math.max(nodes.length, 1) * 100) * 3.6}deg` } as CSSProperties}><span>{completedCount}<small>/{nodes.length}</small></span></div>
                  <div className={styles.logCopy}>
                    <span>{isRunning ? "正在执行" : completedCount === nodes.length ? "流程已完成" : "等待运行"}</span>
                    <strong>{runningNode?.title ?? (completedCount === nodes.length ? "全部节点通过" : "从原作章节开始")}</strong>
                    <small>{isRunning ? "输出将自动传递至下一个节点" : "运行只演示节点状态，不会消耗模型额度"}</small>
                  </div>
                  <div className={styles.logStats}><span><i className={styles.green} />成功 {completedCount}</span><span><i className={styles.amber} />警告 {nodes.filter((node) => node.status === "warning").length}</span><span><i />等待 {nodes.filter((node) => ["idle", "ready"].includes(node.status)).length}</span></div>
                </div>}
              </section>
          </div>
        </main>

        {inspectorOpen ? (
          <aside className={styles.inspector}>
            <header className={styles.inspectorHeader}>
              <div><span>INSPECTOR</span><strong>属性面板</strong></div>
              <button className={styles.iconButton} onClick={() => setInspectorOpen(false)} title="收起属性面板"><PanelRightClose size={16} /></button>
            </header>
            {selectedNode ? (
              <div className={styles.inspectorContent}>
                <div className={styles.selectedSummary}>
                  <span className={`${styles.summaryIcon} ${styles[`summary_${selectedNode.kind}`]}`}>{(() => { const Icon = kindIcon[selectedNode.kind]; return <Icon size={18} />; })()}</span>
                  <div><small>{selectedNode.eyebrow} / {selectedNode.id.toUpperCase()}</small><strong>{selectedNode.title}</strong><span><i className={styles[`status_${selectedNode.status}`]} />{statusLabel[selectedNode.status]}</span></div>
                </div>

                <section className={styles.inspectorSection}>
                  <h2>项目绑定 <span>CONTEXT</span></h2>
                  <div className={`${styles.projectBindingCard} ${activeProject && !projectDataError ? "" : styles.projectBindingCardOff}`}>
                    <span><Link2 size={14} /></span>
                    <div>
                      <strong>{activeProject?.name ?? (projectsQuery.isLoading ? "正在连接项目…" : "未绑定项目")}</strong>
                      <p>{selectedBindingCopy}</p>
                    </div>
                    <small>{activeProject && !projectDataError ? "已连接" : "未连接"}</small>
                  </div>
                </section>

                <section className={styles.inspectorSection}>
                  <h2>基本信息 <span>BASIC</span></h2>
                  <label className={styles.fieldLabel} htmlFor={`flow-title-${selectedNode.id}`}>节点名称</label>
                  <input id={`flow-title-${selectedNode.id}`} className={styles.textInput} value={selectedNode.title} onChange={(event) => updateNode({ title: event.target.value })} />
                  <label className={styles.fieldLabel} htmlFor={`flow-description-${selectedNode.id}`}>说明</label>
                  <textarea id={`flow-description-${selectedNode.id}`} className={styles.textArea} value={selectedNode.description} onChange={(event) => updateNode({ description: event.target.value })} />
                </section>

                <section className={styles.inspectorSection}>
                  <h2>执行设置 <span>RUNTIME</span></h2>
                  <label className={styles.fieldLabel} htmlFor={`flow-model-${selectedNode.id}`}>使用模型</label>
                  <label className={styles.selectBox}>
                    <select id={`flow-model-${selectedNode.id}`} value={selectedNode.settings.model} onChange={(event) => updateSettings({ model: event.target.value })}>
                      <option>Gemini 3.5 Flash</option>
                      <option>Nano Banana 2</option>
                      <option>Nano Banana Pro</option>
                    </select>
                    <ChevronDown size={14} />
                  </label>
                  <div className={styles.twoFields}>
                    <div><label className={styles.fieldLabel} htmlFor={`flow-resolution-${selectedNode.id}`}>清晰度</label><label className={styles.selectBox}><select id={`flow-resolution-${selectedNode.id}`} value={selectedNode.settings.resolution} onChange={(event) => updateSettings({ resolution: event.target.value })}><option>1K 草稿</option><option>2K 标准</option><option>4K 高清</option></select><ChevronDown size={14} /></label></div>
                    <div><label className={styles.fieldLabel} htmlFor={`flow-concurrency-${selectedNode.id}`}>并发数</label><input id={`flow-concurrency-${selectedNode.id}`} className={styles.numberInput} min={1} max={8} type="number" value={selectedNode.settings.concurrency} onChange={(event) => updateSettings({ concurrency: Number(event.target.value) })} /></div>
                  </div>
                  <button aria-pressed={selectedNode.settings.locked} className={selectedNode.settings.locked ? `${styles.toggleRow} ${styles.toggleOn}` : styles.toggleRow} onClick={() => updateSettings({ locked: !selectedNode.settings.locked })}>
                    <span><LockKeyhole size={14} /><span><strong>锁定节点设定</strong><small>运行时禁止自动改写</small></span></span><i />
                  </button>
                </section>

                <section className={styles.inspectorSection}>
                  <h2>端口 <span>PORTS</span></h2>
                  <div className={styles.portList}>
                    {[...selectedNode.inputs.map((item) => ({ ...item, direction: "输入" })), ...selectedNode.outputs.map((item) => ({ ...item, direction: "输出" }))].map((item) => (
                      <div key={`${item.direction}-${item.id}`}><i className={styles[`port_${item.dataType}`]} /><span><strong>{item.label}</strong><small>{item.direction} · {item.dataType}</small></span><CircleDot size={13} /></div>
                    ))}
                  </div>
                </section>

                <section className={styles.inspectorSection}>
                  <h2>备注 <span>NOTES</span></h2>
                  <textarea aria-label="备注" className={`${styles.textArea} ${styles.notesArea}`} value={selectedNode.settings.notes} onChange={(event) => updateSettings({ notes: event.target.value })} placeholder="记录需要人工确认的规则…" />
                </section>

                <div className={styles.inspectorActions}>
                  <button onClick={() => updateNode({ status: "ready" })}><RotateCcw size={14} />重置状态</button>
                  <button className={styles.dangerAction} onClick={deleteSelection}><Trash2 size={14} />删除节点</button>
                </div>
              </div>
            ) : (
              <div className={styles.emptyInspector}><Box size={26} /><strong>未选择节点</strong><p>单击画布中的节点以编辑名称、模型、端口和运行参数。</p></div>
            )}
          </aside>
        ) : (
          <button className={styles.openInspector} onClick={() => setInspectorOpen(true)} title="打开属性面板"><Settings2 size={16} /></button>
        )}
      </div>

      {toast && <div className={styles.toast}><Check size={15} />{toast}</div>}
    </div>
  );
}
