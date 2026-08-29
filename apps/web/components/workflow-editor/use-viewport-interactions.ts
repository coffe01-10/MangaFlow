"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type RefObject, type SetStateAction } from "react";

import {
  edge,
  MAX_ZOOM,
  MIN_ZOOM,
  NODE_HEIGHT,
  NODE_WIDTH,
  WORLD_HEIGHT,
  WORLD_WIDTH,
} from "./graph-model";
import { clamp, getPortPoint } from "./geometry";
import type {
  PointerEvent as ReactPointerEvent,
  WheelEvent as ReactWheelEvent,
} from "react";
import styles from "../workflow-editor.module.css";
import type { ConnectionAnchor, FlowEdge, FlowNode, Gesture } from "./types";

export function useViewportInteractions({
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
}: {
  viewportRef: RefObject<HTMLDivElement | null>;
  nodes: FlowNode[];
  nodeMap: Map<string, FlowNode>;
  edges: FlowEdge[];
  setNodes: Dispatch<SetStateAction<FlowNode[]>>;
  setEdges: Dispatch<SetStateAction<FlowEdge[]>>;
  setSaved: Dispatch<SetStateAction<boolean>>;
  setSelectedNodeId: Dispatch<SetStateAction<string | null>>;
  setSelectedEdgeId: Dispatch<SetStateAction<string | null>>;
  saveFlow: () => void;
  deleteSelection: () => void;
}) {
  const [pan, setPan] = useState({ x: 44, y: 92 });
  const [zoom, setZoom] = useState(0.72);
  const [draftEnd, setDraftEnd] = useState<{ x: number; y: number } | null>(null);
  const [connectionAnchor, setConnectionAnchor] = useState<ConnectionAnchor | null>(null);
  const gestureRef = useRef<Gesture | null>(null);

  const worldPoint = useCallback((clientX: number, clientY: number) => {
    const rect = viewportRef.current?.getBoundingClientRect();
    return {
      x: ((clientX - (rect?.left ?? 0)) - pan.x) / zoom,
      y: ((clientY - (rect?.top ?? 0)) - pan.y) / zoom,
    };
  }, [pan.x, pan.y, viewportRef, zoom]);

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
  }, [setEdges, setNodes, setSaved, worldPoint, zoom]);

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

  return {
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
  };
}
