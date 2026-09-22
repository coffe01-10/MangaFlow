import type { Edge, Node } from "@xyflow/react";
import type { WorkflowGraph, WorkflowGraphNode, WorkflowGroup, WorkflowNodeRun, WorkflowNodeType } from "./api";

export type StudioNodeData = {
  graphNode: WorkflowGraphNode;
  runStatus?: string;
  run?: WorkflowNodeRun;
  group?: WorkflowGroup;
  membersSelected?: boolean;
};
export type StudioNode = Node<StudioNodeData, "mangaNode" | "flowGroup">;
export type StudioEdge = Edge<{ sourcePort: string; targetPort: string }>;
export type Arrangement = "left" | "right" | "top" | "bottom" | "horizontal" | "vertical";

export const EMPTY_CONFIG: WorkflowGraphNode["config"] = {
  model_alias: null, prompt_template: "", system_instruction: "", temperature: 0.2,
  timeout_seconds: 900, max_attempts: 3, concurrency: 1, resolution: null,
  locked: false, notes: "", condition: {}, requires_approval: false,
};

export function makeNode(type: WorkflowNodeType, position: { x: number; y: number }): StudioNode {
  const id = `${type.type.replaceAll(".", "-")}-${crypto.randomUUID().slice(0, 8)}`;
  const graphNode: WorkflowGraphNode = {
    id, type: type.type, name: type.label, position,
    inputs: type.inputs, outputs: type.outputs,
    config: { ...EMPTY_CONFIG, condition: {},
      model_alias: /^(agent|quality|director)\./.test(type.type) ? "auto" : null,
      resolution: type.type === "generator.page" ? "1K" : null,
      requires_approval: ["generator.page", "control.approval"].includes(type.type),
    },
  };
  return { id, type: "mangaNode", position, selected: true, data: { graphNode } };
}

export function cleanGroups(groups: WorkflowGroup[], nodes: StudioNode[]) {
  const ids = new Set(nodes.map((node) => node.id));
  return groups.map((group) => ({ ...group, node_ids: group.node_ids.filter((id) => ids.has(id)) }))
    .filter((group) => group.node_ids.length);
}

export function duplicateNodes(nodes: StudioNode[], edges: StudioEdge[], ids: Set<string>) {
  const mapping = new Map([...ids].map((id) => [id, `copy-${crypto.randomUUID()}`]));
  const copies = nodes.filter((node) => ids.has(node.id)).map((node): StudioNode => {
    const id = mapping.get(node.id)!;
    const position = { x: node.position.x + 48, y: node.position.y + 48 };
    return { id, type: "mangaNode", position, selected: true,
      data: { graphNode: { ...structuredClone(node.data.graphNode), id, position, name: `${node.data.graphNode.name} 副本` } } };
  });
  const links = edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)).map((edge) => ({
    ...edge, id: `edge-${crypto.randomUUID()}`, source: mapping.get(edge.source)!, target: mapping.get(edge.target)!, selected: false,
  }));
  return { nodes: [...nodes.map((node) => ({ ...node, selected: false })), ...copies], edges: [...edges, ...links], copies, mapping };
}

export function arrangeNodes(nodes: StudioNode[], ids: Set<string>, mode: Arrangement) {
  const axis = ["left", "right", "horizontal"].includes(mode) ? "x" : "y";
  const size = (node: StudioNode) => axis === "x" ? node.measured?.width ?? 224 : node.measured?.height ?? 154;
  const selected = nodes.filter((node) => ids.has(node.id)).sort((a, b) => a.position[axis] - b.position[axis]);
  if (selected.length < 2) return nodes;
  const min = Math.min(...selected.map((node) => node.position[axis]));
  const max = Math.max(...selected.map((node) => node.position[axis] + size(node)));
  const distribute = ["horizontal", "vertical"].includes(mode);
  if (distribute && selected.length < 3) return nodes;
  const gap = (max - min - selected.reduce((sum, node) => sum + size(node), 0)) / (selected.length - 1);
  let cursor = min;
  const positions = new Map(selected.map((node) => {
    const value = distribute ? cursor : ["right", "bottom"].includes(mode) ? max - size(node) : min;
    cursor += size(node) + gap;
    return [node.id, value];
  }));
  return nodes.map((node) => positions.has(node.id) ? { ...node, position: { ...node.position, [axis]: positions.get(node.id)! } } : node);
}

export function groupBounds(group: WorkflowGroup, nodes: StudioNode[]) {
  const members = nodes.filter((node) => group.node_ids.includes(node.id));
  const x = Math.min(...members.map((node) => node.position.x)) - 24;
  const y = Math.min(...members.map((node) => node.position.y)) - 56;
  return { x, y,
    width: Math.max(...members.map((node) => node.position.x + (node.measured?.width ?? 224))) - x + 24,
    height: Math.max(...members.map((node) => node.position.y + (node.measured?.height ?? 154))) - y + 24,
  };
}

// Proxy handles encode the actual node and port, never change executable edges.
export function resolveHandle(nodeId: string, handle: string | null | undefined, groups: WorkflowGroup[]) {
  if (!groups.some((group) => group.id === nodeId)) return { nodeId, handle: handle ?? "" };
  const [id, port] = (handle ?? "").split(":");
  return { nodeId: id, handle: port };
}

export function projectGroups(nodes: StudioNode[], edges: StudioEdge[], groups: WorkflowGroup[]) {
  const collapsed = new Map<string, string>();
  const groupNodes: StudioNode[] = [];
  for (const group of cleanGroups(groups, nodes)) {
    const bounds = groupBounds(group, nodes);
    const members = nodes.filter((node) => group.node_ids.includes(node.id));
    const memberIds = new Set(group.node_ids);
    if (group.collapsed) group.node_ids.forEach((id) => collapsed.set(id, group.id));
    const ports = (direction: "inputs" | "outputs") => members.flatMap((node) => node.data.graphNode[direction]
      .filter((port) => {
        const links = edges.filter((edge) => direction === "inputs"
          ? edge.target === node.id && edge.targetHandle === port.id
          : edge.source === node.id && edge.sourceHandle === port.id);
        return !links.length || links.some((edge) => !memberIds.has(direction === "inputs" ? edge.source : edge.target));
      })
      .map((port) => ({ ...port, id: `${node.id}:${port.id}`, label: `${node.data.graphNode.name} · ${port.label}` })));
    const statuses = members.map((node) => node.data.runStatus);
    const runStatus = statuses.includes("FAILED") ? "FAILED" : statuses.includes("RUNNING") ? "RUNNING"
      : statuses.every((status) => status === "COMPLETED" || status === "SKIPPED") ? "COMPLETED"
      : statuses.find(Boolean);
    groupNodes.push({ id: group.id, type: "flowGroup", position: { x: bounds.x, y: bounds.y },
      selected: false, zIndex: group.collapsed ? 1 : -1,
      style: { width: group.collapsed ? 280 : bounds.width, height: group.collapsed ? undefined : bounds.height },
      data: { group, membersSelected: members.every((node) => node.selected), runStatus, graphNode: {
        id: group.id, type: "group", name: group.name, position: bounds,
        inputs: ports("inputs"), outputs: ports("outputs"), config: EMPTY_CONFIG,
      } },
    });
  }
  return {
    nodes: [...groupNodes, ...nodes.map((node) => ({ ...node, hidden: collapsed.has(node.id) }))],
    edges: edges.map((edge) => {
      const source = collapsed.get(edge.source), target = collapsed.get(edge.target);
      return { ...edge, hidden: Boolean(source && source === target),
        source: source ?? edge.source, target: target ?? edge.target,
        sourceHandle: source ? `${edge.source}:${edge.sourceHandle}` : edge.sourceHandle,
        targetHandle: target ? `${edge.target}:${edge.targetHandle}` : edge.targetHandle };
    }),
  };
}

export type InsertContext = { position: { x: number; y: number }; source?: { nodeId: string; handle: string }; target?: { nodeId: string; handle: string }; edgeId?: string };
export function matchingPorts(type: WorkflowNodeType, context: InsertContext, nodes: StudioNode[]) {
  const sourceType = nodes.find((node) => node.id === context.source?.nodeId)?.data.graphNode.outputs.find((port) => port.id === context.source?.handle)?.data_type;
  const targetType = nodes.find((node) => node.id === context.target?.nodeId)?.data.graphNode.inputs.find((port) => port.id === context.target?.handle)?.data_type;
  const input = type.inputs.find((port) => port.data_type === sourceType);
  const output = type.outputs.find((port) => port.data_type === targetType);
  return { input, output, compatible: (!context.source || Boolean(input)) && (!context.target || Boolean(output)) };
}

export function insertNode(type: WorkflowNodeType, context: InsertContext, nodes: StudioNode[], edges: StudioEdge[]) {
  const ports = matchingPorts(type, context, nodes);
  if (!ports.compatible || (context.edgeId && !edges.some((edge) => edge.id === context.edgeId))) return null;
  const node = makeNode(type, context.position);
  const connect = (source: string, sourcePort: string, target: string, targetPort: string): StudioEdge => ({
    id: `edge-${crypto.randomUUID()}`, source, sourceHandle: sourcePort, target, targetHandle: targetPort,
    data: { sourcePort, targetPort },
  });
  const links = edges.filter((edge) => edge.id !== context.edgeId);
  if (context.source && ports.input) links.push(connect(context.source.nodeId, context.source.handle, node.id, ports.input.id));
  if (context.target && ports.output) links.push(connect(node.id, ports.output.id, context.target.nodeId, context.target.handle));
  return { node, nodes: [...nodes.map((item) => ({ ...item, selected: false })), node], edges: links };
}

export type SavedTemplate = { id: string; name: string; graph: WorkflowGraph };
export function readLocalList<T>(key: string): T[] {
  try { const value: unknown = JSON.parse(localStorage.getItem(key) ?? "[]"); return Array.isArray(value) ? value : []; }
  catch { return []; }
}

export function elapsed(run?: WorkflowNodeRun, now = Date.now()) {
  if (!run?.started_at) return "等待执行";
  return `${Math.max(0, ((run.finished_at ? Date.parse(run.finished_at) : now) - Date.parse(run.started_at)) / 1000).toFixed(1)} 秒`;
}
