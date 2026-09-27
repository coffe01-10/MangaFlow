"use client";

// 撤销历史树弹层（轻量版：当前链 + 分支列表）。按树的深度缩进列出全部命
// 令节点——主链上的是当前位置所在的祖先链，分支节点挂在各自父节点下；点
// 击任意节点经 commandsToTarget 回跳到该历史位置（含基线）。
import { History } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { CommandStackState } from "./command-stack";
import { storyboardCopy } from "./storyboard-copy";

export function HistoryTree({
  stack,
  onJump,
}: {
  stack: CommandStackState;
  onJump: (nodeId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target instanceof Node ? event.target : null)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setOpen(false);
        rootRef.current?.querySelector<HTMLElement>("[aria-haspopup='dialog']")?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [open]);

  const pathIds = new Set<string>();
  {
    const byId = new Map(stack.nodes.map((node) => [node.id, node]));
    let id = stack.activeId;
    while (id) {
      const node = byId.get(id);
      if (!node) break;
      pathIds.add(node.id);
      id = node.parentId;
    }
  }
  const childrenOf = (parentId: string | null) => stack.nodes.filter((node) => node.parentId === parentId);
  const flatten = (parentId: string | null, depth: number): { node: CommandStackState["nodes"][number]; depth: number }[] =>
    childrenOf(parentId).flatMap((node) => [{ node, depth }, ...flatten(node.id, depth + 1)]);
  const flat = flatten(null, 0);
  return <div className="history-tree" ref={rootRef}>
    <button
      type="button"
      aria-label={storyboardCopy.history}
      aria-haspopup="dialog"
      aria-expanded={open}
      disabled={stack.nodes.length === 0}
      onClick={() => setOpen((value) => !value)}
    ><History size={14} /></button>
    {open && <div className="history-tree-panel" role="dialog" aria-label={storyboardCopy.historyTitle}>
      <button
        type="button"
        role="menuitem"
        className={`history-node${stack.activeId === null ? " active" : ""}`}
        onClick={() => { setOpen(false); onJump(null); }}
      >{storyboardCopy.historyBaseline}</button>
      {flat.map(({ node, depth }, index) => <div key={node.id}>
        <button
          type="button"
          role="menuitem"
          className={`history-node${node.id === stack.activeId ? " active" : ""}${pathIds.has(node.id) ? " on-path" : " branch"}`}
          style={{ paddingLeft: `${8 + depth * 16}px` }}
          onClick={() => { setOpen(false); onJump(node.id); }}
        >
          {pathIds.has(node.id) ? `${index + 1}.` : "↳"} {node.label}
        </button>
      </div>)}
      {stack.nodes.length === 0 && <p className="history-empty">{storyboardCopy.historyEmpty}</p>}
    </div>}
  </div>;
}
