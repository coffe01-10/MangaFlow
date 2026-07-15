"use client";

import {
  api,
  type Character,
  type MangaPage,
  type Outfit,
  type PanelDialogue,
  type StoryboardPanel,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, MessageSquarePlus, Pencil, RotateCcw, Save, Trash2, X } from "lucide-react";
import { useState } from "react";

type PanelDraft = Pick<StoryboardPanel, "shot_type" | "camera_angle" | "camera_height" | "characters" | "outfits" | "actions" | "expressions" | "background" | "sound_effects" | "bleed" | "borderless">;
type DialogueDraft = Pick<PanelDialogue, "target_text" | "speaker_character_id" | "text_direction" | "rewrite_forbidden">;

const shotTypes = [["establishing", "远景建立"], ["wide_action", "全景动作"], ["medium_close_up", "中近景"], ["close_up", "近景"], ["extreme_close_up", "大特写"]] as const;
const cameraAngles = [["eye_level", "平视"], ["low_angle", "仰拍"], ["high_angle", "俯拍"], ["dutch_angle", "倾斜镜头"], ["over_shoulder", "越肩"]] as const;

export function StoryboardEditor({
  chapterId,
  pages,
  characters,
  outfits,
  onReplan,
  replanPending,
  replanError,
}: {
  chapterId: string;
  pages: MangaPage[];
  characters: Character[];
  outfits: Outfit[];
  onReplan: (pageNumber: number) => void;
  replanPending: boolean;
  replanError?: Error | null;
}) {
  const queryClient = useQueryClient();
  const [pageId, setPageId] = useState(pages[0]?.id ?? "");
  const currentPage = pages.find((page) => page.id === pageId) ?? pages[0];
  const storyboard = useQuery({
    queryKey: ["storyboard", currentPage?.id],
    queryFn: () => api.storyboard(currentPage!.id),
    enabled: Boolean(currentPage),
  });
  const [panelId, setPanelId] = useState("");
  const selectedPanel = storyboard.data?.panels.find((panel) => panel.id === panelId) ?? storyboard.data?.panels[0] ?? null;
  const [editingPanel, setEditingPanel] = useState(false);
  const [panelDraft, setPanelDraft] = useState<PanelDraft | null>(null);
  const [dialogueDrafts, setDialogueDrafts] = useState<Record<string, DialogueDraft>>({});
  const [newDialogue, setNewDialogue] = useState<DialogueDraft | null>(null);
  const [notice, setNotice] = useState("");

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["storyboard", currentPage?.id] });
    queryClient.invalidateQueries({ queryKey: ["pages", chapterId] });
  };
  const savePanel = useMutation({
    mutationFn: () => api.updatePanel(selectedPanel!.id, { version: selectedPanel!.version, ...panelDraft! }),
    onSuccess: () => {
      setEditingPanel(false);
      setPanelDraft(null);
      setNotice("本格分镜已保存；本页及后续页已进入待复查状态。");
      refresh();
    },
  });
  const saveDialogue = useMutation({
    mutationFn: ({ dialogue, draft }: { dialogue: PanelDialogue; draft: DialogueDraft }) => api.updateDialogue(dialogue.id, { panel_version: selectedPanel!.version, ...draft }),
    onSuccess: (_, variables) => {
      setDialogueDrafts((values) => { const next = { ...values }; delete next[variables.dialogue.id]; return next; });
      setNotice("文字与说话人已保存，容量统计已重新计算。");
      refresh();
    },
  });
  const addDialogue = useMutation({
    mutationFn: () => api.createDialogue(selectedPanel!.id, { panel_version: selectedPanel!.version, ...newDialogue! }),
    onSuccess: () => {
      setNewDialogue(null);
      setNotice("已新增一个文字气泡。");
      refresh();
    },
  });
  const removeDialogue = useMutation({
    mutationFn: (dialogueId: string) => api.deleteDialogue(dialogueId, selectedPanel!.version),
    onSuccess: () => {
      setNotice("文字气泡已删除。");
      refresh();
    },
  });

  function beginPanel(panel: StoryboardPanel) {
    setPanelId(panel.id);
    setEditingPanel(true);
    setPanelDraft({
      shot_type: panel.shot_type,
      camera_angle: panel.camera_angle,
      camera_height: panel.camera_height,
      characters: [...panel.characters],
      outfits: { ...panel.outfits },
      actions: { ...panel.actions },
      expressions: { ...panel.expressions },
      background: panel.background,
      sound_effects: [...panel.sound_effects],
      bleed: panel.bleed,
      borderless: panel.borderless,
    });
    setNotice("");
  }

  function toggleCharacter(characterId: string) {
    if (!panelDraft) return;
    const selected = panelDraft.characters.includes(characterId);
    const charactersNext = selected ? panelDraft.characters.filter((id) => id !== characterId) : [...panelDraft.characters, characterId];
    const outfitsNext = { ...panelDraft.outfits };
    const expressionsNext = { ...panelDraft.expressions };
    if (selected) {
      delete outfitsNext[characterId];
      delete expressionsNext[characterId];
    }
    setPanelDraft({ ...panelDraft, characters: charactersNext, outfits: outfitsNext, expressions: expressionsNext });
  }

  const error = savePanel.error ?? saveDialogue.error ?? addDialogue.error ?? removeDialogue.error ?? replanError;
  if (!currentPage) return null;
  return <div className="storyboard-desk">
    <div className="storyboard-page-strip">{pages.map((page) => <button key={page.id} className={page.id === currentPage.id ? "active" : ""} onClick={() => { setPageId(page.id); setPanelId(""); setEditingPanel(false); }}><span>P.{String(page.page_number).padStart(3, "0")}</span><strong>{page.panel_count} 格</strong><small>{page.continuity_status === "NEEDS_REVIEW" ? "待复查" : page.selected_candidate_id ? "已采用" : "已规划"}</small></button>)}</div>
    <div className="storyboard-status"><div><strong>第 {currentPage.page_number} 页 · {currentPage.estimated_text_chars}/180 字 · {currentPage.estimated_bubbles}/8 气泡</strong><span>点击格子进入导演修订；修改不会删除已有候选。</span></div><button disabled={replanPending} onClick={() => onReplan(currentPage.page_number)}><RotateCcw size={12} />从本页重新计算</button></div>
    {notice && <p className="edit-notice"><Check size={13} />{notice}</p>}
    {error && <p className="form-error"><CircleAlert size={14} />{error.message}</p>}
    {storyboard.isLoading ? <div className="storyboard-loading">正在展开格子脚本…</div> : <div className="storyboard-worktable">
      <div className="panel-contact-sheet">{storyboard.data?.panels.map((panel) => <button key={panel.id} className={panel.id === selectedPanel?.id ? "panel-proof active" : "panel-proof"} onClick={() => { setPanelId(panel.id); setEditingPanel(false); setPanelDraft(null); }}>
        <span>格 {String(panel.reading_order).padStart(2, "0")}</span><div className="panel-proof-frame"><i>{shotTypes.find(([value]) => value === panel.shot_type)?.[1] ?? panel.shot_type}</i><strong>{panel.actions.script_action || panel.actions.source_text || "动作待补充"}</strong><small>{panel.background || "背景待补充"}</small></div><em>{panel.dialogues.length} 气泡 · {panel.characters.length} 人物</em>
      </button>)}</div>
      {selectedPanel && <aside className="panel-inspector">
        <header><div><span>P.{String(currentPage.page_number).padStart(3, "0")} / PANEL {String(selectedPanel.reading_order).padStart(2, "0")}</span><strong>分镜导演台</strong></div>{editingPanel ? <button onClick={() => { setEditingPanel(false); setPanelDraft(null); }}><X size={12} />退出编辑</button> : <button onClick={() => beginPanel(selectedPanel)}><Pencil size={12} />编辑本格</button>}</header>
        {editingPanel && panelDraft ? <div className="panel-edit-form">
          <div className="panel-edit-grid"><label><span>景别</span><select value={panelDraft.shot_type} onChange={(event) => setPanelDraft({ ...panelDraft, shot_type: event.target.value })}>{shotTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>镜头角度</span><select value={panelDraft.camera_angle} onChange={(event) => setPanelDraft({ ...panelDraft, camera_angle: event.target.value })}>{cameraAngles.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>机位高度</span><select value={panelDraft.camera_height} onChange={(event) => setPanelDraft({ ...panelDraft, camera_height: event.target.value })}><option value="eye_level">视线高度</option><option value="ground_level">贴地机位</option><option value="waist_level">腰部机位</option><option value="overhead">顶视机位</option></select></label></div>
          <label><span>动作与表演</span><textarea value={panelDraft.actions.script_action ?? ""} onChange={(event) => setPanelDraft({ ...panelDraft, actions: { ...panelDraft.actions, script_action: event.target.value } })} /></label>
          <label><span>背景</span><textarea value={panelDraft.background} onChange={(event) => setPanelDraft({ ...panelDraft, background: event.target.value })} /></label>
          <label><span>拟声词（用逗号分隔）</span><input value={panelDraft.sound_effects.map(String).join("，")} onChange={(event) => setPanelDraft({ ...panelDraft, sound_effects: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label>
          <fieldset><legend>入镜人物</legend><div className="character-checks">{characters.map((character) => <label key={character.id} className={panelDraft.characters.includes(character.id) ? "active" : ""}><input type="checkbox" checked={panelDraft.characters.includes(character.id)} onChange={() => toggleCharacter(character.id)} />{character.primary_name}</label>)}</div></fieldset>
          {panelDraft.characters.map((characterId) => {
            const character = characters.find((item) => item.id === characterId);
            const options = outfits.filter((item) => item.character_id === characterId);
            return <div className="character-direction" key={characterId}><strong>{character?.primary_name ?? characterId}</strong><label><span>表情</span><input value={panelDraft.expressions[characterId] ?? ""} onChange={(event) => setPanelDraft({ ...panelDraft, expressions: { ...panelDraft.expressions, [characterId]: event.target.value } })} /></label>{options.length > 0 && <label><span>服装</span><select value={panelDraft.outfits[characterId] ?? ""} onChange={(event) => setPanelDraft({ ...panelDraft, outfits: { ...panelDraft.outfits, [characterId]: event.target.value } })}><option value="">沿用场景服装</option>{options.map((outfit) => <option key={outfit.id} value={outfit.id}>{outfit.name}</option>)}</select></label>}</div>;
          })}
          <div className="panel-flags"><label><input type="checkbox" checked={panelDraft.bleed} onChange={(event) => setPanelDraft({ ...panelDraft, bleed: event.target.checked })} />出血格</label><label><input type="checkbox" checked={panelDraft.borderless} onChange={(event) => setPanelDraft({ ...panelDraft, borderless: event.target.checked })} />无边框</label></div>
          <button className="panel-save" disabled={savePanel.isPending || !panelDraft.actions.script_action?.trim()} onClick={() => savePanel.mutate()}><Save size={13} />保存本格分镜</button>
        </div> : <div className="panel-readout"><dl><div><dt>景别</dt><dd>{shotTypes.find(([value]) => value === selectedPanel.shot_type)?.[1] ?? selectedPanel.shot_type}</dd></div><div><dt>角度</dt><dd>{cameraAngles.find(([value]) => value === selectedPanel.camera_angle)?.[1] ?? selectedPanel.camera_angle}</dd></div><div><dt>动作</dt><dd>{selectedPanel.actions.script_action || "待补充"}</dd></div><div><dt>背景</dt><dd>{selectedPanel.background || "待补充"}</dd></div></dl><div className="panel-cast">{selectedPanel.characters.map((id) => <span key={id}>{characters.find((item) => item.id === id)?.primary_name ?? "未知角色"}</span>)}</div></div>}
        <section className="dialogue-editor"><header><div><span>LETTERING</span><strong>文字与气泡</strong></div><button onClick={() => setNewDialogue({ target_text: "", speaker_character_id: null, text_direction: "vertical", rewrite_forbidden: true })}><MessageSquarePlus size={12} />新增</button></header>
          {selectedPanel.dialogues.map((dialogue) => { const draft = dialogueDrafts[dialogue.id] ?? { target_text: dialogue.target_text, speaker_character_id: dialogue.speaker_character_id, text_direction: dialogue.text_direction, rewrite_forbidden: dialogue.rewrite_forbidden }; return <div className="dialogue-row" key={dialogue.id}><select aria-label="说话人" value={draft.speaker_character_id ?? ""} onChange={(event) => setDialogueDrafts({ ...dialogueDrafts, [dialogue.id]: { ...draft, speaker_character_id: event.target.value || null } })}><option value="">旁白 / 无说话人</option>{characters.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select><textarea aria-label="对白或旁白文字" value={draft.target_text} onChange={(event) => setDialogueDrafts({ ...dialogueDrafts, [dialogue.id]: { ...draft, target_text: event.target.value } })} /><div><select aria-label="排字方向" value={draft.text_direction} onChange={(event) => setDialogueDrafts({ ...dialogueDrafts, [dialogue.id]: { ...draft, text_direction: event.target.value as DialogueDraft["text_direction"] } })}><option value="vertical">竖排</option><option value="horizontal">横排</option></select><button disabled={!draft.target_text.trim() || saveDialogue.isPending} onClick={() => saveDialogue.mutate({ dialogue, draft })}><Save size={11} />保存</button><button aria-label="删除气泡" className="danger-action" disabled={removeDialogue.isPending} onClick={() => window.confirm("删除这个文字气泡？") && removeDialogue.mutate(dialogue.id)}><Trash2 size={11} /></button></div></div>; })}
          {newDialogue && <div className="dialogue-row new"><select aria-label="新增气泡说话人" value={newDialogue.speaker_character_id ?? ""} onChange={(event) => setNewDialogue({ ...newDialogue, speaker_character_id: event.target.value || null })}><option value="">旁白 / 无说话人</option>{characters.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select><textarea aria-label="新增气泡文字" autoFocus placeholder="输入对白或旁白" value={newDialogue.target_text} onChange={(event) => setNewDialogue({ ...newDialogue, target_text: event.target.value })} /><div><select aria-label="新增气泡排字方向" value={newDialogue.text_direction} onChange={(event) => setNewDialogue({ ...newDialogue, text_direction: event.target.value as DialogueDraft["text_direction"] })}><option value="vertical">竖排</option><option value="horizontal">横排</option></select><button onClick={() => setNewDialogue(null)}><X size={11} />取消</button><button disabled={!newDialogue.target_text.trim() || addDialogue.isPending} onClick={() => addDialogue.mutate()}><Save size={11} />新增</button></div></div>}
        </section>
      </aside>}
    </div>}
  </div>;
}
