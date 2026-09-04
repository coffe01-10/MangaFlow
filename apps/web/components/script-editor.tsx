"use client";

import {
  api,
  isConflictError,
  type Character,
  type Outfit,
  type SceneAsset,
  type Script,
  type ScriptBeat,
  type ScriptScene,
} from "@/lib/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, LoaderCircle, Pencil, RefreshCw, Save, Shirt, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";

import { ScenePicker } from "./project-workspace/scene-picker";
import { scriptStatusLabels } from "./project-workspace/labels";

type SceneDraft = Pick<ScriptScene, "location" | "time_label" | "weather" | "purpose" | "emotional_arc">;
type BeatDraft = Pick<ScriptBeat, "action" | "speaker_name" | "dialogue" | "narration" | "subtext" | "emotion" | "importance" | "must_visualize" | "mergeable" | "page_turn_hook">;

function sceneDraftDiffers(scene: ScriptScene, draft: SceneDraft) {
  return draft.location !== scene.location
    || draft.time_label !== scene.time_label
    || draft.weather !== scene.weather
    || draft.purpose !== scene.purpose
    || draft.emotional_arc !== scene.emotional_arc;
}

function beatDraftDiffers(beat: ScriptBeat, draft: BeatDraft) {
  return draft.action !== beat.action
    || draft.speaker_name !== beat.speaker_name
    || draft.dialogue !== beat.dialogue
    || draft.narration !== beat.narration
    || draft.subtext !== beat.subtext
    || draft.emotion !== beat.emotion
    || draft.importance !== beat.importance
    || draft.must_visualize !== beat.must_visualize
    || draft.mergeable !== beat.mergeable
    || draft.page_turn_hook !== beat.page_turn_hook;
}

export function ScriptEditor({
  chapterId,
  projectId,
  script,
  characters,
  outfits,
  sceneAssets,
  onAssignOutfit,
  assignOutfitError,
  assignOutfitPending,
  onDirtyChange,
}: {
  chapterId: string;
  projectId: string;
  script: Script;
  characters: Character[];
  outfits: Outfit[];
  sceneAssets: SceneAsset[];
  onAssignOutfit: (sceneId: string, assignments: Record<string, string>) => void;
  assignOutfitError?: Error | null;
  assignOutfitPending?: boolean;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [editingScene, setEditingScene] = useState<string | null>(null);
  const [sceneDraft, setSceneDraft] = useState<SceneDraft | null>(null);
  const [editingBeat, setEditingBeat] = useState<string | null>(null);
  const [beatDraft, setBeatDraft] = useState<BeatDraft | null>(null);
  const [notice, setNotice] = useState("");
  // Mirrors the storyboard editor's rule: opening an edit form alone is not
  // an edit — the leave guards arm only when a draft actually diverges from
  // the server scene/beat.
  const editorDirty = (editingScene !== null && sceneDraft !== null
    && script.scenes.some((scene) => scene.id === editingScene && sceneDraftDiffers(scene, sceneDraft)))
    || (editingBeat !== null && beatDraft !== null
      && script.scenes.some((scene) => scene.beats.some((beat) => beat.id === editingBeat && beatDraftDiffers(beat, beatDraft))));
  useEffect(() => { onDirtyChange?.(editorDirty); }, [editorDirty, onDirtyChange]);

  // Leave protection mirrors storyboard-editor: typed dialogue / scene drafts
  // are the highest-effort content here, so sidebar navigation and reloads
  // must confirm instead of silently discarding the open edit forms.
  useEffect(() => {
    if (!editorDirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target;
      const anchor = target instanceof Element ? target.closest("a[href]") : null;
      if (!anchor) return;
      const href = anchor.getAttribute("href") ?? "";
      if (!href.startsWith("/") || href === window.location.pathname) return;
      if (!window.confirm("当前场景 / 情节拍的修改尚未保存，离开页面会丢弃这些修改。仍要离开吗？")) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
    };
  }, [editorDirty]);

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["script", chapterId] });
    queryClient.invalidateQueries({ queryKey: ["pages", chapterId] });
  };
  const saveScene = useMutation({
    mutationFn: (scene: ScriptScene) => api.updateScene(scene.id, { version: scene.version, ...sceneDraft! }),
    onSuccess: () => {
      setEditingScene(null);
      setSceneDraft(null);
      setNotice("场景修改已保存；相关页面已标记为待复查。");
      refresh();
    },
  });
  const saveBeat = useMutation({
    mutationFn: (beat: ScriptBeat) => api.updateBeat(beat.id, { version: beat.version, ...beatDraft! }),
    onSuccess: () => {
      setEditingBeat(null);
      setBeatDraft(null);
      setNotice("情节拍修改已保存；分镜与历史候选保留，相关页面需复查。");
      refresh();
    },
  });
  const deleteScript = useMutation({
    mutationFn: () => api.deleteScript(chapterId),
    onSuccess: () => {
      setNotice("");
      refresh();
      queryClient.invalidateQueries({ queryKey: ["chapters"] });
    },
  });

  function beginScene(scene: ScriptScene) {
    setEditingScene(scene.id);
    setEditingBeat(null);
    setSceneDraft({
      location: scene.location,
      time_label: scene.time_label,
      weather: scene.weather,
      purpose: scene.purpose,
      emotional_arc: scene.emotional_arc,
    });
    setNotice("");
  }

  function beginBeat(beat: ScriptBeat) {
    setEditingBeat(beat.id);
    setEditingScene(null);
    setBeatDraft({
      action: beat.action,
      speaker_name: beat.speaker_name,
      dialogue: beat.dialogue,
      narration: beat.narration,
      subtext: beat.subtext,
      emotion: beat.emotion,
      importance: beat.importance,
      must_visualize: beat.must_visualize,
      mergeable: beat.mergeable,
      page_turn_hook: beat.page_turn_hook,
    });
    setNotice("");
  }

  const error = saveScene.error ?? saveBeat.error ?? deleteScript.error ?? assignOutfitError ?? null;
  const conflict = isConflictError(error);
  const refreshScript = () => {
    setEditingScene(null);
    setSceneDraft(null);
    setEditingBeat(null);
    setBeatDraft(null);
    void queryClient.invalidateQueries({ queryKey: ["script", chapterId] });
  };
  return <div className="script-scenes">
    <div className="script-coverage"><div><strong>原文覆盖 {Math.round((script.coverage.ratio ?? 0) * 100)}%</strong><span>{script.coverage.covered ?? 0} / {script.coverage.expected ?? 0} 个原文片段 · {scriptStatusLabels[script.status] ?? script.status}</span></div><button className="script-delete" type="button" disabled={deleteScript.isPending} onClick={() => { if (window.confirm("删除本章漫画剧本？分页、分镜和页面候选会同时从工作区移除；原文、素材文件与任务记录保留，之后可重新生成。")) deleteScript.mutate(); }}>{deleteScript.isPending ? <LoaderCircle className="spin" size={13} /> : <Trash2 size={13} />}删除剧本</button></div>
    <div className="revision-rule"><Pencil size={14} /><div><strong>导演修订模式</strong><span>场景与情节拍可直接修改；来源区间保持只读，避免剧情丢失。</span></div></div>
    {notice && <p className="edit-notice" role="status"><Check size={13} />{notice}</p>}
    {error && <p className="form-error" role="alert"><CircleAlert size={14} />{error.message}{conflict && <> · 内容已被其他页面修改。<button type="button" className="conflict-refresh" onClick={refreshScript}><RefreshCw size={12} />刷新数据</button>（将关闭当前编辑表单并载入最新版本）</>}</p>}
    {script.scenes.map((scene) => <section className={editingScene === scene.id ? "script-scene editing" : "script-scene"} key={scene.id}>
      {editingScene === scene.id && sceneDraft ? <div className="scene-edit-sheet">
        <header><div><span>SCENE {String(scene.ordinal).padStart(2, "0")} / 修订</span><strong>场景调度单</strong></div><div><button type="button" onClick={() => { setEditingScene(null); setSceneDraft(null); }}><X size={13} />取消</button><button type="button" className="save-edit" disabled={saveScene.isPending} onClick={() => saveScene.mutate(scene)}><Save size={13} />保存场景</button></div></header>
        <div className="scene-edit-grid">
          <label><span>地点（历史兜底，绑定资产时不会清空）</span><input value={sceneDraft.location} onChange={(event) => setSceneDraft({ ...sceneDraft, location: event.target.value })} /></label>
          <label><span>时间</span><input value={sceneDraft.time_label} onChange={(event) => setSceneDraft({ ...sceneDraft, time_label: event.target.value })} /></label>
          <label><span>天气 / 氛围</span><input value={sceneDraft.weather} onChange={(event) => setSceneDraft({ ...sceneDraft, weather: event.target.value })} /></label>
          <label className="wide"><span>本场目的</span><textarea value={sceneDraft.purpose} onChange={(event) => setSceneDraft({ ...sceneDraft, purpose: event.target.value })} /></label>
          <label className="wide"><span>情绪弧线</span><textarea value={sceneDraft.emotional_arc} onChange={(event) => setSceneDraft({ ...sceneDraft, emotional_arc: event.target.value })} /></label>
        </div>
      </div> : <header><span>SCENE {String(scene.ordinal).padStart(2, "0")}</span><strong>{scene.location || "未命名场景"} · {scene.time_label || "时间未定"}</strong><small>{scene.purpose}</small><button className="edit-mark" type="button" onClick={() => beginScene(scene)}><Pencil size={12} />编辑场景</button></header>}
      {editingScene !== scene.id && <p className="emotion-arc">情绪线：{scene.emotional_arc || "待补充"}{scene.weather ? ` · ${scene.weather}` : ""}</p>}
      <ScenePicker projectId={projectId} scene={scene} sceneAssets={sceneAssets} />
      <div className="scene-wardrobe"><strong><Shirt size={13} />本场服装指定</strong><div>{characters.map((character) => {
        const options = outfits.filter((outfit) => outfit.character_id === character.id);
        if (!options.length) return null;
        return <label key={character.id}><span>{character.primary_name}</span><select value={scene.outfit_assignments[character.id] ?? ""} disabled={assignOutfitPending} onChange={(event) => {
          const assignments = { ...scene.outfit_assignments };
          if (event.target.value) assignments[character.id] = event.target.value;
          else delete assignments[character.id];
          onAssignOutfit(scene.id, assignments);
        }}><option value="">未指定</option>{options.map((outfit) => <option key={outfit.id} value={outfit.id}>{outfit.name}</option>)}</select></label>;
      })}</div></div>
      <div className="beat-list">{scene.beats.map((beat) => <article className={editingBeat === beat.id ? "beat-row editing" : "beat-row"} key={beat.id}>
        <i>{String(beat.ordinal).padStart(2, "0")}</i>
        {editingBeat === beat.id && beatDraft ? <div className="beat-edit-sheet">
          <div className="beat-edit-heading"><strong>情节拍修订</strong><div><button type="button" onClick={() => { setEditingBeat(null); setBeatDraft(null); }}><X size={12} />取消</button><button className="save-edit" type="button" disabled={saveBeat.isPending || !beatDraft.action.trim()} onClick={() => saveBeat.mutate(beat)}><Save size={12} />保存</button></div></div>
          <label className="wide"><span>可视化动作</span><textarea value={beatDraft.action} onChange={(event) => setBeatDraft({ ...beatDraft, action: event.target.value })} /></label>
          <div className="beat-edit-grid"><label><span>说话人（可填绰号，保存后归一）</span><input value={beatDraft.speaker_name} onChange={(event) => setBeatDraft({ ...beatDraft, speaker_name: event.target.value })} /></label><label><span>情绪</span><input value={beatDraft.emotion} onChange={(event) => setBeatDraft({ ...beatDraft, emotion: event.target.value })} /></label></div>
          <div className="beat-edit-grid"><label><span>对白</span><textarea value={beatDraft.dialogue} onChange={(event) => setBeatDraft({ ...beatDraft, dialogue: event.target.value })} /></label><label><span>旁白</span><textarea value={beatDraft.narration} onChange={(event) => setBeatDraft({ ...beatDraft, narration: event.target.value })} /></label></div>
          <label className="wide"><span>潜台词 / 表演提示</span><input value={beatDraft.subtext} onChange={(event) => setBeatDraft({ ...beatDraft, subtext: event.target.value })} /></label>
          <div className="beat-flags"><label><input type="checkbox" checked={beatDraft.must_visualize} onChange={(event) => setBeatDraft({ ...beatDraft, must_visualize: event.target.checked })} />必须画出</label><label><input type="checkbox" checked={beatDraft.mergeable} onChange={(event) => setBeatDraft({ ...beatDraft, mergeable: event.target.checked })} />允许合并</label><label><input type="checkbox" checked={beatDraft.page_turn_hook} onChange={(event) => setBeatDraft({ ...beatDraft, page_turn_hook: event.target.checked })} />翻页悬念</label><label className="importance"><span>重要度 {Math.round(beatDraft.importance * 100)}%</span><input type="range" min="0" max="1" step="0.05" value={beatDraft.importance} onChange={(event) => setBeatDraft({ ...beatDraft, importance: Number(event.target.value) })} /></label></div>
        </div> : <div><div className="beat-read-heading"><strong>{beat.action || "动作待补充"}</strong><button type="button" onClick={() => beginBeat(beat)}><Pencil size={11} />修改</button></div>{beat.dialogue && <p><b>{beat.speaker_name || "说话人待确认"}</b>{beat.dialogue}</p>}{beat.narration && <p><b>旁白</b>{beat.narration}</p>}<small>{beat.emotion || "情绪未标注"} · 来源 {beat.source_range.segment_ids?.length ?? 0} 段 · V{beat.version}</small></div>}
      </article>)}</div>
    </section>)}
  </div>;
}
