"use client";

import {
  api,
  type Character,
  type CharacterPresence,
  type MangaPage,
  type Outfit,
  type PanelDialogue,
  type StoryboardPanel,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Maximize2, MessageSquarePlus, Minimize2, Pencil, RotateCcw, Save, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";

type PanelDraft = Pick<StoryboardPanel, "shot_type" | "camera_angle" | "camera_height" | "characters" | "character_presence" | "props" | "outfits" | "actions" | "expressions" | "background" | "sound_effects" | "bleed" | "borderless">;
type DialogueDraft = Pick<PanelDialogue, "target_text" | "speaker_character_id" | "text_direction" | "rewrite_forbidden">;

const shotTypes = [["establishing", "远景建立"], ["wide_action", "全景动作"], ["medium_close_up", "中近景"], ["close_up", "近景"], ["extreme_close_up", "大特写"]] as const;
const cameraAngles = [["eye_level", "平视"], ["low_angle", "仰拍"], ["high_angle", "俯拍"], ["dutch_angle", "倾斜镜头"], ["over_shoulder", "越肩"]] as const;

function DialogueCard({
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

export function StoryboardEditor({
  chapterId,
  pages,
  characters,
  outfits,
  onReplan,
  replanPending,
  replanError,
  initialPageId,
  focusCharacterId,
}: {
  chapterId: string;
  pages: MangaPage[];
  characters: Character[];
  outfits: Outfit[];
  onReplan: (pageNumber: number) => void;
  replanPending: boolean;
  replanError?: Error | null;
  initialPageId?: string | null;
  focusCharacterId?: string | null;
}) {
  const queryClient = useQueryClient();
  const [pageId, setPageId] = useState(
    pages.some((page) => page.id === initialPageId) ? initialPageId! : pages[0]?.id ?? "",
  );
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
  const [focusMode, setFocusMode] = useState(false);
  const [focusHandled, setFocusHandled] = useState(false);
  const [inspectorWidth, setInspectorWidth] = useState(() => {
    if (typeof window === "undefined") return 390;
    const stored = Number(window.localStorage.getItem("mangaflow.storyboard-inspector-width"));
    return stored >= 320 && stored <= 620 ? stored : 390;
  });

  const persistInspectorWidth = (value: number) => {
    const next = Math.min(620, Math.max(320, value));
    setInspectorWidth(next);
    window.localStorage.setItem("mangaflow.storyboard-inspector-width", String(next));
  };

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["storyboard", currentPage?.id] });
    queryClient.invalidateQueries({ queryKey: ["pages", chapterId] });
  };
  const savePanel = useMutation({
    mutationFn: () => api.updatePanel(selectedPanel!.id, { version: selectedPanel!.version, ...panelDraft! }),
    onSuccess: () => {
      setEditingPanel(false);
      setPanelDraft(null);
      setNotice(`已保存 · 当前 V${currentPage.storyboard_version + 1} · 将使 ${storyboard.data?.candidate_count ?? 0} 个候选过期`);
      refresh();
    },
  });
  const saveDialogue = useMutation({
    mutationFn: ({ dialogue, draft }: { dialogue: PanelDialogue; draft: DialogueDraft }) => api.updateDialogue(dialogue.id, { panel_version: selectedPanel!.version, ...draft }),
    onSuccess: (_, variables) => {
      setDialogueDrafts((values) => { const next = { ...values }; delete next[variables.dialogue.id]; return next; });
      setNotice(`已保存 · 当前 V${currentPage.storyboard_version + 1} · 将使 ${storyboard.data?.candidate_count ?? 0} 个候选过期`);
      refresh();
    },
  });
  const addDialogue = useMutation({
    mutationFn: () => api.createDialogue(selectedPanel!.id, { panel_version: selectedPanel!.version, ...newDialogue! }),
    onSuccess: () => {
      setNewDialogue(null);
      setNotice(`已保存 · 当前 V${currentPage.storyboard_version + 1} · 将使 ${storyboard.data?.candidate_count ?? 0} 个候选过期`);
      refresh();
    },
  });
  const removeDialogue = useMutation({
    mutationFn: (dialogueId: string) => api.deleteDialogue(dialogueId, selectedPanel!.version),
    onSuccess: () => {
      setNotice(`已保存 · 当前 V${currentPage.storyboard_version + 1} · 将使 ${storyboard.data?.candidate_count ?? 0} 个候选过期`);
      refresh();
    },
  });
  const updateLayout = useMutation({
    mutationFn: ({ panelCount, layoutMode }: { panelCount: number; layoutMode: "dynamic" | "balanced" }) =>
      api.updatePageLayout(currentPage.id, panelCount, layoutMode),
    onSuccess: () => {
      setPanelId("");
      setEditingPanel(false);
      setNotice(`已保存 · 当前 V${currentPage.storyboard_version + 1} · 将使 ${storyboard.data?.candidate_count ?? 0} 个候选过期`);
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
      character_presence: Object.keys(panel.character_presence ?? {}).length
        ? { ...panel.character_presence }
        : Object.fromEntries(panel.characters.map((characterId) => [characterId, "VISIBLE" as const])),
      props: [...(panel.props ?? [])],
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

  useEffect(() => {
    if (focusHandled || !focusCharacterId || !storyboard.data?.panels.length) return;
    const targetPanel = storyboard.data.panels.find((panel) => {
      const presence = panel.character_presence?.[focusCharacterId]
        ?? (panel.characters.includes(focusCharacterId) ? "VISIBLE" : null);
      return presence === "VISIBLE" && !panel.outfits?.[focusCharacterId];
    });
    if (!targetPanel) return;
    setPanelId(targetPanel.id);
    setEditingPanel(true);
    setPanelDraft({
      shot_type: targetPanel.shot_type,
      camera_angle: targetPanel.camera_angle,
      camera_height: targetPanel.camera_height,
      characters: [...targetPanel.characters],
      character_presence: Object.keys(targetPanel.character_presence ?? {}).length
        ? { ...targetPanel.character_presence }
        : Object.fromEntries(
          targetPanel.characters.map((characterId) => [characterId, "VISIBLE" as const]),
        ),
      props: [...(targetPanel.props ?? [])],
      outfits: { ...targetPanel.outfits },
      actions: { ...targetPanel.actions },
      expressions: { ...targetPanel.expressions },
      background: targetPanel.background,
      sound_effects: [...targetPanel.sound_effects],
      bleed: targetPanel.bleed,
      borderless: targetPanel.borderless,
    });
    setNotice("已定位到缺少服装的出镜格，请在人物下方选择服装并保存本格分镜。");
    setFocusHandled(true);
  }, [focusCharacterId, focusHandled, storyboard.data?.panels]);

  function setPresence(characterId: string, presence: CharacterPresence | "NONE") {
    if (!panelDraft) return;
    const presenceNext = { ...panelDraft.character_presence };
    if (presence === "NONE") delete presenceNext[characterId];
    else presenceNext[characterId] = presence;
    const charactersNext = Object.entries(presenceNext).filter(([, value]) => value === "VISIBLE").map(([id]) => id);
    const outfitsNext = { ...panelDraft.outfits };
    const expressionsNext = { ...panelDraft.expressions };
    if (presence !== "VISIBLE") {
      delete outfitsNext[characterId];
      delete expressionsNext[characterId];
    }
    setPanelDraft({ ...panelDraft, characters: charactersNext, character_presence: presenceNext, outfits: outfitsNext, expressions: expressionsNext });
  }

  const error = savePanel.error ?? saveDialogue.error ?? addDialogue.error ?? removeDialogue.error ?? updateLayout.error ?? replanError;
  const saving = savePanel.isPending || saveDialogue.isPending || addDialogue.isPending || removeDialogue.isPending || updateLayout.isPending || replanPending;
  const saveStatus = saving ? "保存中" : error ? "保存失败" : "已保存";
  if (!currentPage) return null;
  return <div className={focusMode ? "storyboard-desk focus-mode" : "storyboard-desk"}>
    <div className={`storyboard-save-status ${error ? "failed" : saving ? "saving" : "saved"}`} aria-live="polite"><span>{saveStatus}</span><strong>当前 V{storyboard.data?.page.storyboard_version ?? currentPage.storyboard_version}</strong><button type="button" aria-pressed={focusMode} onClick={() => setFocusMode((value) => !value)}>{focusMode ? <Minimize2 size={14} /> : <Maximize2 size={14} />}{focusMode ? "退出专注" : "画布专注"}</button></div>
    <label className="storyboard-page-select"><span>当前页面</span><select value={currentPage.id} onChange={(event) => { setPageId(event.target.value); setPanelId(""); setEditingPanel(false); }}>{pages.map((page) => <option key={page.id} value={page.id}>第 {page.page_number} 页 · {page.panel_count} 格</option>)}</select></label>
    <div className="storyboard-page-strip">{pages.map((page) => <button key={page.id} className={page.id === currentPage.id ? "active" : ""} onClick={() => { setPageId(page.id); setPanelId(""); setEditingPanel(false); }}><span>P.{String(page.page_number).padStart(3, "0")}</span><strong>{page.panel_count} 格</strong><small>{page.continuity_status === "NEEDS_REVIEW" ? "待复查" : page.selected_candidate_id ? "已采用" : "已规划"}</small></button>)}</div>
    <div className="storyboard-status"><div><strong>第 {currentPage.page_number} 页 · {currentPage.estimated_text_chars}/180 字 · {currentPage.estimated_bubbles}/8 气泡</strong><span>来自漫画剧本：{currentPage.scene_ids.length} 个场景 · {currentPage.beat_ids.length} 个情节拍；修改不会删除已有候选。</span></div><button disabled={replanPending} onClick={() => onReplan(currentPage.page_number)}><RotateCcw size={12} />从本页重新计算</button></div>
    <div className="storyboard-layout-controls"><div><span>本页格数</span>{[3, 4, 5].map((count) => <button type="button" className={currentPage.panel_count === count ? "active" : ""} disabled={updateLayout.isPending} key={count} onClick={() => updateLayout.mutate({ panelCount: count, layoutMode: currentPage.source_coverage.layout_mode ?? "dynamic" })}>{count} 格</button>)}</div><div><span>版式</span><button type="button" className={(currentPage.source_coverage.layout_mode ?? "dynamic") === "dynamic" ? "active" : ""} disabled={updateLayout.isPending} onClick={() => updateLayout.mutate({ panelCount: currentPage.panel_count, layoutMode: "dynamic" })}>动态错落</button><button type="button" className={currentPage.source_coverage.layout_mode === "balanced" ? "active" : ""} disabled={updateLayout.isPending} onClick={() => updateLayout.mutate({ panelCount: currentPage.panel_count, layoutMode: "balanced" })}>均衡网格</button></div><p>格子数量与版式只重排当前页；内容仍从已确认剧本与原文区间重新映射。</p></div>
    {notice && <p className="edit-notice"><Check size={13} />{notice}</p>}
    {error && <p className="form-error"><CircleAlert size={14} />{error.message}</p>}
    {storyboard.isLoading ? <div className="storyboard-loading">正在展开格子脚本…</div> : <div className="storyboard-worktable" style={{ "--inspector-width": `${inspectorWidth}px` } as CSSProperties}>
      <div className="panel-contact-sheet">{storyboard.data?.panels.map((panel) => <button key={panel.id} style={{ left: `${(panel.bounds.x ?? 0) * 100}%`, top: `${(panel.bounds.y ?? 0) * 100}%`, width: `${(panel.bounds.width ?? 1) * 100}%`, height: `${(panel.bounds.height ?? 1) * 100}%` } as CSSProperties} className={panel.id === selectedPanel?.id ? "panel-proof active" : "panel-proof"} onClick={() => { setPanelId(panel.id); setEditingPanel(false); setPanelDraft(null); }}>
        <span>格 {String(panel.reading_order).padStart(2, "0")}</span><div className="panel-proof-frame"><i>{shotTypes.find(([value]) => value === panel.shot_type)?.[1] ?? panel.shot_type}</i><strong>{panel.actions.script_action || panel.actions.source_text || "动作待补充"}</strong><small>{panel.background || "背景待补充"}</small></div><em>{panel.dialogues.length} 气泡 · {panel.characters.length} 人物 · {panel.props?.length ?? 0} 道具</em>
      </button>)}</div>
      {selectedPanel && <div className="panel-inspector-resizer" role="separator" aria-label="调整属性面板宽度" aria-orientation="vertical" aria-valuemin={320} aria-valuemax={620} aria-valuenow={inspectorWidth} tabIndex={0} onKeyDown={(event) => { if (event.key === "ArrowLeft") persistInspectorWidth(inspectorWidth + 16); if (event.key === "ArrowRight") persistInspectorWidth(inspectorWidth - 16); }} onPointerDown={(event) => { event.currentTarget.setPointerCapture(event.pointerId); const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) persistInspectorWidth(worktable.right - event.clientX); }} onPointerMove={(event) => { if (!event.currentTarget.hasPointerCapture(event.pointerId)) return; const worktable = event.currentTarget.parentElement?.getBoundingClientRect(); if (worktable) persistInspectorWidth(worktable.right - event.clientX); }} onPointerUp={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} onPointerCancel={(event) => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}><span /></div>}
      {selectedPanel && <aside className="panel-inspector">
        <header><div><span>P.{String(currentPage.page_number).padStart(3, "0")} / PANEL {String(selectedPanel.reading_order).padStart(2, "0")}</span><strong>分镜导演台</strong></div>{editingPanel ? <button onClick={() => { setEditingPanel(false); setPanelDraft(null); }}><X size={12} />退出编辑</button> : <button onClick={() => beginPanel(selectedPanel)}><Pencil size={12} />编辑本格</button>}</header>
        {editingPanel && panelDraft ? <div className="panel-edit-form">
          <div className="panel-edit-grid"><label><span>景别</span><select value={panelDraft.shot_type} onChange={(event) => setPanelDraft({ ...panelDraft, shot_type: event.target.value })}>{shotTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>镜头角度</span><select value={panelDraft.camera_angle} onChange={(event) => setPanelDraft({ ...panelDraft, camera_angle: event.target.value })}>{cameraAngles.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label><span>机位高度</span><select value={panelDraft.camera_height} onChange={(event) => setPanelDraft({ ...panelDraft, camera_height: event.target.value })}><option value="eye_level">视线高度</option><option value="ground_level">贴地机位</option><option value="waist_level">腰部机位</option><option value="overhead">顶视机位</option></select></label></div>
          <label><span>动作与表演</span><textarea value={panelDraft.actions.script_action ?? ""} onChange={(event) => setPanelDraft({ ...panelDraft, actions: { ...panelDraft.actions, script_action: event.target.value } })} /></label>
          <label><span>背景</span><textarea value={panelDraft.background} onChange={(event) => setPanelDraft({ ...panelDraft, background: event.target.value })} /></label>
          <label><span>场景道具（用逗号分隔）</span><input value={panelDraft.props.join("，")} onChange={(event) => setPanelDraft({ ...panelDraft, props: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} placeholder="例如：爸爸的灵牌、香炉、白菊" /></label>
          <label><span>拟声词（用逗号分隔）</span><input value={panelDraft.sound_effects.map(String).join("，")} onChange={(event) => setPanelDraft({ ...panelDraft, sound_effects: event.target.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean) })} /></label>
          <fieldset><legend>人物状态</legend><p className="presence-help">只有“实际出镜”会要求人物与服装参考；画外音和被提及人物不会阻塞生图。</p><div className="character-presence-grid">{characters.map((character) => { const presence = (panelDraft.character_presence[character.id] || "NONE") as CharacterPresence | "NONE"; return <label key={character.id} className={presence !== "NONE" ? `active presence-${presence.toLowerCase()}` : ""}><strong>{character.primary_name}</strong><select aria-label={`${character.primary_name}人物状态`} value={presence} onChange={(event) => setPresence(character.id, event.target.value as CharacterPresence | "NONE")}><option value="NONE">不在本格</option><option value="VISIBLE">实际出镜</option><option value="OFFSCREEN">画外人物</option><option value="MENTIONED">仅被提及</option></select></label>; })}</div></fieldset>
          {panelDraft.characters.map((characterId) => {
            const character = characters.find((item) => item.id === characterId);
            const options = outfits.filter((item) => item.character_id === characterId);
            return <div className="character-direction" key={characterId}><strong>{character?.primary_name ?? characterId}</strong><label><span>表情</span><input value={panelDraft.expressions[characterId] ?? ""} onChange={(event) => setPanelDraft({ ...panelDraft, expressions: { ...panelDraft.expressions, [characterId]: event.target.value } })} /></label>{options.length > 0 && <label><span>服装</span><select value={panelDraft.outfits[characterId] ?? ""} onChange={(event) => setPanelDraft({ ...panelDraft, outfits: { ...panelDraft.outfits, [characterId]: event.target.value } })}><option value="">沿用场景服装</option>{options.map((outfit) => <option key={outfit.id} value={outfit.id}>{outfit.name}</option>)}</select></label>}</div>;
          })}
          <div className="panel-flags"><label><input type="checkbox" checked={panelDraft.bleed} onChange={(event) => setPanelDraft({ ...panelDraft, bleed: event.target.checked })} />出血格</label><label><input type="checkbox" checked={panelDraft.borderless} onChange={(event) => setPanelDraft({ ...panelDraft, borderless: event.target.checked })} />无边框</label></div>
          <button className="panel-save" disabled={savePanel.isPending || !panelDraft.actions.script_action?.trim()} onClick={() => savePanel.mutate()}><Save size={13} />保存本格分镜</button>
        </div> : <div className="panel-readout"><dl><div><dt>景别</dt><dd>{shotTypes.find(([value]) => value === selectedPanel.shot_type)?.[1] ?? selectedPanel.shot_type}</dd></div><div><dt>角度</dt><dd>{cameraAngles.find(([value]) => value === selectedPanel.camera_angle)?.[1] ?? selectedPanel.camera_angle}</dd></div><div><dt>动作</dt><dd>{selectedPanel.actions.script_action || "待补充"}</dd></div><div><dt>背景</dt><dd>{selectedPanel.background || "待补充"}</dd></div><div><dt>道具</dt><dd>{selectedPanel.props?.join("、") || "无"}</dd></div></dl><div className="panel-presence-readout">{Object.entries(selectedPanel.character_presence ?? Object.fromEntries(selectedPanel.characters.map((id) => [id, "VISIBLE"]))).map(([id, presence]) => <span className={`presence-${String(presence).toLowerCase()}`} key={id}>{characters.find((item) => item.id === id)?.primary_name ?? "未知角色"} · {presence === "VISIBLE" ? "实际出镜" : presence === "OFFSCREEN" ? "画外" : "提及"}</span>)}</div></div>}
        <section className="dialogue-editor"><header><div><span>LETTERING</span><strong>文字与气泡</strong><small>{selectedPanel.dialogues.length} 个气泡 · 本格文字单独校对</small></div><button type="button" disabled={Boolean(newDialogue)} onClick={() => setNewDialogue({ target_text: "", speaker_character_id: null, text_direction: "vertical", rewrite_forbidden: true })}><MessageSquarePlus size={12} />新增气泡</button></header>
          <div className="dialogue-stack">{selectedPanel.dialogues.map((dialogue, index) => { const draft = dialogueDrafts[dialogue.id] ?? { target_text: dialogue.target_text, speaker_character_id: dialogue.speaker_character_id, text_direction: dialogue.text_direction, rewrite_forbidden: dialogue.rewrite_forbidden }; return <DialogueCard key={dialogue.id} index={index + 1} draft={draft} characters={characters} busy={saveDialogue.isPending || removeDialogue.isPending} onChange={(value) => setDialogueDrafts({ ...dialogueDrafts, [dialogue.id]: value })} onSave={() => saveDialogue.mutate({ dialogue, draft })} onDelete={() => window.confirm("删除这个文字气泡？") && removeDialogue.mutate(dialogue.id)} />; })}
          {newDialogue && <DialogueCard index={selectedPanel.dialogues.length + 1} draft={newDialogue} characters={characters} isNew busy={addDialogue.isPending} onChange={setNewDialogue} onSave={() => addDialogue.mutate()} onCancel={() => setNewDialogue(null)} />}</div>
        </section>
      </aside>}
    </div>}
  </div>;
}
