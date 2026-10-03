"use client";

// Sound-effect text on the page canvas (contract §12): x/y is the element
// center in normalized page space, rotation in degrees, size the normalized
// font size (resolved to px by the caller so zoom stays consistent).
import type { PointerEvent as ReactPointerEvent } from "react";
import type { CSSProperties } from "react";

import { storyboardCopy } from "./storyboard-copy";

export interface SfxNodeData {
  panelId: string;
  index: number;
  text: string;
  x: number;
  y: number;
  rotation: number;
  size: number;
}

export function SfxNode({
  sfx,
  fontPx,
  selected,
  interactive,
  onPointerDown,
  onHandlePointerDown,
  elementRef,
}: {
  sfx: SfxNodeData;
  fontPx: number;
  selected: boolean;
  interactive: boolean;
  onPointerDown?: (sfx: SfxNodeData, event: ReactPointerEvent<HTMLDivElement>) => void;
  onHandlePointerDown?: (handle: "rotate" | "scale", event: ReactPointerEvent<HTMLDivElement>) => void;
  elementRef?: (element: HTMLDivElement | null) => void;
}) {
  const style: CSSProperties = {
    left: `${sfx.x * 100}%`,
    top: `${sfx.y * 100}%`,
    fontSize: `${Math.max(fontPx, 6)}px`,
    transform: `translate(-50%, -50%) rotate(${sfx.rotation}deg)`,
  };
  return <div
    id={`canvas-sfx-${sfx.panelId}-${sfx.index}`}
    ref={elementRef}
    role="button"
    aria-label={storyboardCopy.sfxLabel(sfx.text)}
    aria-current={selected ? "true" : undefined}
    className={selected ? "canvas-sfx selected" : "canvas-sfx"}
    style={style}
    onPointerDown={interactive ? (event) => onPointerDown?.(sfx, event) : undefined}
  >
    <span className="canvas-sfx-text">{sfx.text}</span>
    {selected && interactive && <>
      <div
        role="button"
        aria-label={storyboardCopy.rotateHandle}
        data-handle="rotate"
        className="canvas-handle rotate"
        style={{ left: "50%", top: "-1.6em" }}
        onPointerDown={(event) => {
          event.stopPropagation();
          if (event.button !== 0) return;
          onHandlePointerDown?.("rotate", event);
        }}
      />
      <div
        role="button"
        aria-label={storyboardCopy.handleResize("大小")}
        data-handle="scale"
        className="canvas-handle corner"
        style={{ left: "calc(50% + 1.2em)", top: "calc(50% + 0.9em)" }}
        onPointerDown={(event) => {
          event.stopPropagation();
          if (event.button !== 0) return;
          onHandlePointerDown?.("scale", event);
        }}
      />
    </>}
  </div>;
}
