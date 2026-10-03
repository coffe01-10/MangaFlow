"use client";

// Visual storyboard canvas editor (V02-31B). Owns page state, the geometry
// draft/undo stack and leave protection; canvas gestures land here as commands
// and saving is one whole-page PUT per audit §2.3 J.
import {
  api,
  isConflictError,
  type BubbleGeometryShape,
  type Character,
  type CharacterPresence,
  type MangaPage,
  type NormalizedRect,
  type Outfit,
  type Storyboard,
  type StoryboardGeometrySavePayload,
  type StoryboardPanel,
} from "@/lib/api";
import { useLocalStorageValue, writeLocalStorage } from "@/lib/local-storage-store";
import { useUnsavedChangesGuard } from "@/lib/unsaved-changes-guard";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Maximize2, Minimize2, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import {
  commandsToTarget,
  emptyCommandStack,
  pushCommand,
  redoCommand,
  undoCommand,
  type CommandStackState,
  type GeometryCommandChange,
  type PanelMetaGeometry,
  type SoundEffectGeometry,
} from "./command-stack";
import type { DialogueDraft } from "./dialogue-card";
import {
  alignRects,
  BASE_PAGE_WIDTH,
  clamp01,
  defaultSfxPosition,
  distributeRects,
  gridLinesFor,
  panelZOrder,
  sameRect,
  sameSizeRects,
  ZOOM_MAX,
  ZOOM_MIN,
  ZOOM_STEP,
  zOrderChanges,
  bubbleGeometry,
  defaultCanvas,
  isPolygonPanel,
  legacyBubbleRect,
  newRequestId,
  panelGeometry,
  panelRect,
  round4,
  toPayloadBubble,
  toPayloadRect,
  type AlignMode,
  type SameSizeMode,
  type ZOrderOp,
} from "./geometry";
import { CompareBar } from "./compare-bar";
import { LayoutRebuildDialog } from "./layout-rebuild-dialog";
import { LibraryBar } from "./library-bar";
import { PageCanvas, syntheticBubbleShape, type CanvasBubble, type CanvasSelection } from "./page-canvas";
import { PanelInspector, type PanelDraft } from "./panel-inspector";
import { ReplayBar } from "./replay-bar";
import { downloadBlob, exportReplayVideo } from "./replay-export";
import type { SfxNodeData } from "./sfx-node";
import { SpreadPage } from "./spread-preview";
import { HistoryTree } from "./history-tree";
import { storyboardCopy } from "./storyboard-copy";
import {
  annotationsKey,
  BUILT_IN_TEMPLATES,
  captureCanvasState,
  parseAnnotations,
  parseSnapshots,
  parseUserTemplates,
  sameCanvasState,
  snapshotsKey,
  stateChanges,
  TEMPLATES_KEY,
  templateChanges,
  type AnnotationStroke,
  type LayoutTemplate,
  type ReplayEntry,
} from "./storyboard-history";
import { StoryboardToolbar, type ToolbarToggleState } from "./storyboard-toolbar";

export function StoryboardEditor({
  chapterId,
  pages,
  characters,
  outfits,
  onReplan,
  replanPending,
  replanError,
  initialPageId,
  focusCharacterId,
  onDirtyChange,
}: {
  chapterId: string;
  pages: MangaPage[];
  characters: Character[];
  outfits: Outfit[];
  onReplan: (pageNumber: number) => void;
  replanPending: boolean;
  replanError?: Error | null;
  initialPageId?: string | null;
  focusCharacterId?: string | null;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [pageId, setPageId] = useState(
    pages.some((page) => page.id === initialPageId) ? initialPageId! : pages[0]?.id ?? "",
  );
  const currentPage = pages.find((page) => page.id === pageId) ?? pages[0];
  const storyboard = useQuery({
    queryKey: ["storyboard", currentPage?.id],
    queryFn: () => api.storyboard(currentPage!.id),
    enabled: Boolean(currentPage),
  });

  const [selection, setSelection] = useState<CanvasSelection>(null);
  const [editingPanel, setEditingPanel] = useState(false);
  // The draft is bound to the panel it was composed for. activePanel follows
  // the selection, so keying the form on it let a bubble click or canvas
  // clear re-label the still-open form: 保存本格分镜 then PATCHed the draft
  // onto the WRONG panel with that panel's valid version — silent
  // cross-panel corruption.
  const [editingPanelId, setEditingPanelId] = useState<string | null>(null);
  const [panelDraft, setPanelDraft] = useState<PanelDraft | null>(null);
  const [dialogueDrafts, setDialogueDrafts] = useState<Record<string, DialogueDraft>>({});
  const [newDialogue, setNewDialogue] = useState<DialogueDraft | null>(null);
  const [notice, setNotice] = useState("");
  const [focusMode, setFocusMode] = useState(false);
  const [focusHandled, setFocusHandled] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [panelBoundsDrafts, setPanelBoundsDrafts] = useState<Record<string, NormalizedRect>>({});
  const [bubbleDrafts, setBubbleDrafts] = useState<Record<string, BubbleGeometryShape | null>>({});
  const [panelMetaDrafts, setPanelMetaDrafts] = useState<Record<string, PanelMetaGeometry>>({});
  // Sound-effect geometry drafts keyed panelId → entry index; they are saved
  // through panel PATCH (narrative path), not the geometry PUT.
  const [sfxDrafts, setSfxDrafts] = useState<Record<string, Record<number, SoundEffectGeometry>>>({});
  const [commandStack, setCommandStack] = useState<CommandStackState>(emptyCommandStack);
  // 层序命令的透明度脉冲（对齐原生 PulseZIndex）：token 变化让受影响格重放
  // 一次 z-flash 关键帧；panel-meta 变更（撤销/重做/层序/粘贴 z_order）都触发。
  const [zFlash, setZFlash] = useState<{ token: number; ids: string[] } | null>(null);
  const [zoom, setZoom] = useState(1);
  const [gridStep, setGridStep] = useState(10);
  const [toggles, setToggles] = useState<ToolbarToggleState>({
    snap: true,
    readingOrder: true,
    bleed: false,
    safe: false,
    grid: false,
    spread: false,
    annotate: false,
  });
  const [rebuild, setRebuild] = useState<{ open: boolean; panelCount: number; layoutMode: "dynamic" | "balanced" }>({
    open: false,
    panelCount: 3,
    layoutMode: "dynamic",
  });
  // 属性面板宽度持久化走水合安全的 localStorage 外部存储（水合渲染用默认
  // 390，真实存储值在水合后同步生效；渲染期直读会造成水合不匹配）。拖拽期间
  // 只走本地状态，松手才写存储——逐帧写存储会同步落盘并扇出通知全部订阅者。
  const storedInspectorWidth = useLocalStorageValue("mangaflow.storyboard-inspector-width", "");
  const [dragInspectorWidth, setDragInspectorWidth] = useState<number | null>(null);
  const clampInspectorWidth = (value: number) => {
    // Same viewport cap as the read: canvas min 320 + gap 10 + sidebar up to
    // 360 + page padding must all fit alongside the inspector.
    const viewportCap = typeof window === "undefined" ? 620 : Math.max(320, Math.min(620, window.innerWidth - 740));
    return Math.min(viewportCap, Math.max(320, value));
  };
  const persistedInspectorWidth = useMemo(() => {
    if (typeof window === "undefined") return 390;
    const stored = Number(storedInspectorWidth);
    // Cap by viewport so a stored width from a larger window cannot push the
    // worktable into horizontal overflow (canvas column has minmax(320px)).
    const viewportCap = Math.max(320, Math.min(620, window.innerWidth - 740));
    return stored >= 320 && stored <= viewportCap ? stored : 390;
  }, [storedInspectorWidth]);
  const inspectorWidth = dragInspectorWidth ?? persistedInspectorWidth;
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const geometryRequestRef = useRef<{ id: string; fingerprint: string } | null>(null);

  const panels = useMemo(() => storyboard.data?.panels ?? [], [storyboard.data]);
  const serverPage = storyboard.data?.page ?? null;
  const canvas = defaultCanvas(serverPage);
  const canvasKnown = Boolean(serverPage?.canvas);

  // 对开预览（roadmap 已选定 2/5）：前后页骨架拼在画布两侧，位置按印刷
  // 对开约定——RTL（漫画）前一页在右、后一页在左；LTR 反向。邻页分镜复用
  // storyboard 查询缓存（与切页同一 key），只在开关打开时才取数。
  const sortedPages = useMemo(
    () => [...pages].sort((a, b) => a.page_number - b.page_number),
    [pages],
  );
  const pageIndex = sortedPages.findIndex((page) => page.id === currentPage?.id);
  const spreadPrev = pageIndex > 0 ? sortedPages[pageIndex - 1] : null;
  const spreadNext = pageIndex >= 0 && pageIndex < sortedPages.length - 1 ? sortedPages[pageIndex + 1] : null;
  const spreadPrevQuery = useQuery({
    queryKey: ["storyboard", spreadPrev?.id],
    queryFn: () => api.storyboard(spreadPrev!.id),
    enabled: toggles.spread && Boolean(spreadPrev),
  });
  const spreadNextQuery = useQuery({
    queryKey: ["storyboard", spreadNext?.id],
    queryFn: () => api.storyboard(spreadNext!.id),
    enabled: toggles.spread && Boolean(spreadNext),
  });
  const readingRtl = (serverPage ?? currentPage)?.reading_direction !== "ltr";
  const spreadSide = (page: MangaPage | null, query: { data?: Storyboard; isLoading: boolean }, label: string) =>
    toggles.spread && page
      ? <SpreadPage
          page={page}
          panels={query.data?.panels ?? []}
          canvas={defaultCanvas(query.data?.page)}
          label={label}
          loading={query.isLoading}
          width={BASE_PAGE_WIDTH * zoom * 0.55}
          onJump={() => switchPage(page.id)}
        />
      : null;
  // Flex 从左往右排：RTL 时左槽位放后一页、右槽位放前一页；LTR 相反。
  const spreadLeft = readingRtl
    ? spreadSide(spreadNext, spreadNextQuery, storyboardCopy.spreadNext)
    : spreadSide(spreadPrev, spreadPrevQuery, storyboardCopy.spreadPrev);
  const spreadRight = readingRtl
    ? spreadSide(spreadPrev, spreadPrevQuery, storyboardCopy.spreadPrev)
    : spreadSide(spreadNext, spreadNextQuery, storyboardCopy.spreadNext);

  const panelRects: Record<string, NormalizedRect> = useMemo(() => {
    const map: Record<string, NormalizedRect> = {};
    for (const panel of panels) map[panel.id] = panelRect(panel);
    return { ...map, ...panelBoundsDrafts };
  }, [panels, panelBoundsDrafts]);

  const panelOfDialogue = (dialogueId: string) =>
    panels.find((panel) => panel.dialogues.some((dialogue) => dialogue.id === dialogueId)) ?? null;

  const bubbles: CanvasBubble[] = useMemo(() => {
    const list: CanvasBubble[] = [];
    for (const panel of panels) {
      panel.dialogues.forEach((dialogue, index) => {
        const draft = bubbleDrafts[dialogue.id];
        const stored = bubbleGeometry(dialogue);
        const shape = draft !== undefined ? draft : stored.shape;
        const panelBounds = panelRects[panel.id] ?? panelRect(panel);
        const rect = shape?.rect ?? legacyBubbleRect(panel, dialogue, index);
        list.push({
          dialogue,
          panelId: panel.id,
          panelRect: panelBounds,
          rect,
          shape,
          legacy: draft === undefined && stored.legacy,
          shapeType: shape?.type === "ellipse" ? "ellipse" : "rect",
        });
      });
    }
    return list;
  }, [panels, bubbleDrafts, panelRects]);

  // Sound-effect nodes rendered on the canvas: stored structured entries with
  // canvas drafts applied on top; null x/y/size fall back to a cascaded
  // position inside the owning panel (contract §12 leaves them to the layout).
  const sfxNodes: SfxNodeData[] = useMemo(() => {
    const list: SfxNodeData[] = [];
    for (const panel of panels) {
      const bounds = panelRects[panel.id] ?? panelRect(panel);
      (panel.sound_effects ?? []).forEach((entry, index) => {
        const stored = typeof entry === "object" && entry ? entry : {};
        const text = typeof entry === "string" ? entry : String(stored.text ?? "");
        if (!text) return;
        const fallback = defaultSfxPosition(bounds, index);
        const draft = sfxDrafts[panel.id]?.[index];
        list.push({
          panelId: panel.id,
          index,
          text,
          x: draft?.x ?? (typeof stored.x === "number" ? stored.x : fallback.x),
          y: draft?.y ?? (typeof stored.y === "number" ? stored.y : fallback.y),
          rotation: draft?.rotation ?? (typeof stored.rotation === "number" ? stored.rotation : 0),
          size: draft?.size ?? (typeof stored.size === "number" && stored.size > 0 ? stored.size : 0.05),
        });
      });
    }
    return list;
  }, [panels, panelRects, sfxDrafts]);

  // --- V02-33: templates / snapshots / replay (client-local) ----------------
  //
  // canvasState 是画布"所见即所得"的几何快照：面板 bounds+meta、气泡有效形
  // 状、拟声词有效几何,全部 round4。快照对比、模板套用与回放帧共用这一个
  // 投影——diff 走 stateChanges,应用走 handleCommand,撤销天然覆盖三者。
  const resolvedPanelMeta = useMemo(() => Object.fromEntries(
    panels.map((panel) => [panel.id, {
      rotation: panelMetaDrafts[panel.id]?.rotation ?? panelGeometry(panel)?.rotation ?? 0,
      z_order: panelMetaDrafts[panel.id]?.z_order ?? panelZOrder(panel),
    }]),
  ), [panels, panelMetaDrafts]);

  const canvasState = useMemo(() => captureCanvasState({
    panelIds: panels.map((panel) => panel.id),
    rects: panelRects,
    meta: resolvedPanelMeta,
    bubbles: bubbles.map((bubble) => ({
      id: bubble.dialogue.id,
      shape: bubble.shape ?? syntheticBubbleShape(bubble),
    })),
    sfx: sfxNodes.map((item) => ({
      panelId: item.panelId,
      index: item.index,
      value: { x: item.x, y: item.y, rotation: item.rotation, size: item.size },
    })),
  }), [panels, panelRects, resolvedPanelMeta, bubbles, sfxNodes]);

  const templatesRaw = useLocalStorageValue(TEMPLATES_KEY, "");
  const userTemplates = useMemo(() => parseUserTemplates(templatesRaw), [templatesRaw]);
  const templates = useMemo(() => [...BUILT_IN_TEMPLATES, ...userTemplates], [userTemplates]);

  const snapshotsRaw = useLocalStorageValue(snapshotsKey(currentPage?.id ?? ""), "");
  const snapshots = useMemo(() => parseSnapshots(snapshotsRaw), [snapshotsRaw]);
  // 手绘批注：逐页 localStorage，换页自动换 key 重读；不进几何保存载荷。
  const annotationsRaw = useLocalStorageValue(annotationsKey(currentPage?.id ?? ""), "");
  const annotations = useMemo(() => parseAnnotations(annotationsRaw), [annotationsRaw]);
  const [compareId, setCompareId] = useState<string | null>(null);
  // A/B 版式对比：两份快照各标一侧后双色幽灵叠层同屏，选择条采用其一。
  const [compareAB, setCompareAB] = useState<{ a: string | null; b: string | null } | null>(null);

  // 回放时间线：每个已应用命令后的完整 canvasState。帧标签由命令入口在
  // applyChanges 之前写入 pendingLabel;去重靠 sameCanvasState,无实际几何
  // 变化的命令(如同几何粘贴)不产生帧,其标签也随之丢弃。
  const [timeline, setTimeline] = useState<ReplayEntry[]>([]);
  const timelinePageRef = useRef<string | null>(null);
  const [replayExporting, setReplayExporting] = useState(false);
  const pendingLabelRef = useRef<string | null>(null);
  const [replay, setReplay] = useState<{ open: boolean; index: number; playing: boolean; speed: number }>({
    open: false,
    index: 0,
    playing: false,
    speed: 1,
  });
  const hasServerPage = Boolean(serverPage);
  useEffect(() => {
    if (!hasServerPage || !currentPage) return;
    const pageNow = currentPage.id;
    const label = pendingLabelRef.current;
    pendingLabelRef.current = null;
    setTimeline((prev) => {
      if (timelinePageRef.current !== pageNow) {
        timelinePageRef.current = pageNow;
        return [{ label: "初始状态", at: Date.now(), state: canvasState }];
      }
      const last = prev[prev.length - 1];
      if (last && sameCanvasState(last.state, canvasState)) return prev;
      return [...prev, { label: label ?? "外部更新", at: Date.now(), state: canvasState }];
    });
  }, [canvasState, hasServerPage, currentPage]);

  // 播放驱动:每帧驻留 620ms(含 170ms 命令过渡),到末尾自动停。
  useEffect(() => {
    if (!replay.open || !replay.playing) return;
    const timer = window.setTimeout(() => {
      setReplay((value) => {
        if (value.index >= timeline.length - 1) return { ...value, playing: false };
        return { ...value, index: value.index + 1 };
      });
    }, 620 / replay.speed);
    return () => window.clearTimeout(timer);
  }, [replay.open, replay.playing, replay.index, replay.speed, timeline.length]);

  const selectedPanelIds = selection?.kind === "panels" ? selection.ids : [];
  const movableSelectedIds = selectedPanelIds.filter((id) => {
    const panel = panels.find((item) => item.id === id);
    return panel && !isPolygonPanel(panel) && panelRects[id];
  });

  const gridLines = useMemo(
    () => (toggles.grid ? gridLinesFor(canvas, gridStep) : null),
    [toggles.grid, canvas, gridStep],
  );

  const activePanel: StoryboardPanel | null = selection?.kind === "panels"
    ? panels.find((panel) => panel.id === selection.ids[selection.ids.length - 1]) ?? null
    : selection?.kind === "bubble"
      ? panelOfDialogue(selection.dialogueId)
      : panels[0] ?? null;
  const editedPanel = editingPanelId ? panels.find((panel) => panel.id === editingPanelId) ?? null : null;
  // The edited panel vanished (deleted here or by an external refetch): the
  // form must close, never silently re-target whatever activePanel resolves
  // to next. Derived, not an effect: the stale editing state behind it is
  // inert (draft compares unequal-to-nothing → not dirty; the next
  // selectPanels/beginPanel resets it).
  const editFormOpen = editingPanel && editedPanel !== null;
  // While the edit form is open the inspector stays pinned to the edited
  // panel (header, dialogue editor, layer list); selection changes on the
  // canvas highlight there without moving the form under the user.
  const inspectorPanel = editFormOpen ? editedPanel : activePanel;

  // Leave protection covers geometry commands AND unsaved narrative drafts:
  // typed dialogue text is the highest-effort content in the editor, so it must
  // never vanish on page/section switch without confirmation. panelDraft only
  // counts when it actually diverges from the server panel (opening the edit
  // form alone is not an edit).
  const panelDraftDirty = editFormOpen && panelDraft && editedPanel
    ? JSON.stringify(panelDraft) !== JSON.stringify(makePanelDraft(editedPanel))
    : false;
  // Drafts for dialogues that no longer exist (deleted here or removed by an
  // external refetch) must not keep the editor dirty forever.
  const liveDialogueIds = useMemo(
    () => new Set(panels.flatMap((panel) => panel.dialogues.map((dialogue) => dialogue.id))),
    [panels],
  );
  const hasLiveDialogueDraft = Object.keys(dialogueDrafts).some((id) => liveDialogueIds.has(id));
  const dirty = commandStack.activeId !== null
    || hasLiveDialogueDraft
    || newDialogue !== null
    || panelDraftDirty;
  // The section-level chapter <select> cannot see editor state, so it needs
  // the dirty flag lifted to guard chapter switches like the editor's own
  // page switch does.
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  const persistInspectorWidth = (value: number) => {
    writeLocalStorage("mangaflow.storyboard-inspector-width", String(clampInspectorWidth(value)));
  };

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["storyboard", currentPage?.id] });
    queryClient.invalidateQueries({ queryKey: ["pages", chapterId] });
    // 每次保存都会在服务端 bump storyboard_version。生成工作台持有页版本
    // （抽卡与「沿用并重新检查」都按它提交，staleTime 15s），不失效时首次
    // 操作即 409；候选卡 version_state 标签由 ["candidates"] 查询渲染，也要
    // 一并失效。守护理由同 project-workspace assignOutfit 的注释（#544）。
    queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    queryClient.invalidateQueries({ queryKey: ["candidates"] });
  };

  const clearGeometryDrafts = () => {
    setPanelBoundsDrafts({});
    setBubbleDrafts({});
    setPanelMetaDrafts({});
    setSfxDrafts({});
    setCommandStack(emptyCommandStack());
    geometryRequestRef.current = null;
  };

  const geometrySave = useMutation({
    mutationFn: async ({ pageId: targetPageId, payload, sfxPatches }: {
      pageId: string;
      payload: StoryboardGeometrySavePayload;
      sfxPatches: { panelId: string; version: number; sound_effects: Array<Record<string, unknown>> }[];
    }) => {
      // Sound-effect geometry persists through the narrative PATCH path
      // (panel.version), sequentially before the whole-page geometry PUT.
      for (const patch of sfxPatches) {
        await api.updatePanel(patch.panelId, { version: patch.version, sound_effects: patch.sound_effects });
      }
      return api.saveStoryboardGeometry(targetPageId, payload);
    },
    onSuccess: (response, variables) => {
      clearGeometryDrafts();
      // variables.pageId, not currentPage: a mid-save page switch re-renders
      // this callback against the NEW page, and writing the old page's
      // response under the new key would corrupt the canvas cache.
      queryClient.setQueryData(["storyboard", variables.pageId], response);
      queryClient.invalidateQueries({ queryKey: ["pages", chapterId] });
      // 几何整包 PUT 同样 bump storyboard_version：生成工作台与候选标签必须
      // 一并失效（同 refresh()，理由见其注释，#544）。
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      setNotice(storyboardCopy.savedNotice(response.page.storyboard_version, response.candidate_count));
    },
  });

  const geometrySaving = geometrySave.isPending;

  // --- geometry commands (from canvas gestures) ----------------------------

  const applyChanges = (changes: GeometryCommandChange[], direction: "before" | "after") => {
    const metaIds: string[] = [];
    for (const change of changes) {
      if (change.kind === "panel") {
        setPanelBoundsDrafts((drafts) => ({ ...drafts, [change.id]: change[direction] }));
      } else if (change.kind === "panel-meta") {
        metaIds.push(change.id);
        setPanelMetaDrafts((drafts) => ({ ...drafts, [change.id]: { ...drafts[change.id], ...change[direction] } }));
      } else if (change.kind === "bubble") {
        setBubbleDrafts((drafts) => ({ ...drafts, [change.id]: change[direction] }));
      } else {
        setSfxDrafts((drafts) => ({
          ...drafts,
          [change.panelId]: { ...drafts[change.panelId], [change.index]: change[direction] },
        }));
      }
    }
    if (metaIds.length) setZFlash({ token: Date.now(), ids: metaIds });
  };

  const handleCommand = (label: string, changes: GeometryCommandChange[]) => {
    // 回放是只读模式:命令通道整体关闭(interactive=false 已封画布,这里兜
    // 住检查器/工具栏等程序化入口)。
    if (replay.open) return;
    pendingLabelRef.current = label;
    applyChanges(changes, "after");
    setCommandStack((state) => pushCommand(state, { label, changes }));
  };

  const handleUndo = () => {
    if (replay.open) return;
    const { state, command } = undoCommand(commandStack);
    if (!command) return;
    pendingLabelRef.current = `撤销：${command.label}`;
    setCommandStack(state);
    applyChanges(command.changes, "before");
  };

  const handleRedo = () => {
    if (replay.open) return;
    const { state, command } = redoCommand(commandStack);
    if (!command) return;
    pendingLabelRef.current = `重做：${command.label}`;
    setCommandStack(state);
    applyChanges(command.changes, "after");
  };

  // 回跳到历史树任意节点：先沿撤销段应用 before 到公共祖先，再沿重做段
  // 应用 after 到目标；一次批量落位，回放时间线只录一帧。
  const jumpToHistory = (targetId: string | null) => {
    if (replay.open) return;
    const plan = commandsToTarget(commandStack, targetId);
    if (!plan || plan.undos.length + plan.redos.length === 0) return;
    const last = plan.redos.length > 0 ? plan.redos[plan.redos.length - 1] : null;
    pendingLabelRef.current = `回跳：${last?.label ?? storyboardCopy.historyBaseline}`;
    for (const node of plan.undos) applyChanges(node.changes, "before");
    for (const node of plan.redos) applyChanges(node.changes, "after");
    setCommandStack(plan.state);
    setNotice(storyboardCopy.historyJumped(last?.label ?? storyboardCopy.historyBaseline));
  };

  // --- precision editing handlers (align / distribute / z-order / numeric) --

  const runAlign = (mode: AlignMode) => {
    const changes = alignRects(panelRects, movableSelectedIds, mode)
      .map((change) => ({ kind: "panel" as const, ...change }));
    if (changes.length) handleCommand("对齐", changes);
  };

  const runDistribute = (axis: "x" | "y") => {
    const changes = distributeRects(panelRects, movableSelectedIds, axis)
      .map((change) => ({ kind: "panel" as const, ...change }));
    if (changes.length) handleCommand(axis === "x" ? "水平等距" : "垂直等距", changes);
  };

  const runSameSize = (mode: SameSizeMode) => {
    const changes = sameSizeRects(panelRects, movableSelectedIds, mode)
      .map((change) => ({ kind: "panel" as const, ...change }));
    if (changes.length) handleCommand("同尺寸", changes);
  };

  const runZOrder = (op: ZOrderOp) => {
    const targetId = selectedPanelIds.length === 1 ? selectedPanelIds[0] : inspectorPanel?.id;
    if (!targetId) return;
    const zOrders = Object.fromEntries(
      panels.map((panel) => [panel.id, panelMetaDrafts[panel.id]?.z_order ?? panelZOrder(panel)]),
    );
    const changes = zOrderChanges(zOrders, targetId, op).map((change) => ({
      kind: "panel-meta" as const,
      id: change.id,
      before: { z_order: change.before },
      after: { z_order: change.after },
    }));
    if (changes.length) handleCommand("调整图层", changes);
  };

  const commitPanelRect = (panelId: string, after: NormalizedRect) => {
    const before = panelRects[panelId];
    if (!before || sameRect(before, after)) return;
    handleCommand("输入几何", [{ kind: "panel", id: panelId, before, after }]);
  };

  const commitBubbleRect = (dialogueId: string, rect: NormalizedRect) => {
    const bubble = bubbles.find((item) => item.dialogue.id === dialogueId);
    if (!bubble) return;
    const before = bubble.shape ?? { type: bubble.shapeType, rect: bubble.rect, rotation: 0 } as BubbleGeometryShape;
    handleCommand("输入几何", [{ kind: "bubble", id: dialogueId, before, after: { ...before, rect } }]);
  };

  const commitBubbleRotation = (dialogueId: string, rotation: number) => {
    const bubble = bubbles.find((item) => item.dialogue.id === dialogueId);
    if (!bubble) return;
    const before = bubble.shape ?? { type: bubble.shapeType, rect: bubble.rect, rotation: 0 } as BubbleGeometryShape;
    handleCommand("输入几何", [{ kind: "bubble", id: dialogueId, before, after: { ...before, rotation } }]);
  };

  // --- library bar handlers: templates, snapshots, replay ------------------

  const applyTemplate = (template: LayoutTemplate) => {
    if (replay.open) return;
    // 按阅读序把可动格配到模板格:气泡与拟声词随宿主格做同一仿射映射。
    const orderedIds = panels
      .filter((panel) => !isPolygonPanel(panel) && panelRects[panel.id])
      .sort((a, b) => a.reading_order - b.reading_order)
      .map((panel) => panel.id);
    if (!orderedIds.length) {
      setNotice(storyboardCopy.templateNeedsPanels);
      return;
    }
    const bubblesByPanel: Record<string, { id: string; shape: BubbleGeometryShape }[]> = {};
    for (const bubble of bubbles) {
      bubblesByPanel[bubble.panelId] = [
        ...bubblesByPanel[bubble.panelId] ?? [],
        { id: bubble.dialogue.id, shape: bubble.shape ?? syntheticBubbleShape(bubble) },
      ];
    }
    const sfxByPanel: Record<string, Record<number, SoundEffectGeometry>> = {};
    for (const item of sfxNodes) {
      sfxByPanel[item.panelId] = {
        ...sfxByPanel[item.panelId],
        [item.index]: { x: item.x, y: item.y, rotation: item.rotation, size: item.size },
      };
    }
    const changes = templateChanges({
      orderedPanelIds: orderedIds,
      rects: panelRects,
      bubblesByPanel,
      sfxByPanel,
      cells: template.cells,
    });
    if (!changes.length) return;
    handleCommand("套用模板", changes);
    setNotice(storyboardCopy.templateApplied(template.name, Math.min(orderedIds.length, template.cells.length)));
  };

  const writeTemplates = (list: LayoutTemplate[]) => {
    writeLocalStorage(TEMPLATES_KEY, JSON.stringify(list.filter((item) => !item.builtIn)));
  };

  const saveTemplate = (name: string) => {
    const cells = panels
      .filter((panel) => !isPolygonPanel(panel) && panelRects[panel.id])
      .sort((a, b) => a.reading_order - b.reading_order)
      .map((panel) => panelRects[panel.id]);
    if (!cells.length) {
      setNotice(storyboardCopy.templateNeedsPanels);
      return;
    }
    const template: LayoutTemplate = {
      id: newRequestId(),
      name: name || `模板 ${userTemplates.length + 1}`,
      builtIn: false,
      cells,
      createdAt: Date.now(),
    };
    writeTemplates([...userTemplates, template]);
    setNotice(storyboardCopy.templateSaved(template.name));
  };

  const deleteTemplate = (id: string) => {
    writeTemplates(userTemplates.filter((item) => item.id !== id));
  };

  const writeAnnotations = (list: AnnotationStroke[]) => {
    if (currentPage) writeLocalStorage(annotationsKey(currentPage.id), JSON.stringify(list));
  };
  // 批注是本地标记层：只写 localStorage，不落命令栈、不进几何保存载荷。
  const addAnnotationStroke = (points: { x: number; y: number }[]) => {
    writeAnnotations([
      ...annotations,
      {
        id: newRequestId(),
        points: points.map((point) => ({ x: round4(clamp01(point.x)), y: round4(clamp01(point.y)) })),
      },
    ]);
  };

  const writeSnapshots = (list: ReturnType<typeof parseSnapshots>) => {
    if (currentPage) writeLocalStorage(snapshotsKey(currentPage.id), JSON.stringify(list));
  };

  const saveSnapshot = (name: string) => {
    const snapshot = {
      id: newRequestId(),
      name: name || `快照 ${snapshots.length + 1}`,
      createdAt: Date.now(),
      state: canvasState,
    };
    writeSnapshots([...snapshots, snapshot]);
    setNotice(storyboardCopy.snapshotSaved(snapshot.name));
  };

  const restoreSnapshot = (id: string) => {
    if (replay.open) return;
    const snapshot = snapshots.find((item) => item.id === id);
    if (!snapshot) return;
    // 恢复就是一条普通几何命令:命令栈接管撤销,canvasState 录制接管回放帧。
    const changes = stateChanges(canvasState, snapshot.state);
    if (!changes.length) return;
    handleCommand("恢复快照", changes);
    setNotice(storyboardCopy.snapshotRestored(snapshot.name));
  };

  const deleteSnapshot = (id: string) => {
    if (compareId === id) setCompareId(null);
    if (compareAB?.a === id || compareAB?.b === id) {
      setCompareAB(compareAB.a === id ? (compareAB.b === id ? null : { a: null, b: compareAB.b }) : { a: compareAB.a, b: null });
    }
    writeSnapshots(snapshots.filter((item) => item.id !== id));
    setNotice(storyboardCopy.snapshotDeleted);
  };

  // 单份对比是紫色幽灵；A/B 对比把两份快照分别以紫/青双色叠上当前画布。
  // 标 A/B 属于另一种对比模式，进入即关掉单份对比。
  const markCompare = (id: string, side: "a" | "b") => {
    setCompareId(null);
    setCompareAB((value) => {
      const next = { a: value?.a ?? null, b: value?.b ?? null };
      next[side] = next[side] === id ? null : id;
      // 同一侧只能标一份；同一份快照可以同时占 A 和 B（与自身比对无意义但无害，
      // 采用它就等于恢复它）。
      return next.a || next.b ? next : null;
    });
  };

  const adoptCompare = (side: "a" | "b") => {
    const target = compareAB?.[side] ? snapshots.find((item) => item.id === compareAB[side]) : null;
    if (!target) return;
    const changes = stateChanges(canvasState, target.state);
    if (changes.length) handleCommand("采用版式", changes);
    setCompareAB(null);
    setNotice(storyboardCopy.compareAdopted(target.name));
  };

  const compareSnapshot = compareId ? snapshots.find((item) => item.id === compareId) ?? null : null;
  const ghostRects = (snapshot: NonNullable<typeof compareSnapshot>, variant: "a" | "b") => [
    ...Object.entries(snapshot.state.panels).map(([id, panel]) => ({
      key: `${variant}:panel:${id}`,
      rect: panel.rect,
      variant,
    })),
    ...Object.entries(snapshot.state.bubbles).map(([id, bubble]) => ({
      key: `${variant}:bubble:${id}`,
      rect: bubble.rect,
      ellipse: bubble.type === "ellipse",
      variant,
    })),
  ];
  const snapshotA = compareAB?.a ? snapshots.find((item) => item.id === compareAB.a) ?? null : null;
  const snapshotB = compareAB?.b ? snapshots.find((item) => item.id === compareAB.b) ?? null : null;
  const ghosts = compareAB
    ? [...(snapshotA ? ghostRects(snapshotA, "a") : []), ...(snapshotB ? ghostRects(snapshotB, "b") : [])]
    : compareSnapshot
      ? ghostRects(compareSnapshot, "a")
      : null;

  // 回放展示覆盖:只换投影,不动草稿。interactive=false 封掉全部手势与键
  // 盘命令,handleCommand 的 replay 门禁兜住程序化入口——回放是纯只读的。
  const replayFrame = replay.open ? timeline[Math.min(replay.index, Math.max(timeline.length - 1, 0))] ?? null : null;
  const displayPanelRects = replayFrame
    ? Object.fromEntries(Object.entries(replayFrame.state.panels).map(([id, panel]) => [id, panel.rect]))
    : panelRects;
  const displayBubbles = replayFrame
    ? bubbles.map((bubble) => {
      const shape = replayFrame.state.bubbles[bubble.dialogue.id];
      return shape
        ? { ...bubble, rect: shape.rect, shape, shapeType: shape.type === "ellipse" ? "ellipse" as const : "rect" as const }
        : bubble;
    })
    : bubbles;
  const displaySfx = replayFrame
    ? sfxNodes.map((item) => ({ ...item, ...(replayFrame.state.sfx[item.panelId]?.[item.index] ?? {}) }))
    : sfxNodes;

  const toggleReplay = () => {
    if (replay.open) {
      setReplay((value) => ({ ...value, open: false, playing: false }));
      return;
    }
    if (timeline.length < 2) {
      setNotice(storyboardCopy.replayEmpty);
      return;
    }
    setSelection(null);
    setCompareId(null);
    setCompareAB(null);
    setReplay({ open: true, index: 0, playing: true, speed: replay.speed });
  };

  const buildGeometryPayload = (): StoryboardGeometrySavePayload | null => {
    if (!storyboard.data || !currentPage) return null;
    const panelsPayload = storyboard.data.panels.map((panel) => {
      const bounds = toPayloadRect(panelRects[panel.id] ?? panelRect(panel));
      const stored = panelGeometry(panel);
      const meta = panelMetaDrafts[panel.id];
      return {
        panel_id: panel.id,
        bounds,
        geometry: isPolygonPanel(panel) && stored
          ? { ...stored, z_order: meta?.z_order ?? stored.z_order }
          : {
            type: "rect",
            rect: bounds,
            rotation: meta?.rotation ?? stored?.rotation ?? 0,
            z_order: meta?.z_order ?? stored?.z_order ?? panel.reading_order,
          },
        reading_order: panel.reading_order,
      };
    });
    const dialoguesPayload = storyboard.data.panels.flatMap((panel) => panel.dialogues.map((dialogue) => {
      const draft = bubbleDrafts[dialogue.id];
      const bubble = draft !== undefined ? draft : bubbleGeometry(dialogue).shape;
      return {
        dialogue_id: dialogue.id,
        bubble: toPayloadBubble(bubble),
        reading_order: dialogue.reading_order,
      };
    }));
    // 幂等键按载荷同一性复用，不再按历史栈深度：撤销后做不同编辑回到同一
    // 深度会带着不同载荷复用旧 request_id，命中服务端「同 id 异内容」409
    // 并形成死循环。相同载荷的重发（网络重试/重复点击）才允许复用同一 id。
    const fingerprint = JSON.stringify({
      storyboard_version: storyboard.data.page.storyboard_version,
      panels: panelsPayload,
      dialogues: dialoguesPayload,
    });
    const reuse = geometryRequestRef.current;
    const requestId = reuse && reuse.fingerprint === fingerprint ? reuse.id : newRequestId();
    geometryRequestRef.current = { id: requestId, fingerprint };
    return {
      request_id: requestId,
      storyboard_version: storyboard.data.page.storyboard_version,
      panels: panelsPayload,
      dialogues: dialoguesPayload,
    };
  };

  const saveGeometry = () => {
    const payload = buildGeometryPayload();
    if (!payload || !currentPage) return;
    setNotice("");
    // Merge sound-effect canvas drafts into the narrative PATCH path: each
    // affected panel gets one versioned update with its full merged list.
    const sfxPatches = Object.entries(sfxDrafts).flatMap(([panelId, byIndex]) => {
      const panel = panels.find((item) => item.id === panelId);
      if (!panel || !Object.keys(byIndex).length) return [];
      const merged = (panel.sound_effects ?? []).map((entry, index) => {
        const base: Record<string, unknown> = typeof entry === "string" ? { text: entry } : { ...entry };
        const draft = byIndex[index];
        if (draft) {
          base.x = draft.x;
          base.y = draft.y;
          base.rotation = draft.rotation;
          base.size = draft.size;
        }
        return base;
      });
      return [{ panelId, version: panel.version, sound_effects: merged }];
    });
    geometrySave.mutate({ pageId: currentPage.id, payload, sfxPatches });
  };

  const discardDraft = () => {
    clearGeometryDrafts();
    geometrySave.reset();
    // Narrative recovery (#155): drafts were composed against a stale panel
    // version — keeping them after the reload would immediately re-arm the
    // same 409 loop the recovery button exists to break. Drop them like the
    // geometry path drops its own drafts, then refetch the panel snapshot.
    savePanel.reset();
    saveDialogue.reset();
    addDialogue.reset();
    removeDialogue.reset();
    setEditingPanel(false);
    setEditingPanelId(null);
    setPanelDraft(null);
    setDialogueDrafts({});
    setNewDialogue(null);
    setNotice("");
    queryClient.invalidateQueries({ queryKey: ["storyboard", currentPage?.id] });
  };

  // --- narrative saves keep the existing single-object PATCH path ----------

  // Narrative mutations resolve their target from the dialogue's OWNING panel
  // (or the pinned inspector panel for creation): activePanel follows the
  // canvas selection, and while the edit form is open the inspector shows
  // editedPanel — keying versions on activePanel would send the wrong
  // panel_version (false 409s) or create bubbles in the wrong panel.
  const savePanel = useMutation({
    mutationFn: (draft: PanelDraft) => {
      const target = editedPanel;
      if (!target) throw new Error("目标分格已不存在，无法保存");
      // 逗号列表输入为保住分隔符允许末尾空段留在草稿里；提交前统一清洗，
      // 空道具/空拟声词不写进 PATCH。
      const cleaned: PanelDraft = {
        ...draft,
        props: draft.props.filter((item) => item.trim() !== ""),
        sound_effects: draft.sound_effects.filter((entry) =>
          (typeof entry === "string" ? entry : String((entry as { text?: unknown }).text ?? "")).trim() !== ""),
      };
      return api.updatePanel(target.id, { version: target.version, ...cleaned });
    },
    onSuccess: (_, draft) => {
      // 表单在保存在途时不锁输入:用户继续敲入的内容不能随成功静默丢弃。
      // 只有草稿仍等于提交内容时才收起表单;有更新的输入则保留表单。
      if (JSON.stringify(panelDraft) !== JSON.stringify(draft)) {
        setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
        refresh();
        return;
      }
      setEditingPanel(false);
      setEditingPanelId(null);
      setPanelDraft(null);
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const saveDialogue = useMutation({
    mutationFn: ({ dialogue, draft }: { dialogue: { id: string }; draft: DialogueDraft }) => {
      const owner = panelOfDialogue(dialogue.id);
      if (!owner) throw new Error("气泡所属分格已不存在，无法保存");
      return api.updateDialogue(dialogue.id, { panel_version: owner.version, ...draft });
    },
    onSuccess: (_, variables) => {
      setDialogueDrafts((values) => {
        const current = values[variables.dialogue.id];
        // 在途保存期间继续敲入的文本不能被这次成功静默丢弃:仅当草稿仍
        // 等于提交内容时清除,否则保留新输入(编辑器保持 dirty)。
        if (current && JSON.stringify(current) !== JSON.stringify(variables.draft)) {
          return values;
        }
        const next = { ...values }; delete next[variables.dialogue.id]; return next;
      });
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const addDialogue = useMutation({
    mutationFn: (draft: DialogueDraft) => {
      const target = inspectorPanel;
      if (!target) throw new Error("目标分格已不存在，无法新增气泡");
      return api.createDialogue(target.id, { panel_version: target.version, ...draft });
    },
    onSuccess: (_, draft) => {
      // 新气泡卡片同样不锁输入:提交后又输入的文本保留在卡片上。
      if (JSON.stringify(newDialogue) !== JSON.stringify(draft)) {
        setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
        refresh();
        return;
      }
      setNewDialogue(null);
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const removeDialogue = useMutation({
    mutationFn: (dialogueId: string) => {
      const owner = panelOfDialogue(dialogueId);
      if (!owner) throw new Error("气泡所属分格已不存在，无法删除");
      return api.deleteDialogue(dialogueId, owner.version);
    },
    onSuccess: (_, dialogueId) => {
      // An orphaned draft would keep the editor permanently dirty and arm the
      // unsaved-changes guard for a bubble that no longer exists.
      setDialogueDrafts((values) => { const next = { ...values }; delete next[dialogueId]; return next; });
      setBubbleDrafts((values) => { const next = { ...values }; delete next[dialogueId]; return next; });
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const updateLayout = useMutation({
    mutationFn: ({ panelCount, layoutMode }: { panelCount: number; layoutMode: "dynamic" | "balanced" }) =>
      // 携带版本锚点：整页重建在服务端硬删全部格与对白，陈旧锚点必须 409
      // 而不是静默抹掉并发编辑（与 storyboard-geometry 同语义）。
      api.updatePageLayout(currentPage.id, panelCount, layoutMode, serverPage?.storyboard_version ?? currentPage.storyboard_version),
    onSuccess: () => {
      clearGeometryDrafts();
      setSelection(null);
      setEditingPanel(false);
      setEditingPanelId(null);
      setRebuild((value) => ({ ...value, open: false }));
      setNotice(storyboardCopy.rebuildNotice);
      refresh();
    },
  });

  function beginPanel(panel: StoryboardPanel) {
    // Opening a different panel's form discards the current draft: an unsaved
    // draft confirms first, like every other exit path.
    if (editingPanel && panelDraftDirty && panel.id !== editingPanelId
      && !window.confirm(storyboardCopy.leaveConfirm)) {
      return;
    }
    setSelection({ kind: "panels", ids: [panel.id] });
    setEditingPanel(true);
    setEditingPanelId(panel.id);
    setPanelDraft(makePanelDraft(panel));
    setNotice("");
  }

  useEffect(() => {
    if (focusHandled || !focusCharacterId || !panels.length) return;
    const targetPanel = panels.find((panel) => {
      const presence = panel.character_presence?.[focusCharacterId]
        ?? (panel.characters.includes(focusCharacterId) ? "VISIBLE" : null);
      return presence === "VISIBLE" && !panel.outfits?.[focusCharacterId];
    });
    if (!targetPanel) return;
    let cancelled = false;
    queueMicrotask(() => {
      if (cancelled) return;
      setSelection({ kind: "panels", ids: [targetPanel.id] });
      setEditingPanel(true);
      setEditingPanelId(targetPanel.id);
      setPanelDraft(makePanelDraft(targetPanel));
      setNotice("已定位到缺少服装的出镜格，请在人物下方选择服装并保存本格分镜。");
      setFocusHandled(true);
    });
    return () => {
      cancelled = true;
    };
  }, [focusCharacterId, focusHandled, panels]);

  function setPresence(characterId: string, presence: CharacterPresence | "NONE") {
    if (!panelDraft) return;
    const presenceNext = { ...panelDraft.character_presence };
    if (presence === "NONE") delete presenceNext[characterId];
    else presenceNext[characterId] = presence;
    const charactersNext = Object.entries(presenceNext).filter(([, value]) => value === "VISIBLE").map(([id]) => id);
    const outfitsNext = { ...panelDraft.outfits };
    const expressionsNext = { ...panelDraft.expressions };
    if (presence !== "VISIBLE") {
      delete outfitsNext[characterId];
      delete expressionsNext[characterId];
    }
    setPanelDraft({ ...panelDraft, characters: charactersNext, character_presence: presenceNext, outfits: outfitsNext, expressions: expressionsNext });
  }

  // --- leave protection (audit §2.3 J) --------------------------------------

  // beforeunload + 锚点捕获 + REQUEST_NAVIGATION 事件三路合一：CommandPalette
  // 的 router.push 不走锚点，没有事件守卫会绕过脏确认静默丢草稿。
  useUnsavedChangesGuard(dirty, storyboardCopy.leaveConfirm);

  const switchPage = (nextPageId: string) => {
    if (!nextPageId || nextPageId === currentPage.id) return;
    if (dirty && !window.confirm(storyboardCopy.leaveConfirm)) return;
    clearGeometryDrafts();
    setSelection(null);
    setEditingPanel(false);
    setEditingPanelId(null);
    setPanelDraft(null);
    // Drafts belong to the previous page's dialogues; keeping them would leak
    // stale text into the next page's editor.
    setDialogueDrafts({});
    setNewDialogue(null);
    // 快照对比与回放都是本页视图态,换页即关闭(回放时间线按页重建)。
    setCompareId(null);
    setCompareAB(null);
    setReplay((value) => ({ ...value, open: false, playing: false }));
    setPageId(nextPageId);
  };

  // 同一路由内的 ?page= 变化(前进/后退)不会重挂载编辑器,一次性 useState
  // 初值只认挂载那一刻;后续变化要走 switchPage,复用脏确认与草稿清理。
  // ref 只在目标页真正进入 pages 后前进:深链常落在 pages 查询落地之前,
  // 一次性提交会让 effect 在数据到达后判定"无变化",?page= 被静默丢弃
  // (use-assets-workspace 的 ?outfit= 深链同款写法)。参数离开时重置 ref,
  // 前进/后退回到同一深链条目才能再次应用。
  const appliedInitialPageRef = useRef<string | null>(null);
  const switchPageRef = useRef(switchPage);
  useEffect(() => { switchPageRef.current = switchPage; });
  useEffect(() => {
    const target = initialPageId ?? null;
    if (appliedInitialPageRef.current === target) return;
    if (!target) {
      appliedInitialPageRef.current = null;
      return;
    }
    if (!pages.some((page) => page.id === target)) return;
    appliedInitialPageRef.current = target;
    // 初次挂载时 useState 已应用同一值,switchPage 对相同页是 no-op。
    switchPageRef.current(target);
  }, [initialPageId, pages]);

  const selectPanels = (ids: string[]) => {
    // Leaving the panel being edited closes its form; an unsaved draft must
    // confirm first (same copy as every other exit path). Re-clicking the
    // edited panel keeps the form open instead of discarding it silently.
    const leavingEdit = editingPanel && editingPanelId !== null && !ids.includes(editingPanelId);
    if (leavingEdit && panelDraftDirty && !window.confirm(storyboardCopy.leaveConfirm)) return;
    setSelection({ kind: "panels", ids });
    if (leavingEdit) {
      setEditingPanel(false);
      setEditingPanelId(null);
      setPanelDraft(null);
    }
  };

  const selectBubble = (dialogueId: string) => {
    setSelection({ kind: "bubble", dialogueId });
  };

  const selectSfx = (panelId: string, index: number) => {
    setSelection({ kind: "sfx", panelId, index });
  };

  const selectedCanvasBubble = selection?.kind === "bubble"
    ? bubbles.find((item) => item.dialogue.id === selection.dialogueId) ?? null
    : null;

  const zoomTo = (next: number) => setZoom(Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, next)));

  const fitToViewport = () => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const width = viewport.clientWidth - 48;
    const height = viewport.clientHeight - 48;
    if (width <= 0 || height <= 0) return;
    const pageHeight = BASE_PAGE_WIDTH * (canvas.height_mm / canvas.width_mm);
    zoomTo(Math.min(width / BASE_PAGE_WIDTH, height / pageHeight));
  };

  // Fit mode starts on and is re-armed by 适配窗口: while it is on, viewport
  // resizes (sidebar collapse, inspector drag, window resize) re-fit the zoom.
  // Any manual zoom hands control back to the user until the next explicit fit.
  const fitModeRef = useRef(true);
  const fitRef = useRef(() => {});
  useEffect(() => {
    fitRef.current = () => fitToViewport();
  });

  const zoomManually = (next: number) => {
    fitModeRef.current = false;
    zoomTo(next);
  };

  const fitAndFollow = () => {
    fitModeRef.current = true;
    fitToViewport();
  };

  // Attached once the canvas actually mounts: the storyboard query gates the
  // viewport, so re-running when serverPage arrives re-attaches after the
  // viewport exists (cold-cache visits included) instead of only on mount.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || !hasServerPage || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (fitModeRef.current) fitRef.current();
    });
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [hasServerPage, currentPage?.id]);

  // Narrative saves 409 the same way geometry does (dialogue CRUD bumps
  // panel.version server-side), so the conflict banner + 「放弃并重新加载」
  // recovery must cover both paths — otherwise a stale panelDraft retries the
  // identical version forever (#155).
  const narrativeError = savePanel.error ?? saveDialogue.error ?? addDialogue.error ?? removeDialogue.error;
  const error = narrativeError ?? geometrySave.error ?? updateLayout.error ?? replanError;
  const conflict = (geometrySave.error != null && isConflictError(geometrySave.error))
    || (narrativeError != null && isConflictError(narrativeError));
  const narrativeSaving = savePanel.isPending || saveDialogue.isPending || addDialogue.isPending || removeDialogue.isPending;
  // 叙事保存在途时同样冻结几何入口（审查 R2）：整包 PUT 与画布气泡删除都是
  // 版本化写入，与 addDialogue 等并发会互相制造虚假 409。
  const canvasBusy = geometrySaving || narrativeSaving;
  const saving = canvasBusy || updateLayout.isPending || replanPending;
  const saveStatus = saving ? storyboardCopy.saving : error ? "保存失败" : "已保存";
  if (!currentPage) return null;
  return <div className={focusMode ? "storyboard-desk focus-mode" : "storyboard-desk"}>
    <div className={`storyboard-save-status ${error ? "failed" : saving ? "saving" : "saved"}`} aria-live="polite"><span>{saveStatus}</span><strong>当前 V{serverPage?.storyboard_version ?? currentPage.storyboard_version}</strong><button type="button" aria-pressed={focusMode} onClick={() => setFocusMode((value) => !value)}>{focusMode ? <Minimize2 size={14} /> : <Maximize2 size={14} />}{focusMode ? storyboardCopy.exitFocusMode : storyboardCopy.focusMode}</button></div>
    <label className="storyboard-page-select"><span>当前页面</span><select value={currentPage.id} onChange={(event) => switchPage(event.target.value)}>{pages.map((page) => <option key={page.id} value={page.id}>第 {page.page_number} 页 · {page.panel_count} 格</option>)}</select></label>
    <div className="storyboard-page-strip">{pages.map((page) => <button key={page.id} className={page.id === currentPage.id ? "active" : ""} onClick={() => switchPage(page.id)}><span>P.{String(page.page_number).padStart(3, "0")}</span><strong>{page.panel_count} 格</strong><small>{page.continuity_status === "NEEDS_REVIEW" ? "待复查" : page.selected_candidate_id ? "已采用" : "已规划"}</small></button>)}</div>
    <div className="storyboard-status"><div><strong>第 {currentPage.page_number} 页 · {currentPage.estimated_text_chars}/180 字 · {currentPage.estimated_bubbles}/8 气泡</strong><span>来自漫画剧本：{currentPage.scene_ids.length} 个场景 · {currentPage.beat_ids.length} 个情节拍；修改不会删除已有候选。</span></div><button disabled={replanPending} onClick={() => onReplan(currentPage.page_number)}><RotateCcw size={12} />从本页重新计算</button></div>
    {notice && <p className="edit-notice" role="status"><Check size={13} />{notice}</p>}
    {conflict && <div className="storyboard-conflict" role="alert"><CircleAlert size={14} /><span>{storyboardCopy.conflict}</span><button type="button" onClick={discardDraft}>{storyboardCopy.discardReload}</button></div>}
    {error && !conflict && <p className="form-error"><CircleAlert size={14} />{error.message}
      {/* 几何保存失败（网络错误等）同样保留草稿：可放弃并重新加载，不假装已保存。 */}
      {geometrySave.error && <button type="button" onClick={discardDraft}>{storyboardCopy.discardReload}</button>}
    </p>}
    <StoryboardToolbar
      zoomLabel={`${Math.round(zoom * 100)}%`}
      toggles={toggles}
      bleedAvailable={canvasKnown}
      safeAvailable={canvasKnown}
      canUndo={commandStack.activeId !== null}
      canRedo={commandStack.nodes.some((node) => node.parentId === commandStack.activeId)}
      canSave={commandStack.activeId !== null}
      saving={canvasBusy}
      overlayHint={toggles.annotate ? storyboardCopy.annotateHint : !canvasKnown ? storyboardCopy.canvasMissing : null}
      onZoomIn={() => zoomManually(zoom * ZOOM_STEP)}
      onZoomOut={() => zoomManually(zoom / ZOOM_STEP)}
      onFit={fitAndFollow}
      onReset={() => zoomManually(1)}
      onToggle={(key) => setToggles((value) => ({ ...value, [key]: !value[key] }))}
      onUndo={handleUndo}
      onRedo={handleRedo}
      historySlot={<HistoryTree stack={commandStack} onJump={jumpToHistory} />}
      onSave={saveGeometry}
      onRebuildLayout={() => {
        if (replay.open) return;
        setRebuild({ open: true, panelCount: currentPage.panel_count, layoutMode: currentPage.source_coverage.layout_mode ?? "dynamic" });
      }}
      alignCount={movableSelectedIds.length}
      onAlign={runAlign}
      onDistribute={runDistribute}
      onSameSize={runSameSize}
      gridStep={gridStep}
      onGridStep={setGridStep}
      annotationCount={annotations.length}
      onUndoAnnotation={() => {
        writeAnnotations(annotations.slice(0, -1));
        setNotice(storyboardCopy.annotateUndoDone);
      }}
      onClearAnnotations={() => {
        writeAnnotations([]);
        setNotice(storyboardCopy.annotateCleared);
      }}
      endSlot={<LibraryBar
        templates={templates}
        snapshots={snapshots}
        compareId={compareId}
        compareAB={compareAB}
        replayOpen={replay.open}
        disabled={canvasBusy || replay.open}
        onApplyTemplate={applyTemplate}
        onSaveTemplate={saveTemplate}
        onDeleteTemplate={deleteTemplate}
        onSaveSnapshot={saveSnapshot}
        onRestoreSnapshot={restoreSnapshot}
        onToggleCompare={(id) => {
          setCompareAB(null);
          setCompareId((value) => (value === id ? null : id));
        }}
        onMarkCompare={markCompare}
        onDeleteSnapshot={deleteSnapshot}
        onToggleReplay={toggleReplay}
      />}
    />
    {storyboard.isLoading ? <div className="storyboard-loading">{storyboardCopy.loading}</div>
      : storyboard.isError ? <div className="storyboard-loading" role="alert"><span>{storyboardCopy.loadError}</span><button type="button" onClick={() => storyboard.refetch()}>{storyboardCopy.retry}</button></div>
      : <div className="storyboard-worktable" style={{ "--inspector-width": `${inspectorWidth}px` } as CSSProperties}>
        <PageCanvas
          page={serverPage ?? currentPage}
          canvas={canvas}
          panels={panels}
          panelRects={displayPanelRects}
          bubbles={displayBubbles}
          sfx={displaySfx}
          zoom={zoom}
          viewportRef={viewportRef}
          snapEnabled={toggles.snap}
          extraSnapTargets={gridLines}
          grid={gridLines}
          showReadingOrder={toggles.readingOrder}
          showBleed={toggles.bleed}
          showSafe={toggles.safe}
          interactive={!canvasBusy && !replay.open}
          annotating={toggles.annotate}
          annotations={annotations}
          onAnnotateStroke={addAnnotationStroke}
          ghosts={ghosts}
          overlay={replay.open ? <ReplayBar
            index={Math.min(replay.index, Math.max(timeline.length - 1, 0))}
            count={timeline.length}
            label={replayFrame?.label ?? ""}
            playing={replay.playing}
            speed={replay.speed}
            onPlayPause={() => setReplay((value) => ({ ...value, playing: !value.playing }))}
            onSeek={(index) => setReplay((value) => ({ ...value, index, playing: false }))}
            onStep={(delta) => setReplay((value) => ({
              ...value,
              index: Math.min(Math.max(value.index + delta, 0), timeline.length - 1),
              playing: false,
            }))}
            onSpeed={(speed) => setReplay((value) => ({ ...value, speed }))}
            onExit={() => setReplay((value) => ({ ...value, open: false, playing: false }))}
            exporting={replayExporting}
            onExport={() => {
              // 导出只读时间线数据，不触碰画布态：canvas 帧渲染 + MediaRecorder
              // 录成 webm；不支持的浏览器（无 captureStream/MediaRecorder）落 notice。
              if (replayExporting) return;
              if (timeline.length < 2) { setNotice(storyboardCopy.replayEmpty); return; }
              setReplayExporting(true);
              void exportReplayVideo({ timeline, canvas, speed: replay.speed })
                .then((blob) => {
                  if (!blob) { setNotice(storyboardCopy.replayExportUnsupported); return; }
                  const name = downloadBlob(blob, `分镜回放-P${String(currentPage.page_number).padStart(3, "0")}.webm`);
                  setNotice(storyboardCopy.replayExportDone(name));
                })
                .catch(() => setNotice(storyboardCopy.replayExportUnsupported))
                .finally(() => setReplayExporting(false));
            }}
          /> : snapshotA && snapshotB ? <CompareBar
            nameA={snapshotA.name}
            nameB={snapshotB.name}
            onAdopt={adoptCompare}
            onExit={() => setCompareAB(null)}
          /> : null}
          selection={selection}
          onCommand={handleCommand}
          onSelectPanels={selectPanels}
          onSelectBubble={selectBubble}
          onSelectSfx={selectSfx}
          onNotice={setNotice}
          onClearSelection={() => setSelection(null)}
          onOpenInspector={() => {
            // Canvas double-click pairs onSelectPanels with this callback: a
            // CANCELLED leave-confirm leaves the form on the edited panel, and
            // re-beginning that same panel here would silently reset the dirty
            // draft the user just declined to discard.
            if (editingPanel) return;
            if (activePanel) beginPanel(activePanel);
          }}
          onDeleteBubble={(dialogueId) => {
            const bubble = bubbles.find((item) => item.dialogue.id === dialogueId);
            if (bubble && window.confirm("删除这个文字气泡？")) removeDialogue.mutate(dialogueId);
          }}
          onBubbleBounce={() => setNotice(storyboardCopy.bubbleBelongs)}
          onZoomFactor={(factor) => {
            fitModeRef.current = false;
            setZoom((current) => Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, current * factor)));
          }}
          zFlash={zFlash}
          beforePage={spreadLeft}
          afterPage={spreadRight}
        />
        {activePanel && <div className="panel-inspector-resizer" role="separator" aria-label="调整属性面板宽度" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={620} aria-valuenow={inspectorWidth} tabIndex={0} onKeyDown={(event) => { if (event.key === "ArrowLeft") persistInspectorWidth(inspectorWidth + 16); if (event.key === "ArrowRight") persistInspectorWidth(inspectorWidth - 16); }} onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) setDragInspectorWidth(clampInspectorWidth(worktable.right - event.clientX)); }} onPointerMove={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) setDragInspectorWidth(clampInspectorWidth(worktable.right - event.clientX)); }} onPointerUp={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; event.currentTarget.releasePointerCapture(event.pointerId); const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) persistInspectorWidth(worktable.right - event.clientX); setDragInspectorWidth(null); }} onPointerCancel={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; event.currentTarget.releasePointerCapture(event.pointerId); persistInspectorWidth(inspectorWidth); setDragInspectorWidth(null); }}><span /></div>}
        {activePanel && <PanelInspector
          page={serverPage ?? currentPage}
          panel={inspectorPanel ?? activePanel}
          panels={panels}
          panelRects={panelRects}
          characters={characters}
          outfits={outfits}
          editingPanel={editFormOpen}
          panelDraft={panelDraft}
          dialogueDrafts={dialogueDrafts}
          newDialogue={newDialogue}
          selectedBubbleId={selection?.kind === "bubble" ? selection.dialogueId : null}
          // 组合 busy（几何 PUT + 叙事）双向门禁检查器保存按钮（R2 审查修复）：
          // 只传 narrativeSaving 会让「保存本格分镜」/气泡卡在几何 PUT 在途时
          // 仍可点击，与整包保存并发制造虚假 409——与下方画布冻结正好相反。
          // 输入框不受影响：busy 只禁用按钮（dialogue-card 的 busy 语义相同）。
          saving={canvasBusy}
          onBeginEdit={() => inspectorPanel && beginPanel(inspectorPanel)}
          onExitEdit={() => {
            if (panelDraftDirty && !window.confirm(storyboardCopy.leaveConfirm)) return;
            setEditingPanel(false);
            setEditingPanelId(null);
            setPanelDraft(null);
          }}
          onPanelDraftChange={setPanelDraft}
          onPresenceChange={setPresence}
          onSavePanel={() => panelDraft && savePanel.mutate(panelDraft)}
          onDialogueDraftChange={(dialogue, draft) => setDialogueDrafts({ ...dialogueDrafts, [dialogue.id]: draft })}
          onSaveDialogue={(dialogue, draft) => saveDialogue.mutate({ dialogue, draft })}
          onRemoveDialogue={(dialogueId) => window.confirm("删除这个文字气泡？") && removeDialogue.mutate(dialogueId)}
          onNewDialogueChange={setNewDialogue}
          onAddDialogue={() => newDialogue && addDialogue.mutate(newDialogue)}
          onCancelNewDialogue={() => {
            // 取消新增气泡：已输入的文字不能无确认丢弃（同族：删除气泡、离开
            // 编辑等退出路径都先确认）；空文本直接收起，不打扰（#546）。
            if (newDialogue?.target_text.trim() && !window.confirm("取消将丢弃已输入的气泡文字，确定取消吗？")) return;
            setNewDialogue(null);
          }}
          onSelectPanel={(panelId) => selectPanels([panelId])}
          onSelectBubble={selectBubble}
          inspectorOpen={inspectorOpen}
          onToggleInspector={() => setInspectorOpen((open) => !open)}
          canvas={canvas}
          onRectCommit={commitPanelRect}
          onZOrder={runZOrder}
          bubbleFields={selectedCanvasBubble ? {
            dialogueId: selectedCanvasBubble.dialogue.id,
            rect: selectedCanvasBubble.rect,
            rotation: selectedCanvasBubble.shape?.rotation ?? 0,
          } : null}
          onBubbleRectCommit={commitBubbleRect}
          onBubbleRotationCommit={commitBubbleRotation}
        />}
      </div>}
    {rebuild.open && <LayoutRebuildDialog
      page={currentPage}
      pending={updateLayout.isPending}
      panelCount={rebuild.panelCount}
      layoutMode={rebuild.layoutMode}
      onPanelCountChange={(panelCount) => setRebuild((value) => ({ ...value, panelCount }))}
      onLayoutModeChange={(layoutMode) => setRebuild((value) => ({ ...value, layoutMode }))}
      onConfirm={() => updateLayout.mutate({ panelCount: rebuild.panelCount, layoutMode: rebuild.layoutMode })}
      onCancel={() => setRebuild((value) => ({ ...value, open: false }))}
    />}
  </div>;
}

function makePanelDraft(panel: StoryboardPanel): PanelDraft {
  return {
    shot_type: panel.shot_type,
    camera_angle: panel.camera_angle,
    camera_height: panel.camera_height,
    characters: [...panel.characters],
    character_presence: Object.keys(panel.character_presence ?? {}).length
      ? { ...panel.character_presence }
      : Object.fromEntries(panel.characters.map((characterId) => [characterId, "VISIBLE" as const])),
    props: [...(panel.props ?? [])],
    outfits: { ...panel.outfits },
    actions: { ...panel.actions },
    expressions: { ...panel.expressions },
    background: panel.background,
    sound_effects: [...panel.sound_effects],
    bleed: panel.bleed,
    borderless: panel.borderless,
  };
}
