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
  const [inspectorWidth, setInspectorWidth] = useState(() => {
    if (typeof window === "undefined") return 390;
    const stored = Number(window.localStorage.getItem("mangaflow.storyboard-inspector-width"));
    // Cap by viewport so a stored width from a larger window cannot push the
    // worktable into horizontal overflow (canvas column has minmax(320px)).
    const viewportCap = Math.max(320, Math.min(620, window.innerWidth - 740));
    return stored >= 320 && stored <= viewportCap ? stored : 390;
  });
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

  // Leave protection covers geometry commands AND unsaved narrative drafts:
  // typed dialogue text is the highest-effort content in the editor, so it must
  // never vanish on page/section switch without confirmation. panelDraft only
  // counts when it actually diverges from the server panel (opening the edit
  // form alone is not an edit).
  const panelDraftDirty = editingPanel && panelDraft && activePanel
    ? JSON.stringify(panelDraft) !== JSON.stringify(makePanelDraft(activePanel))
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
    // Same viewport cap as the initial read: canvas min 320 + gap 10 + sidebar
    // up to 360 + page padding must all fit alongside the inspector.
    const viewportCap = typeof window === "undefined" ? 620 : Math.max(320, Math.min(620, window.innerWidth - 740));
    const next = Math.min(viewportCap, Math.max(320, value));
    setInspectorWidth(next);
    window.localStorage.setItem("mangaflow.storyboard-inspector-width", String(next));
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
    setNotice("");
    queryClient.invalidateQueries({ queryKey: ["storyboard", currentPage?.id] });
  };

  // --- narrative saves keep the existing single-object PATCH path ----------

  const savePanel = useMutation({
    mutationFn: () => api.updatePanel(activePanel!.id, { version: activePanel!.version, ...panelDraft! }),
    onSuccess: () => {
      setEditingPanel(false);
      setPanelDraft(null);
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const saveDialogue = useMutation({
    mutationFn: ({ dialogue, draft }: { dialogue: { id: string }; draft: DialogueDraft }) =>
      api.updateDialogue(dialogue.id, { panel_version: activePanel!.version, ...draft }),
    onSuccess: (_, variables) => {
      setDialogueDrafts((values) => { const next = { ...values }; delete next[variables.dialogue.id]; return next; });
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const addDialogue = useMutation({
    mutationFn: () => api.createDialogue(activePanel!.id, { panel_version: activePanel!.version, ...newDialogue! }),
    onSuccess: () => {
      setNewDialogue(null);
      setNotice(storyboardCopy.savedNotice((serverPage?.storyboard_version ?? currentPage.storyboard_version) + 1, storyboard.data?.candidate_count ?? 0));
      refresh();
    },
  });
  const removeDialogue = useMutation({
    mutationFn: (dialogueId: string) => api.deleteDialogue(dialogueId, activePanel!.version),
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
      setRebuild((value) => ({ ...value, open: false }));
      setNotice(storyboardCopy.rebuildNotice);
      refresh();
    },
  });

  function beginPanel(panel: StoryboardPanel) {
    setSelection({ kind: "panels", ids: [panel.id] });
    setEditingPanel(true);
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
    setPanelDraft(null);
    // Drafts belong to the previous page's dialogues; keeping them would leak
    // stale text into the next page's editor.
    setDialogueDrafts({});
    setNewDialogue(null);
    setPageId(nextPageId);
  };

  const selectPanels = (ids: string[]) => {
    setSelection({ kind: "panels", ids });
    setEditingPanel(false);
    setPanelDraft(null);
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

  const error = savePanel.error ?? saveDialogue.error ?? addDialogue.error ?? removeDialogue.error
    ?? geometrySave.error ?? updateLayout.error ?? replanError;
  const conflict = geometrySave.error != null && isConflictError(geometrySave.error);
  const saving = savePanel.isPending || saveDialogue.isPending || addDialogue.isPending || removeDialogue.isPending
    || updateLayout.isPending || geometrySaving || replanPending;
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
      dirty={dirty}
      saving={geometrySaving}
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
          interactive={!geometrySaving}
          selection={selection}
          onCommand={handleCommand}
          onSelectPanels={selectPanels}
          onSelectBubble={selectBubble}
          onClearSelection={() => setSelection(null)}
          onOpenInspector={() => activePanel && beginPanel(activePanel)}
          onDeleteBubble={(dialogueId) => {
            const bubble = bubbles.find((item) => item.dialogue.id === dialogueId);
            if (bubble && window.confirm("删除这个文字气泡？")) removeDialogue.mutate(dialogueId);
          }}
          onBubbleBounce={() => setNotice(storyboardCopy.bubbleBelongs)}
          onZoomStep={(direction) => zoomManually(direction === 1 ? zoom * ZOOM_STEP : zoom / ZOOM_STEP)}
        />
        {activePanel && <div className="panel-inspector-resizer" role="separator" aria-label="调整属性面板宽度" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={620} aria-valuenow={inspectorWidth} tabIndex={0} onKeyDown={(event) => { if (event.key === "ArrowLeft") persistInspectorWidth(inspectorWidth + 16); if (event.key === "ArrowRight") persistInspectorWidth(inspectorWidth - 16); }} onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) persistInspectorWidth(worktable.right - event.clientX); }} onPointerMove={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) persistInspectorWidth(worktable.right - event.clientX); }} onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}><span /></div>}
        {activePanel && <PanelInspector
          page={serverPage ?? currentPage}
          panel={activePanel}
          panels={panels}
          panelRects={panelRects}
          characters={characters}
          outfits={outfits}
          editingPanel={editingPanel}
          panelDraft={panelDraft}
          dialogueDrafts={dialogueDrafts}
          newDialogue={newDialogue}
          selectedBubbleId={selection?.kind === "bubble" ? selection.dialogueId : null}
          saving={savePanel.isPending || saveDialogue.isPending || removeDialogue.isPending}
          onBeginEdit={() => beginPanel(activePanel)}
          onExitEdit={() => { setEditingPanel(false); setPanelDraft(null); }}
          onPanelDraftChange={setPanelDraft}
          onPresenceChange={setPresence}
          onSavePanel={() => savePanel.mutate()}
          onDialogueDraftChange={(dialogue, draft) => setDialogueDrafts({ ...dialogueDrafts, [dialogue.id]: draft })}
          onSaveDialogue={(dialogue, draft) => saveDialogue.mutate({ dialogue, draft })}
          onRemoveDialogue={(dialogueId) => window.confirm("删除这个文字气泡？") && removeDialogue.mutate(dialogueId)}
          onNewDialogueChange={setNewDialogue}
          onAddDialogue={() => addDialogue.mutate()}
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
