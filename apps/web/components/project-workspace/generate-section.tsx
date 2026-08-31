"use client";

import Link from "next/link";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleAlert,
  Download,
  Heart,
  ImagePlus,
  LoaderCircle,
  Pencil,
  Plus,
  Sparkles,
  Star,
  Trash2,
} from "lucide-react";

import { ProductionReadiness } from "@/components/production-readiness";
import { api, type ImageModelAlias } from "@/lib/api";

import { assetName } from "./display";
import { CandidateArtwork, ImageModelPicker } from "./shared";
import { InspectionPanel } from "./inspection-panel";
import type { GenerationWorkspace } from "./use-generation-workspace";
import type { WorkspaceQueries } from "./use-workspace-queries";

export function GenerateSection({
  id,
  pages,
  assets,
  characters,
  outfits,
  modelOptions,
  catalogModelOptions,
  activeDrawModel,
  setDrawModel,
  openPreview,
  projectPath,
  setSelectedPageId,
  workspace,
}: {
  id: string;
  pages: WorkspaceQueries["pages"];
  assets: WorkspaceQueries["assets"];
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  modelOptions: { alias: ImageModelAlias; name: string; id: string; provider: string }[];
  catalogModelOptions: { alias: ImageModelAlias; name: string; id: string; provider: string }[];
  activeDrawModel: ImageModelAlias | null;
  setDrawModel: (model: ImageModelAlias) => void;
  openPreview: (url: string, label: string) => void;
  projectPath: (target: string) => string;
  setSelectedPageId: (pageId: string | null) => void;
  workspace: GenerationWorkspace;
}) {
  const {
    setViewedBatchId,
    reviewCandidateId,
    setReviewCandidateId,
    setReferenceSelections,
    setReferenceOverridePageId,
    selectedPage,
    candidates,
    generateWorkbenchReady,
    orderedPageBatches,
    latestBatch,
    viewedBatch,
    previousBatch,
    nextBatch,
    isViewingHistoricalBatch,
    generationStoryboard,
    pageReadiness,
    pageProduction,
    selectedPageStructureIssue,
    selectedPageGenerationIssue,
    visibleCharacterIds,
    effectiveReferenceSelections,
    generationReferenceReady,
    referenceOverrideOpen,
    targetDialogues,
    latestInspections,
    reviewJob,
    selectedWorkbenchCandidate,
    productionBlocker,
    startBatch,
    generate,
    favorite,
    deleteCandidate,
    inspectCandidate,
    repairCandidate,
    upscaleCandidate,
    selectCandidate,
    keepSelectedCandidate,
    goNext,
  } = workspace;

  return (
    <div className="generate-workbench">
      <header className="canvas-header"><div><span>DRAW / 单页抽卡</span><h2>{selectedPage ? `第 ${selectedPage.page_number} 页候选` : "选择一页开始"}</h2></div><small>每次只生成 1 页</small></header>
      {selectedPage ? !generateWorkbenchReady ? <div className="generate-skeleton" role="status" aria-label="正在载入生成工作台"><LoaderCircle className="spin" size={22} /><span>正在载入生成工作台…</span></div> : <>
        {selectedPageStructureIssue && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>当前页暂不能生成</strong><p>{selectedPageStructureIssue}</p></div><Link className="button outline compact" href={projectPath("script")}>前往漫画剧本</Link></div>}
        {selectedPage.continuity_status === "NEEDS_REVIEW" && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>剧本或分镜已修改</strong><p>历史候选仍然保留，但可能不再对应当前脚本。建议重新抽卡并执行连续性检查。</p></div><Link className="button outline compact" href={projectPath("storyboard")}>检查分镜</Link></div>}
        {selectedWorkbenchCandidate && ["STALE", "LEGACY_UNKNOWN"].includes(selectedWorkbenchCandidate.version_state) && <div className="stale-candidate-banner"><div><span>版本需要决定</span><strong>旧候选基于 {selectedWorkbenchCandidate.based_on_storyboard_version ? `V${selectedWorkbenchCandidate.based_on_storyboard_version}` : "未知版本"}，当前分镜为 V{selectedPage.storyboard_version}</strong><p>旧图可以继续查看，但必须确认版本并重新完成视觉检查后，才能进入下一页或导出。</p></div><div><button disabled={keepSelectedCandidate.isPending} onClick={() => keepSelectedCandidate.mutate(selectedWorkbenchCandidate.id)}><Check size={14} />沿用并重新检查</button><button className="primary" disabled={generate.isPending || !pageReadiness.data?.ready || !generationReferenceReady || isViewingHistoricalBatch} onClick={() => generate.mutate()}><Sparkles size={14} />{isViewingHistoricalBatch ? "先切回最新批次" : `按当前 V${selectedPage.storyboard_version} 重新生成`}</button></div></div>}
        <div className="draw-toolbar">
          <div className="page-picker">{pages.data?.map((page) => <button key={page.id} className={selectedPage.id === page.id ? "active" : ""} onClick={() => { setSelectedPageId(page.id); setViewedBatchId(null); setReviewCandidateId(null); setReferenceSelections({}); setReferenceOverridePageId(null); }}>{page.page_number}</button>)}</div>
          <div className="batch-toolbar-actions">
            {viewedBatch && <div className="batch-switcher" aria-label="切换生成批次">
              <button type="button" title="查看上一批次" disabled={!previousBatch} onClick={() => { setViewedBatchId(previousBatch?.id ?? null); setReviewCandidateId(null); }}><ArrowLeft size={13} />上一批</button>
              <label><span>查看批次</span><select aria-label="选择要查看的生成批次" value={viewedBatch.id} onChange={(event) => { setViewedBatchId(event.target.value); setReviewCandidateId(null); }}>{[...orderedPageBatches].reverse().map((batch) => <option key={batch.id} value={batch.id}>批次 {batch.ordinal}{batch.id === latestBatch?.id ? " · 最新" : ""}</option>)}</select></label>
              <button type="button" title="查看下一批次" disabled={!nextBatch} onClick={() => { setViewedBatchId(nextBatch?.id ?? null); setReviewCandidateId(null); }}>下一批<ArrowRight size={13} /></button>
            </div>}
            <button className="button ghost compact" disabled={startBatch.isPending || Boolean(selectedPageStructureIssue) || !pageReadiness.data?.ready} onClick={() => startBatch.mutate()}><Plus size={14} />新批次</button>
          </div>
        </div>
        <div className="draw-context"><div><span>PAGE LOAD</span><strong>{selectedPage.estimated_text_chars} 字</strong><small>{selectedPage.panel_count} 格 / {selectedPage.estimated_bubbles} 气泡</small></div><p>{selectedPage.source_coverage.ranges?.map((item) => item.text).join("").slice(0, 180)}</p></div>
        <ProductionReadiness projectId={id} readiness={pageReadiness.data} loading={pageReadiness.isLoading} error={pageReadiness.error} targetDialogues={targetDialogues} />
        <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次页面生成模型（仅显示支持图片编辑的已启用模型）" />
        <section className="generation-reference-check">
          <header><div><span>CAST & REFERENCES</span><strong>自动继承已确认的人物与服装参考</strong></div><button type="button" className="reference-override-toggle" onClick={() => setReferenceOverridePageId(referenceOverrideOpen ? null : selectedPage.id)}><Pencil size={11} />{referenceOverrideOpen ? "收起选择" : "本页更换"}</button></header>
          {generationReferenceReady && !referenceOverrideOpen && <div className="reference-inheritance-summary">{visibleCharacterIds.map((characterId) => {
            const character = characters.data?.find((item) => item.id === characterId);
            const selection = effectiveReferenceSelections[characterId];
            const characterAsset = assets.data?.find((item) => item.id === selection?.character_asset_id);
            const outfit = outfits.data?.find((item) => item.id === selection?.outfit_id);
            const outfitAsset = assets.data?.find((item) => item.id === selection?.outfit_asset_id);
            return <article key={characterId}><Check size={14} /><div><strong>{character?.primary_name ?? characterId}{outfit ? ` · ${outfit.name}` : ""}</strong><span>已继承：{characterAsset ? assetName(characterAsset) : "人物主参考"}{outfitAsset ? ` ＋ ${assetName(outfitAsset)}` : ""}</span></div></article>;
          })}</div>}
          {generationStoryboard.isLoading ? <p className="reference-check-loading"><LoaderCircle className="spin" size={15} />正在读取当前分镜…</p> : (referenceOverrideOpen || !generationReferenceReady) && <div className="reference-check-grid">
            {visibleCharacterIds.map((characterId) => {
              const character = characters.data?.find((item) => item.id === characterId);
              const selection = effectiveReferenceSelections[characterId];
              const outfit = outfits.data?.find((item) => item.id === selection?.outfit_id);
              return <article key={characterId}><div><strong>{character?.primary_name ?? characterId}</strong><span>{outfit ? `穿着：${outfit.name}` : "分镜未指定服装"}</span></div><label><span>人物参考图</span><select value={selection?.character_asset_id ?? ""} onChange={(event) => setReferenceSelections((values) => ({ ...values, [characterId]: { ...(effectiveReferenceSelections[characterId] ?? { outfit_id: null, outfit_asset_id: null }), character_asset_id: event.target.value || null } }))}><option value="">请选择人物参考</option>{character?.references.map((reference, referenceIndex) => { const asset = assets.data?.find((item) => item.id === reference.asset_id); return <option value={reference.asset_id} key={reference.id}>{character.primary_name} · {reference.is_canonical ? "主参考" : `人物参考 ${String(referenceIndex + 1).padStart(2, "0")}`} · {asset?.display_name ?? asset?.original_name ?? reference.asset_id} · {reference.asset_id.slice(0, 8)}</option>; })}</select></label>{outfit && <label><span>该服装参考图</span><select value={selection?.outfit_asset_id ?? ""} onChange={(event) => setReferenceSelections((values) => ({ ...values, [characterId]: { ...effectiveReferenceSelections[characterId], outfit_asset_id: event.target.value || null } }))}><option value="">请选择服装参考</option>{outfit.reference_asset_ids.map((assetId, assetIndex) => <option value={assetId} key={assetId}>{outfit.name} · 服装参考 {String(assetIndex + 1).padStart(2, "0")} · {assets.data?.find((item) => item.id === assetId)?.original_name ?? assetId}</option>)}</select></label>}</article>;
            })}
            {!visibleCharacterIds.length && <p className="reference-check-empty">当前分镜没有入镜人物，将只按场景、动作和风格生成。</p>}
          </div>}
          {!generationReferenceReady && <p className="reference-check-warning"><CircleAlert size={13} />有角色缺少可用参考图，请先到“参考资产”绑定；分镜指定服装时也必须选择对应服装图。</p>}
        </section>
        <div className="generation-bar"><div className="generation-options"><div><span>正式模型</span><strong>{modelOptions.find((item) => item.alias === activeDrawModel)?.name ?? "尚未选择"}</strong></div><div><span>本次规格</span><strong>1K · 彩色 · 1 个候选</strong></div></div><button className="button ink generate-one" disabled={generate.isPending || Boolean(selectedPageGenerationIssue) || !pageReadiness.data?.ready || !generationReferenceReady || isViewingHistoricalBatch} onClick={() => generate.mutate()}>{generate.isPending ? <LoaderCircle className="spin" size={17} /> : <Star size={17} />}{generate.isPending ? "正在加入 1 个正式任务" : isViewingHistoricalBatch ? "先切回最新批次再生成" : selectedPageStructureIssue ? "请先补全剧本与分镜" : !activeDrawModel ? "先选择图片模型" : !pageReadiness.data?.ready ? "先完成页面生产准备" : !generationReferenceReady ? "先补齐人物与服装参考" : "生成 1 个 1K 彩色候选"}</button></div>
        {(generate.isError || startBatch.isError) && <p className="form-error"><CircleAlert size={14} />{(generate.error ?? startBatch.error)?.message}</p>}
        <div className="batch-heading"><div><span>{isViewingHistoricalBatch ? "HISTORY / 历史批次" : "BATCH / 当前批次"}</span><strong>{viewedBatch ? `批次 ${viewedBatch.ordinal}` : "尚未开始批次"}</strong></div><small>{isViewingHistoricalBatch ? `正在查看历史结果 · 共 ${orderedPageBatches.length} 个批次` : "每个候选记录实际供应商与模型 · 收藏不等于采用"}</small></div>
        <div className="candidate-grid">{!candidates.data && <article className="candidate-card" aria-hidden="true"><CandidateArtwork contentUrl={null} label="候选加载中" eager /></article>}{candidates.data?.map((candidate, candidateIndex) => <article className={`${candidate.is_selected ? "candidate-card selected" : "candidate-card"} version-${candidate.version_state.toLowerCase()}`} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`候选 ${candidate.ordinal}`} eager={candidateIndex === 0} onOpen={(url, label) => openPreview(url, label)} /><div className="candidate-meta"><span>候选 {String(candidate.ordinal).padStart(2, "0")}</span><strong>{catalogModelOptions.find((item) => item.alias === candidate.model_alias)?.name ?? candidate.model_alias}</strong><small>{candidate.resolution} · {candidate.status}</small><em>{candidate.based_on_storyboard_version ? `生成依据 V${candidate.based_on_storyboard_version}` : "生成版本未知"} · {candidate.version_state}</em></div><div className="candidate-actions"><button className={candidate.is_favorite ? "favorited" : ""} onClick={() => favorite.mutate({ candidateId: candidate.id, value: !candidate.is_favorite })}><Heart size={14} fill={candidate.is_favorite ? "currentColor" : "none"} />收藏</button><button disabled={!candidate.asset_id || candidate.is_selected} onClick={() => { if (window.confirm("请确认页面文字已人工校对。暂选后还需要完成视觉检查，才能进入下一页或导出。是否继续？")) selectCandidate.mutate({ candidateId: candidate.id, manualTextConfirmed: true, acceptStale: candidate.version_state !== "CURRENT" }); }}><Check size={14} />{candidate.is_selected ? "已暂选" : candidate.version_state === "CURRENT" ? "人工校对并暂选" : "确认旧版本并暂选"}</button><button className={reviewCandidateId === candidate.id ? "reviewing" : ""} disabled={!candidate.asset_id || inspectCandidate.isPending} onClick={() => { setReviewCandidateId(candidate.id); inspectCandidate.mutate(candidate.id); }}><CircleAlert size={14} />视觉检查</button>{candidate.asset_id && candidate.resolution === "1K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "2K" })}>升至 2K</button>}{candidate.asset_id && candidate.resolution !== "4K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "4K" })}>升至 4K</button>}<button className="danger-action" disabled={candidate.is_selected} onClick={() => { if (window.confirm("删除这个候选？收藏状态也会一并移除。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={14} />删除</button></div></article>)}</div>
        {reviewCandidateId && <InspectionPanel
          latestInspections={latestInspections}
          reviewJob={reviewJob}
          inspectCandidate={inspectCandidate}
          repairCandidate={repairCandidate}
          upscaleCandidate={upscaleCandidate}
          onClose={() => setReviewCandidateId(null)}
        />}
        <section className={`production-gate ${pageProduction?.ready ? "ready" : "blocked"}`}>
          <header>
            <div><span>PRODUCTION GATE / 页面生产门禁</span><strong>{pageProduction?.ready ? "当前页已通过，可以进入下一页" : "当前页尚未生产通过"}</strong></div>
            <em>{pageProduction?.ready ? "READY" : pageProduction?.state ?? "LOADING"}</em>
          </header>
          <div className="production-gate-steps">
            <span className={selectedWorkbenchCandidate ? "done" : ""}><Check size={13} />人工校对并暂选</span>
            <span className={selectedWorkbenchCandidate && !["STALE", "LEGACY_UNKNOWN"].includes(selectedWorkbenchCandidate.version_state) ? "done" : ""}><Check size={13} />确认当前分镜版本</span>
            <span className={pageProduction?.ready ? "done" : ""}><Check size={13} />视觉检查通过</span>
          </div>
          {!pageProduction?.ready && <p><CircleAlert size={14} />{productionBlocker?.message ?? "正在读取当前页生产状态"}</p>}
        </section>
        {!candidates.data?.length && <div className="asset-empty"><ImagePlus size={25} /><strong>这个批次还没有候选</strong><p>完成生产准备并确认参考图后，使用本次选择的图片模型生成 1 张彩色页面。</p></div>}
        <div className="next-page-row"><span>{pageProduction?.ready ? "人工校对、版本确认和视觉检查均已通过" : productionBlocker?.message ?? "完成页面生产门禁后才能继续"}</span><div>{pageProduction?.ready && <a className="button ghost compact" href={api.selectedPagePngUrl(selectedPage.id)!}><Download size={14} />单页 PNG</a>}<button className="button outline" disabled={!pageProduction?.ready || goNext.isPending} onClick={() => goNext.mutate()}>生成下一页 <ArrowRight size={15} /></button></div></div>
      </> : pages.isLoading || pages.data === undefined ? null : <div className="asset-empty tall"><Sparkles size={28} /><strong>没有可抽卡页面</strong><p>先完成动态分页。</p></div>}
    </div>
  );
}
