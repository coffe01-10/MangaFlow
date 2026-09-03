"use client";

// Resize / tail / anchor handles for the current canvas selection.
// Only the selected object mounts DOM handles (audit §4).
import type { NormalizedRect } from "@/lib/api";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import { handleCornerLabels, storyboardCopy } from "./storyboard-copy";

const cornerHandles = ["nw", "ne", "se", "sw"] as const;
const edgeHandles = ["n", "e", "s", "w"] as const;
const allHandles = [...cornerHandles, ...edgeHandles] as const;

export type HandleName = (typeof allHandles)[number];

const anchorPosition = (rect: NormalizedRect, handle: string): { left: number; top: number } => {
  const middleX = rect.x + rect.width / 2;
  const middleY = rect.y + rect.height / 2;
  return {
    left: handle.includes("w") ? rect.x : handle.includes("e") ? rect.x + rect.width : middleX,
    top: handle.includes("n") ? rect.y : handle.includes("s") ? rect.y + rect.height : middleY,
  };
};

const handleStyle = (left: number, top: number): CSSProperties => ({
  left: `${left * 100}%`,
  top: `${top * 100}%`,
});

export function TransformHandles({
  rect,
  kind,
  disabled,
  anchor,
  tailTarget,
  onHandlePointerDown,
}: {
  rect: NormalizedRect | null;
  kind: "panel" | "bubble";
  disabled: boolean;
  anchor?: { x: number; y: number } | null;
  tailTarget?: { x: number; y: number } | null;
  onHandlePointerDown?: (handle: HandleName | "tail" | "anchor", event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  if (!rect) return null;
  const names = kind === "panel" ? [...allHandles] : [...cornerHandles];
  return <div className="canvas-handles" aria-hidden={false}>
    {names.map((name) => {
      const position = anchorPosition(rect, name);
      return <div
        key={name}
        role="button"
        aria-label={storyboardCopy.handleResize(handleCornerLabels[name] ?? name)}
        aria-disabled={disabled || undefined}
        data-handle={name}
        className={`canvas-handle ${cornerHandles.includes(name as never) ? "corner" : ""}`}
        style={handleStyle(position.left, position.top)}
        onPointerDown={disabled ? undefined : (event) => {
          event.stopPropagation();
          onHandlePointerDown?.(name, event);
        }}
      />;
    })}
    {kind === "bubble" && anchor && <div
      role="button"
      aria-label={storyboardCopy.anchorHandle}
      aria-disabled={disabled || undefined}
      data-handle="anchor"
      className="canvas-handle anchor"
      style={handleStyle(anchor.x, anchor.y)}
      onPointerDown={disabled ? undefined : (event) => {
        event.stopPropagation();
        onHandlePointerDown?.("anchor", event);
      }}
    />}
    {kind === "bubble" && tailTarget && <div
      role="button"
      aria-label={storyboardCopy.tailHandle}
      aria-disabled={disabled || undefined}
      data-handle="tail"
      className="canvas-handle tail"
      style={handleStyle(tailTarget.x, tailTarget.y)}
      onPointerDown={disabled ? undefined : (event) => {
        event.stopPropagation();
        onHandlePointerDown?.("tail", event);
      }}
    />}
  </div>;
}
