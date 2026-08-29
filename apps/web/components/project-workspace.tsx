"use client";

import { AppShell } from "@/components/shell";
import { ProductionReadiness } from "@/components/production-readiness";
import {
  api,
  publicUrl,
  type ImageModelAlias,
  type InspectionResult,
  type Job,
  type Project,
  type Resolution,
} from "@/lib/api";
import { getPageGenerationIssue, getPageStructureIssue } from "@/lib/generation-rules";
import {
  activePollInterval,
  hasActiveItem,
  isActiveTaskStatus,
  isTerminalTaskStatus,
} from "@/lib/task-status";
import {
  generationKindLabels,
  inspectionLabels,
  jobLabels,
  navigationItems,
  repairTypeLabels,
} from "./project-workspace/labels";
import {
  assetName,
  formatBytes,
  inspectionBubbleDiffs,
  inspectionSummary,
  queueStatsOf,
  recommendedRepairType,
} from "./project-workspace/display";
import { CandidateArtwork, ImageModelPicker } from "./project-workspace/shared";
import type { AssetWorkspaceView, WorkspaceSection } from "./project-workspace/types";
import { useWorkspaceQueries } from "./project-workspace/use-workspace-queries";
import { useSourceWorkspace } from "./project-workspace/use-source-workspace";
import { SourceSection } from "./project-workspace/source-section";
import { ScriptSection } from "./project-workspace/script-section";
import { StoryboardSection } from "./project-workspace/storyboard-section";
import { AssetsSection } from "./project-workspace/assets-section";
import { useAssetsWorkspace } from "./project-workspace/use-assets-workspace";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Archive,
  Check,
  CircleAlert,
  ChevronDown,
  ChevronUp,
  Download,
  FileImage,
  Heart,
  History,
  ImagePlus,
  LibraryBig,
  ListTodo,
  LoaderCircle,
  Maximize2,
  ZoomIn,
  ZoomOut,
  Menu,
  Plus,
  Pencil,
  RotateCcw,
  Sparkles,
  Star,
  Trash2,
  Settings,
  Workflow,
  X,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

export type { AssetWorkspaceView, WorkspaceSection } from "./project-workspace/types";

export default function ProjectWorkspace({
  section,
  assetView = "characters",
}: {
  section: WorkspaceSection;
  assetView?: AssetWorkspaceView;
}) {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [navOpen, setNavOpen] = useState(false);
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [showArchivedJobs, setShowArchivedJobs] = useState(false);
  const [queueDockHidden, setQueueDockHidden] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("mangaflow.queue-dock-hidden") === "true";
  });
  const [jobNotice, setJobNotice] = useState("");
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(() => searchParams.get("page"));
  const [drawModel, setDrawModelState] = useState<ImageModelAlias | null>(() => {
    if (typeof window === "undefined") return null;
    const stored = window.localStorage.getItem(`mangaflow.image-model.${id}`);
    return stored && stored !== "auto" ? stored : null;
  });
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [libraryChapter, setLibraryChapter] = useState("");
  const [libraryCharacter, setLibraryCharacter] = useState("");
  const [libraryKind, setLibraryKind] = useState("");
  const [libraryModel, setLibraryModel] = useState("");
  const [libraryResolution, setLibraryResolution] = useState("");
  const [libraryDateFrom, setLibraryDateFrom] = useState("");
  const [libraryDateTo, setLibraryDateTo] = useState("");
  const [libraryCursor, setLibraryCursor] = useState("");
  const [libraryHistory, setLibraryHistory] = useState<string[]>([]);
  const [reviewCandidateId, setReviewCandidateId] = useState<string | null>(null);
  const [previewImage, setPreviewImageState] = useState<{ url: string; label: string } | null>(null);
  const [previewZoom, setPreviewZoom] = useState(1);
  const setPreviewImage = (value: { url: string; label: string } | null) => {
    if (value) setPreviewZoom(1);
    setPreviewImageState(value);
  };
  const openPreview = (url: string, label: string) => {
    setPreviewZoom(1);
    setPreviewImage({ url, label });
  };
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    if (typeof window === "undefined") return 214;
    const stored = Number(window.localStorage.getItem("mangaflow.project-sidebar-width"));
    return stored >= 188 && stored <= 360 ? stored : 214;
  });
  const [referenceSelections, setReferenceSelections] = useState<Record<string, { character_asset_id: string | null; outfit_id: string | null; outfit_asset_id: string | null }>>({});
  const [referenceOverridePageId, setReferenceOverridePageId] = useState<string | null>(null);
  const [viewedBatchId, setViewedBatchId] = useState<string | null>(null);

  const workspaceQueries = useWorkspaceQueries({ id, section, assetView, selectedChapterId });
  const {
    project,
    models,
    assets,
    chapters,
    characters,
    outfits,
    pages,
    script,
    activeChapterId,
    needsChapters,
    needsCharacters,
    needsOutfits,
    needsPages,
    needsScript,
  } = workspaceQueries;

  const projectPath = (target: string) =>
    target === "assets" ? `/projects/${id}/assets/characters` : `/projects/${id}/${target}`;
  const setDrawModel = (model: ImageModelAlias) => {
    setDrawModelState(model);
    window.localStorage.setItem(`mangaflow.image-model.${id}`, model);
  };
  const toggleQueueDock = (hidden: boolean) => {
    setQueueDockHidden(hidden);
    window.localStorage.setItem("mangaflow.queue-dock-hidden", String(hidden));
  };

  const source = useSourceWorkspace({
    id,
    projectPath,
    router,
    activeChapterId,
    setSelectedChapterId,
    setSelectedPageId,
  });

  const library = useQuery({
    queryKey: ["library", id, favoriteOnly, libraryChapter, libraryCharacter, libraryKind, libraryModel, libraryResolution, libraryDateFrom, libraryDateTo, libraryCursor],
    queryFn: () => api.library(id, {
      favorite: favoriteOnly ? true : undefined,
      chapter_id: libraryChapter || undefined,
      character_id: libraryCharacter || undefined,
      generation_kind: libraryKind || undefined,
      model_alias: (libraryModel || undefined) as ImageModelAlias | undefined,
      resolution: (libraryResolution || undefined) as Resolution | undefined,
      date_from: libraryDateFrom ? `${libraryDateFrom}T00:00:00Z` : undefined,
      date_to: libraryDateTo ? `${libraryDateTo}T23:59:59Z` : undefined,
      cursor: libraryCursor || undefined,
      limit: 30,
    }),
    enabled: section === "library",
  });
  const jobs = useQuery({
    queryKey: ["jobs", id, showArchivedJobs],
    queryFn: () => api.jobs(id, showArchivedJobs),
    enabled: ["assets", "jobs", "generate"].includes(section),
    refetchInterval: (query) => activePollInterval(query.state.data, 3000),
  });
  const exportsQuery = useQuery({ queryKey: ["exports", id], queryFn: () => api.exports(id), enabled: section === "library" });
  const chapterProduction = useQuery({
    queryKey: ["chapter-production", activeChapterId],
    queryFn: () => api.chapterProductionReadiness(activeChapterId!),
    enabled: section === "library" && Boolean(activeChapterId),
  });
  const selectedPageEntry = pages.data?.find((item) => item.id === selectedPageId) ?? pages.data?.[0] ?? null;
  const workbench = useQuery({
    queryKey: ["generation-workbench", selectedPageEntry?.id],
    queryFn: () => api.generationWorkbench(selectedPageEntry!.id),
    enabled: section === "generate" && Boolean(selectedPageEntry),
    refetchInterval: (query) => {
      const generating = hasActiveItem(query.state.data?.candidates);
      const candidateIds = new Set((query.state.data?.candidates ?? []).map((candidate) => candidate.id));
      const checking = (jobs.data ?? []).some((job) =>
        job.job_type === "PAGE_INSPECT"
        && candidateIds.has(job.target_id)
        && !isTerminalTaskStatus(job.status));
      return generating || checking ? 3000 : false;
    },
  });
  const selectedPage = workbench.data?.page ?? selectedPageEntry;
  const currentBatch = workbench.data?.current_batch ?? null;
  const pageBatches = useQuery({
    queryKey: ["batches", selectedPageEntry?.id],
    queryFn: () => api.batches(selectedPageEntry!.id),
    enabled: section === "generate" && Boolean(selectedPageEntry),
  });
  // The workbench inserts tall blocks (readiness panel, reference check) whose
  // height is unknown until the workbench query lands; rendering them in stages
  // pushed the whole canvas down (measured CLS 0.477). Show one skeleton until
  // the workbench, batch and model data exist, then insert the canvas at once.
  const generateWorkbenchReady = !workbench.isLoading && !pageBatches.isLoading && !models.isLoading;
  const orderedPageBatches = useMemo(
    () => [...(pageBatches.data ?? [])].sort((left, right) => left.ordinal - right.ordinal),
    [pageBatches.data],
  );
  const latestBatch = currentBatch ?? orderedPageBatches[orderedPageBatches.length - 1] ?? null;
  const viewedBatch = orderedPageBatches.find((batch) => batch.id === viewedBatchId) ?? latestBatch;
  const viewedBatchIndex = viewedBatch
    ? orderedPageBatches.findIndex((batch) => batch.id === viewedBatch.id)
    : -1;
  const previousBatch = viewedBatchIndex > 0 ? orderedPageBatches[viewedBatchIndex - 1] : null;
  const nextBatch = viewedBatchIndex >= 0 && viewedBatchIndex < orderedPageBatches.length - 1
    ? orderedPageBatches[viewedBatchIndex + 1]
    : null;
  const isViewingHistoricalBatch = Boolean(
    viewedBatch && latestBatch && viewedBatch.id !== latestBatch.id,
  );
  const candidates = useQuery({
    queryKey: ["candidates", viewedBatch?.id],
    queryFn: () => api.candidates(viewedBatch!.id),
    enabled: section === "generate" && Boolean(viewedBatch),
    refetchInterval: (query) => activePollInterval(query.state.data, 3000),
  });
  const generationStoryboard = { data: workbench.data?.storyboard, isLoading: workbench.isLoading };
  const pageReadiness = { data: workbench.data?.readiness, isLoading: workbench.isLoading, error: workbench.error };
  const pageProduction = workbench.data?.production ?? null;
  const inspections = useQuery({
    queryKey: ["inspections", reviewCandidateId],
    queryFn: () => api.inspections(reviewCandidateId!),
    enabled: section === "generate" && Boolean(reviewCandidateId),
    refetchInterval: (query) => {
      const checking = (jobs.data ?? []).some((job) =>
        job.job_type === "PAGE_INSPECT"
        && job.target_id === reviewCandidateId
        && !isTerminalTaskStatus(job.status));
      return checking || !(query.state.data ?? []).length ? 2500 : false;
    },
  });

  const workspaceRouteReady = !project.isLoading
    && (!needsChapters || !chapters.isLoading)
    && (!needsCharacters || !characters.isLoading)
    && (!needsOutfits || !outfits.isLoading)
    && (!needsPages || !pages.isLoading)
    && (!needsScript || !script.isLoading)
    && (section !== "assets" || !assets.isLoading)
    && (section !== "library" || !library.isLoading)
    && (section !== "jobs" || !jobs.isLoading);

  useEffect(() => {
    if (!workspaceRouteReady) return;
    const key = `mangaflow.workspace-scroll.${id}`;
    const saved = window.sessionStorage.getItem(key);
    if (saved === null) return;
    const top = Number(saved);
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number.isFinite(top) ? top : 0, behavior: "auto" });
      window.sessionStorage.removeItem(key);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [assetView, id, section, workspaceRouteReady]);

  const draft = localDraft ?? project.data ?? null;
  const modelOptions = useMemo(
    () => {
      const available = (models.data ?? [])
        .filter((model) => model.enabled && model.model_type === "IMAGE" && model.operations.includes("image_edit"));
      return available.map((model) => ({
        alias: model.logical_alias,
        name: model.display_name,
        id: model.model_id,
        provider: model.provider,
      }));
    },
    [models.data],
  );
  const activeDrawModel = drawModel && modelOptions.some((option) => option.alias === drawModel)
    ? drawModel
    : null;
  const selectedPageStructureIssue = getPageStructureIssue(selectedPage);
  const selectedPageGenerationIssue = getPageGenerationIssue(selectedPage, activeDrawModel);
  const visibleCharacterIds = useMemo(
    () => Array.from(new Set((generationStoryboard.data?.panels ?? []).flatMap((panel) => panel.characters))),
    [generationStoryboard.data?.panels],
  );
  const defaultReferenceSelections = useMemo(() => {
    const next: typeof referenceSelections = {};
    for (const characterId of visibleCharacterIds) {
      const character = characters.data?.find((item) => item.id === characterId);
      const assignedOutfitId = generationStoryboard.data?.panels.find((panel) => panel.outfits[characterId])?.outfits[characterId] ?? null;
      const outfit = outfits.data?.find((item) => item.id === assignedOutfitId);
      next[characterId] = {
        character_asset_id: character?.references.find((item) => item.is_canonical)?.asset_id ?? character?.references[0]?.asset_id ?? null,
        outfit_id: assignedOutfitId,
        outfit_asset_id: outfit?.reference_asset_ids[0] ?? null,
      };
    }
    return next;
  }, [characters.data, generationStoryboard.data?.panels, outfits.data, visibleCharacterIds]);
  const effectiveReferenceSelections = useMemo(
    () => ({ ...defaultReferenceSelections, ...referenceSelections }),
    [defaultReferenceSelections, referenceSelections],
  );
  const generationReferenceReady = visibleCharacterIds.every((characterId) => {
    const selection = effectiveReferenceSelections[characterId];
    if (!selection?.character_asset_id) return false;
    const outfit = outfits.data?.find((item) => item.id === selection.outfit_id);
    return !outfit || Boolean(outfit.reference_asset_ids.length && selection.outfit_asset_id);
  });
  const referenceOverrideOpen = referenceOverridePageId === selectedPage?.id;
  const targetDialogues = useMemo(
    () => (generationStoryboard.data?.panels ?? []).flatMap((panel) => panel.dialogues.map((dialogue) => dialogue.target_text)).filter(Boolean),
    [generationStoryboard.data?.panels],
  );

  function rememberWorkspaceScroll() {
    window.sessionStorage.setItem(`mangaflow.workspace-scroll.${id}`, String(window.scrollY));
    setNavOpen(false);
  }

  function beginSidebarResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    const move = (moveEvent: PointerEvent) => setSidebarWidth(Math.min(360, Math.max(188, startWidth + moveEvent.clientX - startX)));
    const stop = (stopEvent: PointerEvent) => {
      const next = Math.min(360, Math.max(188, startWidth + stopEvent.clientX - startX));
      setSidebarWidth(next);
      window.localStorage.setItem("mangaflow.project-sidebar-width", String(next));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function requireDrawModel(): ImageModelAlias {
    if (!activeDrawModel) throw new Error("请先选择一个支持参考图编辑的图片模型");
    return activeDrawModel;
  }
  const assetsWorkspace = useAssetsWorkspace({
    id,
    section,
    assetView,
    router,
    projectPath,
    activeChapterId,
    assets,
    characters,
    outfits,
    requireDrawModel,
  });
  const queueStats = useMemo(() => queueStatsOf(jobs.data ?? []), [jobs.data]);
  const latestInspections = useMemo(() => {
    const latest = new Map<string, InspectionResult>();
    for (const item of inspections.data ?? []) {
      if (!latest.has(item.category)) latest.set(item.category, item);
    }
    return [...latest.values()];
  }, [inspections.data]);
  const reviewCandidate = candidates.data?.find((item) => item.id === reviewCandidateId) ?? null;
  const reviewJob = jobs.data?.find((item) => item.target_id === reviewCandidateId && item.job_type === "PAGE_INSPECT") ?? null;
  const selectedWorkbenchCandidate = workbench.data?.selected_candidate ?? null;
  const productionBlocker = pageProduction?.blockers[0] ?? null;

  const assignOutfit = useMutation({
    mutationFn: ({ sceneId, assignments }: { sceneId: string; assignments: Record<string, string> }) =>
      api.assignSceneOutfits(sceneId, assignments),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["script", activeChapterId] }),
  });

  const replanPage = useMutation({
    mutationFn: (pageNumber: number) => api.planChapter(activeChapterId!, pageNumber),
    onSuccess: (result, pageNumber) => {
      setSelectedPageId(
        result.pages.find((page) => page.page_number === pageNumber)?.id ?? null,
      );
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const startBatch = useMutation({
    mutationFn: () => {
      const issue = getPageStructureIssue(selectedPage);
      if (issue) throw new Error(issue);
      return api.startBatch(selectedPage!.id);
    },
    onSuccess: (batch) => {
      setViewedBatchId(batch.id);
      setReviewCandidateId(null);
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
    },
  });

  const generate = useMutation({
    mutationFn: async () => {
      const issue = getPageGenerationIssue(selectedPage, activeDrawModel);
      if (issue) throw new Error(issue);
      if (!pageReadiness.data?.ready) throw new Error(pageReadiness.isLoading ? "正在检查页面生产条件" : "页面生产准备尚未完成，请先处理阻塞项");
      if (!generationReferenceReady) throw new Error("请为本页每个入镜人物选择人物参考图，并补齐分镜指定服装的参考图");
      const batch = currentBatch ?? await api.startBatch(selectedPage!.id);
      return api.generateCandidate(
        batch.id,
        requireDrawModel(),
        "1K",
        selectedPage!.storyboard_version,
        effectiveReferenceSelections,
      );
    },
    onSuccess: () => {
      setDraft(null);
      setViewedBatchId(null);
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["project", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["page-readiness", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
    },
  });

  const favorite = useMutation({
    mutationFn: ({ candidateId, value }: { candidateId: string; value: boolean }) =>
      api.favoriteCandidate(candidateId, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const deleteCandidate = useMutation({
    mutationFn: (candidateId: string) => api.deleteCandidate(candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const cancelJob = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });
  const retryJob = useMutation({
    mutationFn: (jobId: string) => api.retryJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });
  const archiveJob = useMutation({
    mutationFn: (jobId: string) => api.archiveJob(jobId),
    onSuccess: () => {
      setJobNotice("任务已移入历史记录");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "归档失败"),
  });
  const restoreJob = useMutation({
    mutationFn: (jobId: string) => api.restoreJob(jobId),
    onSuccess: () => {
      setJobNotice("任务已恢复到近期记录");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "恢复失败"),
  });
  const archiveCompletedJobs = useMutation({
    mutationFn: () => api.archiveCompletedJobs(id),
    onSuccess: (result) => {
      setJobNotice(result.archived_count ? `已归档 ${result.archived_count} 条已结束任务` : "没有可归档的已结束任务");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "清空失败"),
  });
  const bulkArchiveJobs = useMutation({
    mutationFn: () => api.bulkArchiveJobs(id, selectedJobIds),
    onSuccess: (result) => {
      setJobNotice(`已批量归档 ${result.archived_count} 条任务`);
      setSelectedJobIds([]);
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "批量归档失败"),
  });
  const deleteJob = useMutation({
    mutationFn: (jobId: string) => api.deleteJob(jobId),
    onSuccess: () => {
      setJobNotice("无引用任务已彻底删除");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "删除失败"),
  });

  const inspectCandidate = useMutation({
    mutationFn: (candidateId: string) => api.inspectCandidate(candidateId),
    onSuccess: (_, candidateId) => {
      setReviewCandidateId(candidateId);
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["inspections", candidateId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["chapter-production", activeChapterId] });
    },
  });

  const repairCandidate = useMutation({
    mutationFn: (inspection: InspectionResult) => api.repairCandidate(reviewCandidateId!, {
      inspection_result_id: inspection.id,
      repair_type: recommendedRepairType(inspection.category),
      target_regions: inspection.regions,
      target_fields: [],
      model_alias: requireDrawModel(),
      resolution: reviewCandidate?.resolution ?? "1K",
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["chapter-production", activeChapterId] });
    },
  });

  const upscaleCandidate = useMutation({
    mutationFn: ({ candidateId, resolution }: { candidateId: string; resolution: "2K" | "4K" }) =>
      api.upscaleCandidate(candidateId, requireDrawModel(), resolution),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const selectCandidate = useMutation({
    mutationFn: ({ candidateId, manualTextConfirmed, acceptStale = false }: { candidateId: string; manualTextConfirmed: boolean; acceptStale?: boolean }) =>
      api.selectCandidate(selectedPage!.id, candidateId, manualTextConfirmed, acceptStale),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["chapter-production", activeChapterId] });
    },
  });

  const keepSelectedCandidate = useMutation({
    mutationFn: (candidateId: string) =>
      api.keepSelectedCandidate(selectedPage!.id, candidateId, selectedPage!.storyboard_version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["chapter-production", activeChapterId] });
    },
  });

  const retractSelectedCandidate = useMutation({
    mutationFn: (pageId: string) => api.retractSelectedCandidate(pageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
      queryClient.invalidateQueries({ queryKey: ["chapter-production", activeChapterId] });
    },
  });

  const goNext = useMutation({
    mutationFn: () => api.nextPage(selectedPage!.id),
    onSuccess: (next) => {
      setSelectedPageId(next.id);
      setReferenceSelections({});
      setReferenceOverridePageId(null);
      queryClient.invalidateQueries({ queryKey: ["batches", next.id] });
    },
  });

  const createExport = useMutation({
    mutationFn: (type: "PNG" | "PDF" | "JSON") => api.createExport(activeChapterId!, type),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", id] }),
  });

  const activeJobs = (jobs.data ?? []).filter((job) => isActiveTaskStatus(job.status));
  const failedJobs = (jobs.data ?? []).filter((job) => job.status === "FAILED");
  const completedJobGroups = Object.entries(
    (jobs.data ?? []).filter((job) => !isActiveTaskStatus(job.status) && job.status !== "FAILED").reduce<Record<string, Job[]>>((groups, job) => {
      const date = new Date(job.created_at).toLocaleDateString("zh-CN");
      groups[date] = [...(groups[date] ?? []), job];
      return groups;
    }, {}),
  );

  function renderJob(job: Job, showProgress: boolean) {
    const terminal = ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(job.status);
    const resultUrl = publicUrl(job.result?.content_url ?? null);
    const showResult = () => {
      if (resultUrl && job.result) openPreview(resultUrl, job.result.label);
    };
    return <article className={`job-row status-${job.status.toLowerCase()} ${resultUrl ? "has-result" : ""}`} key={job.id} onClick={resultUrl ? showResult : undefined}>
      {!showArchivedJobs && terminal && <label className="job-select" onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`选择${jobLabels[job.job_type] ?? job.job_type}`} checked={selectedJobIds.includes(job.id)} onChange={(event) => setSelectedJobIds((values) => event.target.checked ? [...values, job.id] : values.filter((id) => id !== job.id))} /></label>}
      <div className="job-type"><span>{jobLabels[job.job_type] ?? job.job_type}</span><strong>{job.status}</strong></div>
      {showProgress && <div className="job-progress"><i><b style={{ width: `${job.progress}%` }} /></i><span>{job.progress}% · 尝试 {job.attempt_count}/{job.max_attempts}</span></div>}
      <div className="job-detail"><span>{job.workflow_node_id ? `节点 ${job.workflow_node_id}` : job.model_alias ? modelOptions.find((item) => item.alias === job.model_alias)?.name ?? job.model_alias : "系统任务"}</span><small>{job.duration_ms === null ? "尚未完成" : `耗时 ${(job.duration_ms / 1000).toFixed(1)} 秒`} · {job.estimated_cost === null ? "费用暂不可估算" : `预估 ¥${job.estimated_cost.toFixed(3)}`}</small>{job.error_message && <em>{job.error_code ? `${job.error_code} · ` : ""}{job.error_message}</em>}</div>
      <div className="job-actions" onClick={(event) => event.stopPropagation()}>{resultUrl && <button className="job-result-action" onClick={showResult}><Maximize2 size={12} />查看结果</button>}{!showArchivedJobs && isActiveTaskStatus(job.status) && <button onClick={() => cancelJob.mutate(job.id)}>取消</button>}{!showArchivedJobs && job.status === "FAILED" && <button onClick={() => retryJob.mutate(job.id)}><RotateCcw size={12} />重试</button>}{!showArchivedJobs && terminal && <button onClick={() => archiveJob.mutate(job.id)}><Archive size={12} />归档</button>}{showArchivedJobs && <button onClick={() => restoreJob.mutate(job.id)}><RotateCcw size={12} />恢复</button>}{showArchivedJobs && ["FAILED", "CANCELLED"].includes(job.status) && <button className="danger-action" onClick={() => { if (window.confirm("仅无候选、生成记录、工作流或任务依赖的失败任务可以彻底删除。继续吗？")) deleteJob.mutate(job.id); }}><Trash2 size={12} />彻底删除</button>}</div>
    </article>;
  }

  if (project.isLoading || !draft) {
    return <AppShell><div className="full-loading"><LoaderCircle className="spin" />加载项目工作区…</div></AppShell>;
  }
  if (project.isError) {
    return <AppShell><div className="full-loading error"><CircleAlert />项目无法打开</div></AppShell>;
  }

  return (
    <AppShell>
      <header className="workspace-topbar">
        <div className="workspace-crumb"><button className="project-nav-toggle" aria-expanded={navOpen} aria-label={navOpen ? "关闭项目导航" : "打开项目导航"} onClick={() => setNavOpen(!navOpen)}>{navOpen ? <X size={17} /> : <Menu size={17} />}</button><Link href="/"><ArrowLeft size={17} />项目</Link><i /><span>{draft.name}</span></div>
        <div className="workspace-status"><span><i />项目工作区</span><Link className="button outline compact" href={projectPath("workflow")}><Workflow size={15} />在工作流中查看</Link><Link className="button ink compact" href={projectPath("settings")}><Settings size={15} />项目设置</Link></div>
      </header>

      <div className="workspace-layout" style={{ "--workspace-sidebar-width": `${sidebarWidth}px` } as CSSProperties}>
        <button className={navOpen ? "workspace-nav-backdrop show" : "workspace-nav-backdrop"} onClick={() => setNavOpen(false)} aria-label="关闭项目导航" />
        <aside className={navOpen ? "workspace-left open" : "workspace-left"}>
          <button type="button" className="workspace-resizer" aria-label="拖动调整项目侧边栏宽度" onPointerDown={beginSidebarResize} />
          <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{draft.name}</h1><p>{needsChapters ? `${chapters.data?.length ?? 0} 章` : "漫画生产工作区"}{needsPages ? ` · ${pages.data?.length ?? 0} 页已规划` : ""}</p></div>
          <nav className="workspace-steps">
            {navigationItems.map(([target, label, , index, Icon]) => <Link scroll={false} key={target} href={projectPath(target)} className={section === target ? "active" : ""} aria-current={section === target ? "page" : undefined} onClick={rememberWorkspaceScroll}><Icon size={17} /><span>{label}</span><i>{index}</i></Link>)}
            <span className="workspace-nav-divider" />
            <Link href={projectPath("workflow")} onClick={() => setNavOpen(false)}><Workflow size={17} /><span>流程编排</span><i>FL</i></Link>
            <Link href={projectPath("settings")} onClick={() => setNavOpen(false)}><Settings size={17} /><span>项目设置</span><i>ST</i></Link>
          </nav>
        </aside>

        <section className="workspace-canvas">
          {section === "source" && (
            <SourceSection
              chapters={chapters}
              script={script}
              activeChapterId={activeChapterId}
              setSelectedChapterId={setSelectedChapterId}
              source={source}
            />
          )}
          {section === "assets" && (
            <AssetsSection
              id={id}
              assetView={assetView}
              draft={draft}
              assets={assets}
              characters={characters}
              outfits={outfits}
              modelOptions={modelOptions}
              activeDrawModel={activeDrawModel}
              setDrawModel={setDrawModel}
              openPreview={openPreview}
              rememberWorkspaceScroll={rememberWorkspaceScroll}
              workspace={assetsWorkspace}
            />
          )}
          {section === "script" && (
            <ScriptSection
              chapters={chapters}
              script={script}
              characters={characters}
              outfits={outfits}
              activeChapterId={activeChapterId}
              setSelectedChapterId={setSelectedChapterId}
              parseChapter={source.parseChapter}
              assignOutfit={assignOutfit}
            />
          )}
          {section === "storyboard" && (
            <StoryboardSection
              chapters={chapters}
              pages={pages}
              characters={characters}
              outfits={outfits}
              activeChapterId={activeChapterId}
              setSelectedChapterId={setSelectedChapterId}
              replanPage={replanPage}
              projectPath={projectPath}
              initialPageId={searchParams.get("page")}
              focusCharacterId={searchParams.get("character")}
            />
          )}
          {section === "generate" && (
            <div className="generate-workbench">
              <header className="canvas-header"><div><span>DRAW / 单页抽卡</span><h2>{selectedPage ? `第 ${selectedPage.page_number} 页候选` : "选择一页开始"}</h2></div><small>每次只生成 1 页</small></header>
              {selectedPage ? !generateWorkbenchReady ? <div className="generate-skeleton" role="status" aria-label="正在载入生成工作台"><LoaderCircle className="spin" size={22} /><span>正在载入生成工作台…</span></div> : <>
                {selectedPageStructureIssue && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>当前页暂不能生成</strong><p>{selectedPageStructureIssue}</p></div><Link className="button outline compact" href={projectPath("script")}>前往漫画剧本</Link></div>}
                {selectedPage.continuity_status === "NEEDS_REVIEW" && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>剧本或分镜已修改</strong><p>历史候选仍然保留，但可能不再对应当前脚本。建议重新抽卡并执行连续性检查。</p></div><Link className="button outline compact" href={projectPath("storyboard")}>检查分镜</Link></div>}
                {selectedWorkbenchCandidate && ["STALE", "LEGACY_UNKNOWN"].includes(selectedWorkbenchCandidate.version_state) && <div className="stale-candidate-banner"><div><span>版本需要决定</span><strong>旧候选基于 {selectedWorkbenchCandidate.based_on_storyboard_version ? `V${selectedWorkbenchCandidate.based_on_storyboard_version}` : "未知版本"}，当前分镜为 V{selectedPage.storyboard_version}</strong><p>旧图可以继续查看，但必须确认版本并重新完成视觉检查后，才能进入下一页或导出。</p></div><div><button disabled={keepSelectedCandidate.isPending} onClick={() => keepSelectedCandidate.mutate(selectedWorkbenchCandidate.id)}><Check size={14} />沿用并重新检查</button><button className="primary" disabled={generate.isPending || !pageReadiness.data?.ready || !generationReferenceReady || isViewingHistoricalBatch} onClick={() => generate.mutate()}><Sparkles size={14} />{isViewingHistoricalBatch ? "先切回最新批次" : `按当前 V${selectedPage.storyboard_version} 重新生成`}</button></div></div>}
                <div className="draw-toolbar">
                  <div className="page-picker">{pages.data?.map((page) => <button key={page.id} className={selectedPage.id === page.id ? "active" : ""} onClick={() => { setSelectedPageId(page.id); setViewedBatchId(null); setReviewCandidateId(null); setReferenceSelections({}); setReferenceOverridePageId(null); }}>{page.page_number}</button>)}</div>
                  <div className="batch-toolbar-actions">
                    {viewedBatch && <div className="batch-switcher" aria-label="切换生成批次">
                      <button type="button" title="查看上一批次" disabled={!previousBatch} onClick={() => { setViewedBatchId(previousBatch?.id ?? null); setReviewCandidateId(null); }}><ArrowLeft size={13} />上一批</button>
                      <label><span>查看批次</span><select aria-label="选择要查看的生成批次" value={viewedBatch.id} onChange={(event) => { setViewedBatchId(event.target.value); setReviewCandidateId(null); }}>{[...orderedPageBatches].reverse().map((batch) => <option key={batch.id} value={batch.id}>批次 {batch.ordinal}{batch.id === latestBatch?.id ? " · 最新" : ""}</option>)}</select></label>
                      <button type="button" title="查看下一批次" disabled={!nextBatch} onClick={() => { setViewedBatchId(nextBatch?.id ?? null); setReviewCandidateId(null); }}>下一批<ArrowRight size={13} /></button>
                    </div>}
                    <button className="button ghost compact" disabled={startBatch.isPending || Boolean(selectedPageStructureIssue) || !pageReadiness.data?.ready} onClick={() => startBatch.mutate()}><Plus size={14} />新批次</button>
                  </div>
                </div>
                <div className="draw-context"><div><span>PAGE LOAD</span><strong>{selectedPage.estimated_text_chars} 字</strong><small>{selectedPage.panel_count} 格 / {selectedPage.estimated_bubbles} 气泡</small></div><p>{selectedPage.source_coverage.ranges?.map((item) => item.text).join("").slice(0, 180)}</p></div>
                <ProductionReadiness projectId={id} readiness={pageReadiness.data} loading={pageReadiness.isLoading} error={pageReadiness.error} targetDialogues={targetDialogues} />
                <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次页面生成模型（仅显示支持图片编辑的已启用模型）" />
                <section className="generation-reference-check">
                  <header><div><span>CAST & REFERENCES</span><strong>自动继承已确认的人物与服装参考</strong></div><button type="button" className="reference-override-toggle" onClick={() => setReferenceOverridePageId(referenceOverrideOpen ? null : selectedPage.id)}><Pencil size={11} />{referenceOverrideOpen ? "收起选择" : "本页更换"}</button></header>
                  {generationReferenceReady && !referenceOverrideOpen && <div className="reference-inheritance-summary">{visibleCharacterIds.map((characterId) => {
                    const character = characters.data?.find((item) => item.id === characterId);
                    const selection = effectiveReferenceSelections[characterId];
                    const characterAsset = assets.data?.find((item) => item.id === selection?.character_asset_id);
                    const outfit = outfits.data?.find((item) => item.id === selection?.outfit_id);
                    const outfitAsset = assets.data?.find((item) => item.id === selection?.outfit_asset_id);
                    return <article key={characterId}><Check size={14} /><div><strong>{character?.primary_name ?? characterId}{outfit ? ` · ${outfit.name}` : ""}</strong><span>已继承：{characterAsset ? assetName(characterAsset) : "人物主参考"}{outfitAsset ? ` ＋ ${assetName(outfitAsset)}` : ""}</span></div></article>;
                  })}</div>}
                  {generationStoryboard.isLoading ? <p className="reference-check-loading"><LoaderCircle className="spin" size={15} />正在读取当前分镜…</p> : (referenceOverrideOpen || !generationReferenceReady) && <div className="reference-check-grid">
                    {visibleCharacterIds.map((characterId) => {
                      const character = characters.data?.find((item) => item.id === characterId);
                      const selection = effectiveReferenceSelections[characterId];
                      const outfit = outfits.data?.find((item) => item.id === selection?.outfit_id);
                      return <article key={characterId}><div><strong>{character?.primary_name ?? characterId}</strong><span>{outfit ? `穿着：${outfit.name}` : "分镜未指定服装"}</span></div><label><span>人物参考图</span><select value={selection?.character_asset_id ?? ""} onChange={(event) => setReferenceSelections((values) => ({ ...values, [characterId]: { ...(effectiveReferenceSelections[characterId] ?? { outfit_id: null, outfit_asset_id: null }), character_asset_id: event.target.value || null } }))}><option value="">请选择人物参考</option>{character?.references.map((reference, referenceIndex) => { const asset = assets.data?.find((item) => item.id === reference.asset_id); return <option value={reference.asset_id} key={reference.id}>{character.primary_name} · {reference.is_canonical ? "主参考" : `人物参考 ${String(referenceIndex + 1).padStart(2, "0")}`} · {asset?.display_name ?? asset?.original_name ?? reference.asset_id} · {reference.asset_id.slice(0, 8)}</option>; })}</select></label>{outfit && <label><span>该服装参考图</span><select value={selection?.outfit_asset_id ?? ""} onChange={(event) => setReferenceSelections((values) => ({ ...values, [characterId]: { ...effectiveReferenceSelections[characterId], outfit_asset_id: event.target.value || null } }))}><option value="">请选择服装参考</option>{outfit.reference_asset_ids.map((assetId, assetIndex) => <option value={assetId} key={assetId}>{outfit.name} · 服装参考 {String(assetIndex + 1).padStart(2, "0")} · {assets.data?.find((item) => item.id === assetId)?.original_name ?? assetId}</option>)}</select></label>}</article>;
                    })}
                    {!visibleCharacterIds.length && <p className="reference-check-empty">当前分镜没有入镜人物，将只按场景、动作和风格生成。</p>}
                  </div>}
                  {!generationReferenceReady && <p className="reference-check-warning"><CircleAlert size={13} />有角色缺少可用参考图，请先到“参考资产”绑定；分镜指定服装时也必须选择对应服装图。</p>}
                </section>
                <div className="generation-bar"><div className="generation-options"><div><span>正式模型</span><strong>{modelOptions.find((item) => item.alias === activeDrawModel)?.name ?? "尚未选择"}</strong></div><div><span>本次规格</span><strong>1K · 彩色 · 1 个候选</strong></div></div><button className="button ink generate-one" disabled={generate.isPending || Boolean(selectedPageGenerationIssue) || !pageReadiness.data?.ready || !generationReferenceReady || isViewingHistoricalBatch} onClick={() => generate.mutate()}>{generate.isPending ? <LoaderCircle className="spin" size={17} /> : <Star size={17} />}{generate.isPending ? "正在加入 1 个正式任务" : isViewingHistoricalBatch ? "先切回最新批次再生成" : selectedPageStructureIssue ? "请先补全剧本与分镜" : !activeDrawModel ? "先选择图片模型" : !pageReadiness.data?.ready ? "先完成页面生产准备" : !generationReferenceReady ? "先补齐人物与服装参考" : "生成 1 个 1K 彩色候选"}</button></div>
                {(generate.isError || startBatch.isError) && <p className="form-error"><CircleAlert size={14} />{(generate.error ?? startBatch.error)?.message}</p>}
                <div className="batch-heading"><div><span>{isViewingHistoricalBatch ? "HISTORY / 历史批次" : "BATCH / 当前批次"}</span><strong>{viewedBatch ? `批次 ${viewedBatch.ordinal}` : "尚未开始批次"}</strong></div><small>{isViewingHistoricalBatch ? `正在查看历史结果 · 共 ${orderedPageBatches.length} 个批次` : "每个候选记录实际供应商与模型 · 收藏不等于采用"}</small></div>
                <div className="candidate-grid">{!candidates.data && <article className="candidate-card" aria-hidden="true"><CandidateArtwork contentUrl={null} label="候选加载中" eager /></article>}{candidates.data?.map((candidate, candidateIndex) => <article className={`${candidate.is_selected ? "candidate-card selected" : "candidate-card"} version-${candidate.version_state.toLowerCase()}`} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`候选 ${candidate.ordinal}`} eager={candidateIndex === 0} onOpen={(url, label) => setPreviewImage({ url, label })} /><div className="candidate-meta"><span>候选 {String(candidate.ordinal).padStart(2, "0")}</span><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><small>{candidate.resolution} · {candidate.status}</small><em>{candidate.based_on_storyboard_version ? `生成依据 V${candidate.based_on_storyboard_version}` : "生成版本未知"} · {candidate.version_state}</em></div><div className="candidate-actions"><button className={candidate.is_favorite ? "favorited" : ""} onClick={() => favorite.mutate({ candidateId: candidate.id, value: !candidate.is_favorite })}><Heart size={14} fill={candidate.is_favorite ? "currentColor" : "none"} />收藏</button><button disabled={!candidate.asset_id || candidate.is_selected} onClick={() => { if (window.confirm("请确认页面文字已人工校对。暂选后还需要完成视觉检查，才能进入下一页或导出。是否继续？")) selectCandidate.mutate({ candidateId: candidate.id, manualTextConfirmed: true, acceptStale: candidate.version_state !== "CURRENT" }); }}><Check size={14} />{candidate.is_selected ? "已暂选" : candidate.version_state === "CURRENT" ? "人工校对并暂选" : "确认旧版本并暂选"}</button><button className={reviewCandidateId === candidate.id ? "reviewing" : ""} disabled={!candidate.asset_id || inspectCandidate.isPending} onClick={() => { setReviewCandidateId(candidate.id); inspectCandidate.mutate(candidate.id); }}><CircleAlert size={14} />视觉检查</button>{candidate.asset_id && candidate.resolution === "1K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "2K" })}>升至 2K</button>}{candidate.asset_id && candidate.resolution !== "4K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "4K" })}>升至 4K</button>}<button className="danger-action" disabled={candidate.is_selected} onClick={() => { if (window.confirm("删除这个候选？收藏状态也会一并移除。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={14} />删除</button></div></article>)}</div>
                {reviewCandidateId && <section className="inspection-panel">
                  <header><div><span>AI QUALITY CHECK</span><strong>候选视觉检查</strong><small>检查说话人归属、角色、服装、道具和连续性；文字由人工校对。</small></div><button onClick={() => setReviewCandidateId(null)}>关闭</button></header>
                  {!latestInspections.length ? <div className="inspection-wait"><LoaderCircle className={reviewJob && !["COMPLETED", "FAILED"].includes(reviewJob.status) ? "spin" : ""} size={18} /><span>{reviewJob ? `检查任务 ${reviewJob.status} · ${reviewJob.progress}%` : "正在读取检查结果"}</span></div> : <div className="inspection-results">{latestInspections.map((inspection) => {
                    const passed = ["PASS", "ACCEPTABLE", "MATCH"].includes(inspection.outcome);
                    const repairType = recommendedRepairType(inspection.category);
                    const bubbleDiffs = inspectionBubbleDiffs(inspection.details);
                    return <article className={passed ? "passed" : "failed"} key={inspection.id}>
                      <div><span>{inspectionLabels[inspection.category] ?? inspection.category}</span><strong>{inspection.outcome}</strong><em>{inspection.score === null ? "—" : `${Math.round(inspection.score * 100)}%`}</em></div>
                      <p>{inspectionSummary(inspection.details)}</p>
                      {bubbleDiffs.length > 0 && <div className="bubble-diff-list">{bubbleDiffs.map((diff, index) => <div key={`${inspection.id}:${index}`}><strong>气泡 {String(diff.balloon_index ?? index + 1).padStart(2, "0")}</strong><span>目标：{String(diff.target_text ?? "")}</span><span>识别：{String(diff.recognized_text ?? "")}</span><em>{typeof diff.similarity === "number" ? `${Math.round(diff.similarity * 100)}%` : "—"}</em></div>)}</div>}
                      {!passed && inspection.category !== "TEXT" && <button disabled={repairCandidate.isPending} onClick={() => repairCandidate.mutate(inspection)}><Sparkles size={13} />修复{repairTypeLabels[repairType]}</button>}
                      {!passed && inspection.category === "TEXT" && <span className="manual-review-hint">请人工校对；确认后可直接采用</span>}
                    </article>;
                  })}</div>}
                  {(inspectCandidate.isError || repairCandidate.isError || upscaleCandidate.isError) && <p className="form-error"><CircleAlert size={14} />{(inspectCandidate.error ?? repairCandidate.error ?? upscaleCandidate.error)?.message}</p>}
                </section>}
                <section className={`production-gate ${pageProduction?.ready ? "ready" : "blocked"}`}>
                  <header>
                    <div><span>PRODUCTION GATE / 页面生产门禁</span><strong>{pageProduction?.ready ? "当前页已通过，可以进入下一页" : "当前页尚未生产通过"}</strong></div>
                    <em>{pageProduction?.ready ? "READY" : pageProduction?.state ?? "LOADING"}</em>
                  </header>
                  <div className="production-gate-steps">
                    <span className={selectedWorkbenchCandidate ? "done" : ""}><Check size={13} />人工校对并暂选</span>
                    <span className={selectedWorkbenchCandidate && !["STALE", "LEGACY_UNKNOWN"].includes(selectedWorkbenchCandidate.version_state) ? "done" : ""}><Check size={13} />确认当前分镜版本</span>
                    <span className={pageProduction?.ready ? "done" : ""}><Check size={13} />视觉检查通过</span>
                  </div>
                  {!pageProduction?.ready && <p><CircleAlert size={14} />{productionBlocker?.message ?? "正在读取当前页生产状态"}</p>}
                </section>
                {!candidates.data?.length && <div className="asset-empty"><ImagePlus size={25} /><strong>这个批次还没有候选</strong><p>完成生产准备并确认参考图后，使用本次选择的图片模型生成 1 张彩色页面。</p></div>}
                <div className="next-page-row"><span>{pageProduction?.ready ? "人工校对、版本确认和视觉检查均已通过" : productionBlocker?.message ?? "完成页面生产门禁后才能继续"}</span><div>{pageProduction?.ready && <a className="button ghost compact" href={api.selectedPagePngUrl(selectedPage.id)!}><Download size={14} />单页 PNG</a>}<button className="button outline" disabled={!pageProduction?.ready || goNext.isPending} onClick={() => goNext.mutate()}>生成下一页 <ArrowRight size={15} /></button></div></div>
              </> : pages.isLoading || pages.data === undefined ? null : <div className="asset-empty tall"><Sparkles size={28} /><strong>没有可抽卡页面</strong><p>先完成动态分页。</p></div>}
            </div>
          )}

          {section === "library" && (
            <>
              <header className="canvas-header"><div><span>LIBRARY / 批次素材库</span><h2>保存每一次值得比较的结果</h2></div><small>{library.data?.total_candidates ?? 0} 个候选</small></header>
              <div className="library-toolbar">
                <div className="library-filter-grid">
                  <select className={libraryChapter ? "filter-active" : ""} aria-label="按章节筛选素材" value={libraryChapter} onChange={(event) => { setLibraryChapter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}>
                    <option value="">全部章节</option>
                    {chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>第 {chapter.ordinal} 章 · {chapter.title}</option>)}
                  </select>
                  <button className={favoriteOnly ? "active" : ""} onClick={() => { setFavoriteOnly(!favoriteOnly); setLibraryCursor(""); setLibraryHistory([]); }}><Heart size={14} />只看收藏（{library.data?.favorite_count ?? 0}）</button>
                  <select aria-label="按角色筛选素材" value={libraryCharacter} onChange={(event) => { setLibraryCharacter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部角色</option>{characters.data?.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select>
                  <select aria-label="按生成类型筛选素材" value={libraryKind} onChange={(event) => { setLibraryKind(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部类型</option>{Object.entries(generationKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                  <select aria-label="按模型筛选素材" value={libraryModel} onChange={(event) => { setLibraryModel(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部模型</option>{modelOptions.map((model) => <option key={model.alias} value={model.alias}>{model.name}</option>)}</select>
                  <select aria-label="按分辨率筛选素材" value={libraryResolution} onChange={(event) => { setLibraryResolution(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部清晰度</option>{(["1K", "2K", "4K"] as const).map((value) => <option key={value} value={value}>{value}</option>)}</select>
                  <label>从<input aria-label="素材开始日期" type="date" value={libraryDateFrom} onChange={(event) => { setLibraryDateFrom(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label>
                  <label>至<input aria-label="素材结束日期" type="date" value={libraryDateTo} onChange={(event) => { setLibraryDateTo(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label>
                  <button onClick={() => { setLibraryChapter(""); setFavoriteOnly(false); setLibraryCharacter(""); setLibraryKind(""); setLibraryModel(""); setLibraryResolution(""); setLibraryDateFrom(""); setLibraryDateTo(""); setLibraryCursor(""); setLibraryHistory([]); }}><RotateCcw size={13} />重置</button>
                </div>
              </div>
              <div className="library-groups">{library.data?.groups.map((group, groupIndex) => { const columns = Math.min(Math.max(group.candidates.length, 1), 3); return <section className="library-group" style={{ "--batch-columns": columns } as CSSProperties} key={group.batch.id}><header><div><span>BATCH {String(group.batch.ordinal).padStart(3, "0")}</span><strong>{generationKindLabels[group.batch.generation_kind] ?? group.batch.generation_kind}</strong></div><small>{new Date(group.batch.created_at).toLocaleString("zh-CN")} · {group.candidates.length} 张</small></header><div className="library-candidates">{group.candidates.map((candidate, candidateIndex) => <article className={candidate.is_selected ? "is-selected" : undefined} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`批次候选 ${candidate.ordinal}`} eager={groupIndex === 0 && candidateIndex === 0} onOpen={(url, label) => setPreviewImage({ url, label })} /><div><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><span>{candidate.resolution} · {candidate.status}</span>{candidate.is_favorite && <Heart size={13} fill="currentColor" />}{candidate.is_selected && <div className="library-selection-row"><em><Check size={12} />已暂选</em><button className="library-retract" disabled={!candidate.page_id || retractSelectedCandidate.isPending} onClick={() => { if (candidate.page_id && window.confirm("撤回暂选后，候选图片和生成记录仍会保留，后续页面将标记为待复查。是否继续？")) retractSelectedCandidate.mutate(candidate.page_id); }}><RotateCcw size={13} />撤回</button></div>}{!candidate.is_selected && <button className="library-delete" title="从素材库软删除" disabled={deleteCandidate.isPending} onClick={() => { if (window.confirm("从素材库隐藏这个候选？生成文件和任务记录会保留。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={12} /></button>}</div></article>)}</div></section>; })}</div>
              {!library.data?.groups.length && <div className="asset-empty tall"><LibraryBig size={28} /><strong>素材库还是空的</strong><p>从单页抽卡开始，所有候选都会按批次保留。</p></div>}
              {(libraryHistory.length > 0 || library.data?.next_cursor) && <div className="library-pagination"><button disabled={!libraryHistory.length} onClick={() => { const previous = libraryHistory.at(-1) ?? ""; setLibraryHistory((items) => items.slice(0, -1)); setLibraryCursor(previous); }}><ArrowLeft size={13} />上一页</button><span>每页最多 {library.data?.limit ?? 30} 个批次</span><button disabled={!library.data?.next_cursor} onClick={() => { setLibraryHistory((items) => [...items, libraryCursor]); setLibraryCursor(library.data?.next_cursor ?? ""); }}>下一页<ArrowRight size={13} /></button></div>}
              <div className={`export-desk ${chapterProduction.data?.ready ? "ready" : "blocked"}`}><div><span>EXPORT / 整章导出门禁</span><strong>{chapterProduction.data ? `${chapterProduction.data.ready_pages}/${chapterProduction.data.total_pages} 页生产通过` : "正在核对章节生产状态"}</strong><small>{chapterProduction.data?.ready ? "全部页面已完成校对、版本确认和视觉检查" : chapterProduction.data?.pages.find((page) => !page.ready)?.blockers[0]?.message ?? "章节没有可导出的页面"}</small></div><div>{(["PNG", "PDF", "JSON"] as const).map((type) => <button key={type} disabled={!chapterProduction.data?.ready || createExport.isPending} onClick={() => createExport.mutate(type)}><Download size={14} />{type}</button>)}</div></div>
              {chapterProduction.data && !chapterProduction.data.ready && <div className="chapter-production-blockers">{chapterProduction.data.pages.filter((page) => !page.ready).slice(0, 4).map((page) => { const pageNumber = pages.data?.find((item) => item.id === page.page_id)?.page_number; return <button key={page.page_id} onClick={() => { setSelectedPageId(page.page_id); rememberWorkspaceScroll(); router.push(projectPath("generate")); }}><span>第 {pageNumber ?? "—"} 页</span><strong>{page.blockers[0]?.message ?? "尚未通过"}</strong><ArrowRight size={14} /></button>; })}</div>}
              <div className="export-list">{exportsQuery.data?.map((item) => <a key={item.id} href={publicUrl(item.download_url)!}><FileImage size={14} /><span>{item.export_type} · {item.page_count} 页 · {formatBytes(item.byte_size)}</span><Download size={13} /></a>)}</div>
              {createExport.isError && <p className="form-error"><CircleAlert size={14} />{createExport.error.message}</p>}
            </>
          )}

          {section === "jobs" && (
            <>
              <header className="canvas-header"><div><span>JOBS / 任务中心</span><h2>每个生成任务都能看懂、取消和重试</h2></div><small>{jobs.data?.length ?? 0} 个任务</small></header>
              <div className="job-toolbar"><div><button className={!showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(false); setJobNotice(""); }}><ListTodo size={13} />近期任务</button><button className={showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(true); setJobNotice(""); }}><History size={13} />历史记录</button></div>{!showArchivedJobs && <div className="job-bulk-actions"><button disabled={!selectedJobIds.length || bulkArchiveJobs.isPending} onClick={() => bulkArchiveJobs.mutate()}><Archive size={13} />归档已选（{selectedJobIds.length}）</button><button disabled={archiveCompletedJobs.isPending} onClick={() => { if (window.confirm("将所有已完成、失败和已取消任务移入历史记录？生成候选与溯源信息不会删除。")) archiveCompletedJobs.mutate(); }}><Archive size={13} />归档全部终态</button></div>}</div>
              {jobNotice && <p className="job-notice"><CircleAlert size={13} />{jobNotice}</p>}
              <div className="job-sections">
                {activeJobs.length > 0 && <section><header><strong>正在运行</strong><small>{activeJobs.length} 条</small></header><div className="job-list">{activeJobs.map((job) => renderJob(job, true))}</div></section>}
                {failedJobs.length > 0 && <details open className="job-group failed"><summary><span>失败任务</span><strong>{failedJobs.length} 条 · 展开查看错误与重试</strong></summary><div className="job-list">{failedJobs.map((job) => renderJob(job, false))}</div></details>}
                {completedJobGroups.map(([date, groupedJobs]) => <details className="job-group" key={date}><summary><span>{date}</span><strong>{groupedJobs.length} 条已结束任务</strong></summary><div className="job-list">{groupedJobs.map((job) => renderJob(job, false))}</div></details>)}
              </div>
              {!jobs.data?.length && <div className="asset-empty tall">{showArchivedJobs ? <History size={28} /> : <ListTodo size={28} />}<strong>{showArchivedJobs ? "还没有历史任务" : "当前没有任务"}</strong><p>{showArchivedJobs ? "归档后的已结束任务会保留在这里，可随时恢复。" : "剧本解析、页面生成、检查和修复都会列在这里。"}</p></div>}
            </>
          )}
        </section>

      </div>

      {previewImage && <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={previewImage.label} onClick={() => setPreviewImage(null)}><button type="button" className="lightbox-close" aria-label="关闭大图" onClick={() => setPreviewImage(null)}><X size={20} /></button><div className="lightbox-shell" onClick={(event) => event.stopPropagation()}><div className="lightbox-toolbar"><strong>{previewImage.label}</strong><div><button type="button" aria-label="缩小图片" disabled={previewZoom <= .5} onClick={() => setPreviewZoom((value) => Math.max(.5, value - .25))}><ZoomOut size={17} /></button><button type="button" onClick={() => setPreviewZoom(1)}>{Math.round(previewZoom * 100)}%</button><button type="button" aria-label="放大图片" disabled={previewZoom >= 2.5} onClick={() => setPreviewZoom((value) => Math.min(2.5, value + .25))}><ZoomIn size={17} /></button></div></div><div className="lightbox-stage"><Image style={{ transform: `scale(${previewZoom})` }} src={previewImage.url} alt={previewImage.label} width={1600} height={1600} unoptimized /></div><span>使用 ＋/－ 调整到 50%–250%，点击背景或右上角关闭</span></div></div>}

      {queueDockHidden ? <button type="button" className="queue-dock-reveal" aria-label="显示任务中心快捷栏" title="显示任务中心快捷栏" onClick={() => toggleQueueDock(false)}><ListTodo size={16} /><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><ChevronUp size={13} /></button> : <><Link className="queue-dock" href={projectPath("jobs")}><div><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><strong>打开任务中心</strong><small>{jobs.data?.[0] ? `${jobLabels[jobs.data[0].job_type] ?? jobs.data[0].job_type} · ${jobs.data[0].status}` : section === "jobs" || section === "generate" ? "当前没有任务" : "查看生成、解析与检查进度"}</small></div>{(section === "jobs" || section === "generate") && <div><span>并发上限 {draft.default_concurrency}</span><i /><span>{queueStats.waiting} 等待</span><i /><span>{queueStats.failed} 失败</span></div>}</Link><button type="button" className="queue-dock-hide" aria-label="隐藏任务中心快捷栏" title="隐藏任务中心快捷栏" onClick={() => toggleQueueDock(true)}><ChevronDown size={15} /></button></>}
    </AppShell>
  );
}
