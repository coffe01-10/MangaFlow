import {
  BookOpenText,
  Braces,
  FileOutput,
  Focus,
  ImageIcon,
  LibraryBig,
  ScanSearch,
  Sparkles,
  WandSparkles,
  type LucideIcon,
} from "lucide-react";

import type {
  FlowEdge,
  FlowNode,
  NodeKind,
  NodeStatus,
  PaletteTemplate,
  PortDefinition,
} from "./types";

export const NODE_WIDTH = 264;
export const NODE_HEIGHT = 178;
export const PORT_BASE_Y = 112;
export const PORT_GAP = 29;
export const WORLD_WIDTH = 2600;
export const WORLD_HEIGHT = 1500;
export const MIN_ZOOM = 0.18;
export const MAX_ZOOM = 1.45;
export const STORAGE_KEY = "mangaflow.workflow.v1";
export const ACTIVE_PROJECT_KEY = "mangaflow.workflow.activeProject";

export const port = (id: string, label: string, dataType: PortDefinition["dataType"]): PortDefinition => ({ id, label, dataType });

export const paletteGroups: { label: string; items: PaletteTemplate[] }[] = [
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

export const templateMap = new Map(paletteGroups.flatMap((group) => group.items).map((item) => [item.key, item]));

export const kindLabel: Record<NodeKind, string> = {
  input: "INPUT",
  agent: "AGENT",
  director: "DIRECTOR",
  generator: "GENERATOR",
  quality: "INSPECTOR",
  output: "OUTPUT",
};

export const kindIcon: Record<NodeKind, LucideIcon> = {
  input: BookOpenText,
  agent: Sparkles,
  director: Focus,
  generator: ImageIcon,
  quality: ScanSearch,
  output: FileOutput,
};

export const statusLabel: Record<NodeStatus, string> = {
  idle: "等待",
  ready: "就绪",
  running: "运行中",
  done: "已完成",
  warning: "需检查",
};

export function edge(source: string, sourcePort: string, target: string, targetPort: string): FlowEdge {
  return { id: `${source}:${sourcePort}-${target}:${targetPort}`, source, sourcePort, target, targetPort };
}

export function createNode(
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

export const initialNodes: FlowNode[] = [
  createNode("source", "source-1", 90, 238, { status: "done" }),
  createNode("parser", "parser-1", 410, 188, { status: "done" }),
  createNode("adapter", "adapter-1", 730, 188, { status: "ready" }),
  createNode("director", "director-1", 1050, 188),
  createNode("assets", "assets-1", 730, 470, { status: "done" }),
  createNode("generator", "generator-1", 1370, 268),
  createNode("quality", "quality-1", 1690, 268),
  createNode("export", "export-1", 2050, 268),
];

export const initialEdges: FlowEdge[] = [
  edge("source-1", "source", "parser-1", "source"),
  edge("parser-1", "story", "adapter-1", "story"),
  edge("adapter-1", "script", "director-1", "script"),
  edge("director-1", "panels", "generator-1", "panels"),
  edge("assets-1", "assets", "generator-1", "assets"),
  edge("generator-1", "page", "quality-1", "page"),
  edge("quality-1", "approved", "export-1", "page"),
];
