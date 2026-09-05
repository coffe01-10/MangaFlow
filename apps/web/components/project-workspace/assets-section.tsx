"use client";

import dynamic from "next/dynamic";
import Image from "next/image";
import Link from "next/link";
import {
  Check,
  CircleAlert,
  FileImage,
  ImagePlus,
  LibraryBig,
  Link2,
  LoaderCircle,
  Palette,
  Plus,
  Pencil,
  Shirt,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";

import { publicUrl, type AssetPurpose, type ImageModelAlias } from "@/lib/api";

import { assetName, formatBytes, promptPreview } from "./display";
import { assetStatusLabels, candidateStatusLabels, generationKindLabels, kinds, styleStatusLabels } from "./labels";
import { AssetNameEditor, CandidateArtwork, ComicModeSwitch, ImageModelPicker } from "./shared";
import { CharacterPackageWorkspace } from "./character-package-workspace";
import { SceneWorkspace } from "./scene-workspace";
import type { AssetWorkspaceView } from "./types";
import type { AssetsWorkspace } from "./use-assets-workspace";
import type { WorkspaceQueries } from "./use-workspace-queries";

const CharacterConceptPanel = dynamic(
  () => import("@/components/asset-production-panel").then((mod) => mod.CharacterConceptPanel),
);
const StyleProductionPanel = dynamic(
  () => import("@/components/asset-production-panel").then((mod) => mod.StyleProductionPanel),
);

export function AssetsSection({
  id,
  assetView,
  draft,
  assets,
  characters,
  outfits,
  modelOptions,
  activeDrawModel,
  setDrawModel,
  openPreview,
  rememberWorkspaceScroll,
  workspace,
}: {
  id: string;
  assetView: AssetWorkspaceView;
  draft: { default_style_id: string | null };
  assets: WorkspaceQueries["assets"];
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  modelOptions: { alias: ImageModelAlias; name: string; id: string; provider: string }[];
  activeDrawModel: ImageModelAlias | null;
  setDrawModel: (model: ImageModelAlias) => void;
  openPreview: (url: string, label: string) => void;
  rememberWorkspaceScroll: () => void;
  workspace: AssetsWorkspace;
}) {
  const {
    assetKind,
    setAssetKind,
    currentAssetKind,
    uploadError,
    assetDragActive,
    setAssetDragActive,
    characterName,
    setCharacterName,
    characterAliases,
    setCharacterAliases,
    editCharacterName,
    setEditCharacterName,
    editCharacterAliases,
    setEditCharacterAliases,
    editLockedFeatures,
    setEditLockedFeatures,
    editForbiddenChanges,
    setEditForbiddenChanges,
    bindCharacterId,
    setBindCharacterId,
    outfitName,
    setOutfitName,
    outfitLockedFields,
    setOutfitLockedFields,
    editingOutfitId,
    styleName,
    setStyleName,
    styleLockedFields,
    setStyleLockedFields,
    styleColorMode,
    selectedOutfitAssets,
    setSelectedOutfitAssets,
    showGeneratedReferencePicker,
    setShowGeneratedReferencePicker,
    selectedStyleAssets,
    setSelectedStyleAssets,
    selectedCharacterOutfitId,
    setSelectedCharacterOutfitId,
    styles,
    generatedReferenceLibrary,
    generatedReferenceCandidates,
    boundCharacter,
    editingOutfit,
    selectedOutfitFiles,
    selectedStyleFiles,
    assetCandidates,
    upload,
    deleteAsset,
    reclassifyAsset,
    adoptGeneratedReference,
    renameAsset,
    bindExistingCharacterReference,
    unbindExistingCharacterReference,
    createCharacter,
    updateCharacter,
    createOutfit,
    updateOutfit,
    deleteOutfit,
    generateOutfitPreview,
    createStyle,
    analyzeStyle,
    updateStyleMode,
    selectStyleMode,
    resetOutfitForm,
    beginOutfitEdit,
    chooseFile,
    dropReferenceFile,
    confirmDeleteOutfit,
  } = workspace;
  const visibleAssetKinds = assetView === "references"
    ? kinds
    : kinds.filter(([kind]) => kind === currentAssetKind);

  return (
    <>
      <nav className="asset-subnav" aria-label="参考资产分类">
        {([
          ["characters", "人物设定"],
          ["outfits", "服装档案"],
          ["scenes", "场景资产"],
          ["style", "漫画风格"],
          ["references", "原始参考素材"],
        ] as const).map(([view, label]) => (
          <Link
            scroll={false}
            key={view}
            aria-current={assetView === view ? "page" : undefined}
            className={assetView === view ? "active" : ""}
            href={`/projects/${id}/assets/${view}`}
            onClick={rememberWorkspaceScroll}
          >
            {label}
          </Link>
        ))}
      </nav>
      {assetView === "scenes" && (
        <SceneWorkspace projectId={id} assets={assets.data ?? []} openPreview={openPreview} />
      )}
      {assetView !== "scenes" && <>
      {assetView === "characters" && <>
      <header className="canvas-header"><div><span>CHARACTER BIBLE / 角色资产</span><h2>姓名、绰号与参考图绑定</h2></div><small>{characters.data?.length ?? 0} 个角色</small></header>
      <div className="character-create">
        <input className="text-input" aria-label="新角色主要姓名" value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="主要姓名（剧本默认使用）" />
        <input className="text-input" aria-label="新角色绰号，用逗号分隔" value={characterAliases} onChange={(event) => setCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" />
        <button className="button ink compact" disabled={!characterName.trim() || createCharacter.isPending} onClick={() => createCharacter.mutate()}><Plus size={14} />添加角色</button>
      </div>
      <div className="character-strip">
        {characters.data?.map((character) => <button key={character.id} className={bindCharacterId === character.id ? "character-chip active" : "character-chip"} onClick={() => { if (editingOutfitId) resetOutfitForm(); setBindCharacterId(character.id); setSelectedCharacterOutfitId(""); setEditCharacterName(character.primary_name); setEditCharacterAliases(character.aliases.join("，")); setEditLockedFeatures(character.locked_features.join("，")); setEditForbiddenChanges(character.forbidden_changes.join("，")); }}><strong>{character.primary_name}</strong><span>{character.aliases.length ? `又名 ${character.aliases.join(" / ")}` : "无绰号"}</span>{character.alias_conflict && <em>称呼冲突待确认</em>}<small>{character.references.length} 张参考图 · {character.locked_features.length} 项已锁定</small></button>)}
      </div>
      {boundCharacter && <div className="character-editor"><div><strong>规范姓名与一致性锁</strong><span>剧本统一使用主要姓名；固定特征和禁止改变项会进入每次生图提示。</span></div><input aria-label="编辑主要姓名" className="text-input" value={editCharacterName} onChange={(event) => setEditCharacterName(event.target.value)} /><input aria-label="编辑角色绰号" className="text-input" value={editCharacterAliases} onChange={(event) => setEditCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" /><button className="button outline compact" disabled={!editCharacterName.trim() || updateCharacter.isPending} onClick={() => updateCharacter.mutate()}>{updateCharacter.isPending ? <LoaderCircle className="spin" size={13} /> : <Pencil size={13} />}保存角色规范</button><div className="character-lock-fields"><input aria-label="角色固定特征" className="text-input" value={editLockedFeatures} onChange={(event) => setEditLockedFeatures(event.target.value)} placeholder="固定特征：黑色长发、左眼泪痣…" /><input aria-label="角色禁止改变项" className="text-input" value={editForbiddenChanges} onChange={(event) => setEditForbiddenChanges(event.target.value)} placeholder="禁止改变：发色、瞳色、身高关系…" /></div>{boundCharacter.alias_conflict && <em><CircleAlert size={12} />当前称呼与其他角色冲突，请修改后保存</em>}</div>}
      <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="项目视觉模型（必须显式选择，并在各生成页面保持一致）" />
      {boundCharacter && <CharacterConceptPanel key={boundCharacter.id} projectId={id} character={boundCharacter} model={activeDrawModel} onOpen={openPreview} />}
      <CharacterPackageWorkspace projectId={id} characters={characters.data ?? []} assets={assets.data ?? []} />
      </>}
      {assetView === "outfits" && <>
      <header className="canvas-header"><div><span>WARDROBE / 服装档案</span><h2>角色、服装与参考图逐一绑定</h2></div><small>{outfits.data?.length ?? 0} 份档案</small></header>
      <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次服装预览模型" />
      {selectedCharacterOutfitId && <section className="asset-live-results"><header><div><span>LIVE RESULT</span><strong>服装穿着图实时结果</strong></div><small>{outfits.data?.find((outfit) => outfit.id === selectedCharacterOutfitId)?.name ?? "服装"}</small></header><div className="asset-result-grid">{assetCandidates.data?.map((candidate) => <article key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`服装穿着图 ${candidate.ordinal}`} onOpen={openPreview} /><div><strong>服装穿着预览</strong><span>{candidateStatusLabels[candidate.status] ?? candidate.status} · {candidate.resolution}</span><details><summary>实际提示词</summary><p>{promptPreview(candidate)}</p></details></div></article>)}</div></section>}
      </>}
      <div className="asset-workbench">
        {assetView === "outfits" &&
        <section className="profile-workbench outfit-profile-workbench">
          <header><Shirt size={16} /><div><strong>服装档案</strong><span>明确绑定角色、服装名称与参考图</span></div></header>
          <div className="binding-flow" aria-label="服装参考绑定流程">
            <span className={boundCharacter ? "done" : ""}><i>01</i><small>所属角色</small><strong>{boundCharacter?.primary_name ?? "未选择"}</strong></span><b>→</b>
            <span className={selectedOutfitAssets.length ? "done" : ""}><i>02</i><small>服装参考</small><strong>{selectedOutfitAssets.length} 张</strong></span><b>→</b>
            <span className={outfitName.trim() ? "done" : ""}><i>03</i><small>服装档案</small><strong>{outfitName.trim() || "待命名"}</strong></span>
          </div>
          <div className="profile-compose">
            <div className="workbench-fields">
              <label><span>所属角色</span><select aria-label="服装所属角色" className="text-input" value={editingOutfit?.character_id ?? bindCharacterId} disabled={Boolean(editingOutfit)} onChange={(event) => setBindCharacterId(event.target.value)}><option value="">选择角色后再绑定服装</option>{characters.data?.map((character) => <option key={character.id} value={character.id}>{character.primary_name}{character.aliases.length ? `（${character.aliases.join(" / ")}）` : ""}</option>)}</select></label>
              <label><span>服装档案名称</span><input aria-label="服装档案名称" className="text-input" value={outfitName} onChange={(event) => setOutfitName(event.target.value)} placeholder="例如：校服 / 冬季便装" /></label>
              <label><span>一致性锁定项</span><input aria-label="服装锁定项" className="text-input" value={outfitLockedFields} onChange={(event) => setOutfitLockedFields(event.target.value)} placeholder="颜色、鞋型、领结、配饰…" /></label>
            </div>
            <aside className="reference-selection-summary"><span>当前待绑定</span><strong>{selectedOutfitFiles.length}<small> 张参考图</small></strong><p>{selectedOutfitFiles.length ? selectedOutfitFiles.slice(0, 2).map((asset) => assetName(asset)).join("、") : "在下方“服装参考”中选择图片"}{selectedOutfitFiles.length > 2 ? ` 等 ${selectedOutfitFiles.length} 张` : ""}</p></aside>
            <div className="profile-form-actions">{editingOutfit && <button className="secondary" type="button" onClick={resetOutfitForm}><X size={12} />取消编辑</button>}<button type="button" disabled={!outfitName.trim() || (!editingOutfit && (!bindCharacterId || !selectedOutfitAssets.length)) || createOutfit.isPending || updateOutfit.isPending} onClick={() => editingOutfit ? updateOutfit.mutate() : createOutfit.mutate()}><Link2 size={12} />{editingOutfit ? `保存绑定（${selectedOutfitAssets.length} 图）` : `建立并绑定（${selectedOutfitAssets.length} 图）`}</button></div>
          </div>
          <p className="binding-guide"><Link2 size={12} />上传服装参考后会自动加入当前档案；也可以在下方素材卡中加入、移除，再点击保存绑定。</p>
        </section>}
        {assetView === "outfits" &&
        <section className="outfit-records-workbench" aria-label="已保存服装档案">
          <header><LibraryBig size={16} /><div><strong>已保存服装</strong><span>在右侧集中管理角色已有的服装档案</span></div></header>
          <div className="profile-records">{outfits.data?.map((outfit) => {
            const owner = characters.data?.find((item) => item.id === outfit.character_id);
            return <article className={editingOutfitId === outfit.id ? "editing" : ""} key={outfit.id}><div className="profile-record-title"><span>WARDROBE</span><strong>{outfit.name}</strong><small>{outfit.locked_fields.length} 项锁定</small></div><div className="relationship-chain"><span>{owner?.primary_name ?? "未知角色"}</span><b>→</b><span>{outfit.name}</span><b>→</b><span>{outfit.reference_asset_ids.length} 张参考图</span></div><div className="profile-record-actions"><button className="danger-action" type="button" disabled={deleteOutfit.isPending} onClick={() => confirmDeleteOutfit(outfit)}><Trash2 size={11} />删除档案及图片</button><button type="button" onClick={() => beginOutfitEdit(outfit)}>{editingOutfitId === outfit.id ? "编辑中" : "管理参考图"}</button><button type="button" disabled={generateOutfitPreview.isPending || !activeDrawModel || !outfit.reference_asset_ids.length} onClick={() => generateOutfitPreview.mutate(outfit.id)}>生成穿着图</button></div></article>;
          })}{!outfits.data?.length && <p className="profile-record-empty">还没有服装档案。完成上方 01–03 三步后建立。</p>}</div>
        </section>}
        {assetView === "style" && <>
        <header className="canvas-header"><div><span>STYLE SYSTEM / 漫画风格</span><h2>色板、画面语言与测试图</h2></div><small>{styles.isLoading ? "读取中…" : `${styles.data?.length ?? 0} 份档案`}</small></header>
        <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次风格测试模型" />
        <section className="profile-workbench style-profile-workbench">
          <header><Palette size={16} /><div><strong>创建新风格档案</strong><span>这里的模式只用于下面正在创建的新档案，并会记住本项目上次选择</span></div></header>
          <div className="mode-selector-block"><div><span>新档案色彩模式</span><strong>{styleColorMode === "monochrome" ? "黑白漫画" : "彩色漫画"}</strong></div><ComicModeSwitch value={styleColorMode} onChange={selectStyleMode} /><p>{styleColorMode === "monochrome" ? "分析线稿、网点、黑白对比与留白。" : "分析色板、肤色发色、上色方式与光影。"}</p></div>
          <div className="profile-compose style-compose">
            <div className="workbench-fields"><label><span>风格档案名称</span><input aria-label="漫画风格档案名称" className="text-input" value={styleName} onChange={(event) => setStyleName(event.target.value)} placeholder="风格档案名称" /></label><label><span>一致性锁定项</span><input aria-label="漫画风格锁定项" className="text-input" value={styleLockedFields} onChange={(event) => setStyleLockedFields(event.target.value)} placeholder={styleColorMode === "monochrome" ? "线稿、网点、构图…" : "色板、肤色、光影、构图…"} /></label></div>
            <aside className="reference-selection-summary"><span>当前待分析</span><strong>{selectedStyleFiles.length}<small> 张参考页</small></strong><p>{selectedStyleFiles.length ? selectedStyleFiles.slice(0, 2).map((asset) => assetName(asset)).join("、") : "在下方“漫画风格”中选择参考页"}</p></aside>
            <div className="profile-form-actions"><button type="button" disabled={!styleName.trim() || !selectedStyleAssets.length || createStyle.isPending} onClick={() => createStyle.mutate()}><Sparkles size={12} />创建并分析（{selectedStyleAssets.length} 图）</button></div>
          </div>
          <div className="profile-subsection-title"><div><span>已保存档案</span><strong>逐份修改与切换</strong></div><p>下方开关修改的是该档案本身，不会改变上方新档案表单。</p></div><div className="profile-records">{styles.data?.map((style) => {
            const isActive = draft.default_style_id === style.id && style.status === "ACTIVE";
            const referenceCount = style.profile.reference_asset_ids?.length ?? 0;
            return <article className={isActive ? "active style-production-record" : "style-production-record"} key={style.id}><div className="profile-record-title"><span>{isActive ? "CURRENT STYLE" : "STYLE PROFILE"}</span><strong>{style.name}</strong><small>{styleStatusLabels[style.status] ?? style.status} · {referenceCount} 张参考 · {style.locked_fields.length} 项锁定</small></div><ComicModeSwitch compact value={style.color_mode} disabled={updateStyleMode.isPending} onChange={(colorMode) => updateStyleMode.mutate({ style, colorMode })} />{style.status === "DRAFT" && <p className="reanalyze-note">彩色风格必须依次确认色板和测试图，再激活用于正式页面。</p>}<div className="profile-record-actions"><button type="button" disabled={!referenceCount || analyzeStyle.isPending} onClick={() => analyzeStyle.mutate(style.id)}>重新分析画面语言</button></div><StyleProductionPanel key={`${style.id}:${style.version}`} projectId={id} style={style} model={activeDrawModel} active={isActive} onOpen={openPreview} /></article>;
          })}{!styles.data?.length && !styles.isLoading && <p className="profile-record-empty">选择色彩模式并绑定参考页，建立第一份漫画风格档案。</p>}</div>
        </section></>}
      </div>
      {assetView === "references" && <header className="canvas-header"><div><span>REFERENCE INTAKE / 原始素材</span><h2>上传、分类与追溯原始参考图</h2></div><small>{assets.data?.length ?? 0} 个文件</small></header>}
      <div className="intake-toolbar"><div className="kind-switch">{assetView === "references" ? kinds.map(([value, label]) => <button key={value} className={assetKind === value ? "active" : ""} onClick={() => setAssetKind(value)}>{label}</button>) : <strong>{kinds.find(([value]) => value === currentAssetKind)?.[1]}</strong>}</div><span>{currentAssetKind === "CHARACTER_REFERENCE" ? (bindCharacterId ? "将绑定到选中的角色" : "请先选择要绑定的角色") : currentAssetKind === "OUTFIT_REFERENCE" ? (boundCharacter ? `当前绑定目标：${boundCharacter.primary_name} → ${outfitName.trim() || "未命名服装"}` : "先选择所属角色，再建立服装档案") : currentAssetKind === "SCENE_REFERENCE" ? "上传后请到场景资产工作区绑定地点" : `当前分析目标：${styleColorMode === "monochrome" ? "黑白漫画" : "彩色漫画"}`}</span></div>
      <label className={`upload-stage${upload.isPending ? " busy" : ""}${assetDragActive ? " drag-active" : ""}`} onDragEnter={(event) => { event.preventDefault(); setAssetDragActive(true); }} onDragOver={(event) => { event.preventDefault(); event.dataTransfer.dropEffect = "copy"; setAssetDragActive(true); }} onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setAssetDragActive(false); }} onDrop={dropReferenceFile}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} disabled={upload.isPending} /><span className="upload-icon">{upload.isPending ? <LoaderCircle className="spin" /> : <Upload />}</span><strong>{upload.isPending ? "正在安全上传…" : assetDragActive ? "松开即可上传" : `拖拽图片到这里，或点击上传${kinds.find(([value]) => value === currentAssetKind)?.[1]}`}</strong><p>{currentAssetKind === "CHARACTER_REFERENCE" ? "人物图会和选中的主要姓名绑定，不会只依赖文件名猜测身份。" : currentAssetKind === "OUTFIT_REFERENCE" ? "上传后自动加入当前服装档案，保存时绑定到上方所选角色。" : currentAssetKind === "SCENE_REFERENCE" ? "场景参考图走同一套文件类型、尺寸和安全校验；绑定关系请在场景资产中建立。" : `上传后自动加入当前${styleColorMode === "monochrome" ? "黑白" : "彩色"}风格档案，创建后再由默认视觉模型分析。`}</p></label>
      {assetView === "outfits" && <>
        <div className="reference-source-actions">
          <button type="button" className="button outline compact" disabled={!bindCharacterId} onClick={() => setShowGeneratedReferencePicker((value) => !value)}><LibraryBig size={14} />{showGeneratedReferencePicker ? "收起生成素材" : "从生成素材库导入"}</button>
          {!bindCharacterId && <small>先选择所属角色，再导入生成素材</small>}
        </div>
        {showGeneratedReferencePicker && <section className="generated-reference-picker" aria-label="从生成素材库导入服装参考">
          <header><div><span>GENERATED ASSETS / 生成素材</span><strong>选择一张图加入待绑定服装参考</strong></div><button type="button" className="icon-button" aria-label="关闭生成素材选择" onClick={() => setShowGeneratedReferencePicker(false)}><X size={14} /></button></header>
          <p className="generated-reference-hint">只显示当前角色相关的已生成图片；导入后仍保留在生成素材库，可在上方待绑定列表中继续调整。</p>
          {generatedReferenceLibrary.isLoading && <p className="asset-result-empty">正在读取生成素材…</p>}
          {generatedReferenceLibrary.isError && <p className="form-error"><CircleAlert size={14} />{generatedReferenceLibrary.error.message}</p>}
          {!generatedReferenceLibrary.isLoading && !generatedReferenceLibrary.isError && !generatedReferenceCandidates.length && <p className="asset-result-empty">当前角色还没有可导入的生成图片。</p>}
          <div className="generated-reference-grid">{generatedReferenceCandidates.map(({ candidate, generationKind }, index) => {
            const imported = selectedOutfitAssets.includes(candidate.asset_id!);
            const importing = adoptGeneratedReference.isPending && adoptGeneratedReference.variables === candidate.asset_id;
            return <article key={candidate.asset_id} className={imported ? "imported" : undefined}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`生成素材 ${candidate.ordinal}`} eager={index === 0} onOpen={openPreview} /><div><strong>{generationKindLabels[generationKind] ?? generationKind}</strong><span>{candidate.variant ?? "生成候选"} · {candidate.resolution} · {candidateStatusLabels[candidate.status] ?? candidate.status}</span><button type="button" disabled={imported || adoptGeneratedReference.isPending} onClick={() => adoptGeneratedReference.mutate(candidate.asset_id!)}>{importing ? <LoaderCircle className="spin" size={13} /> : imported ? <Check size={13} /> : <ImagePlus size={13} />}{importing ? "正在导入…" : imported ? "已加入待绑定" : "加入待绑定"}</button></div></article>;
          })}</div>
        </section>}
      </>}
      {uploadError && <p className="form-error" role="alert"><CircleAlert size={15} />{uploadError}</p>}
      {(deleteAsset.isError || reclassifyAsset.isError || renameAsset.isError || adoptGeneratedReference.isError || bindExistingCharacterReference.isError || unbindExistingCharacterReference.isError) && <p className="form-error" role="alert"><CircleAlert size={15} />{(deleteAsset.error ?? reclassifyAsset.error ?? renameAsset.error ?? adoptGeneratedReference.error ?? bindExistingCharacterReference.error ?? unbindExistingCharacterReference.error)?.message}</p>}
      {(createOutfit.isError || updateOutfit.isError || deleteOutfit.isError || createStyle.isError || updateStyleMode.isError) && <p className="form-error" role="alert"><CircleAlert size={15} />{(createOutfit.error ?? updateOutfit.error ?? deleteOutfit.error ?? createStyle.error ?? updateStyleMode.error)?.message}</p>}
      {(createCharacter.isError || updateCharacter.isError || generateOutfitPreview.isError || analyzeStyle.isError) && <p className="form-error" role="alert"><CircleAlert size={15} />{(createCharacter.error ?? updateCharacter.error ?? generateOutfitPreview.error ?? analyzeStyle.error)?.message}</p>}
      {visibleAssetKinds.map(([kind, label]) => {
        const grouped = assets.data?.filter((asset) => asset.kind === kind) ?? [];
        return <section className="asset-purpose-group" key={kind}>
          <div className="asset-list-header"><span>{label}</span><small>{grouped.length} FILES</small></div>
          <p className="purpose-explain">{{ CHARACTER_REFERENCE: "绑定主要姓名与绰号，用于保持脸、发型和体型一致。", OUTFIT_REFERENCE: "选择图片后，上方绑定流程会明确保存“角色 → 服装档案 → 参考图”的关系。", STYLE_REFERENCE: "选择后由默认视觉模型按所选的黑白或彩色模式总结可复用画面语言。", SCENE_REFERENCE: "用于固定地点结构、空间透视和环境基调，请在场景资产中绑定到具体地点。" }[kind]}</p>
          <div className="asset-grid">{grouped.map((asset, index) => {
            const characterReference = kind === "CHARACTER_REFERENCE" ? boundCharacter?.references.find((reference) => reference.asset_id === asset.id) : undefined;
            const linkedCharacter = kind === "CHARACTER_REFERENCE" ? characters.data?.find((character) => character.references.some((reference) => reference.asset_id === asset.id)) : undefined;
            const selected = kind === "CHARACTER_REFERENCE" ? Boolean(characterReference) : kind === "OUTFIT_REFERENCE" ? selectedOutfitAssets.includes(asset.id) : kind === "STYLE_REFERENCE" ? selectedStyleAssets.includes(asset.id) : false;
            const linkedOutfits = kind === "OUTFIT_REFERENCE" ? outfits.data?.filter((outfit) => outfit.reference_asset_ids.includes(asset.id)) ?? [] : [];
            const linkedStyles = kind === "STYLE_REFERENCE" ? styles.data?.filter((style) => style.profile.reference_asset_ids?.includes(asset.id)) ?? [] : [];
            return <article className={selected ? "asset-card selected" : "asset-card"} key={asset.id}>
              <div className={`asset-thumb thumb-${(index % 3) + 1}`}>{asset.content_url ? <Image src={publicUrl(asset.content_url)!} alt={assetName(asset)} width={74} height={74} unoptimized /> : <FileImage size={27} />}<span>{asset.width && asset.height ? `${asset.width}×${asset.height}` : asset.mime_type}</span></div>
              <div><AssetNameEditor asset={asset} pending={renameAsset.isPending && renameAsset.variables?.assetId === asset.id} error={renameAsset.isError && renameAsset.variables?.assetId === asset.id ? renameAsset.error : null} onSave={(displayName) => renameAsset.mutate({ assetId: asset.id, displayName })} /><p>{label} · {formatBytes(asset.byte_size)}</p><span className="tiny-status"><Check size={11} />{assetStatusLabels[asset.status] ?? asset.status}</span>
                {kind === "OUTFIT_REFERENCE" && <p className={linkedOutfits.length ? "reference-binding bound" : "reference-binding"}><Link2 size={10} />{linkedOutfits.length ? `已绑定：${linkedOutfits.map((outfit) => `${characters.data?.find((character) => character.id === outfit.character_id)?.primary_name ?? "未知角色"} → ${outfit.name}`).join("；")}` : "尚未写入服装档案"}</p>}
                {kind === "STYLE_REFERENCE" && <p className={linkedStyles.length ? "reference-binding bound" : "reference-binding"}><Link2 size={10} />{linkedStyles.length ? `已用于：${linkedStyles.map((style) => `${style.name}（${style.color_mode === "monochrome" ? "黑白" : "彩色"}）`).join("；")}` : "尚未写入风格档案"}</p>}
                {kind === "CHARACTER_REFERENCE" && linkedCharacter && !characterReference ? <p className="reference-binding bound"><Link2 size={10} />当前绑定：{linkedCharacter.primary_name}</p> : null}
                {kind === "CHARACTER_REFERENCE" ? <button className={characterReference ? "bind-purpose bound" : "bind-purpose"} disabled={!boundCharacter || bindExistingCharacterReference.isPending || unbindExistingCharacterReference.isPending} onClick={() => characterReference ? unbindExistingCharacterReference.mutate(characterReference.id) : bindExistingCharacterReference.mutate(asset.id)}>{!boundCharacter ? "先选择角色" : characterReference ? `解除与 ${boundCharacter.primary_name} 的绑定` : linkedCharacter ? `改绑到 ${boundCharacter.primary_name}（自动解除 ${linkedCharacter.primary_name}）` : `绑定到 ${boundCharacter.primary_name}`}</button> : kind === "SCENE_REFERENCE" ? <p className="reference-binding">请到场景资产工作区绑定地点，不在此建立假绑定。</p> : <button className="bind-purpose" disabled={kind === "OUTFIT_REFERENCE" && !bindCharacterId} onClick={() => kind === "OUTFIT_REFERENCE" ? setSelectedOutfitAssets((values) => values.includes(asset.id) ? values.filter((item) => item !== asset.id) : [...values, asset.id]) : setSelectedStyleAssets((values) => values.includes(asset.id) ? values.filter((item) => item !== asset.id) : [...values, asset.id])}>{kind === "OUTFIT_REFERENCE" && !bindCharacterId ? "先选择所属角色" : selected ? "已选：保存后绑定" : kind === "OUTFIT_REFERENCE" ? "加入当前服装档案" : "加入当前风格档案"}</button>}
                <div className="asset-actions"><select aria-label="修改素材用途" value={asset.kind} onChange={(event) => reclassifyAsset.mutate({ assetId: asset.id, kind: event.target.value as AssetPurpose })}>{kinds.map(([value, option]) => <option key={value} value={value}>{option}</option>)}</select><button title="删除素材" disabled={deleteAsset.isPending} onClick={() => { if (window.confirm("删除该素材及其候选记录，并解除已有绑定？")) deleteAsset.mutate(asset.id); }}><Trash2 size={13} /></button></div>
              </div>
            </article>;
          })}</div>
          {!grouped.length && <div className="purpose-empty">尚无{label}</div>}
        </section>;
      })}
      </>}
    </>
  );
}
