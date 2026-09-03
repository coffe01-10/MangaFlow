"use client";

// Canvas overlays (audit §4): bleed frame and safe area render from props.
// The reading-order badge list is its own overlay with viewport culling —
// badges outside the visible viewport are not rendered at all.
import type { NormalizedRect, StoryboardPanel } from "@/lib/api";
import { useEffect, useState } from "react";
import type { RefObject } from "react";

function insetStyle(inset: { x: number; y: number }, invert: boolean) {
  const x = invert ? -inset.x : inset.x;
  const y = invert ? -inset.y : inset.y;
  return {
    left: `${x * 100}%`,
    top: `${y * 100}%`,
    width: `${(1 + x * 2) * 100}%`,
    height: `${(1 + y * 2) * 100}%`,
  };
}

export function GuidesOverlay({
  bleedInset,
  safeInset,
  showBleed,
  showSafe,
}: {
  /** Normalized bleed extension per axis; null when the canvas field is absent. */
  bleedInset: { x: number; y: number } | null;
  safeInset: { x: number; y: number } | null;
  showBleed: boolean;
  showSafe: boolean;
}) {
  return <div className="canvas-guides-layer">
    {showBleed && bleedInset && <div className="canvas-bleed-ring" style={insetStyle(bleedInset, true)} />}
    {showSafe && safeInset && <div className="canvas-safe-rect" style={insetStyle(safeInset, false)} />}
  </div>;
}

/** Keep badges whose panel intersects the visible viewport. A `null` set means
 * "unmeasurable" (SSR/jsdom zero rects) and renders every badge, matching the
 * pre-culling behaviour. */
function visiblePanelIds(
  panels: StoryboardPanel[],
  rects: Record<string, NormalizedRect>,
  pageEl: HTMLElement | null,
  viewportEl: HTMLElement | null,
): Set<string> | null {
  if (!pageEl || !viewportEl) return null;
  const page = pageEl.getBoundingClientRect();
  const view = viewportEl.getBoundingClientRect();
  if (!page.width || !page.height || !view.width || !view.height) return null;
  const visible = new Set<string>();
  for (const panel of panels) {
    const rect = rects[panel.id];
    if (!rect) continue;
    const left = page.left + rect.x * page.width;
    const top = page.top + rect.y * page.height;
    const right = left + rect.width * page.width;
    const bottom = top + rect.height * page.height;
    if (right >= view.left && left <= view.right && bottom >= view.top && top <= view.bottom) {
      visible.add(panel.id);
    }
  }
  return visible;
}

export function ReadingOrderOverlay({
  panels,
  rects,
  readingDirection,
  pageRef,
  viewportRef,
}: {
  panels: StoryboardPanel[];
  rects: Record<string, NormalizedRect>;
  readingDirection: string;
  pageRef: RefObject<HTMLElement | null>;
  viewportRef: RefObject<HTMLElement | null>;
}) {
  const [visible, setVisible] = useState<Set<string> | null>(null);

  useEffect(() => {
    const recompute = () => setVisible(visiblePanelIds(panels, rects, pageRef.current, viewportRef.current));
    recompute();
    const viewport = viewportRef.current;
    // Scroll/resize culling is presentation-only: rAF-throttled so a fling of
    // scroll events costs at most one badge re-render per frame. jsdom may
    // lack rAF; there the callback runs immediately so tests stay deterministic.
    let frame = 0;
    const raf = typeof requestAnimationFrame === "function"
      ? requestAnimationFrame
      : (callback: FrameRequestCallback) => {
        callback(performance.now());
        return 0;
      };
    const schedule = () => {
      if (frame) return;
      frame = raf(() => {
        frame = 0;
        recompute();
      });
    };
    viewport?.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    return () => {
      viewport?.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      if (frame) cancelAnimationFrame(frame);
    };
  }, [panels, rects, pageRef, viewportRef]);

  const rtl = readingDirection !== "ltr";
  return <div className="canvas-reading-overlay" aria-hidden="true">
    {panels.map((panel) => {
      if (visible && !visible.has(panel.id)) return null;
      const rect = rects[panel.id];
      if (!rect) return null;
      const style = rtl
        ? { top: `${rect.y * 100}%`, right: `${(1 - rect.x - rect.width) * 100}%` }
        : { top: `${rect.y * 100}%`, left: `${rect.x * 100}%` };
      return <span key={panel.id} className="canvas-reading-badge" style={style}>
        格 {String(panel.reading_order).padStart(2, "0")}
      </span>;
    })}
  </div>;
}
