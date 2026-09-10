"use client";

import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type Job } from "@/lib/api";
import { isTerminalTaskStatus } from "@/lib/task-status";

import type { AssetWorkspaceView, WorkspaceSection } from "./types";

/** 本章当前最近的 SOURCE_PARSE 任务（无则 null）。 */
export function chapterParseJob(
  jobs: Job[] | undefined,
  chapterId: string | null,
): Job | null {
  if (!chapterId) return null;
  return (
    (jobs ?? []).find(
      (job) => job.job_type === "SOURCE_PARSE" && job.target_id === chapterId,
    ) ?? null
  );
}

/** 剧本轮询间隔：仅在本章存在活跃 SOURCE_PARSE 任务时轮询。
 *
 * 后端剧本状态只会输出 NOT_CREATED/READY/INCOMPLETE，历史上挂在
 * status === "PROCESSING" 上的轮询从未启动过（#246-2）。
 */
export function scriptPollInterval(
  jobs: Job[] | undefined,
  chapterId: string | null,
): number | false {
  const parse = chapterParseJob(jobs, chapterId);
  return parse && !isTerminalTaskStatus(parse.status) ? 4000 : false;
}

/**
 * Single owner of the cross-section workspace queries and their enablement
 * rules. Section-local queries live in their own domain hooks; never copy
 * these query keys elsewhere.
 */
export function useWorkspaceQueries({
  id,
  section,
  assetView,
  selectedChapterId,
}: {
  id: string;
  section: WorkspaceSection;
  assetView: AssetWorkspaceView;
  selectedChapterId: string | null;
}) {
  const queryClient = useQueryClient();
  const needsCharacters = section === "assets"
    ? !["style", "scenes"].includes(assetView)
    : ["script", "storyboard", "generate", "library"].includes(section);
  const needsOutfits = section === "assets"
    ? ["outfits", "references"].includes(assetView)
    : ["script", "storyboard", "generate"].includes(section);
  // Library 章节生产门禁的阻塞行需要 pages 列表解析「第 N 页」；不启用时
  // 只能显示「第 — 页」。后端 production-readiness 载荷暂不含 page_number，
  // 在这里启用查询是最小改动。
  const needsPages = ["storyboard", "generate", "library"].includes(section);
  const needsScript = ["source", "script", "generate"].includes(section);
  const needsSceneAssets = section === "script" || section === "generate";

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id), staleTime: 30_000 });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 30_000 });
  const assets = useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id), enabled: ["assets", "generate"].includes(section) });
  // Chapters feed the sidebar summary on every section, so the copy cannot
  // flip between "N 章" and "漫画生产工作区" as the user navigates.
  const chapters = useQuery({ queryKey: ["chapters", id], queryFn: () => api.chapters(id), staleTime: 15_000 });
  const characters = useQuery({ queryKey: ["characters", id], queryFn: () => api.characters(id), enabled: needsCharacters });
  const outfits = useQuery({ queryKey: ["outfits", id], queryFn: () => api.outfits(id), enabled: needsOutfits });
  const activeChapterId = selectedChapterId ?? chapters.data?.[0]?.id ?? null;
  const pages = useQuery({
    queryKey: ["pages", activeChapterId],
    queryFn: () => api.pages(activeChapterId!),
    enabled: needsPages && Boolean(activeChapterId),
  });
  // #246-2: 剧本轮询原先挂在 status === "PROCESSING" 上，但后端剧本状态
  // 只会输出 NOT_CREATED/READY/INCOMPLETE（get_script 读 ScriptRevision，
  // story_parse worker 只写 READY/INCOMPLETE），PROCESSING 永不出现，轮询
  // 从未启动。改为跟随本章的活跃 SOURCE_PARSE 任务：复用队列坞的
  // ["jobs", id, false] 缓存键（同一缓存条目，不产生额外请求）。
  const parseJobs = useQuery({
    queryKey: ["jobs", id, false],
    queryFn: () => api.jobs(id, false),
    enabled: needsScript && Boolean(activeChapterId),
  });
  const chapterParse = chapterParseJob(parseJobs.data, activeChapterId);
  const script = useQuery({
    queryKey: ["script", activeChapterId],
    queryFn: () => api.script(activeChapterId!),
    enabled: needsScript && Boolean(activeChapterId),
    refetchInterval: scriptPollInterval(parseJobs.data, activeChapterId),
  });
  // 解析任务转为终态时补拉一次剧本：最后一次定时轮询可能早于 worker 的
  // 最终提交（与生成工作台 PAGE_INSPECT 终态补拉同一模式）。依赖只取原始
  // 值，避免每次渲染重建的派生对象触发 effect。
  const chapterParseId = chapterParse?.id ?? null;
  const chapterParseTerminal = chapterParse
    ? isTerminalTaskStatus(chapterParse.status)
    : false;
  const finishedParseJobRef = useRef<string | null>(null);
  useEffect(() => {
    if (!chapterParseId || !chapterParseTerminal) return;
    if (finishedParseJobRef.current === chapterParseId) return;
    finishedParseJobRef.current = chapterParseId;
    queryClient.invalidateQueries({ queryKey: ["script", activeChapterId] });
  }, [chapterParseId, chapterParseTerminal, activeChapterId, queryClient]);
  const sceneAssets = useQuery({
    queryKey: ["scene-assets", id],
    // include_deleted: the shared list feeds BOUND-asset lookups (scene
    // picker, generation inheritance) — archiving does not clear bindings,
    // so excluding soft-deleted rows made every designed "已归档" branch dead
    // code and rendered bound selects as unbound. New-binding option lists
    // filter deleted_at themselves (scene-picker activeAssets).
    queryFn: () => api.sceneAssetsAll(id, { include_deleted: true }),
    enabled: needsSceneAssets,
  });

  return {
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
  };
}

export type WorkspaceQueries = ReturnType<typeof useWorkspaceQueries>;
