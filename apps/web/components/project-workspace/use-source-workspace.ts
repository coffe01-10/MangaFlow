"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import type { ChangeEvent } from "react";

import { api } from "@/lib/api";

/**
 * Source domain: chapter import/revision state and the parse/plan actions that
 * hand off to the jobs and storyboard routes.
 */
export function useSourceWorkspace({
  id,
  projectPath,
  router,
  activeChapterId,
  setSelectedChapterId,
  setSelectedPageId,
}: {
  id: string;
  projectPath: (target: string) => string;
  router: ReturnType<typeof useRouter>;
  activeChapterId: string | null;
  setSelectedChapterId: (chapterId: string | null) => void;
  setSelectedPageId: (pageId: string | null) => void;
}) {
  const queryClient = useQueryClient();
  const [sourceTitle, setSourceTitle] = useState("第一章");
  const [sourceText, setSourceText] = useState("");
  const [editingChapterId, setEditingChapterId] = useState<string | null>(null);
  const [deletedChapterId, setDeletedChapterId] = useState<string | null>(null);
  // 修订加载失败/进行中：失败时此前是 unhandled rejection，用户点击“修改原文”
  // 毫无反馈；加载期间提前输入的文本又会被晚到的响应覆盖。
  const [revisionLoadError, setRevisionLoadError] = useState("");
  const [revisionLoading, setRevisionLoading] = useState(false);
  const revisionSeqRef = useRef(0);

  const importSource = useMutation({
    mutationFn: () => editingChapterId
      ? api.reviseSource(editingChapterId, sourceTitle.trim(), sourceText).then(() => ({ chapters: [], total_characters: 0 }))
      : api.importSource(id, sourceTitle.trim(), sourceText),
    onSuccess: (result) => {
      setSelectedChapterId(result.chapters[0]?.id ?? editingChapterId);
      setEditingChapterId(null);
      setSourceText("");
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
      queryClient.invalidateQueries({ queryKey: ["revisions"] });
      queryClient.invalidateQueries({ queryKey: ["script"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    },
  });

  const importSourceFile = useMutation({
    mutationFn: (file: File) => api.uploadSource(
      id,
      sourceTitle.trim() || file.name.replace(/\.(txt|md|markdown)$/i, ""),
      file,
    ),
    onSuccess: (result) => {
      setSelectedChapterId(result.chapters[0]?.id ?? null);
      setEditingChapterId(null);
      setSourceText("");
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
      queryClient.invalidateQueries({ queryKey: ["revisions"] });
      queryClient.invalidateQueries({ queryKey: ["script"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    },
  });

  const deleteChapter = useMutation({
    mutationFn: (chapterId: string) => api.deleteChapter(chapterId),
    onSuccess: (_, chapterId) => {
      setDeletedChapterId(chapterId);
      setSelectedChapterId(null);
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const restoreChapter = useMutation({
    mutationFn: (chapterId: string) => api.restoreChapter(chapterId),
    onSuccess: (chapter) => {
      setDeletedChapterId(null);
      setSelectedChapterId(chapter.id);
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const parseChapter = useMutation({
    mutationFn: () => api.parseChapter(activeChapterId!),
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
  });

  const planChapter = useMutation({
    mutationFn: () => api.planChapter(activeChapterId!),
    onSuccess: (result) => {
      setSelectedPageId(result.pages[0]?.id ?? null);
      router.push(projectPath("storyboard"));
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  function chooseSourceFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) importSourceFile.mutate(file);
    event.target.value = "";
  }

  async function beginEditChapter(chapterId: string, title: string) {
    // Sequence token: two rapid “修改原文” clicks must not let the older
    // response overwrite the form the newer click is loading.
    const seq = ++revisionSeqRef.current;
    setSelectedChapterId(chapterId);
    setRevisionLoadError("");
    setRevisionLoading(true);
    try {
      const values = await queryClient.fetchQuery({ queryKey: ["revisions", chapterId], queryFn: () => api.revisions(chapterId) });
      if (seq !== revisionSeqRef.current) return;
      const revision = values[0];
      setEditingChapterId(chapterId);
      setSourceTitle(title);
      setSourceText(revision?.original_text ?? "");
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (error) {
      if (seq !== revisionSeqRef.current) return;
      setRevisionLoadError(error instanceof Error ? error.message : "原文修订加载失败，请重试");
      setEditingChapterId(null);
    } finally {
      if (seq === revisionSeqRef.current) setRevisionLoading(false);
    }
  }

  function cancelEditChapter() {
    setEditingChapterId(null);
    setSourceText("");
    setRevisionLoadError("");
  }

  return {
    sourceTitle,
    setSourceTitle,
    sourceText,
    setSourceText,
    editingChapterId,
    setEditingChapterId,
    revisionLoadError,
    revisionLoading,
    deletedChapterId,
    importSource,
    importSourceFile,
    deleteChapter,
    restoreChapter,
    parseChapter,
    planChapter,
    chooseSourceFile,
    beginEditChapter,
    cancelEditChapter,
  };
}

export type SourceWorkspace = ReturnType<typeof useSourceWorkspace>;
