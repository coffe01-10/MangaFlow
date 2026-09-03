"use client";

// 100-node stress harness (V02-32 audit §4). Renders the synthetic fixture
// through the same PageCanvas render layer as the product editor, but keeps
// every byte client-side: there is no API import and no save path, so the
// stress page can never be persisted or PUT to the server.
import { Info } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import type { BubbleGeometryShape, NormalizedRect } from "@/lib/api";

import type { GeometryCommandChange } from "./command-stack";
import { bubbleGeometry, defaultCanvas, legacyBubbleRect, panelRect } from "./geometry";
import { PageCanvas, type CanvasBubble, type CanvasSelection } from "./page-canvas";
import { buildStressStoryboard, stressPage } from "./stress-fixture";

export function StressStoryboardCanvas() {
  const snapshot = useMemo(() => buildStressStoryboard(), []);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [selection, setSelection] = useState<CanvasSelection>(null);
  const [zoom, setZoom] = useState(0.6);
  const [panelRects, setPanelRects] = useState<Record<string, NormalizedRect>>(() =>
    Object.fromEntries(snapshot.panels.map((panel) => [panel.id, panelRect(panel)])),
  );
  const [bubbleDrafts, setBubbleDrafts] = useState<Record<string, BubbleGeometryShape | null>>({});

  const bubbles: CanvasBubble[] = [];
  for (const panel of snapshot.panels) {
    panel.dialogues.forEach((dialogue, index) => {
      const draft = bubbleDrafts[dialogue.id];
      const shape = draft !== undefined ? draft : bubbleGeometry(dialogue).shape;
      const panelBounds = panelRects[panel.id] ?? panelRect(panel);
      bubbles.push({
        dialogue,
        panelId: panel.id,
        panelRect: panelBounds,
        rect: shape?.rect ?? legacyBubbleRect(panel, dialogue, index),
        shape,
        legacy: draft === undefined && bubbleGeometry(dialogue).legacy,
        shapeType: shape?.type === "ellipse" ? "ellipse" : "rect",
      });
    });
  }

  // Drafts live in local state only; there is no save path to fork from.
  const handleCommand = (_label: string, changes: GeometryCommandChange[]) => {
    for (const change of changes) {
      if (change.kind === "panel") {
        setPanelRects((drafts) => ({ ...drafts, [change.id]: change.after }));
      } else {
        setBubbleDrafts((drafts) => ({ ...drafts, [change.id]: change.after }));
      }
    }
  };

  const zoomTo = (next: number) => setZoom(Math.min(2, Math.max(0.2, next)));

  return <div className="stress-canvas" data-testid="stress-canvas">
    <p className="stress-note">
      <Info size={14} />
      100 节点压力夹具（20 格 × 每格 4 气泡）：仅前端合成渲染，不落库、不发送任何请求。
      滚轮平移视口，Ctrl+滚轮缩放；点击选中对象后可拖拽。
    </p>
    <PageCanvas
      page={stressPage}
      canvas={defaultCanvas(stressPage)}
      panels={snapshot.panels}
      panelRects={panelRects}
      bubbles={bubbles}
      zoom={zoom}
      viewportRef={viewportRef}
      snapEnabled={false}
      showReadingOrder
      showBleed={false}
      showSafe={false}
      interactive
      selection={selection}
      onCommand={handleCommand}
      onSelectPanels={(ids) => setSelection({ kind: "panels", ids })}
      onSelectBubble={(dialogueId) => setSelection({ kind: "bubble", dialogueId })}
      onClearSelection={() => setSelection(null)}
      onOpenInspector={() => undefined}
      onDeleteBubble={() => undefined}
      onBubbleBounce={() => undefined}
      onZoomStep={(direction) => zoomTo(direction === 1 ? zoom * 1.25 : zoom / 1.25)}
    />
  </div>;
}
