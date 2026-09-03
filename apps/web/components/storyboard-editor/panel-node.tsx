"use client";

// Single panel outline on the page canvas. Panels render as lightweight
// outlines only (audit §4): narrative detail lives in the inspector.
import type { NormalizedRect } from "@/lib/api";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import type { StoryboardPanel } from "@/lib/api";

import { storyboardCopy } from "./storyboard-copy";
import { isPolygonPanel } from "./geometry";

export function PanelNode({
  panel,
  rect,
  selected,
  interactive,
  readingDirection,
  showReadingBadge,
  onPointerDown,
  onDoubleClick,
}: {
  panel: StoryboardPanel;
  rect: NormalizedRect;
  selected: boolean;
  interactive: boolean;
  readingDirection: string;
  showReadingBadge: boolean;
  onPointerDown?: (panel: StoryboardPanel, event: ReactPointerEvent<HTMLDivElement>) => void;
  onDoubleClick?: () => void;
}) {
  const polygon = isPolygonPanel(panel);
  const className = [
    "canvas-panel",
    polygon ? "polygon-shape" : "",
    panel.bleed ? "bleed-panel" : "",
    selected ? "selected" : "",
  ].filter(Boolean).join(" ");
  const style: CSSProperties = {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.width * 100}%`,
    height: `${rect.height * 100}%`,
    zIndex: 10,
  };
  return <div
    id={`canvas-panel-${panel.id}`}
    role="button"
    aria-label={`格 ${String(panel.reading_order).padStart(2, "0")}`}
    aria-current={selected ? "true" : undefined}
    className={className}
    style={style}
    onPointerDown={interactive ? (event) => onPointerDown?.(panel, event) : undefined}
    onDoubleClick={onDoubleClick}
  >
    {showReadingBadge && <span
      className="canvas-reading-badge"
      style={readingDirection === "ltr" ? { left: 0, top: 0 } : { right: 0, top: 0 }}
    >格 {String(panel.reading_order).padStart(2, "0")}</span>}
    {polygon && <span className="canvas-polygon-mark">{storyboardCopy.polygonNote}</span>}
  </div>;
}
