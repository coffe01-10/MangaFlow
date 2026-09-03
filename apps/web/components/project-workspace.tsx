"use client";

import { AppShell } from "@/components/shell";
import { api, type ImageModelAlias, type PageCandidate, type Project } from "@/lib/api";
import { creatorVisibleModels } from "@/lib/model-visibility";
import { SIDEBAR_WIDTH_DEFAULT, clampSidebarWidth, storedSidebarWidth } from "@/lib/workspace-layout";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import { jobLabels } from "./project-workspace/labels";
import { AssetWorkspaceView, WorkspaceSection } from "./project-workspace/types";
import { useWorkspaceQueries } from "./project-workspace/use-workspace-queries";
import { useSourceWorkspace } from "./project-workspace/use-source-workspace";
import { useJobsWorkspace } from "./project-workspace/use-jobs-workspace";
import { useAssetsWorkspace } from "./project-workspace/use-assets-workspace";
import { useGenerationWorkspace } from "./project-workspace/use-generation-workspace";
import { useLibraryWorkspace } from "./project-workspace/use-library-workspace";
import { SourceSection } from "./project-workspace/source-section";
import { AssetsSection } from "./project-workspace/assets-section";
import { ScriptSection } from "./project-workspace/script-section";
import { StoryboardSection } from "./project-workspace/storyboard-section";
import { GenerateSection } from "./project-workspace/generate-section";
import { LibrarySection } from "./project-workspace/library-section";
import { JobsSection } from "./project-workspace/jobs-section";
import {
  ImageLightbox,
  QueueDock,
  WorkspaceSidebar,
  WorkspaceTopbar,
} from "./project-workspace/workspace-chrome";

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
  const [navCollapsed, setNavCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.localStorage.getItem("mangaflow.project-sidebar-collapsed") === "true";
  });
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(() => searchParams.get("page"));
  const [drawModel, setDrawModelState] = useState<ImageModelAlias | null>(() => {
    if (typeof window === "undefined") return null;
    const stored = window.localStorage.getItem(`mangaflow.image-model.${id}`);
    return stored && stored !== "auto" ? stored : null;
  });
  const [previewImage, setPreviewImage] = useState<{ url: string; label: string; candidate?: PageCandidate } | null>(null);
  const [localEditCandidate, setLocalEditCandidate] = useState<PageCandidate | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    if (typeof window === "undefined") return SIDEBAR_WIDTH_DEFAULT;
    return storedSidebarWidth(window.localStorage.getItem("mangaflow.project-sidebar-width"));
  });

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
    sceneAssets,
    activeChapterId,
    needsChapters,
    needsCharacters,
    needsOutfits,
    needsPages,
    needsScript,
    needsSceneAssets,
  } = workspaceQueries;

  const projectPath = (target: string) =>
    target === "assets" ? `/projects/${id}/assets/characters` : `/projects/${id}/${target}`;
  const setDrawModel = (model: ImageModelAlias) => {
    setDrawModelState(model);
    window.localStorage.setItem(`mangaflow.image-model.${id}`, model);
  };
  // Template B (audit §4.2): the left nav collapse persists independently of
  // the draggable width so a rail survives reloads without losing the width.
  const toggleNavCollapsed = (collapsed: boolean) => {
    setNavCollapsed(collapsed);
    window.localStorage.setItem("mangaflow.project-sidebar-collapsed", String(collapsed));
  };

  const source = useSourceWorkspace({
    id,
    projectPath,
    router,
    activeChapterId,
    setSelectedChapterId,
    setSelectedPageId,
  });

  const jobsWorkspace = useJobsWorkspace({ id, section });
  const {
    jobs,
    queueStats,
  } = jobsWorkspace;

  const libraryWorkspace = useLibraryWorkspace({ id, section, activeChapterId });
  const { library } = libraryWorkspace;

  const openPreview = (url: string, label: string) => {
    setPreviewImage({ url, label });
  };

  // Generate desk previews carry the candidate so the lightbox can offer
  // 局部修改 entry into the V02-43B local edit shell.
  const openPreviewWithCandidate = (url: string, label: string, candidate?: PageCandidate) => {
    setPreviewImage({ url, label, candidate });
  };

  const draft = localDraft ?? project.data ?? null;
  const catalogModelOptions = useMemo(
    () => {
      const available = (models.data ?? [])
        .filter((model) => model.model_type === "IMAGE" && model.operations.includes("image_edit"));
      return available.map((model) => ({
        alias: model.logical_alias,
        name: model.display_name,
        id: model.model_id,
        provider: model.provider,
      }));
    },
    [models.data],
  );
  const modelOptions = useMemo(
    () => creatorVisibleModels(
      (models.data ?? []).filter((model) => model.model_type === "IMAGE" && model.operations.includes("image_edit")),
      { logicalAliases: [drawModel] },
    ).map((model) => ({
      alias: model.logical_alias,
      name: model.display_name,
      id: model.model_id,
      provider: model.provider,
    })),
    [drawModel, models.data],
  );
  const activeDrawModel = drawModel && modelOptions.some((option) => option.alias === drawModel)
    ? drawModel
    : null;
  function rememberWorkspaceScroll() {
    window.sessionStorage.setItem(`mangaflow.workspace-scroll.${id}`, String(window.scrollY));
    setNavOpen(false);
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
  const generationWorkspace = useGenerationWorkspace({
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
  });

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

  function beginSidebarResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    const move = (moveEvent: PointerEvent) => setSidebarWidth(clampSidebarWidth(startWidth + moveEvent.clientX - startX));
    const stop = (stopEvent: PointerEvent) => {
      const next = clampSidebarWidth(startWidth + stopEvent.clientX - startX);
      setSidebarWidth(next);
      window.localStorage.setItem("mangaflow.project-sidebar-width", String(next));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  const workspaceRouteReady = !project.isLoading
    && (!needsChapters || !chapters.isLoading)
    && (!needsCharacters || !characters.isLoading)
    && (!needsOutfits || !outfits.isLoading)
    && (!needsPages || !pages.isLoading)
    && (!needsScript || !script.isLoading)
    && (!needsSceneAssets || !sceneAssets.isLoading)
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

  if (project.isLoading || !draft) {
    return <AppShell><div className="full-loading"><LoaderCircle className="spin" />加载项目工作区…</div></AppShell>;
  }
  if (project.isError) {
    return <AppShell><div className="full-loading error"><CircleAlert />项目无法打开</div></AppShell>;
  }

  return (
    <AppShell>
      <WorkspaceTopbar
        navOpen={navOpen}
        setNavOpen={setNavOpen}
        navCollapsed={navCollapsed}
        setNavCollapsed={toggleNavCollapsed}
        projectName={draft.name}
        projectPath={projectPath}
      />

      <div
        className={navCollapsed ? "workspace-layout rail-left" : "workspace-layout"}
        style={{ "--workspace-sidebar-width": `${sidebarWidth}px` } as CSSProperties}
      >
        <WorkspaceSidebar
          navOpen={navOpen}
          navCollapsed={navCollapsed}
          setNavOpen={setNavOpen}
          projectName={draft.name}
          chapterCount={chapters.data?.length ?? 0}
          needsChapters={needsChapters}
          pageCount={pages.data?.length ?? 0}
          needsPages={needsPages}
          section={section}
          projectPath={projectPath}
          rememberWorkspaceScroll={rememberWorkspaceScroll}
          onSidebarResize={beginSidebarResize}
        />

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
              projectId={id}
              chapters={chapters}
              script={script}
              characters={characters}
              outfits={outfits}
              sceneAssets={sceneAssets}
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
            <GenerateSection
              id={id}
              pages={pages}
              assets={assets}
              characters={characters}
              outfits={outfits}
              script={script}
              sceneAssets={sceneAssets}
              modelOptions={modelOptions}
              catalogModelOptions={catalogModelOptions}
              activeDrawModel={activeDrawModel}
              setDrawModel={setDrawModel}
              openPreview={openPreviewWithCandidate}
              projectPath={projectPath}
              setSelectedPageId={setSelectedPageId}
              workspace={generationWorkspace}
              models={models}
              localEditCandidate={localEditCandidate}
              openLocalEdit={setLocalEditCandidate}
              closeLocalEdit={() => setLocalEditCandidate(null)}
            />
          )}
          {section === "library" && (
            <LibrarySection
              pages={pages}
              chapters={chapters}
              characters={characters}
              modelOptions={catalogModelOptions}
              openPreview={openPreview}
              router={router}
              projectPath={projectPath}
              rememberWorkspaceScroll={rememberWorkspaceScroll}
              setSelectedPageId={setSelectedPageId}
              libraryWorkspace={libraryWorkspace}
              generation={generationWorkspace}
            />
          )}
          {section === "jobs" && (
            <JobsSection
              jobs={jobs}
              workspace={jobsWorkspace}
              modelOptions={catalogModelOptions}
              openPreview={openPreview}
            />
          )}
        </section>

      </div>

      {previewImage && <ImageLightbox
        preview={previewImage}
        onClose={() => setPreviewImage(null)}
        onLocalEdit={previewImage.candidate ? (candidate) => {
          setPreviewImage(null);
          setLocalEditCandidate(candidate);
        } : undefined}
      />}

      <QueueDock
        queueStats={queueStats}
        latestJob={jobs.data?.[0]}
        latestJobLabel={jobs.data?.[0] ? jobLabels[jobs.data[0].job_type] ?? jobs.data[0].job_type : ""}
        section={section}
        concurrency={draft.default_concurrency}
        projectPath={projectPath}
      />
    </AppShell>
  );
}
