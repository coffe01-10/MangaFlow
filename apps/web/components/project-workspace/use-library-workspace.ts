"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type ImageModelAlias, type Resolution } from "@/lib/api";

import type { WorkspaceSection } from "./types";

/**
 * Library domain: batch archive filters with cursor pagination, the library
 * and export queries, chapter production gate data and export creation.
 */
export function useLibraryWorkspace({
  id,
  section,
  activeChapterId,
}: {
  id: string;
  section: WorkspaceSection;
  activeChapterId: string | null;
}) {
  const queryClient = useQueryClient();
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
  const exportsQuery = useQuery({ queryKey: ["exports", id], queryFn: () => api.exports(id), enabled: section === "library" });
  const chapterProduction = useQuery({
    queryKey: ["chapter-production", activeChapterId],
    queryFn: () => api.chapterProductionReadiness(activeChapterId!),
    enabled: section === "library" && Boolean(activeChapterId),
  });

  const createExport = useMutation({
    mutationFn: (type: "PNG" | "PDF" | "JSON") => api.createExport(activeChapterId!, type),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", id] }),
  });

  return {
    favoriteOnly,
    setFavoriteOnly,
    libraryChapter,
    setLibraryChapter,
    libraryCharacter,
    setLibraryCharacter,
    libraryKind,
    setLibraryKind,
    libraryModel,
    setLibraryModel,
    libraryResolution,
    setLibraryResolution,
    libraryDateFrom,
    setLibraryDateFrom,
    libraryDateTo,
    setLibraryDateTo,
    libraryCursor,
    setLibraryCursor,
    libraryHistory,
    setLibraryHistory,
    library,
    exportsQuery,
    chapterProduction,
    createExport,
  };
}

export type LibraryWorkspace = ReturnType<typeof useLibraryWorkspace>;
