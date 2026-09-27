"use client";

// Numeric geometry editing for the inspector: X/Y/width/height in mm or %,
// plus rotation for shapes that carry it. Commits go through the canvas
// command stack so every typed value is undoable like a drag.
import { useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

import type { CanvasInfo, NormalizedRect } from "@/lib/api";

import { clampRect, MAX_ROTATION, round4, sameRect } from "./geometry";
import { storyboardCopy } from "./storyboard-copy";

type Field = "x" | "y" | "width" | "height";

const fields: Field[] = ["x", "y", "width", "height"];

export function GeometryFields({
  rect,
  canvas,
  minSize,
  rotation,
  disabled,
  onCommitRect,
  onCommitRotation,
}: {
  rect: NormalizedRect;
  canvas: CanvasInfo;
  minSize: number;
  rotation?: number;
  disabled?: boolean;
  onCommitRect: (rect: NormalizedRect) => void;
  onCommitRotation?: (degrees: number) => void;
}) {
  const [unit, setUnit] = useState<"mm" | "percent">("mm");
  const [drafts, setDrafts] = useState<Partial<Record<Field | "rotation", string>>>({});

  const axisMm = (field: Field) => (field === "x" || field === "width" ? canvas.width_mm : canvas.height_mm);
  const display = (field: Field) => {
    const raw = drafts[field];
    if (raw !== undefined) return raw;
    const value = rect[field];
    return unit === "mm" ? (value * axisMm(field)).toFixed(1) : (value * 100).toFixed(1);
  };
  const rotationDisplay = () => drafts.rotation !== undefined ? drafts.rotation : String(rotation ?? 0);

  const commitRectField = (field: Field) => {
    const raw = drafts[field];
    if (raw === undefined) return;
    const parsed = Number(raw);
    setDrafts((values) => { const next = { ...values }; delete next[field]; return next; });
    if (!Number.isFinite(parsed)) return;
    const normalized = unit === "mm" ? parsed / axisMm(field) : parsed / 100;
    const after = clampRect({ ...rect, [field]: normalized }, minSize);
    if (!sameRect(after, rect)) onCommitRect(after);
  };

  const commitRotation = () => {
    const raw = drafts.rotation;
    if (raw === undefined || rotation === undefined) return;
    const parsed = Number(raw);
    setDrafts((values) => { const next = { ...values }; delete next.rotation; return next; });
    if (!Number.isFinite(parsed)) return;
    const clamped = round4(Math.min(MAX_ROTATION, Math.max(-MAX_ROTATION, parsed)));
    if (clamped !== rotation) onCommitRotation?.(clamped);
  };

  const keyDown = (commit: () => void, field: Field | "rotation") => (event: ReactKeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
      event.currentTarget.blur();
    } else if (event.key === "Escape") {
      setDrafts((values) => { const next = { ...values }; delete next[field]; return next; });
      event.currentTarget.blur();
    }
  };

  const labelFor: Record<Field, string> = {
    x: storyboardCopy.geometryX,
    y: storyboardCopy.geometryY,
    width: storyboardCopy.geometryWidth,
    height: storyboardCopy.geometryHeight,
  };

  return <div className="geometry-fields" data-testid="geometry-fields">
    <div className="geometry-unit-toggle" role="group" aria-label="几何单位">
      <button type="button" aria-pressed={unit === "mm"} className={unit === "mm" ? "active" : ""} onClick={() => setUnit("mm")}>{storyboardCopy.unitMm}</button>
      <button type="button" aria-pressed={unit === "percent"} className={unit === "percent" ? "active" : ""} onClick={() => setUnit("percent")}>{storyboardCopy.unitPercent}</button>
    </div>
    <div className="geometry-field-grid">
      {fields.map((field) => <label key={field}>
        <span>{labelFor[field]}</span>
        <input
          inputMode="decimal"
          aria-label={`${labelFor[field]}（${unit === "mm" ? "mm" : "%"}）`}
          disabled={disabled}
          value={display(field)}
          onChange={(event) => setDrafts((values) => ({ ...values, [field]: event.target.value }))}
          onBlur={() => commitRectField(field)}
          onKeyDown={keyDown(() => commitRectField(field), field)}
        />
      </label>)}
      {rotation !== undefined && <label>
        <span>{storyboardCopy.geometryRotation}</span>
        <input
          inputMode="decimal"
          aria-label={`${storyboardCopy.geometryRotation}（°）`}
          disabled={disabled}
          value={rotationDisplay()}
          onChange={(event) => setDrafts((values) => ({ ...values, rotation: event.target.value }))}
          onBlur={commitRotation}
          onKeyDown={keyDown(commitRotation, "rotation")}
        />
      </label>}
    </div>
  </div>;
}
