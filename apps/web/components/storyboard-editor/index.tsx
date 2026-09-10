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
  type StoryboardGeometrySavePayload,
  type StoryboardPanel,
} from "@/lib/api";
import { useLocalStorageValue, writeLocalStorage } from "@/lib/local-storage-store";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Maximize2, Minimize2, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import {
  emptyCommandStack,
  pushCommand,
  redoCommand,
  undoCommand,
  type CommandStackState,
  type GeometryCommandChange,
} from "./command-stack";
import type { DialogueDraft } from "./dialogue-card";
import {
  BASE_PAGE_WIDTH,
  ZOOM_MAX,
  ZOOM_MIN,
  ZOOM_STEP,
  bubbleGeometry,
  defaultCanvas,
  isPolygonPanel,
  legacyBubbleRect,
  newRequestId,
  panelGeometry,
  panelRect,
  toPayloadBubble,
  toPayloadRect,
} from "./geometry";
import { LayoutRebuildDialog } from "./layout-rebuild-dialog";
import { PageCanvas, type CanvasBubble, type CanvasSelection } from "./page-canvas";
import { PanelInspector, type PanelDraft } from "./panel-inspector";
import { storyboardCopy } from "./storyboard-copy";
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
  const [commandStack, setCommandStack] = useState<CommandStackState>(emptyCommandStack);
  const [zoom, setZoom] = useState(1);
  const [toggles, setToggles] = useState<ToolbarToggleState>({
    snap: true,
    readingOrder: true,
    bleed: false,
    safe: false,
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
  const geometryRequestRef = useRef<{ id: string; stackIndex: number } | null>(null);

  const panels = useMemo(() => storyboard.data?.panels ?? [], [storyboard.data]);
  const serverPage = storyboard.data?.page ?? null;
  const canvas = defaultCanvas(serverPage);
  const canvasKnown = Boolean(serverPage?.canvas);

  const serverPanelRects: Record<string, NormalizedRect> = {};
  for (const panel of panels) serverPanelRects[panel.id] = panelRect(panel);
  const panelRects: Record<string, NormalizedRect> = { ...serverPanelRects, ...panelBoundsDrafts };

  const panelOfDialogue = (dialogueId: string) =>
    panels.find((panel) => panel.dialogues.some((dialogue) => dialogue.id === dialogueId)) ?? null;

  const bubbles: CanvasBubble[] = [];
  for (const panel of panels) {
    panel.dialogues.forEach((dialogue, index) => {
      const draft = bubbleDrafts[dialogue.id];
      const stored = bubbleGeometry(dialogue);
      const shape = draft !== undefined ? draft : stored.shape;
      const panelBounds = panelRects[panel.id] ?? panelRect(panel);
      const rect = shape?.rect ?? legacyBubbleRect(panel, dialogue, index);
      bubbles.push({
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
  const dirty = commandStack.index > 0
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
  };

  const clearGeometryDrafts = () => {
    setPanelBoundsDrafts({});
    setBubbleDrafts({});
    setCommandStack(emptyCommandStack());
    geometryRequestRef.current = null;
  };

  const geometrySave = useMutation({
    mutationFn: ({ pageId: targetPageId, payload }: { pageId: string; payload: StoryboardGeometrySavePayload }) =>
      api.saveStoryboardGeometry(targetPageId, payload),
    onSuccess: (response, variables) => {
      clearGeometryDrafts();
      // variables.pageId, not currentPage: a mid-save page switch re-renders
      // this callback against the NEW page, and writing the old page's
      // response under the new key would corrupt the canvas cache.
      queryClient.setQueryData(["storyboard", variables.pageId], response);
      queryClient.invalidateQueries({ queryKey: ["pages", chapterId] });
      setNotice(storyboardCopy.savedNotice(response.page.storyboard_version, response.candidate_count));
    },
  });

  const geometrySaving = geometrySave.isPending;

  // --- geometry commands (from canvas gestures) ----------------------------

  const applyChanges = (changes: GeometryCommandChange[], direction: "before" | "after") => {
    for (const change of changes) {
      if (change.kind === "panel") {
        setPanelBoundsDrafts((drafts) => ({ ...drafts, [change.id]: change[direction] }));
      } else {
        setBubbleDrafts((drafts) => ({ ...drafts, [change.id]: change[direction] }));
      }
    }
  };

  const handleCommand = (label: string, changes: GeometryCommandChange[]) => {
    applyChanges(changes, "after");
    setCommandStack((state) => pushCommand(state, { label, changes }));
  };

  const handleUndo = () => {
    const { state, command } = undoCommand(commandStack);
    if (!command) return;
    setCommandStack(state);
    applyChanges(command.changes, "before");
  };

  const handleRedo = () => {
    const { state, command } = redoCommand(commandStack);
    if (!command) return;
    setCommandStack(state);
    applyChanges(command.changes, "after");
  };

  const buildGeometryPayload = (): StoryboardGeometrySavePayload | null => {
    if (!storyboard.data || !currentPage) return null;
    const reuse = geometryRequestRef.current;
    const requestId = reuse && reuse.stackIndex === commandStack.index ? reuse.id : newRequestId();
    geometryRequestRef.current = { id: requestId, stackIndex: commandStack.index };
    return {
      request_id: requestId,
      storyboard_version: storyboard.data.page.storyboard_version,
      panels: storyboard.data.panels.map((panel) => {
        const bounds = toPayloadRect(panelRects[panel.id] ?? panelRect(panel));
        const stored = panelGeometry(panel);
        return {
          panel_id: panel.id,
          bounds,
          geometry: isPolygonPanel(panel) && stored
            ? stored
            : {
              type: "rect",
              rect: bounds,
              rotation: stored?.rotation ?? 0,
              z_order: stored?.z_order ?? panel.reading_order,
            },
          reading_order: panel.reading_order,
        };
      }),
      dialogues: storyboard.data.panels.flatMap((panel) => panel.dialogues.map((dialogue) => {
        const draft = bubbleDrafts[dialogue.id];
        const bubble = draft !== undefined ? draft : bubbleGeometry(dialogue).shape;
        return {
          dialogue_id: dialogue.id,
          bubble: toPayloadBubble(bubble),
          reading_order: dialogue.reading_order,
        };
      })),
    };
  };

  const saveGeometry = () => {
    const payload = buildGeometryPayload();
    if (!payload || !currentPage) return;
    setNotice("");
    geometrySave.mutate({ pageId: currentPage.id, payload });
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
      return api.updatePanel(target.id, { version: target.version, ...draft });
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
      api.updatePageLayout(currentPage.id, panelCount, layoutMode),
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

  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target;
      const anchor = target instanceof Element ? target.closest("a[href]") : null;
      if (!anchor) return;
      const href = anchor.getAttribute("href") ?? "";
      if (!href.startsWith("/") || href === window.location.pathname) return;
      if (!window.confirm(storyboardCopy.leaveConfirm)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [dirty]);

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
  const hasServerPage = Boolean(serverPage);
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
      canUndo={commandStack.index > 0}
      canRedo={commandStack.index < commandStack.stack.length}
      canSave={commandStack.index > 0}
      saving={canvasBusy}
      overlayHint={!canvasKnown ? storyboardCopy.canvasMissing : null}
      onZoomIn={() => zoomManually(zoom * ZOOM_STEP)}
      onZoomOut={() => zoomManually(zoom / ZOOM_STEP)}
      onFit={fitAndFollow}
      onReset={() => zoomManually(1)}
      onToggle={(key) => setToggles((value) => ({ ...value, [key]: !value[key] }))}
      onUndo={handleUndo}
      onRedo={handleRedo}
      onSave={saveGeometry}
      onRebuildLayout={() => setRebuild({ open: true, panelCount: currentPage.panel_count, layoutMode: currentPage.source_coverage.layout_mode ?? "dynamic" })}
    />
    {storyboard.isLoading ? <div className="storyboard-loading">{storyboardCopy.loading}</div>
      : storyboard.isError ? <div className="storyboard-loading" role="alert"><span>{storyboardCopy.loadError}</span><button type="button" onClick={() => storyboard.refetch()}>{storyboardCopy.retry}</button></div>
      : <div className="storyboard-worktable" style={{ "--inspector-width": `${inspectorWidth}px` } as CSSProperties}>
        <PageCanvas
          page={serverPage ?? currentPage}
          canvas={canvas}
          panels={panels}
          panelRects={panelRects}
          bubbles={bubbles}
          zoom={zoom}
          viewportRef={viewportRef}
          snapEnabled={toggles.snap}
          showReadingOrder={toggles.readingOrder}
          showBleed={toggles.bleed}
          showSafe={toggles.safe}
          interactive={!canvasBusy}
          selection={selection}
          onCommand={handleCommand}
          onSelectPanels={selectPanels}
          onSelectBubble={selectBubble}
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
          onZoomStep={(direction) => zoomManually(direction === 1 ? zoom * ZOOM_STEP : zoom / ZOOM_STEP)}
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
          onCancelNewDialogue={() => setNewDialogue(null)}
          onSelectPanel={(panelId) => selectPanels([panelId])}
          onSelectBubble={selectBubble}
          inspectorOpen={inspectorOpen}
          onToggleInspector={() => setInspectorOpen((open) => !open)}
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
