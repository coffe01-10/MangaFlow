"use client";

// Resize / rotate / tail / anchor handles for the current canvas selection.
// Only the selected object mounts DOM handles (audit §4).
import type { NormalizedRect } from "@/lib/api";
import type { CSSProperties, PointerEvent as ReactPointerEvent, RefObject } from "react";

import { handleCornerLabels, storyboardCopy } from "./storyboard-copy";
import { rectCenter, rotatePointAround } from "./geometry";

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

/** Page-space positions for every mounted handle of a selection. Shared by the
 * React render and the imperative drag preview so both stay in sync. When the
 * object is rotated, positions orbit the rect center exactly like the CSS
 * `rotate()` transform does (aspect = page pixel height / width). */
export function handlePositions(
  rect: NormalizedRect,
  kind: "panel" | "bubble",
  anchor?: { x: number; y: number } | null,
  tailTarget?: { x: number; y: number } | null,
  rotation = 0,
  aspect = 1,
  rotateOffset = 0.045,
): Map<string, { left: number; top: number }> {
  const names = kind === "panel" ? allHandles : cornerHandles;
  const positions = new Map<string, { left: number; top: number }>();
  for (const name of names) positions.set(name, anchorPosition(rect, name));
  if (kind === "bubble" && anchor) positions.set("anchor", { left: anchor.x, top: anchor.y });
  if (kind === "bubble" && tailTarget) positions.set("tail", { left: tailTarget.x, top: tailTarget.y });
  if (kind === "bubble") {
    positions.set("rotate", {
      left: rect.x + rect.width / 2,
      top: Math.max(0, rect.y - rotateOffset),
    });
  }
  if (rotation) {
    const center = rectCenter(rect);
    for (const [name, position] of positions) {
      const rotated = rotatePointAround({ x: position.left, y: position.top }, center, rotation, aspect);
      positions.set(name, { left: rotated.x, top: rotated.y });
    }
  }
  return positions;
}

export function TransformHandles({
  rect,
  kind,
  disabled,
  anchor,
  tailTarget,
  rotation = 0,
  aspect = 1,
  innerRef,
  onHandlePointerDown,
}: {
  rect: NormalizedRect | null;
  kind: "panel" | "bubble";
  disabled: boolean;
  anchor?: { x: number; y: number } | null;
  tailTarget?: { x: number; y: number } | null;
  /** Stored rotation (degrees); handle positions orbit the rect center. */
  rotation?: number;
  /** Page pixel height / width for aspect-correct rotation math. */
  aspect?: number;
  /** Lets the drag preview reposition handles imperatively (audit §4). */
  innerRef?: RefObject<HTMLDivElement | null>;
  onHandlePointerDown?: (handle: HandleName | "tail" | "anchor" | "rotate", event: ReactPointerEvent<HTMLDivElement>) => void;
}) {
  if (!rect) return null;
  const positions = handlePositions(rect, kind, anchor, tailTarget, rotation, aspect);
  return <div className="canvas-handles" ref={innerRef} aria-hidden={false}>
    {[...positions.entries()].map(([name, position]) => {
      const isCorner = (cornerHandles as readonly string[]).includes(name);
      return <div
        key={name}
        role="button"
        aria-label={name === "tail" ? storyboardCopy.tailHandle
          : name === "anchor" ? storyboardCopy.anchorHandle
          : name === "rotate" ? storyboardCopy.rotateHandle
          : storyboardCopy.handleResize(handleCornerLabels[name] ?? name)}
        aria-disabled={disabled || undefined}
        data-handle={name}
        className={`canvas-handle ${isCorner ? "corner" : ""} ${name === "rotate" ? "rotate" : ""} ${name === "tail" || name === "anchor" ? name : ""}`}
        style={handleStyle(position.left, position.top)}
        onPointerDown={disabled ? undefined : (event) => {
          event.stopPropagation();
          onHandlePointerDown?.(name as HandleName | "tail" | "anchor" | "rotate", event);
        }}
      />;
    })}
  </div>;
}
