// Local undo/redo command tree for canvas geometry drafts (V02-31B + 撤销
// 分支树). Commands stay client-side; the server only ever receives
// whole-page snapshots through PUT /pages/{id}/storyboard-geometry
// (contract §10.4).
//
// 历史是追加式节点树而非线性栈：撤销后再编辑向被撤销的节点追加一个子节
// 点（分叉），旧的重做尾巴不被截断，任何节点都能回跳。lastChildId 记录
// 每个节点上次离开的方向，重做沿用户来时的分支走（对齐原生 HistoryTree）。
import type { BubbleGeometryShape, NormalizedRect } from "@/lib/api";

/** Non-rect fields of PanelGeometryShape editable through canvas commands. */
export interface PanelMetaGeometry {
  z_order?: number;
  rotation?: number;
}

/** Structured SoundEffect geometry (contract §12): x/y are the element
 * center in normalized page space, rotation in degrees, size the normalized
 * font size. All fields are always present in a draft (nulls fall back at
 * render/save time). */
export interface SoundEffectGeometry {
  x: number;
  y: number;
  rotation: number;
  size: number;
}

export type GeometryCommandChange =
  | { kind: "panel"; id: string; before: NormalizedRect; after: NormalizedRect }
  | { kind: "panel-meta"; id: string; before: PanelMetaGeometry; after: PanelMetaGeometry }
  | { kind: "bubble"; id: string; before: BubbleGeometryShape | null; after: BubbleGeometryShape | null }
  | { kind: "sfx"; panelId: string; index: number; before: SoundEffectGeometry; after: SoundEffectGeometry };

export interface GeometryCommand {
  label: string;
  changes: GeometryCommandChange[];
}

export interface CommandNode extends GeometryCommand {
  id: string;
  /** parent on the history tree; null hangs off the pristine baseline. */
  parentId: string | null;
}

export interface CommandStackState {
  /** Append-only node list forming the history tree. */
  nodes: CommandNode[];
  /** Applied tip; null = the pristine server baseline (nothing dirty). */
  activeId: string | null;
  /** parentKey → most recent child visited/pushed, so redo follows the
   * branch the user came from after undoing into a fork. */
  lastChildId: Record<string, string>;
  nextSeq: number;
}

const ROOT = "root";
const parentKey = (id: string | null) => id ?? ROOT;

export const emptyCommandStack = (): CommandStackState => ({
  nodes: [],
  activeId: null,
  lastChildId: {},
  nextSeq: 1,
});

const nodeOf = (state: CommandStackState, id: string | null): CommandNode | null =>
  id === null ? null : state.nodes.find((node) => node.id === id) ?? null;

export function pushCommand(state: CommandStackState, command: GeometryCommand): CommandStackState {
  if (!command.changes.length) return state;
  const node: CommandNode = { ...command, id: `cmd-${state.nextSeq}`, parentId: state.activeId };
  return {
    nodes: [...state.nodes, node],
    activeId: node.id,
    lastChildId: { ...state.lastChildId, [parentKey(state.activeId)]: node.id },
    nextSeq: state.nextSeq + 1,
  };
}

/** Steps back one command; the caller applies each change's `before` value. */
export function undoCommand(state: CommandStackState): { state: CommandStackState; command: GeometryCommand | null } {
  const active = nodeOf(state, state.activeId);
  if (!active) return { state, command: null };
  return {
    state: {
      ...state,
      activeId: active.parentId,
      lastChildId: { ...state.lastChildId, [parentKey(active.parentId)]: active.id },
    },
    command: active,
  };
}

/** Steps forward one command; the caller applies each change's `after` value. */
export function redoCommand(state: CommandStackState): { state: CommandStackState; command: GeometryCommand | null } {
  const key = parentKey(state.activeId);
  const child = nodeOf(state, state.lastChildId[key] ?? null)
    ?? [...state.nodes].reverse().find((node) => node.parentId === state.activeId)
    ?? null;
  if (!child) return { state, command: null };
  return {
    state: { ...state, activeId: child.id, lastChildId: { ...state.lastChildId, [key]: child.id } },
    command: child,
  };
}

/** Ordered root→active chain — the "current branch" shown as the main chain. */
export function historyPath(state: CommandStackState): CommandNode[] {
  const byId = new Map(state.nodes.map((node) => [node.id, node]));
  const path: CommandNode[] = [];
  let id = state.activeId;
  while (id) {
    const node = byId.get(id);
    if (!node) break;
    path.unshift(node);
    id = node.parentId;
  }
  return path;
}

/** Depth of the applied tip — replaces the old linear `index`. */
export function historyDepth(state: CommandStackState): number {
  return historyPath(state).length;
}

/** Nodes forking directly off the current chain — the branch-point list.
 * (Deeper descendants are still reachable through commandsToTarget.) */
export function branchPoints(state: CommandStackState): CommandNode[] {
  const pathIds = new Set(historyPath(state).map((node) => node.id));
  return state.nodes.filter(
    (node) => !pathIds.has(node.id) && (node.parentId === null || pathIds.has(node.parentId)),
  );
}

/** The undo/redo move needed to land on `targetId`: undo segments apply
 * `before` walking up to the shared ancestor, redo segments apply `after`
 * walking down to the target. Returns null when the node does not exist. */
export function commandsToTarget(
  state: CommandStackState,
  targetId: string | null,
): { undos: CommandNode[]; redos: CommandNode[]; state: CommandStackState } | null {
  const byId = new Map(state.nodes.map((node) => [node.id, node]));
  if (targetId !== null && !byId.has(targetId)) return null;
  const chainOf = (id: string | null) => {
    const chain: CommandNode[] = [];
    let cur = id;
    while (cur) {
      const node = byId.get(cur);
      if (!node) break;
      chain.unshift(node);
      cur = node.parentId;
    }
    return chain;
  };
  const activeChain = chainOf(state.activeId);
  const targetChain = chainOf(targetId);
  let shared = 0;
  while (
    shared < Math.min(activeChain.length, targetChain.length)
    && activeChain[shared].id === targetChain[shared].id
  ) shared++;
  // Keep lastChildId honest: every step up/down remembers where we left.
  const lastChildId = { ...state.lastChildId };
  for (const node of activeChain.slice(shared)) lastChildId[parentKey(node.parentId)] = node.id;
  for (const node of targetChain.slice(shared)) lastChildId[parentKey(node.parentId)] = node.id;
  return {
    undos: activeChain.slice(shared).reverse(),
    redos: targetChain.slice(shared),
    state: { ...state, activeId: targetId, lastChildId },
  };
}
