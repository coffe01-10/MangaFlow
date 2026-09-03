"use client";

// Dialogue editing card, moved verbatim from the pre-canvas editor.
// Narrative fields keep the single-object PATCH save path (audit §2.1 L3).
import { Save, Trash2, X } from "lucide-react";

import type { Character, PanelDialogue } from "@/lib/api";

export type DialogueDraft = Pick<PanelDialogue, "target_text" | "speaker_character_id" | "text_direction" | "rewrite_forbidden">;

export function DialogueCard({
  index,
  draft,
  characters,
  isNew = false,
  busy,
  onChange,
  onSave,
  onCancel,
  onDelete,
}: {
  index: number;
  draft: DialogueDraft;
  characters: Character[];
  isNew?: boolean;
  busy: boolean;
  onChange: (draft: DialogueDraft) => void;
  onSave: () => void;
  onCancel?: () => void;
  onDelete?: () => void;
}) {
  return <article className={isNew ? "dialogue-card new" : "dialogue-card"}>
    <header className="dialogue-card-head">
      <div><span>{isNew ? "NEW BALLOON" : `BALLOON ${String(index).padStart(2, "0")}`}</span><strong>{isNew ? "新增文字气泡" : `气泡 ${String(index).padStart(2, "0")}`}</strong></div>
      <div><small>{draft.target_text.trim().length} 字</small>{onDelete && <button type="button" aria-label={`删除气泡 ${index}`} title="删除气泡" className="dialogue-delete" disabled={busy} onClick={onDelete}><Trash2 size={13} /></button>}</div>
    </header>
    <label className="dialogue-copy"><span>文字内容</span><textarea aria-label={isNew ? "新增气泡文字" : `气泡 ${index} 文字`} autoFocus={isNew} placeholder="输入对白、旁白或画外音" value={draft.target_text} onChange={(event) => onChange({ ...draft, target_text: event.target.value })} /></label>
    <div className="dialogue-control-deck">
      <label className="dialogue-speaker"><span>说话人</span><select aria-label={isNew ? "新增气泡说话人" : `气泡 ${index} 说话人`} value={draft.speaker_character_id ?? ""} onChange={(event) => onChange({ ...draft, speaker_character_id: event.target.value || null })}><option value="">旁白 / 无说话人</option>{characters.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select></label>
      <fieldset className="dialogue-direction"><legend>排字方向</legend><button type="button" aria-pressed={draft.text_direction === "vertical"} className={draft.text_direction === "vertical" ? "active" : ""} onClick={() => onChange({ ...draft, text_direction: "vertical" })}>竖排</button><button type="button" aria-pressed={draft.text_direction === "horizontal"} className={draft.text_direction === "horizontal" ? "active" : ""} onClick={() => onChange({ ...draft, text_direction: "horizontal" })}>横排</button></fieldset>
      <label className="dialogue-lock"><input type="checkbox" checked={draft.rewrite_forbidden} onChange={(event) => onChange({ ...draft, rewrite_forbidden: event.target.checked })} /><span>锁定文字</span><small>生图时禁止改写</small></label>
      <div className="dialogue-card-actions">{onCancel && <button type="button" className="dialogue-cancel" onClick={onCancel}><X size={12} />取消</button>}<button type="button" className="dialogue-save" disabled={busy || !draft.target_text.trim()} onClick={onSave}><Save size={12} />{isNew ? "新增气泡" : "保存更改"}</button></div>
    </div>
  </article>;
}
