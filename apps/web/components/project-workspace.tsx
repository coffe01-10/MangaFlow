"use client";

import { AppShell } from "@/components/shell";
import { ScriptEditor } from "@/components/script-editor";
import { StoryboardEditor } from "@/components/storyboard-editor";
import { ProductionReadiness } from "@/components/production-readiness";
import { CharacterConceptPanel, StyleProductionPanel } from "@/components/asset-production-panel";
import {
  api,
  publicUrl,
  type Asset,
  type ImageModelAlias,
  type AssetPurpose,
  type InspectionResult,
  type Job,
  type Outfit,
  type Project,
  type Resolution,
  type StyleProfile,
} from "@/lib/api";
import { getPageGenerationIssue, getPageStructureIssue } from "@/lib/generation-rules";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  Archive,
  BookOpenText,
  Clapperboard,
  Check,
  CircleAlert,
  Download,
  FileImage,
  Heart,
  History,
  ImagePlus,
  LibraryBig,
  Link2,
  ListTodo,
  LoaderCircle,
  Maximize2,
  ZoomIn,
  ZoomOut,
  Menu,
  PanelTop,
  Plus,
  Pencil,
  RotateCcw,
  Save,
  Shirt,
  Sparkles,
  Star,
  Trash2,
  Upload,
  Users,
  Palette,
  Settings,
  Workflow,
  X,
} from "lucide-react";
import Link from "next/link";
import Image from "next/image";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ChangeEvent, useEffect, useMemo, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";

export type WorkspaceSection = "source" | "assets" | "script" | "storyboard" | "generate" | "library" | "jobs";
export type AssetWorkspaceView = "characters" | "outfits" | "style" | "references";

const navigationItems = [
  ["source", "原作与修订", "导入、修改、撤回", "01", BookOpenText],
  ["assets", "参考资产", "人物 / 服装 / 风格", "02", Users],
  ["script", "漫画剧本", "场景、情节拍、对白", "03", Clapperboard],
  ["storyboard", "分页与分镜", "场景切页、格子脚本", "04", PanelTop],
  ["generate", "单页生成", "抽卡、收藏、采用", "05", Sparkles],
  ["library", "生成素材库", "按类型和批次归档", "06", LibraryBig],
  ["jobs", "任务中心", "进度、失败、取消重试", "07", ListTodo],
] as const;

const kinds = [
  ["CHARACTER_REFERENCE", "人物参考"],
  ["OUTFIT_REFERENCE", "服装参考"],
  ["STYLE_REFERENCE", "漫画风格"],
] as const;

const assetKindByView: Record<Exclude<AssetWorkspaceView, "references">, AssetPurpose> = {
  characters: "CHARACTER_REFERENCE",
  outfits: "OUTFIT_REFERENCE",
  style: "STYLE_REFERENCE",
};

const jobLabels: Record<string, string> = {
  SOURCE_PARSE: "解析剧本", PAGE_GENERATE: "生成页面", PAGE_REPAIR: "修复页面",
  PAGE_UPSCALE: "保持结构升清", ASSET_GENERATE: "生成角色/服装素材",
  PAGE_INSPECT: "检查页面", STYLE_ANALYZE: "分析漫画风格",
  WORKFLOW_NODE: "执行工作流节点",
};

const generationKindLabels: Record<string, string> = {
  PAGE: "页面抽卡",
  REPAIR: "页面修复",
  CHARACTER: "角色形象补全",
  OUTFIT: "角色服装形象",
  STYLE_TEST: "漫画风格测试",
  UPSCALE: "保持结构升清",
};

const inspectionLabels: Record<string, string> = {
  TEXT: "文字",
  SPEAKER: "说话人",
  CHARACTER: "角色",
  OUTFIT: "服装",
  PROP: "道具",
  CONTINUITY: "连续性",
};

const repairTypeLabels = {
  BUBBLE_REGION: "气泡区域",
  PANEL: "单格",
  PAGE: "整页",
} as const;

function recommendedRepairType(category: string): "BUBBLE_REGION" | "PANEL" | "PAGE" {
  if (category === "SPEAKER") return "BUBBLE_REGION";
  if (["CHARACTER", "OUTFIT", "PROP"].includes(category)) return "PANEL";
  return "PAGE";
}

function inspectionSummary(details: Record<string, unknown>) {
  const expected = typeof details.expected === "string" ? details.expected : "";
  const observed = typeof details.observed === "string" ? details.observed : "";
  if (expected || observed) return [expected && `应为：${expected}`, observed && `实为：${observed}`].filter(Boolean).join("；");
  return Object.entries(details).map(([key, value]) => `${key}: ${String(value)}`).join("；") || "模型未补充说明";
}

function inspectionBubbleDiffs(details: Record<string, unknown>) {
  if (!Array.isArray(details.bubble_diffs)) return [];
  return details.bubble_diffs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
}

function ImageModelPicker({
  selected,
  onSelect,
  options,
  label,
}: {
  selected: ImageModelAlias | null;
  onSelect: (model: ImageModelAlias) => void;
  options: { alias: ImageModelAlias; name: string; id: string; provider: string }[];
  label?: string;
}) {
  return <div className="model-picker">
    {label && <p>{label}</p>}
    <div className="model-duel">{options.map((option) => <button type="button" aria-pressed={selected === option.alias} key={option.alias} className={selected === option.alias ? "model-choice active" : "model-choice"} onClick={() => onSelect(option.alias)}><Sparkles size={18} /><span><strong>{option.name}</strong><small>{option.provider} · {option.id}</small></span>{selected === option.alias && <Check size={15} />}</button>)}</div>
    {!options.length && <p className="form-error"><CircleAlert size={14} />暂无已启用且支持参考图编辑的图片模型，请先到系统设置配置供应商。</p>}
  </div>;
}

function ComicModeSwitch({
  value,
  onChange,
  compact = false,
  disabled = false,
}: {
  value: StyleProfile["color_mode"];
  onChange: (mode: StyleProfile["color_mode"]) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  return <div className={compact ? "comic-mode-switch compact" : "comic-mode-switch"} role="group" aria-label="漫画色彩模式">
    <button type="button" aria-pressed={value === "monochrome"} className={value === "monochrome" ? "active monochrome" : "monochrome"} disabled={disabled} onClick={() => onChange("monochrome")}><i />黑白漫画</button>
    <button type="button" aria-pressed={value === "color"} className={value === "color" ? "active color" : "color"} disabled={disabled} onClick={() => onChange("color")}><i />彩色漫画</button>
  </div>;
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function assetName(asset: Asset | undefined) {
  return asset?.display_name?.trim() || asset?.original_name || "未命名素材";
}

function AssetNameEditor({ asset, pending, onSave }: { asset: Asset; pending: boolean; onSave: (displayName: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(asset.display_name ?? asset.original_name);
  const visibleName = asset.display_name?.trim() || asset.original_name;

  if (editing) {
    return <form className="asset-name-edit" onSubmit={(event) => {
      event.preventDefault();
      const next = value.trim();
      if (!next) return;
      onSave(next);
      setEditing(false);
    }}>
      <input aria-label={`重命名 ${visibleName}`} maxLength={120} autoFocus value={value} onChange={(event) => setValue(event.target.value)} />
      <button type="submit" aria-label="保存素材名称" disabled={pending || !value.trim()}><Check size={14} /></button>
      <button type="button" aria-label="取消重命名" onClick={() => { setValue(visibleName); setEditing(false); }}><X size={14} /></button>
    </form>;
  }

  return <div className="asset-name-row">
    <strong title={`原始文件名：${asset.original_name}`}>{visibleName}</strong>
    <button type="button" aria-label={`重命名 ${visibleName}`} title="自定义素材名称" onClick={() => { setValue(visibleName); setEditing(true); }}><Pencil size={13} /></button>
  </div>;
}

function promptPreview(candidate: { prompt_snapshot: Record<string, unknown> }) {
  return typeof candidate.prompt_snapshot.prompt_preview === "string"
    ? candidate.prompt_snapshot.prompt_preview
    : "任务排队后会在这里保存本次实际提示词。";
}

function CandidateArtwork({ contentUrl, thumbnailUrl, label, onOpen, eager = false }: { contentUrl: string | null; thumbnailUrl?: string | null; label: string; onOpen?: (url: string, label: string) => void; eager?: boolean }) {
  const url = publicUrl(thumbnailUrl ?? contentUrl);
  const fullUrl = publicUrl(contentUrl ?? thumbnailUrl ?? null);
  return url ? (
    <button type="button" className="candidate-artwork" aria-label={`放大查看${label}`} onClick={() => fullUrl && onOpen?.(fullUrl, label)}><Image className="candidate-image" src={url} alt={label} width={720} height={960} loading={eager ? "eager" : "lazy"} unoptimized /><span><Maximize2 size={15} />放大</span></button>
  ) : (
    <div className="candidate-placeholder"><LoaderCircle size={22} /><span>等待 Worker 生成</span></div>
  );
}

export default function ProjectWorkspace({
  section,
  assetView = "characters",
}: {
  section: WorkspaceSection;
  assetView?: AssetWorkspaceView;
}) {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [navOpen, setNavOpen] = useState(false);
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [assetKind, setAssetKind] = useState<AssetPurpose>("CHARACTER_REFERENCE");
  const currentAssetKind = assetView === "references" ? assetKind : assetKindByView[assetView];
  const [uploadError, setUploadError] = useState("");
  const [showArchivedJobs, setShowArchivedJobs] = useState(false);
  const [jobNotice, setJobNotice] = useState("");
  const [selectedJobIds, setSelectedJobIds] = useState<string[]>([]);
  const [sourceTitle, setSourceTitle] = useState("第一章");
  const [sourceText, setSourceText] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(() => searchParams.get("page"));
  const [drawModel, setDrawModel] = useState<ImageModelAlias | null>(null);
  const [characterName, setCharacterName] = useState("");
  const [characterAliases, setCharacterAliases] = useState("");
  const [editCharacterName, setEditCharacterName] = useState("");
  const [editCharacterAliases, setEditCharacterAliases] = useState("");
  const [editLockedFeatures, setEditLockedFeatures] = useState("");
  const [editForbiddenChanges, setEditForbiddenChanges] = useState("");
  const [bindCharacterId, setBindCharacterId] = useState("");
  const [favoriteOnly, setFavoriteOnly] = useState(false);
  const [libraryChapter, setLibraryChapter] = useState("");
  const [libraryCharacter, setLibraryCharacter] = useState("");
  const [libraryKind, setLibraryKind] = useState("");
  const [libraryModel, setLibraryModel] = useState("");
  const [libraryResolution, setLibraryResolution] = useState("");
  const [libraryDateFrom, setLibraryDateFrom] = useState("");
  const [libraryDateTo, setLibraryDateTo] = useState("");
  const [libraryCursor, setLibraryCursor] = useState("");
  const [libraryHistory, setLibraryHistory] = useState<string[]>([]);
  const [editingChapterId, setEditingChapterId] = useState<string | null>(null);
  const [deletedChapterId, setDeletedChapterId] = useState<string | null>(null);
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
  const [selectedStyleAssets, setSelectedStyleAssets] = useState<string[]>([]);
  const [reviewCandidateId, setReviewCandidateId] = useState<string | null>(null);
  const [selectedCharacterOutfitId, setSelectedCharacterOutfitId] = useState("");
  const [previewImage, setPreviewImageState] = useState<{ url: string; label: string } | null>(null);
  const [previewZoom, setPreviewZoom] = useState(1);
  const setPreviewImage = (value: { url: string; label: string } | null) => {
    if (value) setPreviewZoom(1);
    setPreviewImageState(value);
  };
  const openPreview = (url: string, label: string) => {
    setPreviewZoom(1);
    setPreviewImage({ url, label });
  };
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    if (typeof window === "undefined") return 214;
    const stored = Number(window.localStorage.getItem("mangaflow.project-sidebar-width"));
    return stored >= 188 && stored <= 360 ? stored : 214;
  });
  const [referenceSelections, setReferenceSelections] = useState<Record<string, { character_asset_id: string | null; outfit_id: string | null; outfit_asset_id: string | null }>>({});
  const [confirmedReferencePageId, setConfirmedReferencePageId] = useState<string | null>(null);

  const needsChapters = ["source", "script", "storyboard", "generate", "library"].includes(section);
  const needsCharacters = section === "assets"
    ? assetView !== "style"
    : ["script", "storyboard", "generate", "library"].includes(section);
  const needsOutfits = section === "assets"
    ? ["outfits", "references"].includes(assetView)
    : ["script", "storyboard", "generate"].includes(section);
  const needsPages = ["storyboard", "generate"].includes(section);
  const needsScript = ["source", "script"].includes(section);
  const projectPath = (target: string) =>
    target === "assets" ? `/projects/${id}/assets/characters` : `/projects/${id}/${target}`;

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id), staleTime: 30_000 });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models, staleTime: 30_000 });
  const assets = useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id), enabled: ["assets", "generate"].includes(section) });
  const chapters = useQuery({ queryKey: ["chapters", id], queryFn: () => api.chapters(id), enabled: needsChapters });
  const characters = useQuery({ queryKey: ["characters", id], queryFn: () => api.characters(id), enabled: needsCharacters });
  const outfits = useQuery({ queryKey: ["outfits", id], queryFn: () => api.outfits(id), enabled: needsOutfits });
  const styles = useQuery({
    queryKey: ["styles", id],
    queryFn: () => api.styles(id),
    enabled: section === "assets" && ["style", "references"].includes(assetView),
    refetchInterval: (query) => (query.state.data ?? []).some((style) => style.status === "ANALYZING") ? 2500 : false,
  });
  const library = useQuery({
    queryKey: ["library", id, favoriteOnly, libraryChapter, libraryCharacter, libraryKind, libraryModel, libraryResolution, libraryDateFrom, libraryDateTo, libraryCursor],
    queryFn: () => api.library(id, {
      favorite: favoriteOnly ? true : undefined,
      chapter_id: libraryChapter || undefined,
      character_id: libraryCharacter || undefined,
      generation_kind: libraryKind || undefined,
      model_alias: (libraryModel || undefined) as ImageModelAlias | undefined,
      resolution: (libraryResolution || undefined) as Resolution | undefined,
      date_from: libraryDateFrom ? `${libraryDateFrom}T00:00:00Z` : undefined,
      date_to: libraryDateTo ? `${libraryDateTo}T23:59:59Z` : undefined,
      cursor: libraryCursor || undefined,
      limit: 30,
    }),
    enabled: section === "library",
  });
  const jobs = useQuery({
    queryKey: ["jobs", id, showArchivedJobs],
    queryFn: () => api.jobs(id, showArchivedJobs),
    enabled: ["assets", "jobs", "generate"].includes(section),
    refetchInterval: (query) => (query.state.data ?? []).some((job) => ["WAITING", "QUEUED", "PREPARING", "GENERATING", "RUNNING"].includes(job.status)) ? 3000 : false,
  });
  const exportsQuery = useQuery({ queryKey: ["exports", id], queryFn: () => api.exports(id), enabled: section === "library" });
  const activeChapterId = selectedChapterId ?? chapters.data?.[0]?.id ?? null;
  const pages = useQuery({
    queryKey: ["pages", activeChapterId],
    queryFn: () => api.pages(activeChapterId!),
    enabled: needsPages && Boolean(activeChapterId),
  });
  const script = useQuery({
    queryKey: ["script", activeChapterId],
    queryFn: () => api.script(activeChapterId!),
    enabled: needsScript && Boolean(activeChapterId),
    refetchInterval: (query) => query.state.data?.status === "PROCESSING" ? 4000 : false,
  });
  const selectedPageEntry = pages.data?.find((item) => item.id === selectedPageId) ?? pages.data?.[0] ?? null;
  const workbench = useQuery({
    queryKey: ["generation-workbench", selectedPageEntry?.id],
    queryFn: () => api.generationWorkbench(selectedPageEntry!.id),
    enabled: section === "generate" && Boolean(selectedPageEntry),
    refetchInterval: (query) => (query.state.data?.candidates ?? []).some((candidate) => ["WAITING", "QUEUED", "PREPARING", "GENERATING"].includes(candidate.status)) ? 3000 : false,
  });
  const selectedPage = workbench.data?.page ?? selectedPageEntry;
  const currentBatch = workbench.data?.current_batch ?? null;
  const candidates = { data: workbench.data?.candidates, isLoading: workbench.isLoading };
  const generationStoryboard = { data: workbench.data?.storyboard, isLoading: workbench.isLoading };
  const pageReadiness = { data: workbench.data?.readiness, isLoading: workbench.isLoading, error: workbench.error };
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
    refetchInterval: (query) => (query.state.data ?? []).some((candidate) => ["WAITING", "QUEUED", "PREPARING", "GENERATING"].includes(candidate.status)) ? 2000 : false,
  });
  const inspections = useQuery({
    queryKey: ["inspections", reviewCandidateId],
    queryFn: () => api.inspections(reviewCandidateId!),
    enabled: section === "generate" && Boolean(reviewCandidateId),
    refetchInterval: (query) => (query.state.data ?? []).length ? false : 4000,
  });

  const workspaceRouteReady = !project.isLoading
    && (!needsChapters || !chapters.isLoading)
    && (!needsCharacters || !characters.isLoading)
    && (!needsOutfits || !outfits.isLoading)
    && (!needsPages || !pages.isLoading)
    && (!needsScript || !script.isLoading)
    && (section !== "assets" || !assets.isLoading)
    && (section !== "library" || !library.isLoading)
    && (section !== "jobs" || !jobs.isLoading);

  useEffect(() => {
    if (!workspaceRouteReady) return;
    const key = `mangaflow.workspace-scroll.${id}`;
    const saved = window.sessionStorage.getItem(key);
    if (saved === null) return;
    const top = Number(saved);
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: Number.isFinite(top) ? top : 0, behavior: "auto" });
      window.sessionStorage.removeItem(key);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [assetView, id, section, workspaceRouteReady]);

  const draft = localDraft ?? project.data ?? null;
  const modelOptions = useMemo(
    () => [{
      alias: "auto",
      name: "自动路由",
      id: "仅从已验证模型中按可靠性、延迟与成本选择",
      provider: "MangaFlow",
    }, ...(models.data ?? [])
      .filter((model) => model.enabled && model.model_type === "IMAGE" && model.operations.includes("image_edit"))
      .map((model) => ({
        alias: model.logical_alias,
        name: model.display_name,
        id: model.model_id,
        provider: model.provider,
      }))],
    [models.data],
  );
  const boundCharacter = characters.data?.find((item) => item.id === bindCharacterId) ?? null;
  const editingOutfit = outfits.data?.find((item) => item.id === editingOutfitId) ?? null;
  const selectedOutfitFiles = assets.data?.filter((item) => selectedOutfitAssets.includes(item.id)) ?? [];
  const selectedStyleFiles = assets.data?.filter((item) => selectedStyleAssets.includes(item.id)) ?? [];
  const activeDrawModel = drawModel;
  const visibleAssetKinds = assetView === "references"
    ? kinds
    : kinds.filter(([kind]) => kind === currentAssetKind);
  const selectedPageStructureIssue = getPageStructureIssue(selectedPage);
  const selectedPageGenerationIssue = getPageGenerationIssue(selectedPage, activeDrawModel);
  const invalidPlannedPageCount = (pages.data ?? []).filter((page) => getPageStructureIssue(page)).length;
  const visibleCharacterIds = useMemo(
    () => Array.from(new Set((generationStoryboard.data?.panels ?? []).flatMap((panel) => panel.characters))),
    [generationStoryboard.data?.panels],
  );
  const defaultReferenceSelections = useMemo(() => {
    const next: typeof referenceSelections = {};
    for (const characterId of visibleCharacterIds) {
      const character = characters.data?.find((item) => item.id === characterId);
      const assignedOutfitId = generationStoryboard.data?.panels.find((panel) => panel.outfits[characterId])?.outfits[characterId] ?? null;
      const outfit = outfits.data?.find((item) => item.id === assignedOutfitId);
      next[characterId] = {
        character_asset_id: character?.references.find((item) => item.is_canonical)?.asset_id ?? character?.references[0]?.asset_id ?? null,
        outfit_id: assignedOutfitId,
        outfit_asset_id: outfit?.reference_asset_ids[0] ?? null,
      };
    }
    return next;
  }, [characters.data, generationStoryboard.data?.panels, outfits.data, visibleCharacterIds]);
  const effectiveReferenceSelections = useMemo(
    () => ({ ...defaultReferenceSelections, ...referenceSelections }),
    [defaultReferenceSelections, referenceSelections],
  );
  const generationReferenceReady = visibleCharacterIds.every((characterId) => {
    const selection = effectiveReferenceSelections[characterId];
    if (!selection?.character_asset_id) return false;
    const outfit = outfits.data?.find((item) => item.id === selection.outfit_id);
    return !outfit || Boolean(outfit.reference_asset_ids.length && selection.outfit_asset_id);
  });
  const referencesConfirmed = confirmedReferencePageId === selectedPage?.id;
  const targetDialogues = useMemo(
    () => (generationStoryboard.data?.panels ?? []).flatMap((panel) => panel.dialogues.map((dialogue) => dialogue.target_text)).filter(Boolean),
    [generationStoryboard.data?.panels],
  );

  function selectStyleMode(mode: StyleProfile["color_mode"]) {
    setStyleColorMode(mode);
    window.localStorage.setItem(`mangaflow.style-mode.${id}`, mode);
    setStyleName((current) => ["黑白网点风格", "彩色漫画风格"].includes(current) ? (mode === "monochrome" ? "黑白网点风格" : "彩色漫画风格") : current);
  }

  function rememberWorkspaceScroll() {
    window.sessionStorage.setItem(`mangaflow.workspace-scroll.${id}`, String(window.scrollY));
    setNavOpen(false);
  }

  function beginSidebarResize(event: ReactPointerEvent<HTMLButtonElement>) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = sidebarWidth;
    const move = (moveEvent: PointerEvent) => setSidebarWidth(Math.min(360, Math.max(188, startWidth + moveEvent.clientX - startX)));
    const stop = (stopEvent: PointerEvent) => {
      const next = Math.min(360, Math.max(188, startWidth + stopEvent.clientX - startX));
      setSidebarWidth(next);
      window.localStorage.setItem("mangaflow.project-sidebar-width", String(next));
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function requireDrawModel(): ImageModelAlias {
    if (!activeDrawModel) throw new Error("请先选择一个支持参考图编辑的图片模型");
    return activeDrawModel;
  }
  const queueStats = useMemo(() => {
    const values = jobs.data ?? [];
    return {
      waiting: values.filter((item) => ["WAITING", "QUEUED"].includes(item.status)).length,
      failed: values.filter((item) => item.status === "FAILED").length,
    };
  }, [jobs.data]);
  const latestInspections = useMemo(() => {
    const latest = new Map<string, InspectionResult>();
    for (const item of inspections.data ?? []) {
      if (!latest.has(item.category)) latest.set(item.category, item);
    }
    return [...latest.values()];
  }, [inspections.data]);
  const reviewCandidate = candidates.data?.find((item) => item.id === reviewCandidateId) ?? null;
  const reviewJob = jobs.data?.find((item) => item.target_id === reviewCandidateId && item.job_type === "PAGE_INSPECT") ?? null;
  const selectedWorkbenchCandidate = workbench.data?.selected_candidate ?? null;

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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
    },
  });

  const reclassifyAsset = useMutation({
    mutationFn: ({ assetId, kind }: { assetId: string; kind: AssetPurpose }) => api.updateAsset(assetId, { kind }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
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
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
    },
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
      api.analyzeStyle(style.id).then(() => queryClient.invalidateQueries({ queryKey: ["jobs", id] }));
    },
  });

  const analyzeStyle = useMutation({
    mutationFn: (styleId: string) => api.analyzeStyle(styleId),
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
  });

  const assignOutfit = useMutation({
    mutationFn: ({ sceneId, assignments }: { sceneId: string; assignments: Record<string, string> }) =>
      api.assignSceneOutfits(sceneId, assignments),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["script", activeChapterId] }),
  });

  const importSource = useMutation({
    mutationFn: () => editingChapterId
      ? api.reviseSource(editingChapterId, sourceTitle.trim(), sourceText).then(() => ({ chapters: [], total_characters: 0 }))
      : api.importSource(id, sourceTitle.trim(), sourceText),
    onSuccess: (result) => {
      setSelectedChapterId(result.chapters[0]?.id ?? editingChapterId);
      setEditingChapterId(null);
      setSourceText("");
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
      queryClient.invalidateQueries({ queryKey: ["revisions"] });
      queryClient.invalidateQueries({ queryKey: ["script"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    },
  });

  const importSourceFile = useMutation({
    mutationFn: (file: File) => api.uploadSource(
      id,
      sourceTitle.trim() || file.name.replace(/\.(txt|md|markdown)$/i, ""),
      file,
    ),
    onSuccess: (result) => {
      setSelectedChapterId(result.chapters[0]?.id ?? null);
      setEditingChapterId(null);
      setSourceText("");
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
      queryClient.invalidateQueries({ queryKey: ["revisions"] });
      queryClient.invalidateQueries({ queryKey: ["script"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
    },
  });

  const deleteChapter = useMutation({
    mutationFn: (chapterId: string) => api.deleteChapter(chapterId),
    onSuccess: (_, chapterId) => {
      setDeletedChapterId(chapterId);
      setSelectedChapterId(null);
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const restoreChapter = useMutation({
    mutationFn: (chapterId: string) => api.restoreChapter(chapterId),
    onSuccess: (chapter) => {
      setDeletedChapterId(null);
      setSelectedChapterId(chapter.id);
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const parseChapter = useMutation({
    mutationFn: () => api.parseChapter(activeChapterId!),
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
  });

  const planChapter = useMutation({
    mutationFn: () => api.planChapter(activeChapterId!),
    onSuccess: (result) => {
      setSelectedPageId(result.pages[0]?.id ?? null);
      router.push(projectPath("storyboard"));
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const replanPage = useMutation({
    mutationFn: (pageNumber: number) => api.planChapter(activeChapterId!, pageNumber),
    onSuccess: (result, pageNumber) => {
      setSelectedPageId(
        result.pages.find((page) => page.page_number === pageNumber)?.id ?? null,
      );
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["chapters", id] });
    },
  });

  const startBatch = useMutation({
    mutationFn: () => {
      const issue = getPageStructureIssue(selectedPage);
      if (issue) throw new Error(issue);
      return api.startBatch(selectedPage!.id);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] }),
  });

  const generate = useMutation({
    mutationFn: async () => {
      const issue = getPageGenerationIssue(selectedPage, activeDrawModel);
      if (issue) throw new Error(issue);
      if (!pageReadiness.data?.ready) throw new Error(pageReadiness.isLoading ? "正在检查页面生产条件" : "页面生产准备尚未完成，请先处理阻塞项");
      if (!generationReferenceReady) throw new Error("请为本页每个入镜人物选择人物参考图，并补齐分镜指定服装的参考图");
      if (!referencesConfirmed) throw new Error("请先确认本页人物、服装与参考图对应关系");
      const batch = currentBatch ?? await api.startBatch(selectedPage!.id);
      return api.generateCandidate(
        batch.id,
        requireDrawModel(),
        "1K",
        selectedPage!.storyboard_version,
        effectiveReferenceSelections,
      );
    },
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["project", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["page-readiness", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
    },
  });

  const favorite = useMutation({
    mutationFn: ({ candidateId, value }: { candidateId: string; value: boolean }) =>
      api.favoriteCandidate(candidateId, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const deleteCandidate = useMutation({
    mutationFn: (candidateId: string) => api.deleteCandidate(candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const cancelJob = useMutation({
    mutationFn: (jobId: string) => api.cancelJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });
  const retryJob = useMutation({
    mutationFn: (jobId: string) => api.retryJob(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs", id] }),
  });

  const updateStyleMode = useMutation({
    mutationFn: ({ style, colorMode }: { style: StyleProfile; colorMode: StyleProfile["color_mode"] }) =>
      api.updateStyleMode(style.id, style.version, colorMode),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["styles", id] }),
  });
  const archiveJob = useMutation({
    mutationFn: (jobId: string) => api.archiveJob(jobId),
    onSuccess: () => {
      setJobNotice("任务已移入历史记录");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "归档失败"),
  });
  const restoreJob = useMutation({
    mutationFn: (jobId: string) => api.restoreJob(jobId),
    onSuccess: () => {
      setJobNotice("任务已恢复到近期记录");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "恢复失败"),
  });
  const archiveCompletedJobs = useMutation({
    mutationFn: () => api.archiveCompletedJobs(id),
    onSuccess: (result) => {
      setJobNotice(result.archived_count ? `已归档 ${result.archived_count} 条已结束任务` : "没有可归档的已结束任务");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "清空失败"),
  });
  const bulkArchiveJobs = useMutation({
    mutationFn: () => api.bulkArchiveJobs(id, selectedJobIds),
    onSuccess: (result) => {
      setJobNotice(`已批量归档 ${result.archived_count} 条任务`);
      setSelectedJobIds([]);
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "批量归档失败"),
  });
  const deleteJob = useMutation({
    mutationFn: (jobId: string) => api.deleteJob(jobId),
    onSuccess: () => {
      setJobNotice("无引用任务已彻底删除");
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
    },
    onError: (reason) => setJobNotice(reason instanceof Error ? reason.message : "删除失败"),
  });

  const inspectCandidate = useMutation({
    mutationFn: (candidateId: string) => api.inspectCandidate(candidateId),
    onSuccess: (_, candidateId) => {
      setReviewCandidateId(candidateId);
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["inspections", candidateId] });
    },
  });

  const repairCandidate = useMutation({
    mutationFn: (inspection: InspectionResult) => api.repairCandidate(reviewCandidateId!, {
      inspection_result_id: inspection.id,
      repair_type: recommendedRepairType(inspection.category),
      target_regions: inspection.regions,
      target_fields: [],
      model_alias: requireDrawModel(),
      resolution: reviewCandidate?.resolution ?? "1K",
    }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const upscaleCandidate = useMutation({
    mutationFn: ({ candidateId, resolution }: { candidateId: string; resolution: "2K" | "4K" }) =>
      api.upscaleCandidate(candidateId, requireDrawModel(), resolution),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const selectCandidate = useMutation({
    mutationFn: ({ candidateId, manualTextConfirmed, acceptStale = false }: { candidateId: string; manualTextConfirmed: boolean; acceptStale?: boolean }) =>
      api.selectCandidate(selectedPage!.id, candidateId, manualTextConfirmed, acceptStale),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
    },
  });

  const keepSelectedCandidate = useMutation({
    mutationFn: (candidateId: string) =>
      api.keepSelectedCandidate(selectedPage!.id, candidateId, selectedPage!.storyboard_version),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench", selectedPage?.id] });
    },
  });

  const retractSelectedCandidate = useMutation({
    mutationFn: (pageId: string) => api.retractSelectedCandidate(pageId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
      queryClient.invalidateQueries({ queryKey: ["generation-workbench"] });
    },
  });

  const goNext = useMutation({
    mutationFn: () => api.nextPage(selectedPage!.id),
    onSuccess: (next) => {
      setSelectedPageId(next.id);
      setReferenceSelections({});
      setConfirmedReferencePageId(null);
      queryClient.invalidateQueries({ queryKey: ["batches", next.id] });
    },
  });

  const createExport = useMutation({
    mutationFn: (type: "PNG" | "PDF" | "JSON") => api.createExport(activeChapterId!, type),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", id] }),
  });

  function resetOutfitForm() {
    setEditingOutfitId(null);
    setOutfitName("");
    setOutfitLockedFields("");
    setSelectedOutfitAssets([]);
  }

  function beginOutfitEdit(outfit: Outfit) {
    setEditingOutfitId(outfit.id);
    setBindCharacterId(outfit.character_id);
    setOutfitName(outfit.name);
    setOutfitLockedFields(outfit.locked_fields.join("，"));
    setSelectedOutfitAssets(outfit.reference_asset_ids);
    setAssetKind("OUTFIT_REFERENCE");
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) upload.mutate(file);
    event.target.value = "";
  }

  function chooseSourceFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) importSourceFile.mutate(file);
    event.target.value = "";
  }

  async function beginEditChapter(chapterId: string, title: string) {
    setSelectedChapterId(chapterId);
    const values = await queryClient.fetchQuery({ queryKey: ["revisions", chapterId], queryFn: () => api.revisions(chapterId) });
    const revision = values[0];
    setEditingChapterId(chapterId);
    setSourceTitle(title);
    setSourceText(revision?.original_text ?? "");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const activeJobStatuses = new Set(["WAITING", "QUEUED", "PREPARING", "UPLOADING_REFERENCES", "GENERATING", "CONSISTENCY_CHECKING", "REPAIRING"]);
  const activeJobs = (jobs.data ?? []).filter((job) => activeJobStatuses.has(job.status));
  const failedJobs = (jobs.data ?? []).filter((job) => job.status === "FAILED");
  const completedJobGroups = Object.entries(
    (jobs.data ?? []).filter((job) => !activeJobStatuses.has(job.status) && job.status !== "FAILED").reduce<Record<string, Job[]>>((groups, job) => {
      const date = new Date(job.created_at).toLocaleDateString("zh-CN");
      groups[date] = [...(groups[date] ?? []), job];
      return groups;
    }, {}),
  );

  function renderJob(job: Job, showProgress: boolean) {
    const terminal = ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(job.status);
    const resultUrl = publicUrl(job.result?.content_url ?? null);
    const showResult = () => {
      if (resultUrl && job.result) openPreview(resultUrl, job.result.label);
    };
    return <article className={`job-row status-${job.status.toLowerCase()} ${resultUrl ? "has-result" : ""}`} key={job.id} onClick={resultUrl ? showResult : undefined}>
      {!showArchivedJobs && terminal && <label className="job-select" onClick={(event) => event.stopPropagation()}><input type="checkbox" aria-label={`选择${jobLabels[job.job_type] ?? job.job_type}`} checked={selectedJobIds.includes(job.id)} onChange={(event) => setSelectedJobIds((values) => event.target.checked ? [...values, job.id] : values.filter((id) => id !== job.id))} /></label>}
      <div className="job-type"><span>{jobLabels[job.job_type] ?? job.job_type}</span><strong>{job.status}</strong></div>
      {showProgress && <div className="job-progress"><i><b style={{ width: `${job.progress}%` }} /></i><span>{job.progress}% · 尝试 {job.attempt_count}/{job.max_attempts}</span></div>}
      <div className="job-detail"><span>{job.workflow_node_id ? `节点 ${job.workflow_node_id}` : job.model_alias ? modelOptions.find((item) => item.alias === job.model_alias)?.name ?? job.model_alias : "系统任务"}</span><small>{job.duration_ms === null ? "尚未完成" : `耗时 ${(job.duration_ms / 1000).toFixed(1)} 秒`} · {job.estimated_cost === null ? "费用暂不可估算" : `预估 ¥${job.estimated_cost.toFixed(3)}`}</small>{job.error_message && <em>{job.error_code ? `${job.error_code} · ` : ""}{job.error_message}</em>}</div>
      <div className="job-actions" onClick={(event) => event.stopPropagation()}>{resultUrl && <button className="job-result-action" onClick={showResult}><Maximize2 size={12} />查看结果</button>}{!showArchivedJobs && activeJobStatuses.has(job.status) && <button onClick={() => cancelJob.mutate(job.id)}>取消</button>}{!showArchivedJobs && job.status === "FAILED" && <button onClick={() => retryJob.mutate(job.id)}><RotateCcw size={12} />重试</button>}{!showArchivedJobs && terminal && <button onClick={() => archiveJob.mutate(job.id)}><Archive size={12} />归档</button>}{showArchivedJobs && <button onClick={() => restoreJob.mutate(job.id)}><RotateCcw size={12} />恢复</button>}{showArchivedJobs && ["FAILED", "CANCELLED"].includes(job.status) && <button className="danger-action" onClick={() => { if (window.confirm("仅无候选、生成记录、工作流或任务依赖的失败任务可以彻底删除。继续吗？")) deleteJob.mutate(job.id); }}><Trash2 size={12} />彻底删除</button>}</div>
    </article>;
  }

  if (project.isLoading || !draft) {
    return <AppShell><div className="full-loading"><LoaderCircle className="spin" />加载项目工作区…</div></AppShell>;
  }
  if (project.isError) {
    return <AppShell><div className="full-loading error"><CircleAlert />项目无法打开</div></AppShell>;
  }

  return (
    <AppShell>
      <header className="workspace-topbar">
        <div className="workspace-crumb"><button className="project-nav-toggle" aria-expanded={navOpen} aria-label={navOpen ? "关闭项目导航" : "打开项目导航"} onClick={() => setNavOpen(!navOpen)}>{navOpen ? <X size={17} /> : <Menu size={17} />}</button><Link href="/"><ArrowLeft size={17} />项目</Link><i /><span>{draft.name}</span></div>
        <div className="workspace-status"><span><i />项目工作区</span><Link className="button outline compact" href={projectPath("workflow")}><Workflow size={15} />在工作流中查看</Link><Link className="button ink compact" href={projectPath("settings")}><Settings size={15} />项目设置</Link></div>
      </header>

      <div className="workspace-layout" style={{ "--workspace-sidebar-width": `${sidebarWidth}px` } as CSSProperties}>
        <button className={navOpen ? "workspace-nav-backdrop show" : "workspace-nav-backdrop"} onClick={() => setNavOpen(false)} aria-label="关闭项目导航" />
        <aside className={navOpen ? "workspace-left open" : "workspace-left"}>
          <button type="button" className="workspace-resizer" aria-label="拖动调整项目侧边栏宽度" onPointerDown={beginSidebarResize} />
          <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{draft.name}</h1><p>{needsChapters ? `${chapters.data?.length ?? 0} 章` : "漫画生产工作区"}{needsPages ? ` · ${pages.data?.length ?? 0} 页已规划` : ""}</p></div>
          <nav className="workspace-steps">
            {navigationItems.map(([target, label, , index, Icon]) => <Link scroll={false} key={target} href={projectPath(target)} className={section === target ? "active" : ""} aria-current={section === target ? "page" : undefined} onClick={rememberWorkspaceScroll}><Icon size={17} /><span>{label}</span><i>{index}</i></Link>)}
            <span className="workspace-nav-divider" />
            <Link href={projectPath("workflow")} onClick={() => setNavOpen(false)}><Workflow size={17} /><span>流程编排</span><i>FL</i></Link>
            <Link href={projectPath("settings")} onClick={() => setNavOpen(false)}><Settings size={17} /><span>项目设置</span><i>ST</i></Link>
          </nav>
        </aside>

        <section className="workspace-canvas">
          {section === "source" && (
            <>
              <header className="canvas-header"><div><span>SOURCE / 原作</span><h2>完整导入，不压缩故事</h2></div><small>{chapters.data?.length ?? 0} 个章节</small></header>
              <div className="source-compose">
                <input className="text-input" value={sourceTitle} onChange={(event) => setSourceTitle(event.target.value)} placeholder="章节标题" />
                <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder="粘贴完整章节。系统先无损分段，再根据文字和剧本长度动态计算页数。" />
                <div><span>{editingChapterId ? "保存后生成新修订，旧版本仍保留" : "不会限制总页数 · 单页硬上限 180 个中文字符"}</span><span className="compose-actions">{!editingChapterId && <label className={importSourceFile.isPending ? "button outline compact source-file-button pending" : "button outline compact source-file-button"}><input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={chooseSourceFile} disabled={importSourceFile.isPending} />{importSourceFile.isPending ? <LoaderCircle className="spin" size={15} /> : <FileImage size={15} />}{importSourceFile.isPending ? "正在导入…" : "选择 TXT / MD"}</label>}{editingChapterId && <button className="button ghost compact" onClick={() => { setEditingChapterId(null); setSourceText(""); }}>取消修改</button>}<button className="button ink" disabled={!sourceText.trim() || importSource.isPending} onClick={() => importSource.mutate()}>{importSource.isPending ? <LoaderCircle className="spin" size={16} /> : editingChapterId ? <Save size={16} /> : <Upload size={16} />}{editingChapterId ? "保存新修订" : "导入粘贴原文"}</button></span></div>
                {importSource.isError && <p className="form-error"><CircleAlert size={14} />{importSource.error.message}</p>}
                {importSourceFile.isError && <p className="form-error"><CircleAlert size={14} />{importSourceFile.error.message}</p>}
              </div>
              <div className="chapter-register">
                {chapters.data?.map((chapter) => (
                  <div key={chapter.id} className={activeChapterId === chapter.id ? "chapter-row active" : "chapter-row"} onClick={() => setSelectedChapterId(chapter.id)}>
                    <span>{String(chapter.ordinal).padStart(2, "0")}</span><div><strong>{chapter.title}</strong><small>{chapter.source_character_count} 字 · {chapter.segment_count} 段 · {chapter.page_count} 页 · {chapter.status}</small></div><em>{Math.round(chapter.coverage_ratio * 100)}% 覆盖</em><div className="row-actions"><button title="修改原文" onClick={(event) => { event.stopPropagation(); beginEditChapter(chapter.id, chapter.title); }}><Pencil size={13} /></button><button title="删除章节" onClick={(event) => { event.stopPropagation(); if (window.confirm("删除后会暂时隐藏该章节，可立即撤回。继续吗？")) deleteChapter.mutate(chapter.id); }}><Trash2 size={13} /></button></div>
                  </div>
                ))}
                {!chapters.data?.length && <div className="asset-empty"><BookOpenText size={24} /><strong>尚未导入原作</strong><p>粘贴一个完整章节开始工作。</p></div>}
              </div>
              {deletedChapterId && <div className="undo-banner"><span>章节已移入回收状态</span><button onClick={() => restoreChapter.mutate(deletedChapterId)}><RotateCcw size={13} />撤回删除</button></div>}
              {activeChapterId && <div className="workflow-actions"><button className="button outline" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />生成漫画剧本</button><button className="button ink" disabled={planChapter.isPending || script.data?.status !== "READY"} onClick={() => planChapter.mutate()}>{planChapter.isPending ? <LoaderCircle className="spin" size={15} /> : <PanelTop size={15} />}从剧本计算分页</button></div>}
              {planChapter.isError && <p className="form-error"><CircleAlert size={14} />{planChapter.error.message}</p>}
            </>
          )}

          {section === "assets" && (
            <>
              <nav className="asset-subnav" aria-label="参考资产分类">
                {([
                  ["characters", "人物设定"],
                  ["outfits", "服装档案"],
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
              {assetView === "characters" && <>
              <header className="canvas-header"><div><span>CHARACTER BIBLE / 角色资产</span><h2>姓名、绰号与参考图绑定</h2></div><small>{characters.data?.length ?? 0} 个角色</small></header>
              <div className="character-create">
                <input className="text-input" value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="主要姓名（剧本默认使用）" />
                <input className="text-input" value={characterAliases} onChange={(event) => setCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" />
                <button className="button ink compact" disabled={!characterName.trim() || createCharacter.isPending} onClick={() => createCharacter.mutate()}><Plus size={14} />添加角色</button>
              </div>
              <div className="character-strip">
                {characters.data?.map((character) => <button key={character.id} className={bindCharacterId === character.id ? "character-chip active" : "character-chip"} onClick={() => { if (editingOutfitId) resetOutfitForm(); setBindCharacterId(character.id); setSelectedCharacterOutfitId(""); setEditCharacterName(character.primary_name); setEditCharacterAliases(character.aliases.join("，")); setEditLockedFeatures(character.locked_features.join("，")); setEditForbiddenChanges(character.forbidden_changes.join("，")); }}><strong>{character.primary_name}</strong><span>{character.aliases.length ? `又名 ${character.aliases.join(" / ")}` : "无绰号"}</span>{character.alias_conflict && <em>称呼冲突待确认</em>}<small>{character.references.length} 张参考图 · {character.locked_features.length} 项已锁定</small></button>)}
              </div>
              {boundCharacter && <div className="character-editor"><div><strong>规范姓名与一致性锁</strong><span>剧本统一使用主要姓名；固定特征和禁止改变项会进入每次生图提示。</span></div><input aria-label="编辑主要姓名" className="text-input" value={editCharacterName} onChange={(event) => setEditCharacterName(event.target.value)} /><input aria-label="编辑角色绰号" className="text-input" value={editCharacterAliases} onChange={(event) => setEditCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" /><button className="button outline compact" disabled={!editCharacterName.trim() || updateCharacter.isPending} onClick={() => updateCharacter.mutate()}>{updateCharacter.isPending ? <LoaderCircle className="spin" size={13} /> : <Pencil size={13} />}保存角色规范</button><div className="character-lock-fields"><input aria-label="角色固定特征" className="text-input" value={editLockedFeatures} onChange={(event) => setEditLockedFeatures(event.target.value)} placeholder="固定特征：黑色长发、左眼泪痣…" /><input aria-label="角色禁止改变项" className="text-input" value={editForbiddenChanges} onChange={(event) => setEditForbiddenChanges(event.target.value)} placeholder="禁止改变：发色、瞳色、身高关系…" /></div>{boundCharacter.alias_conflict && <em><CircleAlert size={12} />当前称呼与其他角色冲突，请修改后保存</em>}</div>}
              <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次素材生成模型（按任务显式选择，不沿用上次结果）" />
              {boundCharacter && <CharacterConceptPanel key={boundCharacter.id} projectId={id} character={boundCharacter} model={activeDrawModel} onOpen={openPreview} />}
              </>}
              {assetView === "outfits" && <>
              <header className="canvas-header"><div><span>WARDROBE / 服装档案</span><h2>角色、服装与参考图逐一绑定</h2></div><small>{outfits.data?.length ?? 0} 份档案</small></header>
              <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次服装预览模型" />
              {selectedCharacterOutfitId && <section className="asset-live-results"><header><div><span>LIVE RESULT</span><strong>服装穿着图实时结果</strong></div><small>{outfits.data?.find((outfit) => outfit.id === selectedCharacterOutfitId)?.name ?? "服装"}</small></header><div className="asset-result-grid">{assetCandidates.data?.map((candidate) => <article key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`服装穿着图 ${candidate.ordinal}`} onOpen={openPreview} /><div><strong>服装穿着预览</strong><span>{candidate.status} · {candidate.resolution}</span><details><summary>实际提示词</summary><p>{promptPreview(candidate)}</p></details></div></article>)}</div></section>}
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
                  <div className="profile-records">{outfits.data?.map((outfit) => {
                    const owner = characters.data?.find((item) => item.id === outfit.character_id);
                    return <article className={editingOutfitId === outfit.id ? "editing" : ""} key={outfit.id}><div className="profile-record-title"><span>WARDROBE</span><strong>{outfit.name}</strong><small>{outfit.locked_fields.length} 项锁定</small></div><div className="relationship-chain"><span>{owner?.primary_name ?? "未知角色"}</span><b>→</b><span>{outfit.name}</span><b>→</b><span>{outfit.reference_asset_ids.length} 张参考图</span></div><div className="profile-record-actions"><button type="button" onClick={() => beginOutfitEdit(outfit)}>{editingOutfitId === outfit.id ? "编辑中" : "管理参考图"}</button><button type="button" disabled={generateOutfitPreview.isPending || !activeDrawModel || !outfit.reference_asset_ids.length} onClick={() => generateOutfitPreview.mutate(outfit.id)}>生成穿着图</button></div></article>;
                  })}{!outfits.data?.length && <p className="profile-record-empty">还没有服装档案。完成上方 01–03 三步后建立。</p>}</div>
                </section>}
                {assetView === "style" && <>
                <header className="canvas-header"><div><span>STYLE SYSTEM / 漫画风格</span><h2>色板、画面语言与测试图</h2></div><small>{styles.data?.length ?? 0} 份档案</small></header>
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
                    return <article className={isActive ? "active style-production-record" : "style-production-record"} key={style.id}><div className="profile-record-title"><span>{isActive ? "CURRENT STYLE" : "STYLE PROFILE"}</span><strong>{style.name}</strong><small>{style.status} · {referenceCount} 张参考 · {style.locked_fields.length} 项锁定</small></div><ComicModeSwitch compact value={style.color_mode} disabled={updateStyleMode.isPending} onChange={(colorMode) => updateStyleMode.mutate({ style, colorMode })} />{style.status === "DRAFT" && <p className="reanalyze-note">彩色风格必须依次确认色板和测试图，再激活用于正式页面。</p>}<div className="profile-record-actions"><button type="button" disabled={!referenceCount || analyzeStyle.isPending} onClick={() => analyzeStyle.mutate(style.id)}>重新分析画面语言</button></div><StyleProductionPanel key={`${style.id}:${style.version}`} projectId={id} style={style} model={activeDrawModel} active={isActive} onOpen={openPreview} /></article>;
                  })}{!styles.data?.length && <p className="profile-record-empty">选择色彩模式并绑定参考页，建立第一份漫画风格档案。</p>}</div>
                </section></>}
              </div>
              {assetView === "references" && <header className="canvas-header"><div><span>REFERENCE INTAKE / 原始素材</span><h2>上传、分类与追溯原始参考图</h2></div><small>{assets.data?.length ?? 0} 个文件</small></header>}
              <div className="intake-toolbar"><div className="kind-switch">{assetView === "references" ? kinds.map(([value, label]) => <button key={value} className={assetKind === value ? "active" : ""} onClick={() => setAssetKind(value)}>{label}</button>) : <strong>{kinds.find(([value]) => value === currentAssetKind)?.[1]}</strong>}</div><span>{currentAssetKind === "CHARACTER_REFERENCE" ? (bindCharacterId ? "将绑定到选中的角色" : "请先选择要绑定的角色") : currentAssetKind === "OUTFIT_REFERENCE" ? (boundCharacter ? `当前绑定目标：${boundCharacter.primary_name} → ${outfitName.trim() || "未命名服装"}` : "先选择所属角色，再建立服装档案") : `当前分析目标：${styleColorMode === "monochrome" ? "黑白漫画" : "彩色漫画"}`}</span></div>
              <label className={upload.isPending ? "upload-stage busy" : "upload-stage"}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} disabled={upload.isPending} /><span className="upload-icon">{upload.isPending ? <LoaderCircle className="spin" /> : <Upload />}</span><strong>{upload.isPending ? "正在安全上传…" : `上传${kinds.find(([value]) => value === currentAssetKind)?.[1]}`}</strong><p>{currentAssetKind === "CHARACTER_REFERENCE" ? "人物图会和选中的主要姓名绑定，不会只依赖文件名猜测身份。" : currentAssetKind === "OUTFIT_REFERENCE" ? "上传后自动加入当前服装档案，保存时绑定到上方所选角色。" : `上传后自动加入当前${styleColorMode === "monochrome" ? "黑白" : "彩色"}风格档案，创建后再由默认视觉模型分析。`}</p></label>
              {uploadError && <p className="form-error"><CircleAlert size={15} />{uploadError}</p>}
              {(bindExistingCharacterReference.isError || unbindExistingCharacterReference.isError) && <p className="form-error"><CircleAlert size={15} />{(bindExistingCharacterReference.error ?? unbindExistingCharacterReference.error)?.message}</p>}
              {(createOutfit.isError || updateOutfit.isError || createStyle.isError || updateStyleMode.isError) && <p className="form-error"><CircleAlert size={15} />{(createOutfit.error ?? updateOutfit.error ?? createStyle.error ?? updateStyleMode.error)?.message}</p>}
              {visibleAssetKinds.map(([kind, label]) => {
                const grouped = assets.data?.filter((asset) => asset.kind === kind) ?? [];
                return <section className="asset-purpose-group" key={kind}>
                  <div className="asset-list-header"><span>{label}</span><small>{grouped.length} FILES</small></div>
                  <p className="purpose-explain">{{ CHARACTER_REFERENCE: "绑定主要姓名与绰号，用于保持脸、发型和体型一致。", OUTFIT_REFERENCE: "选择图片后，上方绑定流程会明确保存“角色 → 服装档案 → 参考图”的关系。", STYLE_REFERENCE: "选择后由默认视觉模型按所选的黑白或彩色模式总结可复用画面语言。" }[kind]}</p>
                  <div className="asset-grid">{grouped.map((asset, index) => {
                    const characterReference = kind === "CHARACTER_REFERENCE" ? boundCharacter?.references.find((reference) => reference.asset_id === asset.id) : undefined;
                    const linkedCharacter = kind === "CHARACTER_REFERENCE" ? characters.data?.find((character) => character.references.some((reference) => reference.asset_id === asset.id)) : undefined;
                    const selected = kind === "CHARACTER_REFERENCE" ? Boolean(characterReference) : kind === "OUTFIT_REFERENCE" ? selectedOutfitAssets.includes(asset.id) : selectedStyleAssets.includes(asset.id);
                    const linkedOutfits = kind === "OUTFIT_REFERENCE" ? outfits.data?.filter((outfit) => outfit.reference_asset_ids.includes(asset.id)) ?? [] : [];
                    const linkedStyles = kind === "STYLE_REFERENCE" ? styles.data?.filter((style) => style.profile.reference_asset_ids?.includes(asset.id)) ?? [] : [];
                    return <article className={selected ? "asset-card selected" : "asset-card"} key={asset.id}>
                      <div className={`asset-thumb thumb-${(index % 3) + 1}`}>{asset.content_url ? <Image src={publicUrl(asset.content_url)!} alt={assetName(asset)} width={74} height={74} unoptimized /> : <FileImage size={27} />}<span>{asset.width && asset.height ? `${asset.width}×${asset.height}` : asset.mime_type}</span></div>
                      <div><AssetNameEditor asset={asset} pending={renameAsset.isPending} onSave={(displayName) => renameAsset.mutate({ assetId: asset.id, displayName })} /><p>{label} · {formatBytes(asset.byte_size)}</p><span className="tiny-status"><Check size={11} />{asset.status}</span>
                        {kind === "OUTFIT_REFERENCE" && <p className={linkedOutfits.length ? "reference-binding bound" : "reference-binding"}><Link2 size={10} />{linkedOutfits.length ? `已绑定：${linkedOutfits.map((outfit) => `${characters.data?.find((character) => character.id === outfit.character_id)?.primary_name ?? "未知角色"} → ${outfit.name}`).join("；")}` : "尚未写入服装档案"}</p>}
                        {kind === "STYLE_REFERENCE" && <p className={linkedStyles.length ? "reference-binding bound" : "reference-binding"}><Link2 size={10} />{linkedStyles.length ? `已用于：${linkedStyles.map((style) => `${style.name}（${style.color_mode === "monochrome" ? "黑白" : "彩色"}）`).join("；")}` : "尚未写入风格档案"}</p>}
                        {kind === "CHARACTER_REFERENCE" && linkedCharacter && !characterReference ? <p className="reference-binding bound"><Link2 size={10} />当前绑定：{linkedCharacter.primary_name}</p> : null}
                        {kind === "CHARACTER_REFERENCE" ? <button className={characterReference ? "bind-purpose bound" : "bind-purpose"} disabled={!boundCharacter || bindExistingCharacterReference.isPending || unbindExistingCharacterReference.isPending} onClick={() => characterReference ? unbindExistingCharacterReference.mutate(characterReference.id) : bindExistingCharacterReference.mutate(asset.id)}>{!boundCharacter ? "先选择角色" : characterReference ? `解除与 ${boundCharacter.primary_name} 的绑定` : linkedCharacter ? `改绑到 ${boundCharacter.primary_name}（自动解除 ${linkedCharacter.primary_name}）` : `绑定到 ${boundCharacter.primary_name}`}</button> : <button className="bind-purpose" disabled={kind === "OUTFIT_REFERENCE" && !bindCharacterId} onClick={() => kind === "OUTFIT_REFERENCE" ? setSelectedOutfitAssets((values) => values.includes(asset.id) ? values.filter((item) => item !== asset.id) : [...values, asset.id]) : setSelectedStyleAssets((values) => values.includes(asset.id) ? values.filter((item) => item !== asset.id) : [...values, asset.id])}>{kind === "OUTFIT_REFERENCE" && !bindCharacterId ? "先选择所属角色" : selected ? "已选：保存后绑定" : kind === "OUTFIT_REFERENCE" ? "加入当前服装档案" : "加入当前风格档案"}</button>}
                        <div className="asset-actions"><select aria-label="修改素材用途" value={asset.kind} onChange={(event) => reclassifyAsset.mutate({ assetId: asset.id, kind: event.target.value as AssetPurpose })}>{kinds.map(([value, option]) => <option key={value} value={value}>{option}</option>)}</select><button title="删除素材" onClick={() => { if (window.confirm("删除该导入素材并解除人物绑定？")) deleteAsset.mutate(asset.id); }}><Trash2 size={13} /></button></div>
                      </div>
                    </article>;
                  })}</div>
                  {!grouped.length && <div className="purpose-empty">尚无{label}</div>}
                </section>;
              })}
            </>
          )}

          {section === "script" && (
            <>
              <header className="canvas-header"><div><span>SCREENPLAY / 漫画剧本</span><h2>先写场景与情节拍，再进入分页</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑剧本的章节" value={activeChapterId ?? ""} onChange={(event) => setSelectedChapterId(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{script.data?.scenes.length ?? 0} 个场景</small></div></header>
              {!activeChapterId ? <div className="asset-empty tall"><Clapperboard size={28} /><strong>请先导入原作</strong></div> : !script.data?.scenes.length ? <div className="script-empty"><Clapperboard size={30} /><strong>本章还没有漫画剧本</strong><p>点击“生成漫画剧本”，默认文字模型会逐段补充可视化动作、场景、对白、旁白、情绪和翻页悬念，不会压缩原文。</p><button className="button ink" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />生成漫画剧本</button></div> : <ScriptEditor chapterId={activeChapterId} script={script.data} characters={characters.data ?? []} outfits={outfits.data ?? []} onAssignOutfit={(sceneId, assignments) => assignOutfit.mutate({ sceneId, assignments })} />}
            </>
          )}

          {section === "storyboard" && (
            <>
              <header className="canvas-header"><div><span>PAGE CAPACITY / 动态分页</span><h2>内容有多少，页面就有多少</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑分镜的章节" value={activeChapterId ?? ""} onChange={(event) => setSelectedChapterId(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{pages.data?.length ?? 0} 页</small></div></header>
              {invalidPlannedPageCount > 0 && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>{invalidPlannedPageCount} 页缺少剧本与分镜来源</strong><p>这是旧版分页数据，不能直接生图。请先生成漫画剧本，再从第 1 页重新计算分页。</p></div><Link className="button outline compact" href={projectPath("script")}>前往漫画剧本</Link></div>}
              {!pages.data?.length ? <div className="asset-empty tall"><PanelTop size={28} /><strong>尚未生成分页分镜</strong><p>先完成漫画剧本；系统按场景切换、动作复杂度、对白和气泡容量拆页。</p></div> : <StoryboardEditor chapterId={activeChapterId!} pages={pages.data} characters={characters.data ?? []} outfits={outfits.data ?? []} onReplan={(pageNumber) => replanPage.mutate(pageNumber)} replanPending={replanPage.isPending} replanError={replanPage.error} />}
            </>
          )}

          {section === "generate" && (
            <>
              <header className="canvas-header"><div><span>DRAW / 单页抽卡</span><h2>{selectedPage ? `第 ${selectedPage.page_number} 页候选` : "选择一页开始"}</h2></div><small>每次只生成 1 页</small></header>
              {selectedPage ? <>
                {selectedPageStructureIssue && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>当前页暂不能生成</strong><p>{selectedPageStructureIssue}</p></div><Link className="button outline compact" href={projectPath("script")}>前往漫画剧本</Link></div>}
                {selectedPage.continuity_status === "NEEDS_REVIEW" && <div className="workflow-warning"><CircleAlert size={17} /><div><strong>剧本或分镜已修改</strong><p>历史候选仍然保留，但可能不再对应当前脚本。建议重新抽卡并执行连续性检查。</p></div><Link className="button outline compact" href={projectPath("storyboard")}>检查分镜</Link></div>}
                {selectedWorkbenchCandidate && ["STALE", "LEGACY_UNKNOWN"].includes(selectedWorkbenchCandidate.version_state) && <div className="stale-candidate-banner"><div><span>版本需要决定</span><strong>旧候选基于 {selectedWorkbenchCandidate.based_on_storyboard_version ? `V${selectedWorkbenchCandidate.based_on_storyboard_version}` : "未知版本"}，当前分镜为 V{selectedPage.storyboard_version}</strong><p>旧图仍可查看和导出。请选择继续沿用，或按当前分镜重新生成。</p></div><div><button disabled={keepSelectedCandidate.isPending} onClick={() => keepSelectedCandidate.mutate(selectedWorkbenchCandidate.id)}><Check size={14} />继续使用旧候选</button><button className="primary" disabled={generate.isPending || !pageReadiness.data?.ready || !generationReferenceReady || !referencesConfirmed} onClick={() => generate.mutate()}><Sparkles size={14} />按当前 V{selectedPage.storyboard_version} 重新生成</button></div></div>}
                <div className="draw-toolbar"><div className="page-picker">{pages.data?.map((page) => <button key={page.id} className={selectedPage.id === page.id ? "active" : ""} onClick={() => { setSelectedPageId(page.id); setReferenceSelections({}); setConfirmedReferencePageId(null); }}>{page.page_number}</button>)}</div><button className="button ghost compact" disabled={startBatch.isPending || Boolean(selectedPageStructureIssue) || !pageReadiness.data?.ready} onClick={() => startBatch.mutate()}><Plus size={14} />新批次</button></div>
                <div className="draw-context"><div><span>PAGE LOAD</span><strong>{selectedPage.estimated_text_chars} 字</strong><small>{selectedPage.panel_count} 格 / {selectedPage.estimated_bubbles} 气泡</small></div><p>{selectedPage.source_coverage.ranges?.map((item) => item.text).join("").slice(0, 180)}</p></div>
                <ProductionReadiness projectId={id} readiness={pageReadiness.data} loading={pageReadiness.isLoading} error={pageReadiness.error} targetDialogues={targetDialogues} />
                <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} options={modelOptions} label="本次页面生成模型（仅显示支持图片编辑的已启用模型）" />
                <section className="generation-reference-check">
                  <header><div><span>CAST & REFERENCES</span><strong>生成前确认画面人物与参考图</strong></div><small>{visibleCharacterIds.length} 位入镜人物</small></header>
                  {generationStoryboard.isLoading ? <p className="reference-check-loading"><LoaderCircle className="spin" size={15} />正在读取当前分镜…</p> : <div className="reference-check-grid">
                    {visibleCharacterIds.map((characterId) => {
                      const character = characters.data?.find((item) => item.id === characterId);
                      const selection = effectiveReferenceSelections[characterId];
                      const outfit = outfits.data?.find((item) => item.id === selection?.outfit_id);
                      return <article key={characterId}><div><strong>{character?.primary_name ?? characterId}</strong><span>{outfit ? `穿着：${outfit.name}` : "分镜未指定服装"}</span></div><label><span>人物参考图</span><select value={selection?.character_asset_id ?? ""} onChange={(event) => { setConfirmedReferencePageId(null); setReferenceSelections((values) => ({ ...values, [characterId]: { ...(effectiveReferenceSelections[characterId] ?? { outfit_id: null, outfit_asset_id: null }), character_asset_id: event.target.value || null } })); }}><option value="">请选择人物参考</option>{character?.references.map((reference, referenceIndex) => { const asset = assets.data?.find((item) => item.id === reference.asset_id); return <option value={reference.asset_id} key={reference.id}>{character.primary_name} · {reference.is_canonical ? "主参考" : `人物参考 ${String(referenceIndex + 1).padStart(2, "0")}`} · {asset?.original_name ?? reference.asset_id}</option>; })}</select></label>{outfit && <label><span>该服装参考图</span><select value={selection?.outfit_asset_id ?? ""} onChange={(event) => { setConfirmedReferencePageId(null); setReferenceSelections((values) => ({ ...values, [characterId]: { ...effectiveReferenceSelections[characterId], outfit_asset_id: event.target.value || null } })); }}><option value="">请选择服装参考</option>{outfit.reference_asset_ids.map((assetId, assetIndex) => <option value={assetId} key={assetId}>{outfit.name} · 服装参考 {String(assetIndex + 1).padStart(2, "0")} · {assets.data?.find((item) => item.id === assetId)?.original_name ?? assetId}</option>)}</select></label>}</article>;
                    })}
                    {!visibleCharacterIds.length && <p className="reference-check-empty">当前分镜没有入镜人物，将只按场景、动作和风格生成。</p>}
                  </div>}
                  <label className={referencesConfirmed ? "reference-confirmed" : ""}><input type="checkbox" checked={referencesConfirmed} disabled={!generationReferenceReady} onChange={(event) => setConfirmedReferencePageId(event.target.checked ? selectedPage.id : null)} /><span>我已确认人物姓名、人物参考与服装参考逐一对应</span></label>
                  {!generationReferenceReady && <p className="reference-check-warning"><CircleAlert size={13} />有角色缺少可用参考图，请先到“参考资产”绑定；分镜指定服装时也必须选择对应服装图。</p>}
                </section>
                <div className="generation-bar"><div className="generation-options"><div><span>正式模型</span><strong>{modelOptions.find((item) => item.alias === activeDrawModel)?.name ?? "尚未选择"}</strong></div><div><span>本次规格</span><strong>1K · 彩色 · 1 个候选</strong></div></div><button className="button ink generate-one" disabled={generate.isPending || Boolean(selectedPageGenerationIssue) || !pageReadiness.data?.ready || !generationReferenceReady || !referencesConfirmed} onClick={() => generate.mutate()}>{generate.isPending ? <LoaderCircle className="spin" size={17} /> : <Star size={17} />}{generate.isPending ? "正在加入 1 个正式任务" : selectedPageStructureIssue ? "请先补全剧本与分镜" : !activeDrawModel ? "先选择图片模型" : !pageReadiness.data?.ready ? "先完成页面生产准备" : !referencesConfirmed ? "先确认人物与参考图" : "生成 1 个 1K 彩色候选"}</button></div>
                {(generate.isError || startBatch.isError) && <p className="form-error"><CircleAlert size={14} />{(generate.error ?? startBatch.error)?.message}</p>}
                <div className="batch-heading"><div><span>BATCH</span><strong>{currentBatch ? `批次 ${currentBatch.ordinal}` : "尚未开始批次"}</strong></div><small>每个候选记录实际供应商与模型 · 收藏不等于采用</small></div>
                <div className="candidate-grid">{candidates.data?.map((candidate, candidateIndex) => <article className={`${candidate.is_selected ? "candidate-card selected" : "candidate-card"} version-${candidate.version_state.toLowerCase()}`} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`候选 ${candidate.ordinal}`} eager={candidateIndex === 0} onOpen={(url, label) => setPreviewImage({ url, label })} /><div className="candidate-meta"><span>候选 {String(candidate.ordinal).padStart(2, "0")}</span><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><small>{candidate.resolution} · {candidate.status}</small><em>{candidate.based_on_storyboard_version ? `生成依据 V${candidate.based_on_storyboard_version}` : "生成版本未知"} · {candidate.version_state}</em></div><div className="candidate-actions"><button className={candidate.is_favorite ? "favorited" : ""} onClick={() => favorite.mutate({ candidateId: candidate.id, value: !candidate.is_favorite })}><Heart size={14} fill={candidate.is_favorite ? "currentColor" : "none"} />收藏</button><button disabled={!candidate.asset_id || candidate.is_selected} onClick={() => { if (window.confirm("请人工确认页面文字已经校对。采用后仍可随时导出；是否继续？")) selectCandidate.mutate({ candidateId: candidate.id, manualTextConfirmed: true, acceptStale: candidate.version_state !== "CURRENT" }); }}><Check size={14} />{candidate.is_selected ? "已采用" : candidate.version_state === "CURRENT" ? "人工校对并采用" : "确认旧版本并采用"}</button><button className={reviewCandidateId === candidate.id ? "reviewing" : ""} disabled={!candidate.asset_id || inspectCandidate.isPending} onClick={() => { setReviewCandidateId(candidate.id); inspectCandidate.mutate(candidate.id); }}><CircleAlert size={14} />视觉检查</button>{candidate.asset_id && candidate.resolution === "1K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "2K" })}>升至 2K</button>}{candidate.asset_id && candidate.resolution !== "4K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "4K" })}>升至 4K</button>}<button className="danger-action" disabled={candidate.is_selected} onClick={() => { if (window.confirm("删除这个候选？收藏状态也会一并移除。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={14} />删除</button></div></article>)}</div>
                {reviewCandidateId && <section className="inspection-panel">
                  <header><div><span>AI QUALITY CHECK</span><strong>候选视觉检查</strong><small>检查说话人归属、角色、服装、道具和连续性；文字由人工校对。</small></div><button onClick={() => setReviewCandidateId(null)}>关闭</button></header>
                  {!latestInspections.length ? <div className="inspection-wait"><LoaderCircle className={reviewJob && !["COMPLETED", "FAILED"].includes(reviewJob.status) ? "spin" : ""} size={18} /><span>{reviewJob ? `检查任务 ${reviewJob.status} · ${reviewJob.progress}%` : "正在读取检查结果"}</span></div> : <div className="inspection-results">{latestInspections.map((inspection) => {
                    const passed = ["PASS", "ACCEPTABLE", "MATCH"].includes(inspection.outcome);
                    const repairType = recommendedRepairType(inspection.category);
                    const bubbleDiffs = inspectionBubbleDiffs(inspection.details);
                    return <article className={passed ? "passed" : "failed"} key={inspection.id}>
                      <div><span>{inspectionLabels[inspection.category] ?? inspection.category}</span><strong>{inspection.outcome}</strong><em>{inspection.score === null ? "—" : `${Math.round(inspection.score * 100)}%`}</em></div>
                      <p>{inspectionSummary(inspection.details)}</p>
                      {bubbleDiffs.length > 0 && <div className="bubble-diff-list">{bubbleDiffs.map((diff, index) => <div key={`${inspection.id}:${index}`}><strong>气泡 {String(diff.balloon_index ?? index + 1).padStart(2, "0")}</strong><span>目标：{String(diff.target_text ?? "")}</span><span>识别：{String(diff.recognized_text ?? "")}</span><em>{typeof diff.similarity === "number" ? `${Math.round(diff.similarity * 100)}%` : "—"}</em></div>)}</div>}
                      {!passed && inspection.category !== "TEXT" && <button disabled={repairCandidate.isPending} onClick={() => repairCandidate.mutate(inspection)}><Sparkles size={13} />修复{repairTypeLabels[repairType]}</button>}
                      {!passed && inspection.category === "TEXT" && <span className="manual-review-hint">请人工校对；确认后可直接采用</span>}
                    </article>;
                  })}</div>}
                  {(inspectCandidate.isError || repairCandidate.isError || upscaleCandidate.isError) && <p className="form-error"><CircleAlert size={14} />{(inspectCandidate.error ?? repairCandidate.error ?? upscaleCandidate.error)?.message}</p>}
                </section>}
                {!candidates.data?.length && <div className="asset-empty"><ImagePlus size={25} /><strong>这个批次还没有候选</strong><p>完成生产准备并确认参考图后，使用本次选择的图片模型生成 1 张彩色页面。</p></div>}
                <div className="next-page-row"><span>{selectedPage.selected_candidate_id ? "当前页已有采用版本，可以继续或单独导出" : "采用一个满意候选后才能进入下一页"}</span><div>{selectedPage.selected_candidate_id && <a className="button ghost compact" href={api.selectedPagePngUrl(selectedPage.id)!}><Download size={14} />单页 PNG</a>}<button className="button outline" disabled={!selectedPage.selected_candidate_id || goNext.isPending} onClick={() => goNext.mutate()}>生成下一页 <ArrowRight size={15} /></button></div></div>
              </> : <div className="asset-empty tall"><Sparkles size={28} /><strong>没有可抽卡页面</strong><p>先完成动态分页。</p></div>}
            </>
          )}

          {section === "library" && (
            <>
              <header className="canvas-header"><div><span>LIBRARY / 批次素材库</span><h2>保存每一次值得比较的结果</h2></div><small>{library.data?.total_candidates ?? 0} 个候选</small></header>
              <div className="library-toolbar">
                <div className="library-filter-grid">
                  <select className={libraryChapter ? "filter-active" : ""} aria-label="按章节筛选素材" value={libraryChapter} onChange={(event) => { setLibraryChapter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}>
                    <option value="">全部章节</option>
                    {chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>第 {chapter.ordinal} 章 · {chapter.title}</option>)}
                  </select>
                  <button className={favoriteOnly ? "active" : ""} onClick={() => { setFavoriteOnly(!favoriteOnly); setLibraryCursor(""); setLibraryHistory([]); }}><Heart size={14} />只看收藏（{library.data?.favorite_count ?? 0}）</button>
                  <select aria-label="按角色筛选素材" value={libraryCharacter} onChange={(event) => { setLibraryCharacter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部角色</option>{characters.data?.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select>
                  <select aria-label="按生成类型筛选素材" value={libraryKind} onChange={(event) => { setLibraryKind(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部类型</option>{Object.entries(generationKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select>
                  <select aria-label="按模型筛选素材" value={libraryModel} onChange={(event) => { setLibraryModel(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部模型</option>{modelOptions.map((model) => <option key={model.alias} value={model.alias}>{model.name}</option>)}</select>
                  <select aria-label="按分辨率筛选素材" value={libraryResolution} onChange={(event) => { setLibraryResolution(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部清晰度</option>{(["1K", "2K", "4K"] as const).map((value) => <option key={value} value={value}>{value}</option>)}</select>
                  <label>从<input aria-label="素材开始日期" type="date" value={libraryDateFrom} onChange={(event) => { setLibraryDateFrom(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label>
                  <label>至<input aria-label="素材结束日期" type="date" value={libraryDateTo} onChange={(event) => { setLibraryDateTo(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label>
                  <button onClick={() => { setLibraryChapter(""); setFavoriteOnly(false); setLibraryCharacter(""); setLibraryKind(""); setLibraryModel(""); setLibraryResolution(""); setLibraryDateFrom(""); setLibraryDateTo(""); setLibraryCursor(""); setLibraryHistory([]); }}><RotateCcw size={13} />重置</button>
                </div>
              </div>
              <div className="library-groups">{library.data?.groups.map((group, groupIndex) => { const columns = Math.min(Math.max(group.candidates.length, 1), 3); return <section className="library-group" style={{ "--batch-columns": columns } as CSSProperties} key={group.batch.id}><header><div><span>BATCH {String(group.batch.ordinal).padStart(3, "0")}</span><strong>{generationKindLabels[group.batch.generation_kind] ?? group.batch.generation_kind}</strong></div><small>{new Date(group.batch.created_at).toLocaleString("zh-CN")} · {group.candidates.length} 张</small></header><div className="library-candidates">{group.candidates.map((candidate, candidateIndex) => <article className={candidate.is_selected ? "is-selected" : undefined} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} thumbnailUrl={candidate.thumbnail_url} label={`批次候选 ${candidate.ordinal}`} eager={groupIndex === 0 && candidateIndex === 0} onOpen={(url, label) => setPreviewImage({ url, label })} /><div><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><span>{candidate.resolution} · {candidate.status}</span>{candidate.is_favorite && <Heart size={13} fill="currentColor" />}{candidate.is_selected && <div className="library-selection-row"><em><Check size={12} />已采用</em><button className="library-retract" disabled={!candidate.page_id || retractSelectedCandidate.isPending} onClick={() => { if (candidate.page_id && window.confirm("撤回采用后，候选图片和生成记录仍会保留，后续页面将标记为待复查。是否继续？")) retractSelectedCandidate.mutate(candidate.page_id); }}><RotateCcw size={13} />撤回</button></div>}{!candidate.is_selected && <button className="library-delete" title="从素材库软删除" disabled={deleteCandidate.isPending} onClick={() => { if (window.confirm("从素材库隐藏这个候选？生成文件和任务记录会保留。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={12} /></button>}</div></article>)}</div></section>; })}</div>
              {!library.data?.groups.length && <div className="asset-empty tall"><LibraryBig size={28} /><strong>素材库还是空的</strong><p>从单页抽卡开始，所有候选都会按批次保留。</p></div>}
              {(libraryHistory.length > 0 || library.data?.next_cursor) && <div className="library-pagination"><button disabled={!libraryHistory.length} onClick={() => { const previous = libraryHistory.at(-1) ?? ""; setLibraryHistory((items) => items.slice(0, -1)); setLibraryCursor(previous); }}><ArrowLeft size={13} />上一页</button><span>每页最多 {library.data?.limit ?? 30} 个批次</span><button disabled={!library.data?.next_cursor} onClick={() => { setLibraryHistory((items) => [...items, libraryCursor]); setLibraryCursor(library.data?.next_cursor ?? ""); }}>下一页<ArrowRight size={13} /></button></div>}
              <div className="export-desk"><div><span>EXPORT / 导出</span><strong>采用全部页面后导出整章</strong></div><div>{(["PNG", "PDF", "JSON"] as const).map((type) => <button key={type} disabled={!activeChapterId || createExport.isPending} onClick={() => createExport.mutate(type)}><Download size={14} />{type}</button>)}</div></div>
              <div className="export-list">{exportsQuery.data?.map((item) => <a key={item.id} href={publicUrl(item.download_url)!}><FileImage size={14} /><span>{item.export_type} · {item.page_count} 页 · {formatBytes(item.byte_size)}</span><Download size={13} /></a>)}</div>
              {createExport.isError && <p className="form-error"><CircleAlert size={14} />{createExport.error.message}</p>}
            </>
          )}

          {section === "jobs" && (
            <>
              <header className="canvas-header"><div><span>JOBS / 任务中心</span><h2>每个生成任务都能看懂、取消和重试</h2></div><small>{jobs.data?.length ?? 0} 个任务</small></header>
              <div className="job-toolbar"><div><button className={!showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(false); setJobNotice(""); }}><ListTodo size={13} />近期任务</button><button className={showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(true); setJobNotice(""); }}><History size={13} />历史记录</button></div>{!showArchivedJobs && <div className="job-bulk-actions"><button disabled={!selectedJobIds.length || bulkArchiveJobs.isPending} onClick={() => bulkArchiveJobs.mutate()}><Archive size={13} />归档已选（{selectedJobIds.length}）</button><button disabled={archiveCompletedJobs.isPending} onClick={() => { if (window.confirm("将所有已完成、失败和已取消任务移入历史记录？生成候选与溯源信息不会删除。")) archiveCompletedJobs.mutate(); }}><Archive size={13} />归档全部终态</button></div>}</div>
              {jobNotice && <p className="job-notice"><CircleAlert size={13} />{jobNotice}</p>}
              <div className="job-sections">
                {activeJobs.length > 0 && <section><header><strong>正在运行</strong><small>{activeJobs.length} 条</small></header><div className="job-list">{activeJobs.map((job) => renderJob(job, true))}</div></section>}
                {failedJobs.length > 0 && <details open className="job-group failed"><summary><span>失败任务</span><strong>{failedJobs.length} 条 · 展开查看错误与重试</strong></summary><div className="job-list">{failedJobs.map((job) => renderJob(job, false))}</div></details>}
                {completedJobGroups.map(([date, groupedJobs]) => <details className="job-group" key={date}><summary><span>{date}</span><strong>{groupedJobs.length} 条已结束任务</strong></summary><div className="job-list">{groupedJobs.map((job) => renderJob(job, false))}</div></details>)}
              </div>
              {!jobs.data?.length && <div className="asset-empty tall">{showArchivedJobs ? <History size={28} /> : <ListTodo size={28} />}<strong>{showArchivedJobs ? "还没有历史任务" : "当前没有任务"}</strong><p>{showArchivedJobs ? "归档后的已结束任务会保留在这里，可随时恢复。" : "剧本解析、页面生成、检查和修复都会列在这里。"}</p></div>}
            </>
          )}
        </section>

      </div>

      {previewImage && <div className="image-lightbox" role="dialog" aria-modal="true" aria-label={previewImage.label} onClick={() => setPreviewImage(null)}><button type="button" className="lightbox-close" aria-label="关闭大图" onClick={() => setPreviewImage(null)}><X size={20} /></button><div className="lightbox-shell" onClick={(event) => event.stopPropagation()}><div className="lightbox-toolbar"><strong>{previewImage.label}</strong><div><button type="button" aria-label="缩小图片" disabled={previewZoom <= .5} onClick={() => setPreviewZoom((value) => Math.max(.5, value - .25))}><ZoomOut size={17} /></button><button type="button" onClick={() => setPreviewZoom(1)}>{Math.round(previewZoom * 100)}%</button><button type="button" aria-label="放大图片" disabled={previewZoom >= 2.5} onClick={() => setPreviewZoom((value) => Math.min(2.5, value + .25))}><ZoomIn size={17} /></button></div></div><div className="lightbox-stage"><Image style={{ transform: `scale(${previewZoom})` }} src={previewImage.url} alt={previewImage.label} width={1600} height={1600} unoptimized /></div><span>使用 ＋/－ 调整到 50%–250%，点击背景或右上角关闭</span></div></div>}

      <Link className="queue-dock" href={projectPath("jobs")}><div><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><strong>打开任务中心</strong><small>{jobs.data?.[0] ? `${jobLabels[jobs.data[0].job_type] ?? jobs.data[0].job_type} · ${jobs.data[0].status}` : section === "jobs" || section === "generate" ? "当前没有任务" : "查看生成、解析与检查进度"}</small></div>{(section === "jobs" || section === "generate") && <div><span>并发上限 {draft.default_concurrency}</span><i /><span>{queueStats.waiting} 等待</span><i /><span>{queueStats.failed} 失败</span></div>}</Link>
    </AppShell>
  );
}
