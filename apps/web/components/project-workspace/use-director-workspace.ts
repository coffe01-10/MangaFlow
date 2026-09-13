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
 * history by page, and the propose → preview → accept/reject/undo/redo flow
 * against the V02-40 journal API. No Job is created here — accept executes
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
  // #165 修复补丁：preview 与 previewPlan 合成一个状态原子。原先 discard 回调
  // 用闭包 preview 判命中、用函数式 setPreview 清预览——两个来源可能在回调
  // 与渲染之间漂移（例如 propose 落地/重开历史组换掉了预览），造成「预览关了
  // 文案没清」或反之的分歧。单一函数式更新保证两者永不打架。
  const [previewState, setPreviewState] = useState<{
    group: DirectorCommandGroup | null;
    plan: DirectorCommandPlan | null;
  }>({ group: null, plan: null });
  const preview = previewState.group;
  const previewPlan = previewState.plan;
  // propose 在途期间解析文案可能被并发动作清掉（重开历史组、丢弃预览组等）；
  // 落地时必须带着「自己的」解析文案渲染，否则整页命令的风险行会退回误导性
  // 的「低：局部字段修改」。submitForPreview 写入、propose 落地时取回。
  const pendingProposePlanRef = useRef<DirectorCommandPlan | null>(null);
  const [planState, setPlanState] = useState<DirectorPlan | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const history = useQuery({
    queryKey: ["director-groups", id, page?.id ?? null],
    queryFn: () => api.directorCommandGroups(id, page!.id),
    enabled: Boolean(id && page),
  });

  // #165③: 历史 queryKey 已按页绑定，但本地状态(preview/plan/selection/draft)
  // 原先不随 page 变化——mid-session refetch 把 selectedPage 换成 pages[0] 时,
  // 旧页的预览仍会对新页的命令 id 发 accept,撤销也刷新错误的页。按 page.id
  // 重置全部会话状态,首挂载不触发。
  const boundPageIdRef = useRef<string | null>(page?.id ?? null);
  useEffect(() => {
    const nextPageId = page?.id ?? null;
    if (boundPageIdRef.current === nextPageId) return;
    boundPageIdRef.current = nextPageId;
    setPreviewState({ group: null, plan: null });
    setPlanState(null);
    setSelection(null);
    setDraft({ utterance: "", retryOfCommandId: null });
    setNotice(null);
  }, [page?.id]);

  // #165③ 追加：换页重置只覆盖本地状态——切换前发出的 journal 变更还在途时，
  // 晚到的 onSuccess 会把旧页的命令组重新种进新页工作区（组若为 PREVIEWED，
  // 「确认执行」甚至会对旧页的命令生效）。发起变更时记下当时的 page.id，
  // 回调发现页面已换就整个结果作废。
  const journalMutationPageIdRef = useRef<string | null>(page?.id ?? null);
  const markJournalMutationStart = useCallback(() => {
    journalMutationPageIdRef.current = page?.id ?? null;
  }, [page?.id]);
  const journalMutationPageStillActive = useCallback(
    () => journalMutationPageIdRef.current === boundPageIdRef.current,
    [],
  );

  const invalidateAfterJournalChange = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    if (!page) return;
    queryClient.invalidateQueries({ queryKey: ["generation-workbench", page.id] });
    queryClient.invalidateQueries({ queryKey: ["pages", page.chapter_id] });
    queryClient.invalidateQueries({ queryKey: ["script", page.chapter_id] });
    queryClient.invalidateQueries({ queryKey: ["chapter-production", page.chapter_id] });
  }, [id, page, queryClient]);

  const propose = useMutation({
    mutationFn: (envelope: DirectorCommandEnvelope) => {
      markJournalMutationStart();
      return api.directorProposeCommandGroup(id, {
        command_group_id: envelope.command_group_id,
        commands: [envelope],
      });
    },
    onSuccess: (group) => {
      if (!journalMutationPageStillActive()) return;
      // 落地的组永远带本次 propose 的解析文案（ref 里存着）：即便在途期间
      // 预览/文案被别的动作清空，也不允许整页命令以 previewPlan===null 渲染。
      const plan = pendingProposePlanRef.current;
      pendingProposePlanRef.current = null;
      setPreviewState({ group, plan });
      setPlanState(null);
      queryClient.invalidateQueries({ queryKey: ["director-groups", id] });
    },
    onError: (error: Error) => {
      pendingProposePlanRef.current = null;
      if (!journalMutationPageStillActive()) return;
      // #648：propose 失败后预览卡不得拿新指令的解析文案盖在旧 group 的
      // diff 上（意图/作用域/摘要/风险取 previewPlan，执行按钮执行的却是
      // 旧命令）。回滚 plan 为 null，卡片回退到 operation 标签 + 命令原文
      // 渲染——与 accept/reject 路径清空 previewPlan 的既有语义一致。
      setPreviewState((current) => ({ ...current, plan: null }));
      setNotice(error.message);
    },
  });

  // #165②: accept/reject/undo/redo 把 preview 替换成服务端返回的组时,旧命令的
  // 解析文案(意图/作用域/摘要/风险)不能盖在新 diff 表上——统一在替换时清空
  // previewPlan,渲染回退到 operation 标签与命令原文。
  const showJournalGroup = useCallback((group: DirectorCommandGroup) => {
    setPreviewState({ group, plan: null });
  }, []);

  const accept = useMutation({
    mutationFn: (commandId: string) => {
      markJournalMutationStart();
      return api.directorAcceptCommand(id, commandId);
    },
    onSuccess: (group) => {
      if (!journalMutationPageStillActive()) return;
      showJournalGroup(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => {
      if (!journalMutationPageStillActive()) return;
      setNotice(error.message);
    },
  });

  const reject = useMutation({
    mutationFn: (commandId: string) => {
      markJournalMutationStart();
      return api.directorRejectCommand(id, commandId);
    },
    onSuccess: (group) => {
      if (!journalMutationPageStillActive()) return;
      showJournalGroup(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => {
      if (!journalMutationPageStillActive()) return;
      setNotice(error.message);
    },
  });

  const discard = useMutation({
    mutationFn: (commandGroupId: string) => {
      markJournalMutationStart();
      return api.directorDiscardCommandGroup(id, commandGroupId);
    },
    onSuccess: (_, commandGroupId) => {
      if (!journalMutationPageStillActive()) return;
      // #165①: 从历史区丢弃无关组不能顺手关掉正在预览的组，也不能清空其
      // 解析文案——previewPlan 一旦被误清，整页命令的风险行会退回「低：局部
      // 字段修改」的误导文案（#165②）。命中判定与清理在同一次函数式更新里
      // 完成（单一来源）：被丢弃的组正是当前预览时，预览与文案一起清空；
      // 否则原子原样保留。
      setPreviewState((current) => (
        current.group?.command_group_id === commandGroupId
          ? { group: null, plan: null }
          : current
      ));
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => {
      if (!journalMutationPageStillActive()) return;
      setNotice(error.message);
    },
  });

  const undo = useMutation({
    mutationFn: (commandId: string) => {
      markJournalMutationStart();
      return api.directorUndoCommand(id, commandId);
    },
    onSuccess: (group) => {
      if (!journalMutationPageStillActive()) return;
      showJournalGroup(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => {
      if (!journalMutationPageStillActive()) return;
      setNotice(error.message);
    },
  });

  const redo = useMutation({
    mutationFn: (commandId: string) => {
      markJournalMutationStart();
      return api.directorRedoCommand(id, commandId);
    },
    onSuccess: (group) => {
      if (!journalMutationPageStillActive()) return;
      showJournalGroup(group);
      setNotice(null);
      invalidateAfterJournalChange();
    },
    onError: (error: Error) => {
      if (!journalMutationPageStillActive()) return;
      setNotice(error.message);
    },
  });

  // reject/discard must be in the gate too: otherwise a reject in flight leaves
  // 确认执行 clickable (accept+reject race on the same command) and 拒绝 can
  // double-fire.
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

  /** 预览：compile → clarify/unsupported shown locally; command → propose. */
  const submitForPreview = useCallback(() => {
    if (!page) return;
    setNotice(null);
    const plan = compile();
    if (plan.kind === "command") {
      pendingProposePlanRef.current = plan;
      // 旧预览在 propose 在途期间保持可见；文案先切到新命令的解析。
      setPreviewState((current) => ({ ...current, plan }));
      propose.mutate(plan.envelope);
      return;
    }
    pendingProposePlanRef.current = null;
    setPreviewState({ group: null, plan: null });
    setPlanState(plan);
  }, [compile, page, propose]);

  const retryCommand = useCallback((command: DirectorCommand) => {
    setDraft({
      utterance: command.source.user_prompt,
      retryOfCommandId: command.retry_of_command_id ?? command.command_id,
    });
    setSelection(selectionFromTarget(command.target));
    setPreviewState({ group: null, plan: null });
    setPlanState(null);
    setNotice(null);
    inputRef.current?.focus();
  }, []);

  const reopenGroup = useCallback((group: DirectorCommandGroup) => {
    setPreviewState({ group, plan: null });
    setPlanState(null);
    setNotice(null);
  }, []);

  const closePreview = useCallback(() => {
    setPreviewState({ group: null, plan: null });
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
