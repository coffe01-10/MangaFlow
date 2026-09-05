"use client";

// Destructive layout rebuild confirmation (audit §2.3 K). The count/mode
// controls moved here from the always-visible layout controls. Dialog
// hygiene mirrors scene-modal: Esc cancels, focus starts inside, Tab stays
// inside, and focus returns to the opener on close.
import { CircleAlert } from "lucide-react";
import { useEffect, useRef } from "react";

import type { MangaPage } from "@/lib/api";

import { storyboardCopy } from "./storyboard-copy";

export function LayoutRebuildDialog({
  page,
  pending,
  panelCount,
  layoutMode,
  onPanelCountChange,
  onLayoutModeChange,
  onConfirm,
  onCancel,
}: {
  page: MangaPage;
  pending: boolean;
  panelCount: number;
  layoutMode: "dynamic" | "balanced";
  onPanelCountChange: (count: number) => void;
  onLayoutModeChange: (mode: "dynamic" | "balanced") => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  // Latest-callback ref: the keydown listener binds once per mount instead of
  // re-subscribing (and re-focusing) on every parent re-render.
  const stateRef = useRef({ onCancel, pending });
  useEffect(() => { stateRef.current = { onCancel, pending }; });
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        if (!stateRef.current.pending) stateRef.current.onCancel();
        return;
      }
      if (event.key === "Tab" && dialogRef.current) {
        const focusables = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled])"));
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !(active instanceof Node) || !dialogRef.current.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || !(active instanceof Node) || !dialogRef.current.contains(active))) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);
  return <div className="layout-rebuild-backdrop" role="presentation" onClick={() => { if (!pending) onCancel(); }}>
    <div className="layout-rebuild-dialog" ref={dialogRef} role="dialog" aria-modal="true" aria-label={storyboardCopy.rebuildTitle} onClick={(event) => event.stopPropagation()}>
      <header><CircleAlert size={16} /><strong>{storyboardCopy.rebuildTitle}</strong></header>
      <p className="rebuild-warning">{storyboardCopy.rebuildWarning}</p>
      <div className="rebuild-options">
        <fieldset><legend>{storyboardCopy.rebuildCount}</legend>
          {[3, 4, 5, 6, 7, 8].map((count) => <button
            key={count}
            type="button"
            aria-pressed={panelCount === count}
            className={panelCount === count ? "active" : ""}
            disabled={pending}
            onClick={() => onPanelCountChange(count)}
          >{count} 格</button>)}
        </fieldset>
        <fieldset><legend>{storyboardCopy.rebuildMode}</legend>
          <button type="button" aria-pressed={layoutMode === "dynamic"} className={layoutMode === "dynamic" ? "active" : ""} disabled={pending} onClick={() => onLayoutModeChange("dynamic")}>{storyboardCopy.layoutDynamic}</button>
          <button type="button" aria-pressed={layoutMode === "balanced"} className={layoutMode === "balanced" ? "active" : ""} disabled={pending} onClick={() => onLayoutModeChange("balanced")}>{storyboardCopy.layoutBalanced}</button>
        </fieldset>
      </div>
      <p className="rebuild-current">当前第 {page.page_number} 页为 {page.panel_count} 格。</p>
      <footer>
        <button ref={cancelRef} type="button" onClick={onCancel} disabled={pending}>{storyboardCopy.rebuildCancel}</button>
        <button type="button" className="rebuild-confirm" onClick={onConfirm} disabled={pending}>{storyboardCopy.rebuildConfirm}</button>
      </footer>
    </div>
  </div>;
}
