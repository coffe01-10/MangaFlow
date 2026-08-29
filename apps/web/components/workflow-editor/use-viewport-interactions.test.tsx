import { fireEvent, render, screen } from "@testing-library/react";
import { useMemo, useRef, useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PointerEvent as ReactPointerEvent } from "react";

import { initialEdges, initialNodes } from "./graph-model";
import type { FlowEdge, FlowNode } from "./types";
import { useViewportInteractions } from "./use-viewport-interactions";

function wheelLike(deltaY: number) {
  return { preventDefault: () => {}, deltaY, clientX: 10, clientY: 10 } as unknown as Parameters<ReturnType<typeof useViewportInteractions>["handleWheel"]>[0];
}

function Probe({ saveFlow = () => {}, deleteSelection = () => {} }: { saveFlow?: () => void; deleteSelection?: () => void }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodes);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdges);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [saved, setSaved] = useState(true);
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const interactions = useViewportInteractions({
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
  const adapter = nodes.find((node) => node.id === "adapter-1");
  return (
    <div>
      <div ref={viewportRef} />
      <span data-testid="zoom">{Math.round(interactions.zoom * 100)}%</span>
      <span data-testid="draft">{interactions.draftEnd ? `${Math.round(interactions.draftEnd.x)},${Math.round(interactions.draftEnd.y)}` : "none"}</span>
      <span data-testid="anchor">{interactions.connectionAnchor?.side ?? "none"}</span>
      <span data-testid="saved">{String(saved)}</span>
      <span data-testid="selection">{selectedNodeId ?? selectedEdgeId ?? "none"}</span>
      <span data-testid="adapter-x">{adapter?.x}</span>
      <span data-testid="edge-count">{edges.length}</span>
      <button data-testid="zoom-in" onClick={() => interactions.zoomBy(1.15)}>放大</button>
      <button data-testid="zoom-out-max" onClick={() => interactions.zoomBy(0.01)}>缩小</button>
      <button data-testid="wheel-in" onClick={() => interactions.handleWheel(wheelLike(-100))}>滚轮放大</button>
      <button data-testid="wheel-out" onClick={() => interactions.handleWheel(wheelLike(100))}>滚轮缩小</button>
      <button
        data-testid="start-drag"
        onPointerDown={(event) => interactions.beginNodeDrag(event as unknown as ReactPointerEvent, adapter as FlowNode)}
      >开始拖拽</button>
      <button
        data-testid="start-connect"
        onPointerDown={(event) => interactions.beginOutputConnection(event as unknown as ReactPointerEvent, "source-1", "source")}
      >开始连线</button>
    </div>
  );
}

describe("useViewportInteractions", () => {
  beforeEach(() => {
    document.body.className = "";
  });

  it("Ctrl+S 触发保存，Delete 在非输入焦点时触发删除", () => {
    const saveFlow = vi.fn();
    const deleteSelection = vi.fn();
    render(<Probe saveFlow={saveFlow} deleteSelection={deleteSelection} />);

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "s", ctrlKey: true, cancelable: true }));
    expect(saveFlow).toHaveBeenCalledTimes(1);

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    expect(deleteSelection).toHaveBeenCalledTimes(1);
  });

  it("输入框内的 Delete 不触发删除", () => {
    const deleteSelection = vi.fn();
    render(<Probe deleteSelection={deleteSelection} />);

    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete", bubbles: true }));
    expect(deleteSelection).not.toHaveBeenCalled();
    input.remove();
  });

  it("滚轮缩放按方向调整并夹在上下限内", () => {
    render(<Probe />);
    expect(screen.getByTestId("zoom")).toHaveTextContent("72%");

    fireEvent.click(screen.getByTestId("wheel-in"));
    expect(screen.getByTestId("zoom")).toHaveTextContent("79%");

    for (let index = 0; index < 40; index += 1) {
      fireEvent.click(screen.getByTestId("wheel-out"));
    }
    expect(screen.getByTestId("zoom")).toHaveTextContent("18%");

    for (let index = 0; index < 40; index += 1) {
      fireEvent.click(screen.getByTestId("wheel-in"));
    }
    expect(screen.getByTestId("zoom")).toHaveTextContent("145%");
  });

  it("zoomBy 按系数缩放", () => {
    render(<Probe />);
    fireEvent.click(screen.getByTestId("zoom-in"));
    expect(screen.getByTestId("zoom")).toHaveTextContent("83%");

    fireEvent.click(screen.getByTestId("zoom-out-max"));
    expect(screen.getByTestId("zoom")).toHaveTextContent("18%");
  });

  it("节点拖拽手势按缩放比例移动节点并标记未保存", () => {
    render(<Probe />);
    fireEvent.pointerDown(screen.getByTestId("start-drag"), { button: 0, clientX: 100, clientY: 100 });
    expect(screen.getByTestId("selection")).toHaveTextContent("adapter-1");
    fireEvent.pointerMove(window, { clientX: 172, clientY: 172 });

    expect(screen.getByTestId("adapter-x")).toHaveTextContent(String(730 + Math.round(72 / 0.72)));
    expect(screen.getByTestId("saved")).toHaveTextContent("false");

    fireEvent.pointerUp(window, { clientX: 172, clientY: 172 });
    expect(screen.getByTestId("adapter-x")).toHaveTextContent(String(730 + Math.round(72 / 0.72)));
  });

  it("输出端口连线手势产生草稿终点，释放后清空且不误加连线", () => {
    render(<Probe />);
    const originalElementFromPoint = document.elementFromPoint;
    document.elementFromPoint = () => null;
    try {
      fireEvent.pointerDown(screen.getByTestId("start-connect"), { button: 0, clientX: 100, clientY: 100 });
      expect(screen.getByTestId("anchor")).toHaveTextContent("output");
      expect(screen.getByTestId("draft")).toHaveTextContent("354,350");

      fireEvent.pointerMove(window, { clientX: 100, clientY: 300 });
      expect(screen.getByTestId("draft")).not.toHaveTextContent("none");

      fireEvent.pointerUp(window, { clientX: 100, clientY: 300 });
      expect(screen.getByTestId("anchor")).toHaveTextContent("none");
      expect(screen.getByTestId("draft")).toHaveTextContent("none");
      expect(screen.getByTestId("edge-count")).toHaveTextContent(String(initialEdges.length));
    } finally {
      if (originalElementFromPoint) {
        document.elementFromPoint = originalElementFromPoint;
      } else {
        delete (document as { elementFromPoint?: unknown }).elementFromPoint;
      }
    }
  });
});
