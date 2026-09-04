"use client";

import Image from "next/image";
import {
  Ban,
  Check,
  CircleAlert,
  Eraser,
  Hand,
  LoaderCircle,
  Minus,
  Pencil,
  Plus,
  RotateCcw,
  RotateCw,
  Square,
  SquareDashed,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  api,
  publicUrl,
  type DirectorCommandEnvelope,
  type DirectorCommandGroup,
  type ImageModelAlias,
  type MangaPage,
  type ModelCapability,
  type PageCandidate,
} from "@/lib/api";
import {
  buildRegionRegenerateEnvelope,
  candidateMatchesCommand,
  catalogRegionSurfaceSummary,
  clampPoint,
  derivedCandidatePhase,
  eraseRegions,
  extendStroke,
  formatMaskArea,
  LOCAL_EDIT_MAX_REGIONS,
  localEditGate,
  maskCapableModels,
  pushMaskHistory,
  rectRegion,
  redoMask,
  strokeToRegion,
  undoMask,
  type MaskHistoryState,
  type MaskPoint,
  type MaskRegion,
} from "@/lib/local-edit-rules";

import { ImageModelPicker } from "./shared";

type EditorTool = "rect" | "brush" | "erase" | "pan";
type CompareMode = "side" | "source" | "new";

const FALLBACK_IMAGE = { width: 1024, height: 1024 };
const BASE_STAGE_WIDTH = 640;
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;

/**
 * V02-43B local edit shell: mask tools over the locked source candidate, a
 * side-by-side compare and the V02-42B director regenerate_region flow
 * (propose → user confirm → accept → derived candidate + job). This
 * component never calls api.generateCandidate and never silently falls back
 * to a whole-page POST; an empty mask or an unsupported model simply cannot
 * generate.
 */
export function LocalEditWorkspace({
  id,
  page,
  candidate,
  adoptedCandidate,
  models,
  activeDrawModel,
  onClose,
}: {
  id: string;
  page: MangaPage;
  candidate: PageCandidate;
  adoptedCandidate: PageCandidate | null;
  models: ModelCapability[];
  activeDrawModel: ImageModelAlias | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [tool, setTool] = useState<EditorTool>("rect");
  const [brushSize, setBrushSize] = useState(40);
  const [mask, setMask] = useState<MaskHistoryState>({ past: [], present: [], future: [] });
  const [draftRect, setDraftRect] = useState<{ start: MaskPoint; current: MaskPoint; square: boolean } | null>(null);
  const [draftStroke, setDraftStroke] = useState<MaskPoint[] | null>(null);
  const [view, setView] = useState({ zoom: 1, panX: 0, panY: 0 });
  const [imageDims, setImageDims] = useState(FALLBACK_IMAGE);
  const [instruction, setInstruction] = useState("");
  const [resolution, setResolution] = useState<string>(candidate.resolution);
  const [previewGroup, setPreviewGroup] = useState<DirectorCommandGroup | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [acceptedCommandId, setAcceptedCommandId] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState<CompareMode>("side");
  // L6: when the locked source is not the page's adopted candidate, the
  // source slot can flip to the adopted image so the two are never confused.
  const [viewingAdopted, setViewingAdopted] = useState(false);

  const capableModels = useMemo(() => maskCapableModels(models), [models]);
  const capabilityBlocked = capableModels.length === 0;
  // V02-44B honesty: when blocked, name the surfaces the catalog actually
  // declares (e.g. 仅整图参考编辑) instead of a generic refusal.
  const declaredSurfaces = useMemo(() => catalogRegionSurfaceSummary(models), [models]);
  // Explicit choice wins while it stays mask-capable; otherwise derive the
  // default (active draw model if capable, else the first capable model) so
  // the selection can never rest on a model without the capability bit.
  const [modelAliasChoice, setModelAliasChoice] = useState<ImageModelAlias | null>(null);
  const modelAlias = useMemo(() => {
    const capableAliases = new Set(capableModels.map((model) => model.logical_alias));
    if (modelAliasChoice && capableAliases.has(modelAliasChoice)) return modelAliasChoice;
    if (activeDrawModel && capableAliases.has(activeDrawModel)) return activeDrawModel;
    return capableModels[0]?.logical_alias ?? null;
  }, [capableModels, modelAliasChoice, activeDrawModel]);

  const sourceIsAdopted = Boolean(adoptedCandidate && adoptedCandidate.id === candidate.id);
  const sourceLabel = `候选 ${candidate.ordinal}`;
  const adoptedLabel = adoptedCandidate ? `候选 ${adoptedCandidate.ordinal}` : null;

  const propose = useMutation({
    mutationFn: async (envelope: DirectorCommandEnvelope) => {
      // 换选区重新预览前，丢弃上一次仍未决定的预览组，避免悬挂命令。
      if (previewGroup && previewGroup.status === "PREVIEWED") {
        await api.directorDiscardCommandGroup(id, previewGroup.command_group_id).catch(() => undefined);
      }
      return api.directorProposeCommandGroup(id, {
        command_group_id: envelope.command_group_id,
        commands: [envelope],
      });
    },
    onSuccess: (group) => {
      setPreviewGroup(group);
      setNotice(null);
      queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const accept = useMutation({
    mutationFn: (commandId: string) => api.directorAcceptCommand(id, commandId),
    onSuccess: (_, commandId) => {
      setPreviewGroup(null);
      setNotice(null);
      setAcceptedCommandId(commandId);
      queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const cancelJob = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSuccess: () => {
      setNotice(null);
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const discard = useMutation({
    mutationFn: (commandGroupId: string) => api.directorDiscardCommandGroup(id, commandGroupId),
    onSuccess: () => {
      setPreviewGroup(null);
      setNotice(null);
      queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    },
    onError: (error: Error) => setNotice(error.message),
  });

  // --- derived candidate tracking (L7/L8/L9/L17) -------------------------
  const derivedQuery = useQuery({
    queryKey: ["local-edit-derived", id, acceptedCommandId],
    enabled: Boolean(acceptedCommandId),
    queryFn: async () => {
      const batches = await api.batches(page.id);
      const regionBatches = batches
        .filter((batch) => batch.generation_kind === "REGION_REGENERATED")
        .sort((left, right) => right.ordinal - left.ordinal);
      for (const batch of regionBatches) {
        const items = await api.candidates(batch.id);
        const found = items.find((item) => candidateMatchesCommand(item, acceptedCommandId!));
        if (found) return found;
      }
      return null;
    },
    refetchInterval: (query) => {
      const phase = derivedCandidatePhase(query.state.data);
      return phase === "none" || phase === "pending" ? 2500 : false;
    },
  });
  const derived = derivedQuery.data ?? null;
  const jobPhase = acceptedCommandId ? derivedCandidatePhase(derived) : "none";
  const preparing = propose.isPending || accept.isPending;
  const locked = jobPhase === "pending" || preparing;

  useEffect(() => {
    if (jobPhase === "none" || jobPhase === "pending") return;
    queryClient.invalidateQueries({ queryKey: ["batches", page.id] });
    queryClient.invalidateQueries({ queryKey: ["candidates"] });
    queryClient.invalidateQueries({ queryKey: ["generation-workbench", page.id] });
    queryClient.invalidateQueries({ queryKey: ["jobs", id] });
  }, [jobPhase, id, page.id, queryClient]);

  // --- mask editing ------------------------------------------------------
  const commitRegions = (next: MaskRegion[]) => {
    if (next.length > LOCAL_EDIT_MAX_REGIONS) {
      setNotice(`最多 ${LOCAL_EDIT_MAX_REGIONS} 个选区块（V02-42B 上限）`);
      return;
    }
    setNotice(null);
    setMask((state) => pushMaskHistory(state, next));
  };

  const clearMask = () => {
    if (!mask.present.length) return;
    if (!window.confirm("清空当前选区？已画选区将被移除，可用撤销恢复。")) return;
    setMask((state) => pushMaskHistory(state, []));
  };

  const toImagePoint = (clientX: number, clientY: number): MaskPoint => {
    const rect = stageRef.current?.getBoundingClientRect();
    const displayWidth = rect && rect.width > 0 ? rect.width : BASE_STAGE_WIDTH;
    const displayHeight = displayWidth * (imageDims.width ? imageDims.height / imageDims.width : 1);
    const left = rect?.left ?? 0;
    const top = rect?.top ?? 0;
    const u = 0.5 + ((clientX - left) - displayWidth / 2 - view.panX) / (displayWidth * view.zoom);
    const v = 0.5 + ((clientY - top) - displayHeight / 2 - view.panY) / (displayHeight * view.zoom);
    return clampPoint([u * imageDims.width, v * imageDims.height], imageDims.width, imageDims.height);
  };

  const dragRef = useRef<
    | { kind: "pan"; lastX: number; lastY: number }
    | { kind: "rect" }
    | { kind: "stroke" }
    | null
  >(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (event: PointerEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      if (drag.kind === "pan") {
        const dx = event.clientX - drag.lastX;
        const dy = event.clientY - drag.lastY;
        dragRef.current = { kind: "pan", lastX: event.clientX, lastY: event.clientY };
        setView((current) => ({ ...current, panX: current.panX + dx, panY: current.panY + dy }));
        return;
      }
      const point = toImagePoint(event.clientX, event.clientY);
      if (drag.kind === "rect") {
        setDraftRect((current) => (current ? { ...current, current: point, square: event.shiftKey } : current));
      } else {
        setDraftStroke((current) => extendStroke(current ?? [point], point));
      }
    };
    const onUp = (event: PointerEvent) => {
      const drag = dragRef.current;
      dragRef.current = null;
      setDragging(false);
      if (!drag || drag.kind === "pan") return;
      const point = toImagePoint(event.clientX, event.clientY);
      if (drag.kind === "rect") {
        if (draftRect) {
          commitRegions([...mask.present, rectRegion(draftRect.start, point, imageDims.width, imageDims.height, draftRect.square)]);
        }
        setDraftRect(null);
        return;
      }
      const stroke = draftStroke ? extendStroke(draftStroke, point) : [point];
      setDraftStroke(null);
      if (tool === "erase") {
        commitRegions(eraseRegions(mask.present, stroke));
        return;
      }
      const region = strokeToRegion(stroke, brushSize, imageDims.width, imageDims.height);
      if (region) commitRegions([...mask.present, region]);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    // toImagePoint/commitRegions close over the deps below; they are pure
    // view-model helpers, so re-registering per change keeps drags accurate.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dragging, draftRect, draftStroke, imageDims, mask.present, tool, brushSize, view]);

  const onStagePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (locked) return;
    if (event.button === 1 || tool === "pan") {
      dragRef.current = { kind: "pan", lastX: event.clientX, lastY: event.clientY };
      setDragging(true);
      event.preventDefault();
      return;
    }
    if (event.button !== 0) return;
    const point = toImagePoint(event.clientX, event.clientY);
    if (tool === "rect") {
      dragRef.current = { kind: "rect" };
      setDraftRect({ start: point, current: point, square: event.shiftKey });
    } else {
      dragRef.current = { kind: "stroke" };
      setDraftStroke([point]);
    }
    setDragging(true);
    event.preventDefault();
  };

  // Wheel zoom must be non-passive so preventDefault works without a warning.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      setView((current) => ({
        ...current,
        zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, current.zoom + (event.deltaY < 0 ? 0.25 : -0.25))),
      }));
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, []);

  // --- keyboard shortcuts (audit §8; never while typing) -----------------
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        setMask(event.shiftKey ? redoMask : undoMask);
        return;
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "y") {
        event.preventDefault();
        setMask(redoMask);
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "v" || event.key === "V") setTool("rect");
      else if (event.key === "b" || event.key === "B") setTool("brush");
      else if (event.key === "e" || event.key === "E") setTool("erase");
      else if (event.key === "h" || event.key === "H") setTool("pan");
      else if (event.key === "[") setBrushSize((size) => Math.max(4, size - 4));
      else if (event.key === "]") setBrushSize((size) => Math.min(200, size + 4));
      else if (event.key === "Delete" || event.key === "Backspace") clearMask();
      else if (event.key === "Escape") {
        if (previewGroup) {
          discard.mutate(previewGroup.command_group_id);
          return;
        }
        if (mask.present.length && !window.confirm("放弃当前选区并关闭局部编辑？")) return;
        onClose();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mask.present.length, previewGroup]);

  // Unsubmitted mask must not be lost to an accidental reload (audit L13).
  useEffect(() => {
    if (!mask.present.length) return;
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [mask.present.length]);

  // --- generation (regenerate_region only) -------------------------------
  const gate = localEditGate({
    hasMask: mask.present.length > 0,
    instruction,
    capableModels,
    sourceIsAdopted,
    sourceLabel,
    adoptedLabel,
  });

  // Synchronous in-flight guards: a double-click must not enqueue two
  // commands even before the mutation pending state reaches a re-render
  // (same dedupe discipline as the jobs cancel/retry buttons).
  const submittingRef = useRef(false);
  const acceptingRef = useRef(false);

  const submitPreview = () => {
    if (submittingRef.current || locked) return;
    if (!gate.ok || !modelAlias) {
      setNotice(gate.reason);
      return;
    }
    submittingRef.current = true;
    propose.mutate(buildRegionRegenerateEnvelope({
      projectId: id,
      page,
      regions: mask.present,
      instruction,
      modelAlias,
      resolution,
    }), { onSettled: () => { submittingRef.current = false; } });
  };

  const previewCommand = previewGroup?.commands[0] ?? null;
  const regions = mask.present;
  const areaText = formatMaskArea(regions, imageDims.width, imageDims.height);
  const statusText = jobPhase === "pending"
    ? "局部候选生成中，画笔已锁定；可取消任务。"
    : preparing
      ? "正在提交导演命令…"
      : jobPhase === "done"
        ? "局部候选已生成（未自动暂选），源候选的采用状态不变。"
        : jobPhase === "failed"
          ? "生成失败：mask 已保留，可调整后重试。"
          : jobPhase === "canceled"
            ? "任务已取消：mask 已保留，可调整后重新生成。"
            : capabilityBlocked
              ? "能力不足：当前不能按选区重绘。"
              : acceptedCommandId
                ? "正在等待派生候选出现（任务排队或执行中）…"
                : "空闲";

  const draftPreviewRegions: MaskRegion[] = [];
  if (draftRect) {
    draftPreviewRegions.push(rectRegion(draftRect.start, draftRect.current, imageDims.width, imageDims.height, draftRect.square));
  }
  if (draftStroke && draftStroke.length >= 2) draftPreviewRegions.push({ points: draftStroke });

  const resolutionOptions = selectedModelOptions(resolution, capableModels, modelAlias);

  return (
    <div className="local-edit-shell">
      <header className="local-edit-header">
        <div>
          <span>LOCAL EDIT / 在选区编辑</span>
          <h3>局部选区重绘 · 第 {page.page_number} 页</h3>
        </div>
        <button type="button" className="icon-button" aria-label="关闭局部编辑" onClick={onClose}><X size={16} /></button>
      </header>
      <p className="local-edit-note">
        选区 mask 相对源图像素。生成走导演命令 <strong>regenerate_region</strong>（V02-42B 派生候选链）：预览确认后才会入队，
        不覆盖源候选、不整页重绘；新候选不会自动暂选。
      </p>

      <div className="local-edit-toolbar" role="toolbar" aria-label="局部选区工具">
        <button type="button" aria-pressed={tool === "rect"} className={tool === "rect" ? "active" : ""} disabled={locked} title="矩形选区（V）" onClick={() => setTool("rect")}><Square size={14} />矩形</button>
        <button type="button" aria-pressed={tool === "brush"} className={tool === "brush" ? "active" : ""} disabled={locked} title="画笔（B）" onClick={() => setTool("brush")}><Pencil size={14} />画笔</button>
        <button type="button" aria-pressed={tool === "erase"} className={tool === "erase" ? "active" : ""} disabled={locked} title="擦除（E）：移除画到的选区块" onClick={() => setTool("erase")}><Eraser size={14} />擦除</button>
        <button type="button" aria-pressed={tool === "pan"} className={tool === "pan" ? "active" : ""} disabled={locked} title="平移画布（H），滚轮缩放" onClick={() => setTool("pan")}><Hand size={14} />平移</button>
        <span className="local-edit-toolbar-sep" />
        <button type="button" disabled={locked || !mask.past.length} title="撤销（Ctrl+Z）" onClick={() => setMask(undoMask)}><RotateCcw size={14} />撤销</button>
        <button type="button" disabled={locked || !mask.future.length} title="重做（Ctrl+Y）" onClick={() => setMask(redoMask)}><RotateCw size={14} />重做</button>
        <button type="button" disabled={locked || !regions.length} onClick={clearMask}><Trash2 size={14} />清空</button>
        <span className="local-edit-toolbar-sep" />
        <button type="button" aria-label="缩小画布" disabled={view.zoom <= MIN_ZOOM} onClick={() => setView((current) => ({ ...current, zoom: Math.max(MIN_ZOOM, current.zoom - 0.25) }))}><Minus size={14} /></button>
        <button type="button" aria-label="复位画布缩放" onClick={() => setView({ zoom: 1, panX: 0, panY: 0 })}>{Math.round(view.zoom * 100)}%</button>
        <button type="button" aria-label="放大画布" disabled={view.zoom >= MAX_ZOOM} onClick={() => setView((current) => ({ ...current, zoom: Math.min(MAX_ZOOM, current.zoom + 0.25) }))}><Plus size={14} /></button>
        <span className="local-edit-brush-size">画笔 {brushSize}px（[ / ] 调整）</span>
      </div>

      <div className="local-edit-body">
        <section className="local-edit-canvas-col">
          <div
            ref={stageRef}
            className={`local-edit-stage${locked ? " locked" : ""}`}
            role="img"
            aria-label="局部选区画布"
            title={`已选 ${areaText}% 面积 · ${regions.length} 块`}
            onPointerDown={onStagePointerDown}
          >
            <div
              className="local-edit-inner"
              style={{ aspectRatio: `${imageDims.width} / ${imageDims.height}`, transform: `translate(${view.panX}px, ${view.panY}px) scale(${view.zoom})` }}
            >
              <Image
                src={publicUrl(candidate.thumbnail_url ?? candidate.content_url) ?? ""}
                alt={`源 · 候选 ${candidate.ordinal}`}
                fill
                sizes="(max-width: 900px) 92vw, 560px"
                unoptimized
                onLoad={(event) => {
                  const target = event.currentTarget;
                  if (target.naturalWidth > 0 && target.naturalHeight > 0) {
                    setImageDims({ width: target.naturalWidth, height: target.naturalHeight });
                  }
                }}
              />
              <svg className="local-edit-mask" viewBox={`0 0 ${imageDims.width} ${imageDims.height}`} preserveAspectRatio="none" aria-hidden="true">
                {regions.map((region, index) => (
                  <polygon key={index} className="local-edit-mask-region" points={region.points.map((point) => point.join(",")).join(" ")} />
                ))}
                {draftPreviewRegions.map((region, index) => (
                  <polygon key={`draft-${index}`} className="local-edit-mask-draft" points={region.points.map((point) => point.join(",")).join(" ")} />
                ))}
              </svg>
            </div>
            {locked && <div className="local-edit-lock" role="status"><LoaderCircle className="spin" size={15} />生成中，画笔已锁定</div>}
          </div>
          <p className="local-edit-mask-stats">
            已选 {areaText}% 面积 · {regions.length}/{LOCAL_EDIT_MAX_REGIONS} 块
            {!sourceIsAdopted && <> · 正在编辑 {sourceLabel}（非采用候选）</>}
          </p>
        </section>

        <section className="local-edit-side" aria-label="局部编辑参数与比较">
          <div className="local-edit-form">
            <label>
              <span>局部重绘指令</span>
              <textarea
                rows={2}
                maxLength={4000}
                value={instruction}
                placeholder="例：把选区内的雨改成晴天，区域外保持不变"
                onChange={(event) => setInstruction(event.target.value)}
              />
            </label>
            <ImageModelPicker
              selected={modelAlias}
              onSelect={setModelAliasChoice}
              options={capableModels.map((model) => ({ alias: model.logical_alias, name: model.display_name, id: model.model_id, provider: model.provider }))}
              label="局部重绘模型（仅显示目录声明 accepts_explicit_mask 的已启用模型）"
            />
            <label>
              <span>输出清晰度</span>
              <select value={resolution} onChange={(event) => setResolution(event.target.value)}>
                {resolutionOptions.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
            <p className="local-edit-cost">费用：暂不可估算（真实供应商调用未验收，NOT RUN）。</p>
            {capabilityBlocked && (
              <div className="local-edit-blocked" role="alert">
                <Ban size={15} />
                <p>
                  当前模型不能按选区重绘：目录中没有已启用且声明显式 mask 能力（accepts_explicit_mask）的模型。
                  可到系统设置启用或更换支持 mask 的模型，或取消本次局部编辑；不会按整页重绘静默降级。
                </p>
                {declaredSurfaces && (
                  <p className="local-edit-blocked-detail">目录中已启用的编辑模型能力声明：{declaredSurfaces}。</p>
                )}
                <button type="button" className="button outline compact" onClick={onClose}>取消局部编辑</button>
              </div>
            )}
            {!capabilityBlocked && gate.reason && <p className="local-edit-gate-note"><CircleAlert size={13} />{gate.reason}</p>}
            <button
              type="button"
              className="button ink local-edit-generate"
              disabled={!gate.ok || locked}
              onClick={submitPreview}
            >
              {propose.isPending ? <LoaderCircle className="spin" size={15} /> : <SquareDashed size={15} />}
              {propose.isPending ? "正在解析命令…" : "预览局部命令"}
            </button>
            <p className="local-edit-hint">空选区不能生成；确认预览后才会创建派生候选并入队。</p>
          </div>

          <div className="local-edit-status" role="status" aria-live="polite">
            {(preparing || jobPhase === "pending") && <LoaderCircle className="spin" size={13} />}
            {statusText}
          </div>

          {notice && <p className="form-error" role="alert"><CircleAlert size={14} />{notice}</p>}
          {derivedQuery.isError && acceptedCommandId && (
            <p className="form-error" role="alert">
              <CircleAlert size={14} />
              派生候选状态跟踪失败：{derivedQuery.error instanceof Error ? derivedQuery.error.message : "网络异常"}
            </p>
          )}
          {(propose.error || accept.error || cancelJob.error) && (
            <p className="form-error" role="alert">
              <CircleAlert size={14} />
              {((propose.error ?? accept.error ?? cancelJob.error) as Error).message}
            </p>
          )}

          {previewCommand && (
            <section className="local-edit-preview" role="region" aria-label="局部命令预览">
              <header><strong>局部命令预览 · regenerate_region</strong></header>
              <dl>
                <div><dt>父候选</dt><dd>{adoptedLabel ?? "当前采用候选"}</dd></div>
                <div><dt>指令</dt><dd>{instruction}</dd></div>
                <div><dt>选区块数</dt><dd>{regions.length}</dd></div>
                <div><dt>模型 / 清晰度</dt><dd>{modelAlias ?? "未选择"} · {resolution}</dd></div>
              </dl>
              <p className="local-edit-preview-note">
                确认后创建派生候选（REGION_REGENERATED）并入队；不覆盖源候选、不整页重生，源候选采用状态不变。
              </p>
              <footer>
                <button
                  type="button"
                  className="button ink compact"
                  disabled={accept.isPending}
                  onClick={() => {
                    if (acceptingRef.current) return;
                    acceptingRef.current = true;
                    accept.mutate(previewCommand.command_id, { onSettled: () => { acceptingRef.current = false; } });
                  }}
                >
                  {accept.isPending ? <LoaderCircle className="spin" size={13} /> : <Check size={13} />}确认生成
                </button>
                <button type="button" className="button outline compact" disabled={discard.isPending} onClick={() => discard.mutate(previewGroup!.command_group_id)}>取消预览</button>
              </footer>
            </section>
          )}

          {jobPhase === "pending" && (
            <div className="local-edit-job-row">
              <button
                type="button"
                className="button outline compact"
                disabled={!derived?.job_id || cancelJob.isPending}
                onClick={() => {
                  if (derived?.job_id) cancelJob.mutate(derived.job_id);
                }}
              >取消任务</button>
              <small>任务 {derived?.job_id ? derived.job_id.slice(0, 8) : "入队中"} · 取消后 mask 保留</small>
            </div>
          )}

          <section className="local-edit-compare" aria-label="源与新候选比较">
            <header>
              <strong>比较</strong>
              <div role="group" aria-label="比较方式">
                <button type="button" aria-pressed={compareMode === "side"} className={compareMode === "side" ? "active" : ""} onClick={() => setCompareMode("side")}>并排</button>
                <button type="button" aria-pressed={compareMode === "source"} className={compareMode === "source" ? "active" : ""} onClick={() => setCompareMode("source")}>仅源</button>
                <button type="button" aria-pressed={compareMode === "new"} className={compareMode === "new" ? "active" : ""} onClick={() => setCompareMode("new")}>仅新</button>
              </div>
            </header>
            <div className={`local-edit-slots mode-${compareMode}`}>
              {compareMode !== "new" && (() => {
                const showAdopted = viewingAdopted && !sourceIsAdopted && Boolean(adoptedCandidate?.content_url);
                const slotCandidate = showAdopted && adoptedCandidate ? adoptedCandidate : candidate;
                return (
                  <figure className="local-edit-slot">
                    <figcaption>
                      <span className="local-edit-badge badge-source">源 · 候选 {candidate.ordinal}</span>
                      {sourceIsAdopted
                        ? <span className="local-edit-badge badge-adopted">已暂选</span>
                        : <>
                          <span className="local-edit-badge badge-warn">非采用候选</span>
                          {adoptedCandidate?.content_url && (
                            <button type="button" className="local-edit-toggle-adopted" onClick={() => setViewingAdopted((value) => !value)}>
                              {showAdopted ? "查看源图" : `查看采用图（${adoptedLabel}）`}
                            </button>
                          )}
                        </>}
                    </figcaption>
                    {slotCandidate.content_url || slotCandidate.thumbnail_url
                      ? <Image src={publicUrl(slotCandidate.thumbnail_url ?? slotCandidate.content_url)!} alt={`${showAdopted ? "比较采用图" : "比较源图"} · 候选 ${slotCandidate.ordinal}`} width={560} height={560} unoptimized />
                      : <span className="local-edit-slot-empty">源候选图片缺失，不能局部编辑</span>}
                  </figure>
                );
              })()}
              {compareMode !== "source" && (
                <figure className="local-edit-slot">
                  <figcaption>
                    {jobPhase === "done" && derived
                      ? <>
                        <span className="local-edit-badge badge-new">新局部候选 · 候选 {derived.ordinal}</span>
                        <span className="local-edit-badge">未自动暂选</span>
                      </>
                      : jobPhase === "pending"
                        ? <span className="local-edit-badge badge-pending">生成中…</span>
                        : jobPhase === "failed"
                          ? <span className="local-edit-badge badge-warn">生成失败</span>
                          : jobPhase === "canceled"
                            ? <span className="local-edit-badge badge-warn">已取消</span>
                            : <span className="local-edit-badge">尚无新局部候选</span>}
                  </figcaption>
                  {jobPhase === "done" && derived?.content_url
                    ? <Image src={publicUrl(derived.content_url)!} alt={`局部候选 ${derived.ordinal}，相对源候选 ${candidate.ordinal}`} width={560} height={560} unoptimized />
                    : <span className="local-edit-slot-empty">{jobPhase === "pending" ? "正在生成，完成后在此对照" : "生成后在此对照"}</span>}
                </figure>
              )}
            </div>
          </section>
        </section>
      </div>
    </div>
  );
}

/** Falls back to the candidate's resolution when the model declares none. */
function selectedModelOptions(
  current: string,
  capableModels: ReturnType<typeof maskCapableModels>,
  modelAlias: ImageModelAlias | null,
): string[] {
  const model = capableModels.find((item) => item.logical_alias === modelAlias);
  if (model?.resolutions?.length) return model.resolutions;
  return [current, "1K", "2K", "4K"].filter((item, index, list) => list.indexOf(item) === index);
}
