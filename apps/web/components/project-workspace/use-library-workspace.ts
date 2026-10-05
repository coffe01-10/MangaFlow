"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type DisclosurePlatform, type ExportBundle, type ImageModelAlias, type Resolution, type WebtoonExportOptions } from "@/lib/api";

import type { WorkspaceSection } from "./types";

/**
 * Convert `<input type="date">` values (local calendar days) into the
 * aware-UTC boundaries the library API compares UTC-stored `created_at`
 * against (#658). Splicing a literal "Z" onto the local day shifts the whole
 * window by the timezone offset — a UTC+8 visitor picking Sep 13 would miss
 * local Sep 13 00:00-08:00 and include Sep 14's early hours.
 *
 * Exposed separately so tests can pin the offset via
 * `Date.prototype.getTimezoneOffset`; the wall-clock reading is modeled as
 * UTC, then shifted by the visitor's offset (UTC − local minutes). The
 * offset is re-evaluated once after the shift so DST-transition days resolve
 * to the offset the visitor actually sees at that wall time.
 */
export function localDayBoundariesToUtc(
  dateFrom: string,
  dateTo: string,
): { dateFrom?: string; dateTo?: string } {
  const toUtcIso = (day: string, wallTime: string): string => {
    const wall = new Date(`${day}T${wallTime}Z`);
    const shifted = new Date(wall.getTime() + wall.getTimezoneOffset() * 60_000);
    return new Date(wall.getTime() + shifted.getTimezoneOffset() * 60_000).toISOString();
  };
  return {
    dateFrom: dateFrom ? toUtcIso(dateFrom, "00:00:00") : undefined,
    dateTo: dateTo ? toUtcIso(dateTo, "23:59:59.999") : undefined,
  };
}

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
  const [previewOpen, setPreviewOpen] = useState(false);
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
  // P0-3 创作留痕证明包：导出桌开关对所有导出类型生效。
  const [includeProvenance, setIncludeProvenance] = useState(false);
  const [disclosurePlatform, setDisclosurePlatform] = useState<DisclosurePlatform | "">("");
  // #658：筛选输入是本地日历日，边界按本地墙钟换算成 aware-UTC。
  const libraryDateBoundaries = localDayBoundariesToUtc(libraryDateFrom, libraryDateTo);

  const library = useQuery({
    queryKey: ["library", id, favoriteOnly, libraryChapter, libraryCharacter, libraryKind, libraryModel, libraryResolution, libraryDateFrom, libraryDateTo, libraryCursor],
    queryFn: () => api.library(id, {
      favorite: favoriteOnly ? true : undefined,
      chapter_id: libraryChapter || undefined,
      character_id: libraryCharacter || undefined,
      generation_kind: libraryKind || undefined,
      model_alias: (libraryModel || undefined) as ImageModelAlias | undefined,
      resolution: (libraryResolution || undefined) as Resolution | undefined,
      date_from: libraryDateBoundaries.dateFrom,
      date_to: libraryDateBoundaries.dateTo,
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
    mutationFn: ({
      type,
      options,
    }: {
      type: ExportBundle["export_type"];
      options?: WebtoonExportOptions;
    }) => {
      // 留痕/披露是导出级选项，与条漫参数合并进同一个请求体；全部关闭时
      // 保持历史调用形态（options 原样透传，可能是 undefined）。
      const merged =
        includeProvenance || disclosurePlatform
          ? {
              include_provenance: includeProvenance || undefined,
              disclosure_platform: disclosurePlatform || undefined,
              ...(options ?? {}),
            }
          : options;
      return api.createExport(activeChapterId!, type, merged);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", id] }),
  });

  return {
    activeChapterId,
    previewOpen,
    setPreviewOpen,
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
    includeProvenance,
    setIncludeProvenance,
    disclosurePlatform,
    setDisclosurePlatform,
    createExport,
  };
}

export type LibraryWorkspace = ReturnType<typeof useLibraryWorkspace>;
