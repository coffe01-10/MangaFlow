"use client";

// Bubble box on the page canvas. The tail polyline is drawn by the shared SVG
// layer in PageCanvas; this component renders only the selectable bubble rect.
import type { NormalizedRect } from "@/lib/api";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import type { PanelDialogue } from "@/lib/api";

export function BubbleNode({
  dialogue,
  rect,
  shapeType,
  selected,
  interactive,
  onPointerDown,
  elementRef,
}: {
  dialogue: PanelDialogue;
  rect: NormalizedRect;
  shapeType: "rect" | "ellipse";
  selected: boolean;
  interactive: boolean;
  onPointerDown?: (dialogue: PanelDialogue, event: ReactPointerEvent<HTMLDivElement>) => void;
  elementRef?: (element: HTMLDivElement | null) => void;
}) {
  const style: CSSProperties = {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.width * 100}%`,
    height: `${rect.height * 100}%`,
  };
  return <div
    id={`canvas-bubble-${dialogue.id}`}
    ref={elementRef}
    role="button"
    aria-label={`气泡 ${dialogue.reading_order}`}
    aria-current={selected ? "true" : undefined}
    className={shapeType === "ellipse" ? "canvas-bubble ellipse" : "canvas-bubble"}
    style={style}
    onPointerDown={interactive ? (event) => onPointerDown?.(dialogue, event) : undefined}
  />;
}
