"use client";

// Canvas library bar (V02-33): layout-template and version-snapshot popovers
// plus the session replay toggle. Popovers reuse the toolbar's page-menu
// pattern (outside pointer-down + Escape close); storage stays localStorage
// so this bar never talks to the API.
import { ChevronDown, Film, Layers, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import type { LayoutTemplate, StoryboardSnapshot } from "./storyboard-history";
import { storyboardCopy } from "./storyboard-copy";

function Popover({
  label,
  open,
  onToggle,
  children,
}: {
  label: string;
  open: boolean;
  onToggle: (open: boolean) => void;
  children: ReactNode;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target instanceof Node ? event.target : null)) {
        onToggle(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onToggle(false);
        rootRef.current?.querySelector<HTMLElement>("[aria-haspopup]")?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open, onToggle]);
  return <div className="page-menu library-menu" ref={rootRef}>
    <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => onToggle(!open)}>
      {label}<ChevronDown size={12} />
    </button>
    {open && <div className="page-menu-items library-popover" role="menu">{children}</div>}
  </div>;
}

const stamp = (createdAt: number) =>
  createdAt ? new Date(createdAt).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }) : "";

export function LibraryBar({
  templates,
  snapshots,
  compareId,
  compareAB,
  replayOpen,
  disabled,
  onApplyTemplate,
  onSaveTemplate,
  onDeleteTemplate,
  onSaveSnapshot,
  onRestoreSnapshot,
  onToggleCompare,
  onMarkCompare,
  onDeleteSnapshot,
  onToggleReplay,
}: {
  templates: LayoutTemplate[];
  snapshots: StoryboardSnapshot[];
  compareId: string | null;
  /** A/B 对比两侧的快照 id；两侧都标好才显示选择条。 */
  compareAB: { a: string | null; b: string | null } | null;
  replayOpen: boolean;
  /** Locked during saves and replay playback. */
  disabled: boolean;
  onApplyTemplate: (template: LayoutTemplate) => void;
  onSaveTemplate: (name: string) => void;
  onDeleteTemplate: (id: string) => void;
  onSaveSnapshot: (name: string) => void;
  onRestoreSnapshot: (id: string) => void;
  onToggleCompare: (id: string) => void;
  onMarkCompare: (id: string, side: "a" | "b") => void;
  onDeleteSnapshot: (id: string) => void;
  onToggleReplay: () => void;
}) {
  const [menu, setMenu] = useState<"templates" | "snapshots" | null>(null);
  const [templateName, setTemplateName] = useState("");
  const [snapshotName, setSnapshotName] = useState("");

  const saveTemplate = () => {
    onSaveTemplate(templateName.trim());
    setTemplateName("");
  };
  const saveSnapshot = () => {
    onSaveSnapshot(snapshotName.trim());
    setSnapshotName("");
  };

  return <div className="toolbar-group toolbar-library" role="group" aria-label="模板、快照与回放">
    <Popover
      label={storyboardCopy.templates}
      open={menu === "templates"}
      onToggle={(open) => setMenu(open ? "templates" : null)}
    >
      <div className="library-section" role="group" aria-label="布局模板">
        {templates.map((template) => (
          <div key={template.id} className="library-item" role="menuitem">
            <button
              type="button"
              className="library-apply"
              disabled={disabled}
              onClick={() => { setMenu(null); onApplyTemplate(template); }}
            >
              <Layers size={12} />
              <span className="library-name">{template.name}</span>
              <small>{template.cells.length} 格</small>
            </button>
            {!template.builtIn && <button
              type="button"
              className="library-delete"
              aria-label={`删除模板 ${template.name}`}
              onClick={() => onDeleteTemplate(template.id)}
            ><Trash2 size={12} /></button>}
          </div>
        ))}
        <div className="library-save">
          <input
            aria-label={storyboardCopy.templateNameLabel}
            placeholder={storyboardCopy.templateNamePlaceholder}
            value={templateName}
            onChange={(event) => setTemplateName(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); saveTemplate(); } }}
          />
          <button type="button" disabled={disabled} onClick={saveTemplate}>{storyboardCopy.saveTemplate}</button>
        </div>
      </div>
    </Popover>
    <Popover
      label={storyboardCopy.snapshots}
      open={menu === "snapshots"}
      onToggle={(open) => setMenu(open ? "snapshots" : null)}
    >
      <div className="library-section" role="group" aria-label="版本快照">
        {!snapshots.length && <p className="library-empty">{storyboardCopy.snapshotEmpty}</p>}
        {snapshots.map((snapshot) => (
          <div key={snapshot.id} className="library-item" role="menuitem">
            <span className="library-name" title={snapshot.name}>{snapshot.name}</span>
            <small>{stamp(snapshot.createdAt)}</small>
            <button type="button" disabled={disabled} onClick={() => { setMenu(null); onRestoreSnapshot(snapshot.id); }}>
              {storyboardCopy.restoreSnapshot}
            </button>
            <button
              type="button"
              aria-pressed={compareId === snapshot.id}
              className={compareId === snapshot.id ? "active" : ""}
              onClick={() => onToggleCompare(snapshot.id)}
            >{storyboardCopy.compareSnapshot}</button>
            <button
              type="button"
              aria-pressed={compareAB?.a === snapshot.id}
              className={`compare-mark${compareAB?.a === snapshot.id ? " active" : ""}`}
              disabled={disabled}
              onClick={() => onMarkCompare(snapshot.id, "a")}
            >{storyboardCopy.compareMarkA}</button>
            <button
              type="button"
              aria-pressed={compareAB?.b === snapshot.id}
              className={`compare-mark${compareAB?.b === snapshot.id ? " active" : ""}`}
              disabled={disabled}
              onClick={() => onMarkCompare(snapshot.id, "b")}
            >{storyboardCopy.compareMarkB}</button>
            <button
              type="button"
              className="library-delete"
              aria-label={`删除快照 ${snapshot.name}`}
              onClick={() => onDeleteSnapshot(snapshot.id)}
            ><Trash2 size={12} /></button>
          </div>
        ))}
        <div className="library-save">
          <input
            aria-label={storyboardCopy.snapshotNameLabel}
            placeholder={storyboardCopy.snapshotNamePlaceholder}
            value={snapshotName}
            onChange={(event) => setSnapshotName(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); saveSnapshot(); } }}
          />
          <button type="button" disabled={disabled} onClick={saveSnapshot}>{storyboardCopy.saveSnapshot}</button>
        </div>
      </div>
    </Popover>
    <button
      type="button"
      aria-pressed={replayOpen}
      className={replayOpen ? "active" : ""}
      onClick={onToggleReplay}
    ><Film size={13} />{storyboardCopy.replay}</button>
  </div>;
}
