"use client";

import {
  api,
  originUrl,
  publicUrl,
  type Character,
  type ImageModelAlias,
  type PageCandidate,
  type StyleProfile,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, LoaderCircle, Palette, Plus, RotateCcw, Sparkles, Trash2 } from "lucide-react";
import Image from "next/image";
import { useEffect, useMemo, useState } from "react";
import { activePollInterval } from "@/lib/task-status";

import { candidateStatusLabels } from "./project-workspace/labels";

interface CharacterConceptDraft {
  appearance: string;
  outfitName: string;
  outfitDescription: string;
  lockedFields: string;
}

function conceptDraftKey(projectId: string, characterId: string) {
  return `mangaflow:character-concept-draft:${projectId}:${characterId}`;
}

function CandidatePreview({ candidate, label, onOpen }: { candidate: PageCandidate; label: string; onOpen: (url: string, label: string) => void }) {
  const thumbnail = publicUrl(candidate.thumbnail_url ?? candidate.content_url);
  const full = originUrl(candidate.content_url ?? candidate.thumbnail_url);
  return thumbnail ? <button type="button" className="production-candidate-image" onClick={() => full && onOpen(full, label)}><Image src={thumbnail} alt={label} width={640} height={640} loading="eager" unoptimized /><span>查看大图</span></button> : candidate.status === "FAILED" ? <div className="candidate-placeholder failed"><CircleAlert size={20} /><span>生成失败</span></div> : <div className="candidate-placeholder"><LoaderCircle className="spin" size={20} /><span>等待 Worker 生成</span></div>;
}

function promptPreview(candidate: PageCandidate) {
  return typeof candidate.prompt_snapshot.prompt_preview === "string" ? candidate.prompt_snapshot.prompt_preview : "生成任务开始后会保存实际提示词。";
}

export function CharacterConceptPanel({
  projectId,
  character,
  model,
  onOpen,
}: {
  projectId: string;
  character: Character;
  model: ImageModelAlias | null;
  onOpen: (url: string, label: string) => void;
}) {
  const queryClient = useQueryClient();
  const [appearance, setAppearance] = useState("");
  const [outfitName, setOutfitName] = useState("");
  const [outfitDescription, setOutfitDescription] = useState("");
  const [lockedFields, setLockedFields] = useState("");
  const [draftReady, setDraftReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let draft: Partial<CharacterConceptDraft> = {};
    try {
      const saved = window.localStorage.getItem(conceptDraftKey(projectId, character.id));
      if (saved) {
        draft = JSON.parse(saved) as Partial<CharacterConceptDraft>;
      }
    } catch {
      window.localStorage.removeItem(conceptDraftKey(projectId, character.id));
    }
    queueMicrotask(() => {
      if (!cancelled) {
        setAppearance(typeof draft.appearance === "string" ? draft.appearance : "");
        setOutfitName(typeof draft.outfitName === "string" ? draft.outfitName : "");
        setOutfitDescription(
          typeof draft.outfitDescription === "string" ? draft.outfitDescription : "",
        );
        setLockedFields(typeof draft.lockedFields === "string" ? draft.lockedFields : "");
        setDraftReady(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [character.id, projectId]);

  useEffect(() => {
    if (!draftReady) return;
    const draft: CharacterConceptDraft = {
      appearance,
      outfitName,
      outfitDescription,
      lockedFields,
    };
    window.localStorage.setItem(
      conceptDraftKey(projectId, character.id),
      JSON.stringify(draft),
    );
  }, [appearance, character.id, draftReady, lockedFields, outfitDescription, outfitName, projectId]);

  const batches = useQuery({
    queryKey: ["asset-batches", "CHARACTER", character.id],
    queryFn: () => api.assetBatches("CHARACTER", character.id),
  });
  const batch = batches.data?.[0] ?? null;
  const candidates = useQuery({
    queryKey: ["asset-candidates", batch?.id],
    queryFn: () => api.candidates(batch!.id),
    enabled: Boolean(batch),
    refetchInterval: (query) => activePollInterval(query.state.data, 2000),
  });
  const jobs = useQuery({
    queryKey: ["jobs", projectId, false],
    queryFn: () => api.jobs(projectId),
    refetchInterval: (query) => activePollInterval(query.state.data, 3000),
  });

  const queueConcept = () => {
    if (!model) throw new Error("请先在上方选择本次素材生成模型");
    return api.generateCompleteCharacterSheet(character.id, model, "1K", {
      appearance_description: appearance.trim(),
      outfit_name: outfitName.trim(),
      outfit_description: outfitDescription.trim(),
    });
  };
  const generate = useMutation({
    mutationFn: queueConcept,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-batches", "CHARACTER", character.id] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });
  const approve = useMutation({
    mutationFn: (candidateId: string) => api.approveAssetReference(candidateId, {
      character_id: character.id,
      bind_character_reference: true,
      set_canonical: true,
      outfit_name: outfitName.trim() || undefined,
      outfit_description: outfitName.trim() ? outfitDescription.trim() : undefined,
      outfit_locked_fields: lockedFields.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["characters", projectId] });
      queryClient.invalidateQueries({ queryKey: ["outfits", projectId] });
      queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });
  const rejectAndRedraw = useMutation({
    mutationFn: async (candidateId: string) => {
      await api.deleteCandidate(candidateId);
      return queueConcept();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-batches", "CHARACTER", character.id] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });
  const deleteFailed = useMutation({
    mutationFn: (candidateId: string) => api.deleteCandidate(candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-batches", "CHARACTER", character.id] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", projectId] });
    },
  });
  const retractApproval = useMutation({
    mutationFn: (candidateId: string) => api.retractAssetReference(candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["characters", projectId] });
      queryClient.invalidateQueries({ queryKey: ["outfits", projectId] });
      queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });

  const error = generate.error ?? approve.error ?? deleteFailed.error ?? rejectAndRedraw.error ?? retractApproval.error;
  return <section className="asset-production-panel concept-production-panel">
    <header><div><span>AI CONCEPT / 待确认草稿</span><strong>一张综合设定页，同时建立人物与服装规范</strong></div><small>{character.references.length ? "已有参考也可重做" : "无需已有参考图"}</small></header>
    <div className="concept-form-grid">
      <label><span>人物外观描述</span><textarea value={appearance} onChange={(event) => setAppearance(event.target.value)} placeholder="简述外貌与气质；可留空" /></label>
      <label><span>服装档案名称</span><input value={outfitName} onChange={(event) => setOutfitName(event.target.value)} placeholder="给这套服装起个名称" /></label>
      <label><span>服装描述</span><textarea value={outfitDescription} onChange={(event) => setOutfitDescription(event.target.value)} placeholder="简述款式、颜色与场景" /></label>
      <label><span>确认后锁定项</span><input value={lockedFields} onChange={(event) => setLockedFields(event.target.value)} placeholder="填写必须保持一致的特征" /></label>
    </div>
    <div className="production-action-row"><p>输出为 1 张彩色综合页：正面、侧面、背面、表情、剪裁与配饰细节。生成结果不会自动成为规范参考。</p><button type="button" disabled={!model || !outfitName.trim() || !outfitDescription.trim() || generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? <LoaderCircle className="spin" size={14} /> : <Sparkles size={14} />}{candidates.data?.length ? "编辑描述后重抽" : "生成概念设定草稿"}</button></div>
    {error ? <p className="form-error"><CircleAlert size={14} />{error.message}</p> : null}
    <div className="production-candidate-grid">{candidates.data?.slice(0, 2).map((candidate) => {
      const approval = candidate.prompt_snapshot.reference_approval as { approved?: boolean } | undefined;
      const approved = Boolean(approval?.approved);
      const candidateJob = jobs.data?.find((job) => job.id === candidate.job_id);
      const failureMessage = candidate.status === "FAILED"
        ? candidateJob?.error_message ?? "生成失败，任务未返回详细原因。"
        : null;
      return <article key={candidate.id} className={approved ? "approved" : candidate.status === "FAILED" ? "failed" : ""}><CandidatePreview candidate={candidate} label={`${character.primary_name} 综合设定草稿 ${candidate.ordinal}`} onOpen={onOpen} /><div><span>CONCEPT {String(candidate.ordinal).padStart(2, "0")}</span><strong>{approved ? "已确认为规范参考" : candidate.status === "FAILED" ? "生成失败" : "等待人工确认"}</strong><small>{candidateStatusLabels[candidate.status] ?? candidate.status} · {candidate.resolution}</small>{failureMessage ? <p className="candidate-inline-error"><CircleAlert size={13} />{failureMessage}</p> : null}<details><summary>查看实际提示词</summary><p>{promptPreview(candidate)}</p></details><div className="candidate-decision-row">{candidate.status === "FAILED" ? <><button type="button" className="secondary" disabled={deleteFailed.isPending || rejectAndRedraw.isPending} onClick={() => window.confirm("只删除这条失败记录？") && deleteFailed.mutate(candidate.id)}><Trash2 size={13} />删除记录</button><button type="button" disabled={!model || !outfitName.trim() || !outfitDescription.trim() || deleteFailed.isPending || rejectAndRedraw.isPending} onClick={() => window.confirm("删除失败记录并按当前描述重新生成？") && rejectAndRedraw.mutate(candidate.id)}><RotateCcw size={13} />重新生成</button></> : <><button type="button" className="secondary" disabled={(approved && !candidate.asset_id) || rejectAndRedraw.isPending || retractApproval.isPending} onClick={() => approved ? (window.confirm("撤销采用这张设定图？人物与服装绑定会解除，图片仍保留在素材库。") && retractApproval.mutate(candidate.id)) : (window.confirm("拒绝这张草稿并按当前描述重新生成？") && rejectAndRedraw.mutate(candidate.id))}><RotateCcw size={13} />{approved ? "撤销采用" : "拒绝并重抽"}</button><button type="button" disabled={!candidate.asset_id || approved || approve.isPending} onClick={() => approve.mutate(candidate.id)}><Check size={13} />{approved ? "已绑定人物与服装" : "确认并设为规范参考"}</button></>}</div></div></article>;
    })}</div>
    {(candidates.data?.length ?? 0) > 2 ? <p className="asset-result-empty">另有 {(candidates.data?.length ?? 0) - 2} 个历史候选，可在生成素材库中查看。</p> : null}
    {!candidates.data?.length ? <p className="asset-result-empty">第一张草稿生成后会实时出现在这里；确认前不会进入正式页面提示词。</p> : null}
  </section>;
}

function paletteFromProfile(style: StyleProfile) {
  const source = (
    style.profile.palette_confirmed
      ? style.profile.palette ?? style.profile.palette_draft ?? {}
      : style.profile.palette_draft ?? style.profile.palette ?? {}
  ) as Record<string, unknown>;
  return Object.entries(source).map(([name, value]) => ({ name, value: typeof value === "string" ? value : JSON.stringify(value) }));
}

export function StyleProductionPanel({
  projectId,
  style,
  model,
  active,
  onOpen,
}: {
  projectId: string;
  style: StyleProfile;
  model: ImageModelAlias | null;
  active: boolean;
  onOpen: (url: string, label: string) => void;
}) {
  const queryClient = useQueryClient();
  const [atmosphere, setAtmosphere] = useState("葬礼后的克制情绪、潮湿京都、低饱和但保留人物识别色");
  const [paletteRows, setPaletteRows] = useState(() => paletteFromProfile(style));

  const batches = useQuery({
    queryKey: ["asset-batches", "STYLE", style.id],
    queryFn: () => api.assetBatches("STYLE", style.id),
  });
  const batch = batches.data?.[0] ?? null;
  const candidates = useQuery({
    queryKey: ["asset-candidates", batch?.id],
    queryFn: () => api.candidates(batch!.id),
    enabled: Boolean(batch),
    refetchInterval: (query) => activePollInterval(query.state.data, 2000),
  });
  const hasReadyStyleTest = Boolean(candidates.data?.some((candidate) => candidate.variant === "STYLE_TEST" && candidate.status === "READY"));
  useEffect(() => {
    if (style.status === "DRAFT" && hasReadyStyleTest) {
      queryClient.invalidateQueries({ queryKey: ["styles", projectId] });
    }
  }, [hasReadyStyleTest, projectId, queryClient, style.status]);
  const palette = useMemo(() => Object.fromEntries(paletteRows.filter((row) => row.name.trim() && row.value.trim()).map((row) => [row.name.trim(), row.value.trim()])), [paletteRows]);
  const paletteConfirmed = Boolean(style.profile.palette_confirmed);
  const testApproved = Boolean(style.profile.test_image_approved);
  const approvedCandidateId = typeof style.profile.test_candidate_id === "string" ? style.profile.test_candidate_id : null;

  const draftPalette = useMutation({
    mutationFn: () => api.draftStylePalette(style.id, atmosphere.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["styles", projectId] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });
  const approvePalette = useMutation({
    mutationFn: () => api.approveStylePalette(style.id, style.version, palette),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["styles", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });
  const generateTest = useMutation({
    mutationFn: async () => {
      if (!model) throw new Error("请先在上方选择本次素材生成模型");
      const created = await api.startAssetBatch("STYLE", style.id, "STYLE_TEST");
      return api.generateAssetCandidate(created.id, model, "1K", "STYLE_TEST");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-batches", "STYLE", style.id] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", projectId] });
    },
  });
  const approveTest = useMutation({
    mutationFn: (candidateId: string) => api.approveStyleTest(style.id, style.version, candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["styles", projectId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });
  const activate = useMutation({
    mutationFn: () => api.activateStyle(projectId, style.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["styles", projectId] });
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });
  const error = draftPalette.error ?? approvePalette.error ?? generateTest.error ?? approveTest.error ?? activate.error;
  const activationTarget = !paletteRows.length
    ? `style-palette-draft-${style.id}`
    : !paletteConfirmed
      ? `style-palette-confirm-${style.id}`
      : !testApproved
        ? `style-test-${style.id}`
        : null;
  const activationLabel = active
    ? "已用于正式页面"
    : !paletteRows.length
      ? "先生成色板"
      : !paletteConfirmed
        ? "先确认色板"
        : !testApproved
          ? "先通过测试图"
          : "激活彩色风格";
  const activationHint = active
    ? "该档案已经进入正式页面提示词。"
    : !paletteRows.length
      ? "还缺：先在第 01 步生成 AI 色板草稿。"
      : !paletteConfirmed
        ? "还缺：在第 02 步检查并确认彩色色板。"
        : !testApproved
          ? "还缺：生成 1K 风格测试图并点击人工通过。"
          : "色板和测试图均已确认，可以激活为正式风格。";
  const handleActivation = () => {
    if (activationTarget) {
      document.getElementById(activationTarget)?.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    activate.mutate();
  };

  return <div className="style-production-pipeline">
    <div className="pipeline-stage" id={`style-palette-draft-${style.id}`}><header><span>01 / AI 色板草稿</span><small>{style.status === "ANALYZING" ? "正在分析" : paletteRows.length ? `${paletteRows.length} 项` : "尚未生成"}</small></header><label><span>章节氛围</span><textarea value={atmosphere} onChange={(event) => setAtmosphere(event.target.value)} /></label><button type="button" disabled={style.color_mode !== "color" || draftPalette.isPending || style.status === "ANALYZING"} onClick={() => draftPalette.mutate()}>{draftPalette.isPending || style.status === "ANALYZING" ? <LoaderCircle className="spin" size={13} /> : <Palette size={13} />}{paletteRows.length ? "重新提议色板" : "由默认文字模型提议色板"}</button></div>
    <div className="pipeline-stage" id={`style-palette-confirm-${style.id}`}><header><span>02 / 编辑并确认色板</span><small>{paletteConfirmed ? "已确认" : "待确认"}</small></header><div className="palette-row-list">{paletteRows.map((row, index) => <div key={`${row.name}-${index}`}><input aria-label={`色板项 ${index + 1} 名称`} value={row.name} onChange={(event) => setPaletteRows((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item))} placeholder="色板项" /><input aria-label={`色板项 ${index + 1} 内容`} value={row.value} onChange={(event) => setPaletteRows((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item))} placeholder="颜色或规则" /><button type="button" aria-label={`删除色板项 ${index + 1}`} onClick={() => setPaletteRows((rows) => rows.filter((_, itemIndex) => itemIndex !== index))}><Trash2 size={12} /></button></div>)}</div><button type="button" className="secondary" onClick={() => setPaletteRows((rows) => [...rows, { name: "", value: "" }])}><Plus size={12} />增加色板项</button><button type="button" disabled={!Object.keys(palette).length || approvePalette.isPending} onClick={() => approvePalette.mutate()}><Check size={13} />{paletteConfirmed ? "保存色板修改" : "确认彩色色板"}</button></div>
    <div className="pipeline-stage" id={`style-test-${style.id}`}><header><span>03 / 风格测试图</span><small>{testApproved ? "人工已通过" : "尚未通过"}</small></header><button type="button" disabled={!paletteConfirmed || !model || generateTest.isPending} onClick={() => generateTest.mutate()}>{generateTest.isPending ? <LoaderCircle className="spin" size={13} /> : <Sparkles size={13} />}生成 1K 风格测试图</button><div className="style-test-candidates">{candidates.data?.filter((candidate) => candidate.variant === "STYLE_TEST").map((candidate) => <article className={approvedCandidateId === candidate.id ? "approved" : ""} key={candidate.id}><CandidatePreview candidate={candidate} label={`${style.name} 风格测试 ${candidate.ordinal}`} onOpen={onOpen} /><div><strong>{approvedCandidateId === candidate.id ? "已通过" : `测试图 ${candidate.ordinal}`}</strong><small>{candidateStatusLabels[candidate.status] ?? candidate.status}</small><button type="button" disabled={!candidate.asset_id || approveTest.isPending || approvedCandidateId === candidate.id} onClick={() => approveTest.mutate(candidate.id)}><Check size={12} />人工通过</button></div></article>)}</div></div>
    <div className="pipeline-stage final"><header><span>04 / 激活正式风格</span><small>{active ? "当前使用中" : "待激活"}</small></header><p>{activationHint}</p><button type="button" disabled={active || activate.isPending} onClick={handleActivation}><Check size={13} />{activationLabel}</button></div>
    {error ? <p className="form-error"><CircleAlert size={14} />{error.message}</p> : null}
  </div>;
}
