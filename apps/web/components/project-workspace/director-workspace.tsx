"use client";

import {
  Ban,
  CircleAlert,
  History,
  LoaderCircle,
  MessageSquareQuote,
  RotateCcw,
  RotateCw,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useEffect } from "react";

import {
  type Character,
  type DirectorCommand,
  type MangaPage,
  type ScriptScene,
  type StoryboardPanel,
} from "@/lib/api";
import { DIRECTOR_PARSER_LABEL, type DirectorScopeSelection } from "@/lib/director-rules";

import { useDirectorWorkspace } from "./use-director-workspace";

const OPERATION_LABELS: Record<string, string> = {
  update_page_layout: "整页布局",
  update_panel_layout: "格布局",
  update_panel_shot: "镜头景别",
  update_panel_cast: "角色出场 / 表情",
  update_scene_context: "场景上下文",
  update_dialogue: "气泡台词",
  move_dialogue: "气泡位置",
  regenerate_region: "局部重绘（不可用）",
};

const COMMAND_STATUS_LABELS: Record<string, string> = {
  PROPOSED: "待解析",
  PREVIEWED: "待确认",
  ACCEPTED: "已接受",
  REJECTED: "已拒绝",
  EXECUTED: "已执行",
  SUPERSEDED: "已被更新取代",
  DISCARDED: "已丢弃",
  FAILED: "执行失败",
};

const DIFF_FIELD_LABELS: Record<string, string> = {
  shot_type: "景别",
  camera_angle: "镜头角度",
  target_text: "台词",
  weather: "天气",
  time_label: "时间",
  panel_count: "格数",
  layout_mode: "布局模式",
  characters: "入镜角色",
  character_presence: "出场状态",
  expressions: "表情",
};

function formatDiffValue(key: string, value: unknown, characters: Character[]): string {
  if (value == null) return "（空）";
  if (key === "characters" || key === "character_presence" || key === "expressions") {
    const entries: [string, unknown][] = Array.isArray(value)
      ? value.map((id) => [String(id), "出镜" as unknown])
      : Object.entries(value as Record<string, unknown>);
    if (!entries.length) return "（空）";
    return entries.map(([id, entry]) => {
      const name = characters.find((item) => item.id === id)?.primary_name ?? id;
      return entry === "出镜" ? name : `${name}：${String(entry)}`;
    }).join("、");
  }
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/**
 * Director shell (V02-41B): scope chips, command bar, preview card, local-edit
 * placeholder and history timeline over the V02-40 journal API. Rule stubs
 * only — no model call, no whole-page regeneration.
 */
export function DirectorWorkspace({
  id,
  page,
  panels,
  scenes,
  characters,
  activeDrawModelName,
  pageGenerationPending = false,
  onExecutingChange,
}: {
  id: string;
  page: MangaPage;
  panels: StoryboardPanel[];
  scenes: ScriptScene[];
  characters: Character[];
  activeDrawModelName: string | null;
  pageGenerationPending?: boolean;
  onExecutingChange: (busy: boolean) => void;
}) {
  const director = useDirectorWorkspace({
    id,
    page,
    panels,
    scenes,
    characters,
    pageGenerationPending,
    onExecutingChange,
  });
  const {
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
  } = director;

  // Audit D14 / D15: Ctrl+K focuses the command box, Esc closes the preview /
  // clarification layer and returns focus to the command input.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        focusCommandInput();
        return;
      }
      if (event.key === "Escape") {
        closePreview();
        focusCommandInput();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closePreview, focusCommandInput]);

  const ordered = [...panels].sort((left, right) => left.reading_order - right.reading_order);
  const dialogueChips = ordered.flatMap((panel) =>
    [...panel.dialogues]
      .sort((left, right) => left.reading_order - right.reading_order)
      .map((dialogue, index) => ({
        dialogueId: dialogue.id,
        panelId: panel.id,
        label: `格${panel.reading_order}·气泡${index + 1}`,
      })),
  );
  const characterIds = [...new Set(ordered.flatMap((panel) => panel.characters ?? []))];

  const toggleSelection = (next: DirectorScopeSelection) => {
    const same = selection !== null
      && selection.kind === next.kind
      && scopeKey(selection) === scopeKey(next);
    setSelection(same ? null : next);
  };

  const chipActive = (kind: DirectorScopeSelection["kind"], targetId: string) => {
    if (!selection || selection.kind !== kind) return false;
    return kind === "page" ? true : scopeKey(selection) === targetId;
  };

  const previewCommand: DirectorCommand | null = preview?.commands[0] ?? null;
  const undoTargets = new Set(
    (history.data ?? []).flatMap((group) => group.commands.map((command) => command.inverse_of_command_id)),
  );

  return (
    <div className="director-shell">
      <header className="director-header">
        <div>
          <span>DIRECTOR / 导演台</span>
          <h3>对第 {page.page_number} 页下指令</h3>
        </div>
        <span
          className="director-parser-chip"
          title="V02-40 命令层不解析自然语言；导演台用规则桩把「作用域 + 短指令」编成白名单命令"
        >
          <Sparkles size={12} />{DIRECTOR_PARSER_LABEL}
        </span>
      </header>
      <p className="director-note">先点作用域，再写短指令。预览确认后才会执行；导演台不会自动调用图片模型，也不会整页重绘。</p>

      <div className="director-scopes" aria-live="polite" aria-label="作用域芯片">
        <div className="director-scope-row">
          <span className="director-scope-title">作用域</span>
          <button
            type="button"
            className={chipActive("page", "page") ? "director-chip active" : "director-chip"}
            aria-pressed={chipActive("page", "page")}
            onClick={() => toggleSelection({ kind: "page" })}
          >整页</button>
          {ordered.map((panel) => (
            <button
              key={panel.id}
              type="button"
              className={chipActive("panel", panel.id) ? "director-chip active" : "director-chip"}
              aria-pressed={chipActive("panel", panel.id)}
              onClick={() => toggleSelection({ kind: "panel", panelId: panel.id })}
            >格 {panel.reading_order}</button>
          ))}
          {dialogueChips.map((chip) => (
            <button
              key={chip.dialogueId}
              type="button"
              className={chipActive("dialogue", chip.dialogueId) ? "director-chip active" : "director-chip"}
              aria-pressed={chipActive("dialogue", chip.dialogueId)}
              onClick={() => toggleSelection({ kind: "dialogue", dialogueId: chip.dialogueId, panelId: chip.panelId })}
            ><MessageSquareQuote size={11} />{chip.label}</button>
          ))}
          {characterIds.map((characterId) => (
            <button
              key={characterId}
              type="button"
              className={chipActive("character", characterId) ? "director-chip active" : "director-chip"}
              aria-pressed={chipActive("character", characterId)}
              onClick={() => toggleSelection({ kind: "character", characterId })}
            >{characters.find((item) => item.id === characterId)?.primary_name ?? "未知角色"}</button>
          ))}
          {selection && (
            <button type="button" className="director-chip director-chip-clear" onClick={() => setSelection(null)}>清除作用域</button>
          )}
        </div>
      </div>

      <form
        className="director-command-bar"
        onSubmit={(event) => {
          event.preventDefault();
          submitForPreview();
        }}
      >
        <textarea
          ref={inputRef}
          aria-label="导演指令"
          rows={2}
          value={draft.utterance}
          placeholder={draft.retryOfCommandId ? "修改失败命令的口令后重新预览…" : "例如：第 3 格改成近景 · 台词改成「我没事」 · 雨下大一点 · 改成 6 格"}
          onChange={(event) => setDraft({ utterance: event.target.value, retryOfCommandId: draft.retryOfCommandId })}
        />
        <div className="director-command-actions">
          <button type="submit" className="button ink compact" disabled={!draft.utterance.trim() || propose.isPending || executing}>
            {propose.isPending ? <LoaderCircle className="spin" size={14} /> : <Send size={14} />}
            {propose.isPending ? "解析中…" : "预览"}
          </button>
          <span className="director-kbd-hint">Ctrl+K 聚焦 · Esc 关闭预览</span>
        </div>
      </form>

      {executing && <p className="director-executing" role="status"><LoaderCircle className="spin" size={14} />命令执行中，画布暂忙…</p>}
      {notice && <p className="form-error" role="alert"><CircleAlert size={14} />{notice}</p>}

      {planState?.kind === "clarify" && (
        <section className="director-clarify" role="dialog" aria-label="请确认命令目标">
          <p><CircleAlert size={14} />{planState.reason}</p>
          {planState.options.length > 0 && (
            <div className="director-clarify-options">
              {planState.options.map((option) => (
                <button
                  key={`${option.kind}-${option.id}`}
                  type="button"
                  className="director-chip"
                  onClick={() => {
                    if (option.kind === "page") setSelection({ kind: "page" });
                    else if (option.kind === "panel") setSelection({ kind: "panel", panelId: option.id });
                    else if (option.kind === "dialogue") setSelection({ kind: "dialogue", dialogueId: option.id, panelId: option.panelId ?? "" });
                    else setSelection({ kind: "character", characterId: option.id });
                    closePreview();
                    focusCommandInput();
                  }}
                >{option.label}</button>
              ))}
            </div>
          )}
          <button type="button" className="button ghost compact" onClick={closePreview}>取消</button>
        </section>
      )}

      {planState && (planState.kind === "unsupported" || planState.kind === "blocked") && (
        <p className="form-error" role="alert"><Ban size={14} />{planState.reason}</p>
      )}

      {preview && previewCommand && (() => {
        const command = previewCommand;
        const stale = command.error?.code === "VERSION_CONFLICT";
        const diffEntries = Object.entries(command.diff ?? {}).filter(([key]) => key !== "text_metrics");
        return (
          <section className="director-preview" role="region" aria-label="命令预览">
            <header>
              <div>
                <span>命令预览 · {preview.idempotent_replay ? "历史重放" : "规则解析"}</span>
                <strong>{previewPlan?.intentLabel ?? OPERATION_LABELS[command.operation] ?? command.operation}</strong>
              </div>
              <button type="button" className="icon-button" aria-label="关闭预览" onClick={closePreview}><X size={15} /></button>
            </header>
            <dl className="director-preview-facts">
              <div><dt>作用域</dt><dd>{previewPlan?.scopeLabel ?? scopeLabelFromCommand(command, panels)}</dd></div>
              <div><dt>状态</dt><dd>{COMMAND_STATUS_LABELS[command.status] ?? command.status}</dd></div>
            </dl>
            <p className="director-preview-summary">{previewPlan?.summary ?? command.source.user_prompt}</p>
            {diffEntries.length > 0 && (
              <table className="director-diff">
                <thead><tr><th scope="col">字段</th><th scope="col">当前</th><th scope="col">将改为</th></tr></thead>
                <tbody>
                  {diffEntries.map(([key, change]) => (
                    <tr key={key}>
                      <th scope="row">{DIFF_FIELD_LABELS[key] ?? key}</th>
                      <td>{formatDiffValue(key, change.before, characters)}</td>
                      <td>{formatDiffValue(key, change.after, characters)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <dl className="director-preview-meta">
              <div><dt>模型</dt><dd>规则解析，非模型调用 · 抽卡模型：{activeDrawModelName ?? "未选择"}</dd></div>
              <div><dt>费用</dt><dd>分镜字段修改 · 本次不调用图片模型 · 重新抽卡费用暂不可估算</dd></div>
              <div><dt>风险</dt><dd>{previewPlan?.risk === "high" ? "高：整页命令，候选将过期" : previewPlan?.risk === "medium" ? "中：影响本页后续抽卡" : "低：局部字段修改"}</dd></div>
            </dl>
            {command.error && (
              <p className="form-error" role="alert">
                <CircleAlert size={14} />
                {command.error.message}
                {stale && " 你的草稿已保留；请刷新页面拿到最新分镜版本后重新预览。"}
              </p>
            )}
            <footer className="director-preview-actions">
              {command.status === "PREVIEWED" && <>
                <button type="button" className="button ink compact" disabled={executing} onClick={() => accept.mutate(command.command_id)}>确认执行</button>
                <button type="button" className="button outline compact" disabled={executing} onClick={() => reject.mutate(command.command_id)}>拒绝</button>
                <button type="button" className="button ghost compact" disabled={discard.isPending} onClick={() => discard.mutate(preview.command_group_id)}>丢弃</button>
              </>}
              {command.status === "EXECUTED" && <>
                <span className="director-preview-done">已执行 · 分镜已更新，可在历史里撤销。</span>
                <button type="button" className="button ghost compact" onClick={closePreview}>关闭</button>
              </>}
              {(command.status === "FAILED" || command.status === "REJECTED") && (
                <button type="button" className="button outline compact" onClick={() => retryCommand(command)}>
                  <RotateCcw size={13} />改口令重发
                </button>
              )}
            </footer>
          </section>
        );
      })()}

      <div className="director-local-edit">
        <button type="button" className="button outline compact" disabled title="V02-43 局部选区编辑尚未上线">在选区编辑（mask 局部重绘）</button>
        <p>局部选区画笔与派生候选属于 V02-42 / V02-43，尚未实现；导演台不会静默整页重绘。</p>
      </div>

      <section className="director-history" aria-label="命令历史">
        <header>
          <div><span><History size={12} />HISTORY / 命令历史</span><strong>本页命令</strong></div>
          <small>{history.data?.length ?? 0} 条</small>
        </header>
        {history.isLoading && <p className="reference-check-loading"><LoaderCircle className="spin" size={15} />正在读取命令历史…</p>}
        {history.isError && <p className="form-error" role="alert"><CircleAlert size={14} />{(history.error as Error).message}</p>}
        {!history.isLoading && !history.isError && !(history.data ?? []).length && (
          <p className="director-history-empty">本页还没有导演命令。</p>
        )}
        <ol>
          {(history.data ?? []).map((group) => {
            const command = group.commands[0];
            const failed = command?.status === "FAILED" || command?.status === "REJECTED";
            return (
              <li key={group.id} className={failed ? "director-history-item failed" : "director-history-item"}>
                <div className="director-history-meta">
                  <span className="director-history-op">{OPERATION_LABELS[command?.operation ?? ""] ?? command?.operation}</span>
                  <span className={`director-status director-status-${String(group.status).toLowerCase()}`}>
                    {COMMAND_STATUS_LABELS[group.status] ?? group.status}
                  </span>
                </div>
                <p className="director-history-text">{command?.source.user_prompt}</p>
                {command?.error?.message && <p className="director-history-error">{command.error.message}</p>}
                <div className="director-history-actions">
                  {group.status === "PREVIEWED" && <button type="button" onClick={() => reopenGroup(group)}>继续预览</button>}
                  {command?.status === "EXECUTED" && !command.inverse_of_command_id && !undoTargets.has(command.command_id) && (
                    <button type="button" disabled={undo.isPending} onClick={() => undo.mutate(command.command_id)}>
                      <RotateCcw size={12} />撤销
                    </button>
                  )}
                  {command?.status === "EXECUTED" && Boolean(command.inverse_of_command_id) && (
                    <button type="button" disabled={redo.isPending} onClick={() => redo.mutate(command.command_id)}>
                      <RotateCw size={12} />重做
                    </button>
                  )}
                  {failed && command && (
                    <button type="button" onClick={() => retryCommand(command)}>
                      <RotateCcw size={12} />改口令重发
                    </button>
                  )}
                  {(group.status === "PROPOSED" || group.status === "PREVIEWED") && (
                    <button type="button" disabled={discard.isPending} onClick={() => discard.mutate(group.command_group_id)}>丢弃</button>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </section>
    </div>
  );
}

function scopeKey(selection: DirectorScopeSelection): string {
  if (selection.kind === "page") return "page";
  if (selection.kind === "panel") return selection.panelId;
  if (selection.kind === "dialogue") return selection.dialogueId;
  return selection.characterId;
}

function scopeLabelFromCommand(
  command: DirectorCommand,
  panels: StoryboardPanel[],
): string {
  const target = command.target;
  if (target.dialogue_id) {
    const panel = panels.find((item) => item.id === target.panel_id);
    const index = panel?.dialogues.findIndex((item) => item.id === target.dialogue_id) ?? -1;
    return `格 ${panel?.reading_order ?? "?"} · 气泡 ${index >= 0 ? index + 1 : "?"}`;
  }
  if (target.panel_id) {
    const panel = panels.find((item) => item.id === target.panel_id);
    return `格 ${panel?.reading_order ?? "?"}`;
  }
  if (target.scene_id) return "主场景";
  return "整页";
}
