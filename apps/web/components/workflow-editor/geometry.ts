import styles from "../workflow-editor.module.css";
import { NODE_WIDTH, PORT_BASE_Y, PORT_GAP } from "./graph-model";
import type { FlowNode, NodeKind, PortDefinition } from "./types";

export function clamp(value: number, minimum: number, maximum: number) {
  return Math.min(maximum, Math.max(minimum, value));
}

export function getPortPoint(node: FlowNode, portId: string, side: "input" | "output") {
  const ports = side === "input" ? node.inputs : node.outputs;
  const index = Math.max(0, ports.findIndex((item) => item.id === portId));
  return {
    x: side === "input" ? node.x : node.x + NODE_WIDTH,
    y: node.y + PORT_BASE_Y + index * PORT_GAP,
  };
}

export function pathBetween(start: { x: number; y: number }, end: { x: number; y: number }) {
  const distance = Math.max(72, Math.abs(end.x - start.x) * 0.48);
  return `M ${start.x} ${start.y} C ${start.x + distance} ${start.y}, ${end.x - distance} ${end.y}, ${end.x} ${end.y}`;
}

export function nodeTypeClass(kind: NodeKind) {
  return `${styles.node} ${styles[`node_${kind}`]}`;
}

export function portTypeClass(dataType: PortDefinition["dataType"]) {
  return `${styles.portHandle} ${styles[`port_${dataType}`]}`;
}
