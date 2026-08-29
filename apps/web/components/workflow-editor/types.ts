import type { LucideIcon } from "lucide-react";

export type NodeKind = "input" | "agent" | "director" | "generator" | "quality" | "output";
export type NodeStatus = "idle" | "ready" | "running" | "done" | "warning";

export type PortDefinition = {
  id: string;
  label: string;
  dataType: "text" | "json" | "image" | "asset" | "report";
};

export type FlowNode = {
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

export type FlowEdge = {
  id: string;
  source: string;
  sourcePort: string;
  target: string;
  targetPort: string;
};

export type PaletteTemplate = {
  key: string;
  title: string;
  description: string;
  kind: NodeKind;
  icon: LucideIcon;
  inputs: PortDefinition[];
  outputs: PortDefinition[];
};

export type ConnectionAnchor =
  | { side: "output"; nodeId: string; portId: string }
  | { side: "input"; nodeId: string; portId: string };

export type Gesture =
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
