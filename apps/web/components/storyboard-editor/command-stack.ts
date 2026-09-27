// Local undo/redo command stack for canvas geometry drafts (V02-31B).
// Commands stay client-side; the server only ever receives whole-page
// snapshots through PUT /pages/{id}/storyboard-geometry (contract §10.4).
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

export interface CommandStackState {
  stack: GeometryCommand[];
  /** Commands applied on top of the server state; 0 means pristine. */
  index: number;
}

export const emptyCommandStack = (): CommandStackState => ({ stack: [], index: 0 });

export function pushCommand(state: CommandStackState, command: GeometryCommand): CommandStackState {
  if (!command.changes.length) return state;
  return { stack: [...state.stack.slice(0, state.index), command], index: state.index + 1 };
}

/** Steps back one command; the caller applies each change's `before` value. */
export function undoCommand(state: CommandStackState): { state: CommandStackState; command: GeometryCommand | null } {
  if (state.index <= 0) return { state, command: null };
  return { state: { ...state, index: state.index - 1 }, command: state.stack[state.index - 1] };
}

/** Steps forward one command; the caller applies each change's `after` value. */
export function redoCommand(state: CommandStackState): { state: CommandStackState; command: GeometryCommand | null } {
  if (state.index >= state.stack.length) return { state, command: null };
  return { state: { ...state, index: state.index + 1 }, command: state.stack[state.index] };
}
