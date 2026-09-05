"use client";

import { useMutation, useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { getPageGenerationIssue, getPageStructureIssue } from "@/lib/generation-rules";
import { api, type CharacterPackageSummary, type ImageModelAlias, type InspectionResult, type Job } from "@/lib/api";
import { activePollInterval, hasActiveItem, isTerminalTaskStatus } from "@/lib/task-status";

import { recommendedRepairType } from "./display";
import {
  buildDefaultReferenceSelections,
  collectVisibleCharacterIds,
  isGenerationReferenceReady,
  mergeReferenceSelections,
  type PublishedPackageVersions,
  type ReferenceSelections,
} from "./reference-selection";
import type { WorkspaceSection } from "./types";
import type { WorkspaceQueries } from "./use-workspace-queries";

/**
 * Generate domain: batch/candidate states, the generate workbench queries and
 * every candidate action (generate, favorite, inspect, repair, upscale,
 * select, retract, next page). Polling rules and invalidation keys are kept
 * verbatim from the original monolith.
 */
export function useGenerationWorkspace({
  id,
  section,
  activeChapterId,
  models,
  pages,
  jobs,
  characters,
  outfits,
  selectedPageId,
  setSelectedPageId,
  setDraft,
  activeDrawModel,
  requireDrawModel,
}: {
  id: string;
  section: WorkspaceSection;
  activeChapterId: string | null;
  models: WorkspaceQueries["models"];
  pages: WorkspaceQueries["pages"];
  jobs: UseQueryResult<Job[], Error>;
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  selectedPageId: string | null;
  setSelectedPageId: (pageId: string | null) => void;
  setDraft: (draft: null) => void;
  activeDrawModel: ImageModelAlias | null;
  requireDrawModel: () => ImageModelAlias;
}) {
  const queryClient = useQueryClient();
  const [viewedBatchId, setViewedBatchId] = useState<string | null>(null);
  const [reviewCandidateId, setReviewCandidateId] = useState<string | null>(null);
  const [referenceSelections, setReferenceSelections] = useState<ReferenceSelections>({});
  const [referenceOverridePageId, setReferenceOverridePageId] = useState<string | null>(null);

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
  // Package summaries feed default inheritance (contract §8.1): characters
  // with an ACTIVE package + published version resolve their reference image
  // server-side from the version matrix, so no legacy asset id is sent.
  // Fail-closed: while the list is loading or failed, an unknown package list
  // must not fall back to legacy asset ids — the backend would still enter
  // package mode from the published pointer and 409 on a non-matrix asset.
  const characterPackages = useQuery({
    queryKey: ["character-packages", id],
    queryFn: () => api.characterPackagesAll(id),
    enabled: section === "generate",
  });
  const generationPackagesReady = !characterPackages.isLoading && !characterPackages.isError;
  // The workbench inserts tall blocks (readiness panel, reference check) whose
  // height is unknown until the workbench query lands; rendering them in stages
  // pushed the whole canvas down (measured CLS 0.477). Show one skeleton until
  // the workbench, batch, model and package data exist, then insert the canvas
  // at once.
  const generateWorkbenchReady =
    !workbench.isLoading && !pageBatches.isLoading && !models.isLoading && generationPackagesReady;
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
    refetchInterval: () => {
      const checking = (jobs.data ?? []).some((job) =>
        job.job_type === "PAGE_INSPECT"
        && job.target_id === reviewCandidateId
        && !isTerminalTaskStatus(job.status));
      return checking ? 2500 : false;
    },
  });
  const latestInspectJob = (jobs.data ?? []).find((job) =>
    job.job_type === "PAGE_INSPECT" && job.target_id === reviewCandidateId);
  const inspectJobTerminal = Boolean(
    latestInspectJob && isTerminalTaskStatus(latestInspectJob.status),
  );
  useEffect(() => {
    if (!inspectJobTerminal || !reviewCandidateId) return;
    queryClient.invalidateQueries({ queryKey: ["inspections", reviewCandidateId] });
  }, [latestInspectJob?.id, inspectJobTerminal, reviewCandidateId, queryClient]);

  const selectedPageStructureIssue = getPageStructureIssue(selectedPage);
  const selectedPageGenerationIssue = getPageGenerationIssue(selectedPage, activeDrawModel);
  const visibleCharacterIds = useMemo(
    () => collectVisibleCharacterIds(generationStoryboard.data?.panels ?? []),
    [generationStoryboard.data?.panels],
  );
  const publishedPackageVersions = useMemo<PublishedPackageVersions>(() => {
    const map: PublishedPackageVersions = {};
    for (const item of characterPackages.data ?? []) {
      if (item.status === "ACTIVE" && item.published_version_id) {
        map[item.character_id] = item.published_version_id;
      }
    }
    return map;
  }, [characterPackages.data]);
  // Every package regardless of status: contract §8.1 allows explicitly
  // selecting an ARCHIVED version, so the picker must mount even when the
  // package cannot serve default inheritance.
  const packageSummariesByCharacter = useMemo<Record<string, CharacterPackageSummary>>(() => {
    const map: Record<string, CharacterPackageSummary> = {};
    for (const item of characterPackages.data ?? []) {
      map[item.character_id] = item;
    }
    return map;
  }, [characterPackages.data]);
  const defaultReferenceSelections = useMemo(
    () => buildDefaultReferenceSelections(
      visibleCharacterIds,
      characters.data,
      outfits.data,
      generationStoryboard.data?.panels ?? [],
      publishedPackageVersions,
    ),
    [characters.data, generationStoryboard.data?.panels, outfits.data, publishedPackageVersions, visibleCharacterIds],
  );
  const effectiveReferenceSelections = useMemo(
    () => mergeReferenceSelections(defaultReferenceSelections, referenceSelections),
    [defaultReferenceSelections, referenceSelections],
  );
  const generationReferenceReady = isGenerationReferenceReady(
    effectiveReferenceSelections,
    visibleCharacterIds,
    outfits.data,
    publishedPackageVersions,
  );
  const referenceOverrideOpen = referenceOverridePageId === selectedPage?.id;
  const targetDialogues = useMemo(
    () => (generationStoryboard.data?.panels ?? []).flatMap((panel) => panel.dialogues.map((dialogue) => dialogue.target_text)).filter(Boolean),
    [generationStoryboard.data?.panels],
  );
  const inspectionsData = inspections.data;
  const latestInspections = useMemo(() => {
    const latest = new Map<string, InspectionResult>();
    const currentVersion = selectedPage?.storyboard_version;
    for (const item of inspectionsData ?? []) {
      if (
        currentVersion != null
        && item.storyboard_version != null
        && item.storyboard_version !== currentVersion
      ) {
        continue;
      }
      if (!latest.has(item.category)) latest.set(item.category, item);
    }
    return [...latest.values()];
  }, [inspectionsData, selectedPage?.storyboard_version]);
  const reviewCandidate = candidates.data?.find((item) => item.id === reviewCandidateId) ?? null;
  const reviewJob = jobs.data?.find((item) => item.target_id === reviewCandidateId && item.job_type === "PAGE_INSPECT") ?? null;
  const selectedWorkbenchCandidate = workbench.data?.selected_candidate ?? null;
  const productionBlocker = pageProduction?.blockers[0] ?? null;

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
      // Defense in depth for the fail-closed package gate: the UI keeps the
      // workbench skeleton/error up while the list is unknown, but a stale
      // click must not send a legacy reference payload either.
      if (characterPackages.isLoading) throw new Error("正在读取角色模型包，请稍候重试");
      if (characterPackages.isError) throw new Error("角色模型包状态无法确认，请重试后再生成");
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
    mutationFn: (inspection: NonNullable<typeof inspections.data>[number]) => api.repairCandidate(reviewCandidateId!, {
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

  // Adoption/inspection/navigation actions used to fail with no surface at
  // all: the buttons un-pended and the UI silently diverged from the server.
  // Mutations reset their error on the next submit, so an aggregate derived
  // value is enough for one shared, actionable message.
  // inspect/repair/upscale 已在 InspectionPanel 内展示，不重复聚合。
  const actionError =
    favorite.error ??
    deleteCandidate.error ??
    selectCandidate.error ??
    keepSelectedCandidate.error ??
    retractSelectedCandidate.error ??
    goNext.error ??
    null;

  return {
    viewedBatchId,
    setViewedBatchId,
    reviewCandidateId,
    setReviewCandidateId,
    referenceSelections,
    setReferenceSelections,
    referenceOverridePageId,
    setReferenceOverridePageId,
    characterPackages,
    generationPackagesReady,
    publishedPackageVersions,
    packageSummariesByCharacter,
    selectedPageEntry,
    selectedPage,
    workbench,
    pageBatches,
    candidates,
    inspections,
    generateWorkbenchReady,
    orderedPageBatches,
    latestBatch,
    viewedBatch,
    previousBatch,
    nextBatch,
    isViewingHistoricalBatch,
    generationStoryboard,
    pageReadiness,
    pageProduction,
    selectedPageStructureIssue,
    selectedPageGenerationIssue,
    visibleCharacterIds,
    effectiveReferenceSelections,
    generationReferenceReady,
    referenceOverrideOpen,
    targetDialogues,
    latestInspections,
    reviewCandidate,
    reviewJob,
    selectedWorkbenchCandidate,
    productionBlocker,
    startBatch,
    generate,
    actionError,
    favorite,
    deleteCandidate,
    inspectCandidate,
    repairCandidate,
    upscaleCandidate,
    selectCandidate,
    keepSelectedCandidate,
    retractSelectedCandidate,
    goNext,
  };
}

export type GenerationWorkspace = ReturnType<typeof useGenerationWorkspace>;
