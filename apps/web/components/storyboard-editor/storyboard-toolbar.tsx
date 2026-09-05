"use client";

// Canvas toolbar: zoom, fit/reset, snap + overlay toggles, undo/redo,
// save and the page menu holding the destructive layout rebuild (audit §2.1 L0).
import { ChevronDown, Maximize, Redo2, RefreshCw, Save, Scan, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { storyboardCopy } from "./storyboard-copy";

export interface ToolbarToggleState {
  snap: boolean;
  readingOrder: boolean;
  bleed: boolean;
  safe: boolean;
}

export function StoryboardToolbar({
  zoomLabel,
  toggles,
  bleedAvailable,
  safeAvailable,
  canUndo,
  canRedo,
  dirty,
  saving,
  overlayHint,
  onZoomIn,
  onZoomOut,
  onFit,
  onReset,
  onToggle,
  onUndo,
  onRedo,
  onSave,
  onRebuildLayout,
}: {
  zoomLabel: string;
  toggles: ToolbarToggleState;  bleedAvailable: boolean;
  safeAvailable: boolean;
  canUndo: boolean;
  canRedo: boolean;
  dirty: boolean;
  saving: boolean;
  overlayHint: string | null;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: () => void;
  onReset: () => void;
  onToggle: (key: keyof ToolbarToggleState) => void;
  onUndo: () => void;
  onRedo: () => void;
  onSave: () => void;
  onRebuildLayout: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);
  // The page menu must close on outside pointer-down and Escape like any
  // popover, and return focus to its trigger.
  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target instanceof Node ? event.target : null)) {
        setMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setMenuOpen(false);
        menuRef.current?.querySelector<HTMLElement>("[aria-haspopup='menu']")?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [menuOpen]);
  return <div className="storyboard-toolbar">
    <div className="toolbar-group" role="group" aria-label="画布缩放">
      <button type="button" aria-label={storyboardCopy.zoomOut} onClick={onZoomOut}><ZoomOut size={14} /></button>
      <span className="zoom-label" aria-live="polite">{zoomLabel}</span>
      <button type="button" aria-label={storyboardCopy.zoomIn} onClick={onZoomIn}><ZoomIn size={14} /></button>
      <button type="button" onClick={onFit}><Scan size={13} />{storyboardCopy.fit}</button>
      <button type="button" onClick={onReset}><Maximize size={13} />{storyboardCopy.reset}</button>
    </div>
    <div className="toolbar-group" role="group" aria-label="画布开关">
      <button type="button" aria-pressed={toggles.snap} className={toggles.snap ? "active" : ""} onClick={() => onToggle("snap")}>{storyboardCopy.snap}</button>
      <button type="button" aria-pressed={toggles.readingOrder} className={toggles.readingOrder ? "active" : ""} onClick={() => onToggle("readingOrder")}>{storyboardCopy.readingOrder}</button>
      <button
        type="button"
        aria-pressed={toggles.bleed}
        className={toggles.bleed ? "active" : ""}
        disabled={!bleedAvailable}
        title={bleedAvailable ? undefined : storyboardCopy.canvasMissing}
        onClick={() => onToggle("bleed")}
      >{storyboardCopy.bleedFrame}</button>
      <button
        type="button"
        aria-pressed={toggles.safe}
        className={toggles.safe ? "active" : ""}
        disabled={!safeAvailable}
        title={safeAvailable ? undefined : storyboardCopy.canvasMissing}
        onClick={() => onToggle("safe")}
      >{storyboardCopy.safeArea}</button>
    </div>
    <div className="toolbar-group" role="group" aria-label="撤销与重做">
      <button type="button" aria-label={storyboardCopy.undo} disabled={!canUndo} onClick={onUndo}><Undo2 size={14} /></button>
      <button type="button" aria-label={storyboardCopy.redo} disabled={!canRedo} onClick={onRedo}><Redo2 size={14} /></button>
    </div>
    {overlayHint && <p className="toolbar-hint">{overlayHint}</p>}
    {!overlayHint && <p className="toolbar-hint" aria-hidden="true">Tab 切换格子 · 方向键微调（Shift 加速） · 回车打开属性 · Delete 删除气泡</p>}
    <div className="toolbar-group toolbar-spacer" role="group" aria-label="保存与页操作">
      <div className="page-menu" ref={menuRef}>
        <button type="button" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
          {storyboardCopy.pageMenu}<ChevronDown size={12} />
        </button>
        {menuOpen && <div className="page-menu-items" role="menu">
          <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onRebuildLayout(); }}>{storyboardCopy.rebuildLayout}</button>
        </div>}
      </div>
      <button type="button" className="toolbar-save" disabled={!dirty || saving} onClick={onSave}>
        {saving ? <RefreshCw size={13} className="spin" /> : <Save size={13} />}{saving ? storyboardCopy.saving : storyboardCopy.savePage}
      </button>
    </div>
  </div>;
}
