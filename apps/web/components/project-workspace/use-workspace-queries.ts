"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

import type { AssetWorkspaceView, WorkspaceSection } from "./types";

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
  const needsChapters = ["source", "script", "storyboard", "generate", "library"].includes(section);
  const needsCharacters = section === "assets"
    ? !["style", "scenes"].includes(assetView)
    : ["script", "storyboard", "generate", "library"].includes(section);
  const needsOutfits = section === "assets"
    ? ["outfits", "references"].includes(assetView)
    : ["script", "storyboard", "generate"].includes(section);
  const needsPages = ["storyboard", "generate"].includes(section);
  const needsScript = ["source", "script", "generate"].includes(section);
  const needsSceneAssets = section === "script" || section === "generate";

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id), staleTime: 30_000 });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 30_000 });
  const assets = useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id), enabled: ["assets", "generate"].includes(section) });
  const chapters = useQuery({ queryKey: ["chapters", id], queryFn: () => api.chapters(id), enabled: needsChapters });
  const characters = useQuery({ queryKey: ["characters", id], queryFn: () => api.characters(id), enabled: needsCharacters });
  const outfits = useQuery({ queryKey: ["outfits", id], queryFn: () => api.outfits(id), enabled: needsOutfits });
  const activeChapterId = selectedChapterId ?? chapters.data?.[0]?.id ?? null;
  const pages = useQuery({
    queryKey: ["pages", activeChapterId],
    queryFn: () => api.pages(activeChapterId!),
    enabled: needsPages && Boolean(activeChapterId),
  });
  const script = useQuery({
    queryKey: ["script", activeChapterId],
    queryFn: () => api.script(activeChapterId!),
    enabled: needsScript && Boolean(activeChapterId),
    refetchInterval: (query) => query.state.data?.status === "PROCESSING" ? 4000 : false,
  });
  const sceneAssets = useQuery({
    queryKey: ["scene-assets", id],
    queryFn: () => api.sceneAssets(id, { limit: 200 }),
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
    needsChapters,
    needsCharacters,
    needsOutfits,
    needsPages,
    needsScript,
    needsSceneAssets,
  };
}

export type WorkspaceQueries = ReturnType<typeof useWorkspaceQueries>;
