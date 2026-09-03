"use client";

// Canvas overlays: alignment guide lines, reading-order badges context is on
// nodes; this layer draws bleed frame, safe area and snap guides.
import type { SnapGuide } from "./geometry";

export function GuidesOverlay({
  guides,
  bleedInset,
  safeInset,
  showBleed,
  showSafe,
}: {
  guides: SnapGuide[];
  /** Normalized bleed extension per axis; null when the canvas field is absent. */
  bleedInset: { x: number; y: number } | null;
  safeInset: { x: number; y: number } | null;
  showBleed: boolean;
  showSafe: boolean;
}) {
  return <div className="canvas-guides-layer">
    {showBleed && bleedInset && <div
      className="canvas-bleed-ring"
      style={{
        left: `${-bleedInset.x * 100}%`,
        top: `${-bleedInset.y * 100}%`,
        width: `${(1 + bleedInset.x * 2) * 100}%`,
        height: `${(1 + bleedInset.y * 2) * 100}%`,
      }}
    />}
    {showSafe && safeInset && <div
      className="canvas-safe-rect"
      style={{
        left: `${safeInset.x * 100}%`,
        top: `${safeInset.y * 100}%`,
        width: `${(1 - safeInset.x * 2) * 100}%`,
        height: `${(1 - safeInset.y * 2) * 100}%`,
      }}
    />}
    {guides.map((guide, index) => guide.axis === "x"
      ? <div key={`x-${index}`} className="canvas-guide-line vertical" style={{ left: `${guide.at * 100}%`, top: 0, bottom: 0 }} />
      : <div key={`y-${index}`} className="canvas-guide-line horizontal" style={{ top: `${guide.at * 100}%`, left: 0, right: 0 }} />)}
  </div>;
}
