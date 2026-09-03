"use client";

// Panel inspector: narrative fields keep the existing single-object PATCH
// path; canvas geometry is read-only here (audit §2.1 L3).
import { MessageSquarePlus, Pencil, X } from "lucide-react";
import type { CSSProperties } from "react";

import type { Character, CharacterPresence, MangaPage, NormalizedRect, Outfit, PanelDialogue, StoryboardPanel } from "@/lib/api";

import type { DialogueDraft } from "./dialogue-card";
import { DialogueCard } from "./dialogue-card";
import { geometryReadout, isPolygonPanel, panelGeometry } from "./geometry";
import { storyboardCopy } from "./storyboard-copy";

const shotTypes = [["establishing", "远景建立"], ["wide_action", "全景动作"], ["medium_close_up", "中近景"], ["close_up", "近景"], ["extreme_close_up", "大特写"]] as const;
const cameraAngles = [["eye_level", "平视"], ["low_angle", "仰拍"], ["high_angle", "俯拍"], ["dutch_angle", "倾斜镜头"], ["over_shoulder", "越肩"]] as const;

export type PanelDraft = Pick<StoryboardPanel, "shot_type" | "camera_angle" | "camera_height" | "characters" | "character_presence" | "props" | "outfits" | "actions" | "expressions" | "background" | "sound_effects" | "bleed" | "borderless">;

export function PanelInspector({
  page,
  panel,
  panels,
  panelRects,
  characters,
  outfits,
  editingPanel,
  panelDraft,
  dialogueDrafts,
  newDialogue,
  selectedBubbleId,
  saving,
  onBeginEdit,
  onExitEdit,
  onPanelDraftChange,
  onPresenceChange,
  onSavePanel,
  onDialogueDraftChange,
  onSaveDialogue,
  onRemoveDialogue,
  onNewDialogueChange,
  onAddDialogue,
  onCancelNewDialogue,
  onSelectPanel,
  onSelectBubble,
  inspectorOpen,
  onToggleInspector,
}: {
  page: MangaPage;
  panel: StoryboardPanel;
  panels: StoryboardPanel[];
  panelRects: Record<string, NormalizedRect>;
  characters: Character[];
  outfits: Outfit[];
  editingPanel: boolean;
  panelDraft: PanelDraft | null;
  dialogueDrafts: Record<string, DialogueDraft>;
  newDialogue: DialogueDraft | null;
  selectedBubbleId: string | null;
  saving: boolean;
  onBeginEdit: () => void;
  onExitEdit: () => void;
  onPanelDraftChange: (draft: PanelDraft) => void;
  onPresenceChange: (characterId: string, presence: CharacterPresence | "NONE") => void;
  onSavePanel: () => void;
  onDialogueDraftChange: (dialogue: PanelDialogue, draft: DialogueDraft) => void;
  onSaveDialogue: (dialogue: PanelDialogue, draft: DialogueDraft) => void;
  onRemoveDialogue: (dialogueId: string) => void;
  onNewDialogueChange: (draft: DialogueDraft | null) => void;
  onAddDialogue: () => void;
  onCancelNewDialogue: () => void;
  onSelectPanel: (panelId: string) => void;
  onSelectBubble: (dialogueId: string) => void;
  inspectorOpen: boolean;
  onToggleInspector: () => void;
}) {
  const polygon = isPolygonPanel(panel);
  const geometry = panelGeometry(panel);
  const rect = panelRects[panel.id];
  return <aside className={inspectorOpen ? "panel-inspector drawer-open" : "panel-inspector"}>
    <header>
      <div><span>P.{String(page.page_number).padStart(3, "0")} / PANEL {String(panel.reading_order).padStart(2, "0")}</span><strong>分镜导演台</strong></div>
      <div className="inspector-header-actions">
        {editingPanel
          ? <button type="button" onClick={onExitEdit}><X size={12} />退出编辑</button>
          : <button type="button" onClick={onBeginEdit}><Pencil size={12} />编辑本格</button>}
        <button type="button" className="storyboard-inspector-toggle" aria-expanded={inspectorOpen} aria-label={storyboardCopy.inspectorToggle} onClick={onToggleInspector}><X size={12} /></button>
      </div>
    </header>
    <div className="panel-geometry-readout" data-testid="geometry-readout">
      <strong>{storyboardCopy.geometryReadoutTitle}</strong>
      {polygon
        ? <p className="polygon-note">{storyboardCopy.polygonNote}</p>
        : <p>{rect ? geometryReadout(rect) : "—"}</p>}
      <small>阅读序 {panel.reading_order} · 绘制层 Z{geometry?.z_order ?? panel.reading_order}{panel.bleed ? " · 出血格" : ""}{panel.borderless ? " · 无边框" : ""}</small>
    </div>
    {editingPanel && panelDraft ? <div className="panel-edit-form">
      <div className="panel-edit-grid"><label><span>景别</span><select value={panelDraft.shot_type} onChange={(event) => onPanelDraftChange({ ...panelDraft, shot_type: event.target.value })}>{shotTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>镜头角度</span><select value={panelDraft.camera_angle} onChange={(event) => onPanelDraftChange({ ...panelDraft, camera_angle: event.target.value })}>{cameraAngles.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>机位高度</span><select value={panelDraft.camera_height} onChange={(event) => onPanelDraftChange({ ...panelDraft, camera_height: event.target.value })}><option value="eye_level">视线高度</option><option value="ground_level">贴地机位</option><option value="waist_level">腰部机位</option><option value="overhead">顶视机位</option></select></label></div>
      <label><span>动作与表演</span><textarea value={panelDraft.actions.script_action ?? ""} onChange={(event) => onPanelDraftChange({ ...panelDraft, actions: { ...panelDraft.actions, script_action: event.target.value } })} /></label>
      <label><span>背景</span><textarea value={panelDraft.background} onChange={(event) => onPanelDraftChange({ ...panelDraft, background: event.target.value })} /></label>
      <label><span>场景道具（用逗号分隔）</span><input value={panelDraft.props.join("，")} onChange={(event) => onPanelDraftChange({ ...panelDraft, props: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} placeholder="例如：爸爸的灵牌、香炉、白菊" /></label>
      <label><span>拟声词（用逗号分隔）</span><input value={panelDraft.sound_effects.map(String).join("，")} onChange={(event) => onPanelDraftChange({ ...panelDraft, sound_effects: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label>
      <fieldset><legend>人物状态</legend><p className="presence-help">只有“实际出镜”会要求人物与服装参考；画外音和被提及人物不会阻塞生图。</p><div className="character-presence-grid">{characters.map((character) => {
        const presence = (panelDraft.character_presence[character.id] || "NONE") as CharacterPresence | "NONE";
        return <label key={character.id} className={presence !== "NONE" ? `active presence-${presence.toLowerCase()}` : ""}><strong>{character.primary_name}</strong><select aria-label={`${character.primary_name}人物状态`} value={presence} onChange={(event) => onPresenceChange(character.id, event.target.value as CharacterPresence | "NONE")}><option value="NONE">不在本格</option><option value="VISIBLE">实际出镜</option><option value="OFFSCREEN">画外人物</option><option value="MENTIONED">仅被提及</option></select></label>;
      })}</div></fieldset>
      {panelDraft.characters.map((characterId) => {
        const character = characters.find((item) => item.id === characterId);
        const options = outfits.filter((item) => item.character_id === characterId);
        return <div className="character-direction" key={characterId}><strong>{character?.primary_name ?? characterId}</strong><label><span>表情</span><input value={panelDraft.expressions[characterId] ?? ""} onChange={(event) => onPanelDraftChange({ ...panelDraft, expressions: { ...panelDraft.expressions, [characterId]: event.target.value } })} /></label>{options.length > 0 && <label><span>服装</span><select value={panelDraft.outfits[characterId] ?? ""} onChange={(event) => onPanelDraftChange({ ...panelDraft, outfits: { ...panelDraft.outfits, [characterId]: event.target.value } })}><option value="">沿用场景服装</option>{options.map((outfit) => <option key={outfit.id} value={outfit.id}>{outfit.name}</option>)}</select></label>}</div>;
      })}
      <div className="panel-flags"><label><input type="checkbox" checked={panelDraft.bleed} onChange={(event) => onPanelDraftChange({ ...panelDraft, bleed: event.target.checked })} />出血格</label><label><input type="checkbox" checked={panelDraft.borderless} onChange={(event) => onPanelDraftChange({ ...panelDraft, borderless: event.target.checked })} />无边框</label></div>
      <button className="panel-save" disabled={saving || !panelDraft.actions.script_action?.trim()} onClick={onSavePanel}>保存本格分镜</button>
    </div> : <div className="panel-readout"><dl><div><dt>景别</dt><dd>{shotTypes.find(([value]) => value === panel.shot_type)?.[1] ?? panel.shot_type}</dd></div><div><dt>角度</dt><dd>{cameraAngles.find(([value]) => value === panel.camera_angle)?.[1] ?? panel.camera_angle}</dd></div><div><dt>动作</dt><dd>{panel.actions.script_action || "待补充"}</dd></div><div><dt>背景</dt><dd>{panel.background || "待补充"}</dd></div><div><dt>道具</dt><dd>{panel.props?.join("、") || "无"}</dd></div></dl><div className="panel-presence-readout">{Object.entries(panel.character_presence ?? Object.fromEntries(panel.characters.map((id) => [id, "VISIBLE"]))).map(([id, presence]) => <span className={`presence-${String(presence).toLowerCase()}`} key={id}>{characters.find((item) => item.id === id)?.primary_name ?? "未知角色"} · {presence === "VISIBLE" ? "实际出镜" : presence === "OFFSCREEN" ? "画外" : "提及"}</span>)}</div></div>}
    <section className="dialogue-editor"><header><div><span>LETTERING</span><strong>文字与气泡</strong><small>{panel.dialogues.length} 个气泡 · 本格文字单独校对</small></div><button type="button" disabled={Boolean(newDialogue)} onClick={() => onNewDialogueChange({ target_text: "", speaker_character_id: null, text_direction: "vertical", rewrite_forbidden: true })}><MessageSquarePlus size={12} />新增气泡</button></header>
      <div className="dialogue-stack">{panel.dialogues.map((dialogue, index) => {
        const draft = dialogueDrafts[dialogue.id] ?? { target_text: dialogue.target_text, speaker_character_id: dialogue.speaker_character_id, text_direction: dialogue.text_direction, rewrite_forbidden: dialogue.rewrite_forbidden };
        return <div key={dialogue.id} className={dialogue.id === selectedBubbleId ? "dialogue-card-slot selected" : "dialogue-card-slot"} style={{ cursor: "pointer" } as CSSProperties} onClick={() => onSelectBubble(dialogue.id)}>
          <DialogueCard
            index={index + 1}
            draft={draft}
            characters={characters}
            busy={saving}
            onChange={(value) => onDialogueDraftChange(dialogue, value)}
            onSave={() => onSaveDialogue(dialogue, draft)}
            onDelete={() => onRemoveDialogue(dialogue.id)}
          />
        </div>;
      })}
      {newDialogue && <DialogueCard index={panel.dialogues.length + 1} draft={newDialogue} characters={characters} isNew busy={saving} onChange={onNewDialogueChange} onSave={onAddDialogue} onCancel={onCancelNewDialogue} />}</div>
    </section>
    <section className="panel-layer-list" aria-label={storyboardCopy.layerList}>
      <strong>{storyboardCopy.layerList}</strong>
      <ul>
        <li className="layer-page">{storyboardCopy.layerPage}</li>
        {panels.map((item) => <li key={item.id} className={item.id === panel.id ? "active" : ""}>
          <button type="button" onClick={() => onSelectPanel(item.id)}>格 {String(item.reading_order).padStart(2, "0")} · {item.dialogues.length} {storyboardCopy.layerBubbleSuffix}</button>
          {item.dialogues.length > 0 && <ul>{item.dialogues.map((dialogue, index) => <li key={dialogue.id}><button type="button" className={dialogue.id === selectedBubbleId ? "active" : ""} onClick={() => { onSelectPanel(item.id); onSelectBubble(dialogue.id); }}>气泡 {index + 1}</button></li>)}</ul>}
        </li>)}
      </ul>
    </section>
  </aside>;
}
