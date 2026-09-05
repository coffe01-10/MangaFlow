"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import type { ChangeEvent, DragEvent } from "react";

import { activePollInterval } from "@/lib/task-status";
import { api, type AssetPurpose, type ImageModelAlias, type Outfit, type StyleProfile } from "@/lib/api";

import { assetKindByView } from "./labels";
import type { AssetWorkspaceView, WorkspaceSection } from "./types";
import type { WorkspaceQueries } from "./use-workspace-queries";

/**
 * Assets domain: character/outfit/style reference states, their mutations and
 * the asset-section queries (styles, generated-reference picker, asset
 * batches/candidates). Invalidations keep the original query keys verbatim.
 */
export function useAssetsWorkspace({
  id,
  section,
  assetView,
  router,
  projectPath,
  activeChapterId,
  assets,
  characters,
  outfits,
  requireDrawModel,
}: {
  id: string;
  section: WorkspaceSection;
  assetView: AssetWorkspaceView;
  router: ReturnType<typeof useRouter>;
  projectPath: (target: string) => string;
  activeChapterId: string | null;
  assets: WorkspaceQueries["assets"];
  characters: WorkspaceQueries["characters"];
  outfits: WorkspaceQueries["outfits"];
  requireDrawModel: () => ImageModelAlias;
}) {
  const queryClient = useQueryClient();
  const [assetKind, setAssetKind] = useState<AssetPurpose>("CHARACTER_REFERENCE");
  const currentAssetKind = assetView === "references" ? assetKind : assetKindByView[assetView];
  const [uploadError, setUploadError] = useState("");
  const [assetDragActive, setAssetDragActive] = useState(false);
  const [characterName, setCharacterName] = useState("");
  const [characterAliases, setCharacterAliases] = useState("");
  const [editCharacterName, setEditCharacterName] = useState("");
  const [editCharacterAliases, setEditCharacterAliases] = useState("");
  const [editLockedFeatures, setEditLockedFeatures] = useState("");
  const [editForbiddenChanges, setEditForbiddenChanges] = useState("");
  const [bindCharacterId, setBindCharacterId] = useState("");
  const [outfitName, setOutfitName] = useState("");
  const [outfitLockedFields, setOutfitLockedFields] = useState("");
  const [editingOutfitId, setEditingOutfitId] = useState<string | null>(null);
  const [styleName, setStyleName] = useState("黑白网点风格");
  const [styleLockedFields, setStyleLockedFields] = useState("");
  const [styleColorMode, setStyleColorMode] = useState<StyleProfile["color_mode"]>(() => {
    if (typeof window === "undefined") return "monochrome";
    return window.localStorage.getItem(`mangaflow.style-mode.${id}`) === "color" ? "color" : "monochrome";
  });
  const [selectedOutfitAssets, setSelectedOutfitAssets] = useState<string[]>([]);
  const [showGeneratedReferencePicker, setShowGeneratedReferencePicker] = useState(false);
  const [selectedStyleAssets, setSelectedStyleAssets] = useState<string[]>([]);
  const [selectedCharacterOutfitId, setSelectedCharacterOutfitId] = useState("");

  const styles = useQuery({
    queryKey: ["styles", id],
    queryFn: () => api.styles(id),
    enabled: section === "assets" && ["style", "references"].includes(assetView),
    refetchInterval: (query) => (query.state.data ?? []).some((style) => style.status === "ANALYZING") ? 2500 : false,
  });
  const generatedReferenceLibrary = useQuery({
    queryKey: ["generated-reference-library", id, bindCharacterId],
    queryFn: () => api.library(id, { character_id: bindCharacterId, limit: 30 }),
    enabled: section === "assets" && assetView === "outfits" && showGeneratedReferencePicker && Boolean(bindCharacterId),
  });
  const boundCharacter = characters.data?.find((item) => item.id === bindCharacterId) ?? null;
  const editingOutfit = outfits.data?.find((item) => item.id === editingOutfitId) ?? null;
  const selectedOutfitFiles = assets.data?.filter((item) => selectedOutfitAssets.includes(item.id)) ?? [];
  const generatedReferenceCandidates = useMemo(
    () => (generatedReferenceLibrary.data?.groups ?? [])
      .flatMap((group) => group.candidates.map((candidate) => ({
        candidate,
        generationKind: group.batch.generation_kind,
      })))
      .filter(({ candidate }) => Boolean(candidate.asset_id && candidate.content_url))
      .filter(({ candidate }, index, values) => values.findIndex((item) => item.candidate.asset_id === candidate.asset_id) === index),
    [generatedReferenceLibrary.data],
  );
  const selectedStyleFiles = assets.data?.filter((item) => selectedStyleAssets.includes(item.id)) ?? [];
  const assetGenerationTarget = selectedCharacterOutfitId
    ? { type: "OUTFIT" as const, id: selectedCharacterOutfitId }
    : null;
  const assetBatches = useQuery({
    queryKey: ["asset-batches", assetGenerationTarget?.type, assetGenerationTarget?.id],
    queryFn: () => api.assetBatches(assetGenerationTarget!.type, assetGenerationTarget!.id),
    enabled: section === "assets" && assetView === "outfits" && Boolean(assetGenerationTarget),
  });
  const currentAssetBatch = assetBatches.data?.[0] ?? null;
  const assetCandidates = useQuery({
    queryKey: ["asset-candidates", currentAssetBatch?.id],
    queryFn: () => api.candidates(currentAssetBatch!.id),
    enabled: section === "assets" && assetView === "outfits" && Boolean(currentAssetBatch),
    refetchInterval: (query) => activePollInterval(query.state.data, 2000),
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const uploaded = await api.uploadAsset(id, currentAssetKind, file);
      if (currentAssetKind === "CHARACTER_REFERENCE" && bindCharacterId) {
        await api.bindCharacterReference(bindCharacterId, uploaded.id);
      }
      return uploaded;
    },
    onSuccess: (uploaded) => {
      setUploadError("");
      if (currentAssetKind === "OUTFIT_REFERENCE") {
        setSelectedOutfitAssets((values) => values.includes(uploaded.id) ? values : [...values, uploaded.id]);
      }
      if (currentAssetKind === "STYLE_REFERENCE") {
        setSelectedStyleAssets((values) => values.includes(uploaded.id) ? values : [...values, uploaded.id]);
      }
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
    onError: (reason) => setUploadError(reason instanceof Error ? reason.message : "上传失败"),
  });

  const deleteAsset = useMutation({
    mutationFn: (assetId: string) => api.deleteAsset(assetId),
    onSuccess: (_, assetId) => {
      setSelectedOutfitAssets((values) => values.filter((item) => item !== assetId));
      setSelectedStyleAssets((values) => values.filter((item) => item !== assetId));
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
      queryClient.invalidateQueries({ queryKey: ["styles", id] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });

  const reclassifyAsset = useMutation({
    mutationFn: ({ assetId, kind }: { assetId: string; kind: AssetPurpose }) => api.updateAsset(assetId, { kind }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const adoptGeneratedReference = useMutation({
    mutationFn: (assetId: string) => api.adoptGeneratedAssetAsReference(assetId),
    onSuccess: (asset) => {
      setSelectedOutfitAssets((values) => values.includes(asset.id) ? values : [...values, asset.id]);
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
      queryClient.invalidateQueries({ queryKey: ["generated-reference-library", id] });
    },
  });

  const renameAsset = useMutation({
    mutationFn: ({ assetId, displayName }: { assetId: string; displayName: string }) => api.updateAsset(assetId, { display_name: displayName }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const bindExistingCharacterReference = useMutation({
    mutationFn: (assetId: string) => {
      if (!boundCharacter) throw new Error("请先选择要绑定的角色");
      return api.bindCharacterReference(boundCharacter.id, assetId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const unbindExistingCharacterReference = useMutation({
    mutationFn: (referenceId: string) => api.unbindCharacterReference(referenceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const createCharacter = useMutation({
    mutationFn: () => api.createCharacter(
      id,
      characterName.trim(),
      characterAliases.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
    ),
    onSuccess: (result) => {
      setCharacterName("");
      setCharacterAliases("");
      setBindCharacterId(result.id);
      setEditCharacterName(result.primary_name);
      setEditCharacterAliases(result.aliases.join("，"));
      setEditLockedFeatures(result.locked_features.join("，"));
      setEditForbiddenChanges(result.forbidden_changes.join("，"));
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const updateCharacter = useMutation({
    mutationFn: () => {
      if (!boundCharacter) throw new Error("请先选择角色");
      return api.updateCharacter(
        boundCharacter.id,
        boundCharacter.version,
        editCharacterName.trim(),
        editCharacterAliases.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
        editLockedFeatures.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
        editForbiddenChanges.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
      );
    },
    onSuccess: (result) => {
      setEditCharacterName(result.primary_name);
      setEditCharacterAliases(result.aliases.join("，"));
      setEditLockedFeatures(result.locked_features.join("，"));
      setEditForbiddenChanges(result.forbidden_changes.join("，"));
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const createOutfit = useMutation({
    mutationFn: () => api.createOutfit(id, {
      character_id: bindCharacterId,
      name: outfitName.trim(),
      reference_asset_ids: selectedOutfitAssets,
      locked_fields: outfitLockedFields.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
    }),
    onSuccess: () => {
      setOutfitName("");
      setOutfitLockedFields("");
      setSelectedOutfitAssets([]);
      setEditingOutfitId(null);
      setShowGeneratedReferencePicker(false);
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
    },
  });

  const updateOutfit = useMutation({
    mutationFn: () => {
      if (!editingOutfit) throw new Error("服装档案不存在，请刷新后重试");
      return api.updateOutfit(editingOutfit.id, {
        version: editingOutfit.version,
        name: outfitName.trim(),
        reference_asset_ids: selectedOutfitAssets,
        locked_fields: outfitLockedFields.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
      });
    },
    onSuccess: () => {
      setOutfitName("");
      setOutfitLockedFields("");
      setSelectedOutfitAssets([]);
      setEditingOutfitId(null);
      setShowGeneratedReferencePicker(false);
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
    },
  });

  const deleteOutfit = useMutation({
    mutationFn: (outfit: Outfit) => api.deleteOutfit(outfit.id),
    onSuccess: (_, outfit) => {
      if (editingOutfitId === outfit.id) resetOutfitForm();
      if (selectedCharacterOutfitId === outfit.id) setSelectedCharacterOutfitId("");
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["asset-batches"] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["script", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["storyboard"] });
      setUploadError("");
    },
    onError: (reason) => setUploadError(
      reason instanceof Error ? reason.message : "删除服装档案失败",
    ),
  });

  const generateOutfitPreview = useMutation({
    mutationFn: async (outfitId: string) => {
      setSelectedCharacterOutfitId(outfitId);
      const batch = await api.startAssetBatch("OUTFIT", outfitId, "OUTFIT");
      return api.generateAssetCandidate(batch.id, requireDrawModel(), "1K", "OUTFIT");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset-batches"] });
      queryClient.invalidateQueries({ queryKey: ["asset-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const createStyle = useMutation({
    mutationFn: () => api.createStyle(
      id,
      styleName.trim(),
      styleColorMode,
      selectedStyleAssets,
      styleLockedFields.split(/[，,、]/).map((item) => item.trim()).filter(Boolean),
    ),
    onSuccess: (style) => {
      setSelectedStyleAssets([]);
      setStyleLockedFields("");
      queryClient.invalidateQueries({ queryKey: ["styles", id] });
      // The auto-triggered analysis must not fail silently: an unhandled
      // rejection left the style stuck in DRAFT with no recovery hint.
      api.analyzeStyle(style.id)
        .then(() => queryClient.invalidateQueries({ queryKey: ["jobs", id] }))
        .catch(() => setUploadError("风格分析任务启动失败；请在已保存档案中点击“重新分析画面语言”重试。"));
    },
  });

  const analyzeStyle = useMutation({
    mutationFn: (styleId: string) => api.analyzeStyle(styleId),
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
  });

  const updateStyleMode = useMutation({
    mutationFn: ({ style, colorMode }: { style: StyleProfile; colorMode: StyleProfile["color_mode"] }) =>
      api.updateStyleMode(style.id, style.version, colorMode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["styles", id] }),
  });

  function selectStyleMode(mode: StyleProfile["color_mode"]) {
    setStyleColorMode(mode);
    window.localStorage.setItem(`mangaflow.style-mode.${id}`, mode);
    setStyleName((current) => ["黑白网点风格", "彩色漫画风格"].includes(current) ? (mode === "monochrome" ? "黑白网点风格" : "彩色漫画风格") : current);
  }

  function resetOutfitForm() {
    setEditingOutfitId(null);
    setOutfitName("");
    setOutfitLockedFields("");
    setSelectedOutfitAssets([]);
    setShowGeneratedReferencePicker(false);
  }

  function beginOutfitEdit(outfit: Outfit) {
    setEditingOutfitId(outfit.id);
    setBindCharacterId(outfit.character_id);
    setOutfitName(outfit.name);
    setOutfitLockedFields(outfit.locked_fields.join("，"));
    setSelectedOutfitAssets(outfit.reference_asset_ids);
    setShowGeneratedReferencePicker(false);
    setAssetKind("OUTFIT_REFERENCE");
  }

  function uploadReferenceFile(file?: File) {
    if (!file || upload.isPending) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setUploadError("只支持 PNG、JPEG 或 WebP 图片");
      return;
    }
    setUploadError("");
    upload.mutate(file);
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    uploadReferenceFile(event.target.files?.[0]);
    event.target.value = "";
  }

  function dropReferenceFile(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setAssetDragActive(false);
    uploadReferenceFile(event.dataTransfer.files?.[0]);
  }

  function confirmDeleteOutfit(outfit: Outfit) {
    const message = `删除服装档案“${outfit.name}”？\n\n将同时删除绑定的 ${outfit.reference_asset_ids.length} 张参考图、已生成的穿着图，并清除剧本与分镜中的服装绑定。被其他档案共用的图片会保留。`;
    if (window.confirm(message)) deleteOutfit.mutate(outfit);
  }

  return {
    assetKind,
    setAssetKind,
    currentAssetKind,
    uploadError,
    setUploadError,
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
    setEditingOutfitId,
    styleName,
    setStyleName,
    styleLockedFields,
    setStyleLockedFields,
    styleColorMode,
    setStyleColorMode,
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
    assetBatches,
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
    uploadReferenceFile,
    chooseFile,
    dropReferenceFile,
    confirmDeleteOutfit,
  };
}

export type AssetsWorkspace = ReturnType<typeof useAssetsWorkspace>;
