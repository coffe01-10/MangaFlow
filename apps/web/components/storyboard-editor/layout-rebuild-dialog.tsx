"use client";

// Destructive layout rebuild confirmation (audit §2.3 K). The count/mode
// controls moved here from the always-visible layout controls.
import { CircleAlert } from "lucide-react";

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
  return <div className="layout-rebuild-backdrop" role="presentation">
    <div className="layout-rebuild-dialog" role="dialog" aria-modal="true" aria-label={storyboardCopy.rebuildTitle}>
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
        <button type="button" onClick={onCancel} disabled={pending}>{storyboardCopy.rebuildCancel}</button>
        <button type="button" className="rebuild-confirm" onClick={onConfirm} disabled={pending}>{storyboardCopy.rebuildConfirm}</button>
      </footer>
    </div>
  </div>;
}
