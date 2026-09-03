"use client";

import { X } from "lucide-react";
import type { ReactNode } from "react";

// V02-51B Template B (audit §4.2/§12): the generic right inspector slot.
// Mount it inside `.workspace-layout` as the third child and add the
// `has-inspector` class to the layout. The CSS contract then behaves as one
// slot everywhere: docked as a real column at ≥1280, right drawer at 900–1279,
// bottom sheet below 900 — the storyboard inspector keeps its own (identical)
// breakpoints until its slice migrates onto this API. Pages own the open
// state; the storage key stays reserved for the later DockProvider slice.
export const WORKSPACE_INSPECTOR_STORAGE_KEY = "mangaflow.workspace-inspector-open";

export function WorkspaceInspectorSlot({
  title,
  open,
  onClose,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <>
      {open && <button type="button" className="workspace-inspector-backdrop" aria-label={`关闭${title}`} onClick={onClose} />}
      <aside className={open ? "workspace-inspector drawer-open" : "workspace-inspector"} aria-label={title}>
        <header>
          <strong>{title}</strong>
          <button type="button" className="workspace-inspector-close" aria-label={`关闭${title}`} onClick={onClose}><X size={15} /></button>
        </header>
        <div className="workspace-inspector-body">{children}</div>
      </aside>
    </>
  );
}
