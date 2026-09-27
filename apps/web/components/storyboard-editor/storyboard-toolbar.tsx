"use client";

// Canvas toolbar: zoom, fit/reset, snap + overlay toggles, undo/redo,
// save and the page menu holding the destructive layout rebuild (audit §2.1 L0).
import { ChevronDown, Maximize, Redo2, RefreshCw, Save, Scan, Undo2, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { AlignMode, SameSizeMode } from "./geometry";
import { storyboardCopy } from "./storyboard-copy";

export interface ToolbarToggleState {
  snap: boolean;
  readingOrder: boolean;
  bleed: boolean;
  safe: boolean;
  grid: boolean;
  /** 对开预览：邻页版面骨架按印刷对开位置拼在画布两侧（roadmap 已选定 2/5）。 */
  spread: boolean;
  annotate: boolean;
}

const GRID_STEPS = [5, 10, 20];

export function StoryboardToolbar({
  zoomLabel,
  toggles,
  bleedAvailable,
  safeAvailable,
  canUndo,
  canRedo,
  canSave,
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
  alignCount = 0,
  onAlign,
  onDistribute,
  onSameSize,
  gridStep,
  onGridStep,
  annotationCount = 0,
  onUndoAnnotation,
  onClearAnnotations,
  endSlot,
}: {
  zoomLabel: string;
  toggles: ToolbarToggleState;  bleedAvailable: boolean;
  safeAvailable: boolean;
  canUndo: boolean;
  canRedo: boolean;
  /** 保存本页只提交几何(整包 PUT);叙事草稿有各自的保存按钮。 */
  canSave: boolean;
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
  /** Count of movable rect panels in the current selection; >= 2 enables the
   * align/distribute group. */
  alignCount?: number;
  onAlign?: (mode: AlignMode) => void;
  onDistribute?: (axis: "x" | "y") => void;
  onSameSize?: (mode: SameSizeMode) => void;
  gridStep?: number;
  onGridStep?: (step: number) => void;
  /** 手绘批注（逐页本地存储）：批注开关打开时提供撤笔/清空。 */
  annotationCount?: number;
  onUndoAnnotation?: () => void;
  onClearAnnotations?: () => void;
  /** 末尾插槽（V02-33）：模板/快照/回放的 LibraryBar 由 index 组装传入。 */
  endSlot?: ReactNode;
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
      <button type="button" aria-pressed={toggles.grid} className={toggles.grid ? "active" : ""} onClick={() => onToggle("grid")}>{storyboardCopy.grid}</button>
      <button type="button" aria-pressed={toggles.spread} className={toggles.spread ? "active" : ""} onClick={() => onToggle("spread")}>{storyboardCopy.spread}</button>
      {toggles.grid && onGridStep && <select
        aria-label={storyboardCopy.gridStep}
        value={gridStep}
        onChange={(event) => onGridStep(Number(event.target.value))}
      >{GRID_STEPS.map((step) => <option key={step} value={step}>{step}mm</option>)}</select>}
      <button type="button" aria-pressed={toggles.annotate} className={toggles.annotate ? "active" : ""} onClick={() => onToggle("annotate")}>{storyboardCopy.annotate}</button>
      {toggles.annotate && <>
        <button type="button" disabled={!annotationCount} onClick={onUndoAnnotation}>{storyboardCopy.annotateUndo}</button>
        <button type="button" disabled={!annotationCount} onClick={onClearAnnotations}>{storyboardCopy.annotateClear}</button>
      </>}
    </div>
    {alignCount >= 2 && <div className="toolbar-group toolbar-align" role="group" aria-label="对齐与分布">
      {(["left", "centerX", "right", "top", "middleY", "bottom"] as AlignMode[]).map((mode) => (
        <button key={mode} type="button" disabled={saving} onClick={() => onAlign?.(mode)}>{{
          left: storyboardCopy.alignLeft,
          centerX: storyboardCopy.alignCenterX,
          right: storyboardCopy.alignRight,
          top: storyboardCopy.alignTop,
          middleY: storyboardCopy.alignMiddleY,
          bottom: storyboardCopy.alignBottom,
        }[mode]}</button>
      ))}
      {alignCount >= 3 && <>
        <button type="button" disabled={saving} onClick={() => onDistribute?.("x")}>{storyboardCopy.distributeX}</button>
        <button type="button" disabled={saving} onClick={() => onDistribute?.("y")}>{storyboardCopy.distributeY}</button>
      </>}
      <button type="button" disabled={saving} onClick={() => onSameSize?.("width")}>{storyboardCopy.sameWidth}</button>
      <button type="button" disabled={saving} onClick={() => onSameSize?.("height")}>{storyboardCopy.sameHeight}</button>
      <button type="button" disabled={saving} onClick={() => onSameSize?.("size")}>{storyboardCopy.sameSize}</button>
    </div>}
    <div className="toolbar-group" role="group" aria-label="撤销与重做">
      {/* 撤销/重做与保存按钮同受 saving 禁用（#637）：保存在途的撤销/重做会
          改写草稿与命令栈，随后被保存成功的 clearGeometryDrafts 静默清掉。 */}
      <button type="button" aria-label={storyboardCopy.undo} disabled={!canUndo || saving} onClick={onUndo}><Undo2 size={14} /></button>
      <button type="button" aria-label={storyboardCopy.redo} disabled={!canRedo || saving} onClick={onRedo}><Redo2 size={14} /></button>
    </div>
    {overlayHint && <p className="toolbar-hint">{overlayHint}</p>}
    {!overlayHint && <p className="toolbar-hint" aria-hidden="true">{storyboardCopy.hintKeys}</p>}
    {endSlot}
    <div className="toolbar-group toolbar-spacer" role="group" aria-label="保存与页操作">
      <div className="page-menu" ref={menuRef}>
        <button type="button" aria-haspopup="menu" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>
          {storyboardCopy.pageMenu}<ChevronDown size={12} />
        </button>
        {menuOpen && <div className="page-menu-items" role="menu">
          <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onRebuildLayout(); }}>{storyboardCopy.rebuildLayout}</button>
        </div>}
      </div>
      <button type="button" className="toolbar-save" disabled={!canSave || saving} onClick={onSave}>
        {saving ? <RefreshCw size={13} className="spin" /> : <Save size={13} />}{saving ? storyboardCopy.saving : storyboardCopy.savePage}
      </button>
    </div>
  </div>;
}
