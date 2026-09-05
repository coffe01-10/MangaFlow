"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";

import {
  api,
  type Character,
  type DirectorCommand,
  type DirectorCommandEnvelope,
  type DirectorCommandGroup,
  type MangaPage,
  type ScriptScene,
  type StoryboardPanel,
} from "@/lib/api";
import {
  compileDirectorCommand,
  type DirectorPlan,
  type DirectorScopeSelection,
} from "@/lib/director-rules";

/** Rebuilds a scope selection from a stored command target (retry / reopen). */
export function selectionFromTarget(target: {
  panel_id?: string | null;
  dialogue_id?: string | null;
}): DirectorScopeSelection | null {
  if (target.dialogue_id && target.panel_id) {
    return { kind: "dialogue", dialogueId: target.dialogue_id, panelId: target.panel_id };
  }
  if (target.panel_id) return { kind: "panel", panelId: target.panel_id };
  return null;
}

export type DirectorCommandPlan = Extract<DirectorPlan, { kind: "command" }>;

/**
 * Director workspace domain (V02-41B): scope selection, utterance draft,
 * history by page, and the propose 鈫?preview 鈫?accept/reject/undo/redo flow
 * against the V02-40 journal API. No Job is created here 鈥?accept executes
 * synchronously; "executing" only reflects the in-flight HTTP call so the
 * canvas can show busy.
 */
export function useDirectorWorkspace({
  id,
  page,
  panels,
  scenes,
  characters,
  pageGenerationPending = false,
  onExecutingChange,
}: {
  id: string;
  page: MangaPage | null;
  panels: StoryboardPanel[];
  scenes: ScriptScene[];
  characters: Character[];
  pageGenerationPending?: boolean;
  onExecutingChange?: (busy: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const [selection, setSelection] = useState<DirectorScopeSelection | null>(null);
  const [draft, setDraft] = useState<{ utterance: string; retryOfCommandId: string | null }>({
    utterance: "",
    retryOfCommandId: null,
  });
  const [preview, setPreview] = useState<DirectorCommandGroup | null>(null);
  const [previewPlan, setPreviewPlan] = useState<DirectorCommandPlan | null>(null);
  const [planState, setPlanState] = useState<DirectorPlan | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const history = useQuery({
    queryKey: ["director-groups", id, page?.id ?? null],
    queryFn: () => api.directorCommandGroups(id, page!.id),
    enabled: Boolean(id && page),
  });

  const invalidateAfterJournalChange = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    if (!page) return;
    queryClient.invalidateQueries({ queryKey: ["generation-workbench", page.id] });
    queryClient.invalidateQueries({ queryKey: ["pages", page.chapter_id] });
    queryClient.invalidateQueries({ queryKey: ["script", page.chapter_id] });
    queryClient.invalidateQueries({ queryKey: ["chapter-production", page.chapter_id] });
  }, [id, page, queryClient]);

  const propose = useMutation({
    mutationFn: (envelope: DirectorCommandEnvelope) =>
      api.directorProposeCommandGroup(id, {
        command_group_id: envelope.command_group_id,
        commands: [envelope],
      }),
    onSuccess: (group) => {
      setPreview(group);
      setPlanState(null);
      queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const accept = useMutation({
    mutationFn: (commandId: string) => api.directorAcceptCommand(id, commandId),
    onSuccess: (group) => {
      setPreview(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const reject = useMutation({
    mutationFn: (commandId: string) => api.directorRejectCommand(id, commandId),
    onSuccess: (group) => {
      setPreview(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const discard = useMutation({
    mutationFn: (commandGroupId: string) => api.directorDiscardCommandGroup(id, commandGroupId),
    onSuccess: () => {
      setPreview(null);
      setPreviewPlan(null);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const undo = useMutation({
    mutationFn: (commandId: string) => api.directorUndoCommand(id, commandId),
    onSuccess: (group) => {
      setPreview(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const redo = useMutation({
    mutationFn: (commandId: string) => api.directorRedoCommand(id, commandId),
    onSuccess: (group) => {
      setPreview(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => setNotice(error.message),
  });

  const executing = accept.isPending || reject.isPending || undo.isPending || redo.isPending || discard.isPending;
  useEffect(() => {
    onExecutingChange?.(executing || propose.isPending);
  }, [executing, propose.isPending, onExecutingChange]);

  const compile = useCallback(
    (): DirectorPlan => compileDirectorCommand({
      projectId: id,
      page: page!,
      panels,
      scenes,
      characters,
      selection,
      utterance: draft.utterance,
      pageGenerationPending,
      retryOfCommandId: draft.retryOfCommandId,
    }),
    [characters, draft, id, page, pageGenerationPending, panels, scenes, selection],
  );

  /** 棰勮锛歝ompile 鈫?clarify/unsupported shown locally; command 鈫?propose. */
  const submitForPreview = useCallback(() => {
    if (!page) return;
    setNotice(null);
    const plan = compile();
    if (plan.kind === "command") {
      setPreviewPlan(plan);
      propose.mutate(plan.envelope);
      return;
    }
    setPreviewPlan(null);
    setPreview(null);
    setPlanState(plan);
  }, [compile, page, propose]);

  const retryCommand = useCallback((command: DirectorCommand) => {
    setDraft({
      utterance: command.source.user_prompt,
      retryOfCommandId: command.retry_of_command_id ?? command.command_id,
    });
    setSelection(selectionFromTarget(command.target));
    setPreview(null);
    setPreviewPlan(null);
    setPlanState(null);
    setNotice(null);
    inputRef.current?.focus();
  }, []);

  const reopenGroup = useCallback((group: DirectorCommandGroup) => {
    setPreview(group);
    setPreviewPlan(null);
    setPlanState(null);
    setNotice(null);
  }, []);

  const closePreview = useCallback(() => {
    setPreview(null);
    setPreviewPlan(null);
    setPlanState(null);
    setNotice(null);
  }, []);

  const focusCommandInput = useCallback(() => {
    inputRef.current?.focus();
  }, []);

  return {
    inputRef,
    selection,
    setSelection,
    draft,
    setDraft,
    history,
    preview,
    previewPlan,
    planState,
    notice,
    setNotice,
    propose,
    accept,
    reject,
    discard,
    undo,
    redo,
    executing,
    submitForPreview,
    retryCommand,
    reopenGroup,
    closePreview,
    focusCommandInput,
  };
}

export type DirectorWorkspaceState = ReturnType<typeof useDirectorWorkspace>;
