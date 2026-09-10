"use client";

import { AppShell } from "@/components/shell";
import { api, type ImageModelAlias, type PageCandidate, type Project, type Script } from "@/lib/api";
import { useLocalStorageValue, writeLocalStorage } from "@/lib/local-storage-store";
import { creatorVisibleModels } from "@/lib/model-visibility";
import { clampSidebarWidth, storedSidebarWidth } from "@/lib/workspace-layout";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

import { jobLabels } from "./project-workspace/labels";
import { AssetWorkspaceView, WorkspaceSection } from "./project-workspace/types";
import { useWorkspaceQueries } from "./project-workspace/use-workspace-queries";
import { useSourceWorkspace } from "./project-workspace/use-source-workspace";
import { useJobsWorkspace } from "./project-workspace/use-jobs-workspace";
import { useAssetsWorkspace } from "./project-workspace/use-assets-workspace";
import { useGenerationWorkspace } from "./project-workspace/use-generation-workspace";
import { useLibraryWorkspace } from "./project-workspace/use-library-workspace";
// One dynamic route ([section]) serves all seven sections; static imports
// shipped every section's code (storyboard pulled in the ~207KB canvas
// editor, generate the desk) to every route and executed only the current
// one — the Lighthouse unused-JS attribution on the storyboard route
// (810ms) and the route's LCP/TBT tail. Split per section so each route
// fetches only the code it renders; data hooks stay eager above, so
// queries start before the section chunk arrives.
const SourceSection = dynamic(
  () => import("./project-workspace/source-section").then((m) => m.SourceSection),
);
const AssetsSection = dynamic(
  () => import("./project-workspace/assets-section").then((m) => m.AssetsSection),
);
const ScriptSection = dynamic(
  () => import("./project-workspace/script-section").then((m) => m.ScriptSection),
);
const StoryboardSection = dynamic(
  () => import("./project-workspace/storyboard-section").then((m) => m.StoryboardSection),
);
const GenerateSection = dynamic(
  () => import("./project-workspace/generate-section").then((m) => m.GenerateSection),
);
const LibrarySection = dynamic(
  () => import("./project-workspace/library-section").then((m) => m.LibrarySection),
);
const JobsSection = dynamic(
  () => import("./project-workspace/jobs-section").then((m) => m.JobsSection),
);
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
  // 折叠/宽度/模型选择持久化走水合安全的 localStorage 外部存储：水合渲染
  // 采用默认值，真实存储值在水合后同步生效（见 lib/local-storage-store.ts），
  // 渲染期直读 localStorage 会造成水合不匹配。
  const navCollapsed = useLocalStorageValue("mangaflow.project-sidebar-collapsed", "false") === "true";
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(() => searchParams.get("page"));
  const storedDrawModel = useLocalStorageValue(`mangaflow.image-model.${id}`, "auto");
  const drawModel: ImageModelAlias | null = storedDrawModel !== "auto" ? storedDrawModel : null;
  const [previewImage, setPreviewImage] = useState<{ url: string; label: string; candidate?: PageCandidate } | null>(null);
  const [localEditCandidate, setLocalEditCandidate] = useState<PageCandidate | null>(null);
  // 拖拽期间走本地状态保证逐帧跟手；松手才写入存储（写存储会通知全部订阅者）。
  const [dragSidebarWidth, setDragSidebarWidth] = useState<number | null>(null);
  const storedSidebarWidthValue = useLocalStorageValue("mangaflow.project-sidebar-width", "");
  const sidebarWidth = dragSidebarWidth ?? storedSidebarWidth(storedSidebarWidthValue);

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
    needsCharacters,
    needsOutfits,
    needsPages,
    needsScript,
    needsSceneAssets,
  } = workspaceQueries;

  // Sidebar chrome is section-independent, so the summary must derive from
  // cached chapter data instead of which section happens to enable a query.
  const sidebarSummary = useMemo(() => {
    const list = chapters.data;
    if (!list?.length) return "漫画生产工作区";
    const pageCount = list.reduce((sum, chapter) => sum + chapter.page_count, 0);
    return `${list.length} 章 · ${pageCount} 页已规划`;
  }, [chapters.data]);

  const projectPath = (target: string) =>
    target === "assets" ? `/projects/${id}/assets/characters` : `/projects/${id}/${target}`;
  const setDrawModel = (model: ImageModelAlias) => {
    writeLocalStorage(`mangaflow.image-model.${id}`, model);
  };
  // Template B (audit §4.2): the left nav collapse persists independently of
  // the draggable width so a rail survives reloads without losing the width.
  const toggleNavCollapsed = (collapsed: boolean) => {
    writeLocalStorage("mangaflow.project-sidebar-collapsed", String(collapsed));
  };

  const source = useSourceWorkspace({
    id,
    projectPath,
    router,
    activeChapterId,
    setSelectedChapterId,
    setSelectedPageId,
  });

  const jobsWorkspace = useJobsWorkspace({ id });
  const {
    jobs,
    dockJobs,
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
    // 生产准备“去处理”深链带 ?character=/?outfit=：预选该角色/服装档案，
    // 用户不必再手动点一次。
    initialCharacterId: assetView === "characters" ? searchParams.get("character") : null,
    initialOutfitId: assetView === "outfits" ? searchParams.get("outfit") : null,
  });
  const generationWorkspace = useGenerationWorkspace({
    id,
    section,
    activeChapterId,
    models,
    pages,
    // The generation desk must always see the active-jobs view: after the
    // user visits 任务中心 → 历史记录, the toggle-scoped `jobs` query returns
    // only archived rows, so running PAGE_INSPECT jobs would vanish — the
    // inspection poll stops, the terminal invalidation never fires, and the
    // production gate silently stays blocked. Same rationale as the dock.
    jobs: dockJobs,
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
    // isPending 在成功瞬间翻回 false,而失效重拉尚未落地:第二次指定若仍从
    // 旧 scene.outfit_assignments 组装 payload,后端全量替换会把前一次指定
    // 悄悄回滚。乐观写入 script 缓存,让下一次 onChange 立刻建立在最新映射上。
    onMutate: async ({ sceneId, assignments }) => {
      const queryKey = ["script", activeChapterId];
      if (!activeChapterId) return {};
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<Script>(queryKey);
      if (previous) {
        queryClient.setQueryData<Script>(queryKey, {
          ...previous,
          scenes: previous.scenes.map((scene) => scene.id === sceneId
            ? { ...scene, outfit_assignments: { ...assignments } }
            : scene),
        });
      }
      return { queryKey, previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous && context.queryKey) {
        queryClient.setQueryData(context.queryKey, context.previous);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["script", activeChapterId] });
      // 后端会 bump storyboard_version 并把相关页标记待复查
      // （mark_storyboard_changed + mark_pages_for_review）；不失效 pages 和
      // generation-workbench 时，工作台仍持旧 storyboard_version，首次抽卡即 409。
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
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

  // 拖拽期间挂到 window 的监听必须能在组件卸载时解绑（中途路由离开），
  // pointercancel（触摸抬起/系统打断）与 pointerup 同样收尾。
  const sidebarDragRef = useRef<{ move: (event: PointerEvent) => void; stop: (event: PointerEvent) => void } | null>(null);
  useEffect(() => () => {
    const drag = sidebarDragRef.current;
    if (!drag) return;
    window.removeEventListener("pointermove", drag.move);
    window.removeEventListener("pointerup", drag.stop);
    window.removeEventListener("pointercancel", drag.stop);
  }, []);

  function beginSidebarResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    const move = (moveEvent: PointerEvent) => setDragSidebarWidth(clampSidebarWidth(startWidth + moveEvent.clientX - startX));
    const stop = (stopEvent: PointerEvent) => {
      const next = clampSidebarWidth(startWidth + stopEvent.clientX - startX);
      writeLocalStorage("mangaflow.project-sidebar-width", String(next));
      setDragSidebarWidth(null);
      sidebarDragRef.current = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
    sidebarDragRef.current = { move, stop };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
  }

  const workspaceRouteReady = !project.isLoading
    && !chapters.isLoading
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

  // 首载失败必须先于加载分支判断：rejected 状态下 data 为空、draft 为 null，
  // 若先判 isLoading/!draft 会永远停在「加载项目工作区…」，错误重试界面成为死代码。
  if (project.isError && !draft) {
    return <AppShell><div className="full-loading error">
      <CircleAlert />
      <div>
        <strong>项目无法打开</strong>
        <p>{project.error instanceof Error ? project.error.message : "读取项目失败，请稍后重试。"}</p>
        <div className="full-loading-actions">
          <button type="button" className="button outline compact" onClick={() => project.refetch()}>重试</button>
          <Link className="button ghost compact" href="/">返回项目列表</Link>
        </div>
      </div>
    </div></AppShell>;
  }
  if (project.isLoading || !draft) {
    return <AppShell><div className="full-loading"><LoaderCircle className="spin" />加载项目工作区…</div></AppShell>;
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
          summary={sidebarSummary}
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
              focusStyleId={assetView === "style" ? searchParams.get("style") : null}
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
              // URL ?page= 优先;否则落在当前选中的页(生成台/库的阻断行都
              // 会同步 selectedPageId)。不给兜底时,第 7 页的"检查分镜"会
              // 把用户送回第 1 页的分镜编辑器。
              initialPageId={searchParams.get("page") ?? selectedPageId ?? null}
              focusCharacterId={searchParams.get("character")}
            />
          )}
          {section === "generate" && (
            <GenerateSection
              id={id}
              chapters={chapters}
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
        latestJob={dockJobs.data?.[0]}
        latestJobLabel={dockJobs.data?.[0] ? jobLabels[dockJobs.data[0].job_type] ?? dockJobs.data[0].job_type : ""}
        section={section}
        concurrency={draft.default_concurrency}
        projectPath={projectPath}
      />
    </AppShell>
  );
}
