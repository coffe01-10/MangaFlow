"use client";

import { AppShell } from "@/components/shell";
import { ScriptEditor } from "@/components/script-editor";
import { StoryboardEditor } from "@/components/storyboard-editor";
import {
  api,
  publicUrl,
  type ImageModelAlias,
  type AssetPurpose,
  type InspectionResult,
  type Project,
  type Resolution,
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
  ListTodo,
  LoaderCircle,
  LockKeyhole,
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
import { ChangeEvent, useMemo, useState } from "react";

export type WorkspaceSection = "source" | "assets" | "script" | "storyboard" | "generate" | "library" | "jobs";

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
  TEXT_REGION: "文字区域",
  BUBBLE_REGION: "气泡区域",
  PANEL: "单格",
  PAGE: "整页",
} as const;

function recommendedRepairType(category: string): "TEXT_REGION" | "BUBBLE_REGION" | "PANEL" | "PAGE" {
  if (category === "TEXT") return "TEXT_REGION";
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

const modelOptions: { alias: ImageModelAlias; name: string; id: string }[] = [
  { alias: "image.nano_banana_2", name: "Nano Banana 2", id: "gemini-3.1-flash-image" },
  { alias: "image.nano_banana_pro", name: "Nano Banana Pro", id: "gemini-3-pro-image-preview" },
];

function ImageModelPicker({
  selected,
  onSelect,
  label,
}: {
  selected: ImageModelAlias | null;
  onSelect: (model: ImageModelAlias) => void;
  label?: string;
}) {
  return <div className="model-picker">
    {label && <p>{label}</p>}
    <div className="model-duel">{modelOptions.map((option) => <button type="button" aria-pressed={selected === option.alias} key={option.alias} className={selected === option.alias ? "model-choice active" : "model-choice"} onClick={() => onSelect(option.alias)}><Sparkles size={18} /><span><strong>{option.name}</strong><small>{option.id}</small></span>{selected === option.alias && <Check size={15} />}</button>)}</div>
  </div>;
}

function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function CandidateArtwork({ contentUrl, thumbnailUrl, label }: { contentUrl: string | null; thumbnailUrl?: string | null; label: string }) {
  const url = publicUrl(thumbnailUrl ?? contentUrl);
  return url ? (
    <Image className="candidate-image" src={url} alt={label} width={720} height={960} unoptimized />
  ) : (
    <div className="candidate-placeholder"><LoaderCircle size={22} /><span>等待 Worker 生成</span></div>
  );
}

export default function ProjectWorkspace({ section }: { section: WorkspaceSection }) {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [navOpen, setNavOpen] = useState(false);
  const [localDraft, setDraft] = useState<Project | null>(null);
  const [assetKind, setAssetKind] = useState<AssetPurpose>("CHARACTER_REFERENCE");
  const [uploadError, setUploadError] = useState("");
  const [showArchivedJobs, setShowArchivedJobs] = useState(false);
  const [jobNotice, setJobNotice] = useState("");
  const [sourceTitle, setSourceTitle] = useState("第一章");
  const [sourceText, setSourceText] = useState("");
  const [selectedChapterId, setSelectedChapterId] = useState<string | null>(null);
  const [selectedPageId, setSelectedPageId] = useState<string | null>(() => searchParams.get("page"));
  const [drawModel, setDrawModel] = useState<ImageModelAlias | null>(null);
  const [drawResolution, setDrawResolution] = useState<Resolution>("1K");
  const [drawCount, setDrawCount] = useState(1);
  const [characterName, setCharacterName] = useState("");
  const [characterAliases, setCharacterAliases] = useState("");
  const [editCharacterName, setEditCharacterName] = useState("");
  const [editCharacterAliases, setEditCharacterAliases] = useState("");
  const [editLockedFeatures, setEditLockedFeatures] = useState("");
  const [editForbiddenChanges, setEditForbiddenChanges] = useState("");
  const [bindCharacterId, setBindCharacterId] = useState("");
  const [favoriteOnly, setFavoriteOnly] = useState(false);
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
  const [styleName, setStyleName] = useState("黑白网点风格");
  const [styleLockedFields, setStyleLockedFields] = useState("");
  const [selectedOutfitAssets, setSelectedOutfitAssets] = useState<string[]>([]);
  const [selectedStyleAssets, setSelectedStyleAssets] = useState<string[]>([]);
  const [reviewCandidateId, setReviewCandidateId] = useState<string | null>(null);

  const needsChapters = ["source", "script", "storyboard", "generate", "library"].includes(section);
  const needsCharacters = ["assets", "script", "storyboard", "library"].includes(section);
  const needsOutfits = ["assets", "script", "storyboard"].includes(section);
  const needsPages = ["storyboard", "generate"].includes(section);
  const needsScript = ["source", "script"].includes(section);
  const projectPath = (target: string) => `/projects/${id}/${target}`;

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id), staleTime: 30_000 });
  const assets = useQuery({ queryKey: ["assets", id], queryFn: () => api.assets(id), enabled: section === "assets" });
  const chapters = useQuery({ queryKey: ["chapters", id], queryFn: () => api.chapters(id), enabled: needsChapters });
  const characters = useQuery({ queryKey: ["characters", id], queryFn: () => api.characters(id), enabled: needsCharacters });
  const outfits = useQuery({ queryKey: ["outfits", id], queryFn: () => api.outfits(id), enabled: needsOutfits });
  const styles = useQuery({ queryKey: ["styles", id], queryFn: () => api.styles(id), enabled: section === "assets" });
  const library = useQuery({
    queryKey: ["library", id, favoriteOnly, libraryCharacter, libraryKind, libraryModel, libraryResolution, libraryDateFrom, libraryDateTo, libraryCursor],
    queryFn: () => api.library(id, {
      favorite: favoriteOnly ? true : undefined,
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
    enabled: ["jobs", "generate"].includes(section),
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
  const selectedPage = pages.data?.find((item) => item.id === selectedPageId) ?? pages.data?.[0] ?? null;
  const batches = useQuery({
    queryKey: ["batches", selectedPage?.id],
    queryFn: () => api.batches(selectedPage!.id),
    enabled: section === "generate" && Boolean(selectedPage),
  });
  const currentBatch = batches.data?.find((item) => item.status === "OPEN") ?? batches.data?.[0] ?? null;
  const candidates = useQuery({
    queryKey: ["candidates", currentBatch?.id],
    queryFn: () => api.candidates(currentBatch!.id),
    enabled: section === "generate" && Boolean(currentBatch),
    refetchInterval: (query) => (query.state.data ?? []).some((candidate) => ["WAITING", "QUEUED", "PREPARING", "GENERATING"].includes(candidate.status)) ? 3000 : false,
  });
  const inspections = useQuery({
    queryKey: ["inspections", reviewCandidateId],
    queryFn: () => api.inspections(reviewCandidateId!),
    enabled: section === "generate" && Boolean(reviewCandidateId),
    refetchInterval: (query) => (query.state.data ?? []).length ? false : 4000,
  });

  const draft = localDraft ?? project.data ?? null;
  const boundCharacter = characters.data?.find((item) => item.id === bindCharacterId) ?? null;
  const activeDrawModel = drawModel;
  const effectiveDrawCount = Math.min(drawCount, draft?.default_concurrency ?? 1);
  const selectedPageStructureIssue = getPageStructureIssue(selectedPage);
  const selectedPageGenerationIssue = getPageGenerationIssue(selectedPage, activeDrawModel);
  const invalidPlannedPageCount = (pages.data ?? []).filter((page) => getPageStructureIssue(page)).length;

  function requireDrawModel(): ImageModelAlias {
    if (!activeDrawModel) throw new Error("请先选择 Nano Banana 2 或 Nano Banana Pro");
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

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const uploaded = await api.uploadAsset(id, assetKind, file);
      if (assetKind === "CHARACTER_REFERENCE" && bindCharacterId) {
        await api.bindCharacterReference(bindCharacterId, uploaded.id);
      }
      return uploaded;
    },
    onSuccess: () => {
      setUploadError("");
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
    mutationFn: ({ assetId, kind }: { assetId: string; kind: AssetPurpose }) => api.updateAsset(assetId, kind),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["assets", id] });
      queryClient.invalidateQueries({ queryKey: ["characters", id] });
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

  const generateCharacterAsset = useMutation({
    mutationFn: async (variant: "FRONT" | "SIDE" | "BACK" | "EXPRESSION") => {
      const batch = await api.startAssetBatch("CHARACTER", bindCharacterId, "CHARACTER");
      return api.generateAssetCandidate(batch.id, requireDrawModel(), "1K", variant);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const generateCompleteSheet = useMutation({
    mutationFn: () => api.generateCompleteCharacterSheet(bindCharacterId, requireDrawModel(), "1K"),
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
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
      queryClient.invalidateQueries({ queryKey: ["outfits", id] });
    },
  });

  const generateOutfitPreview = useMutation({
    mutationFn: async (outfitId: string) => {
      const batch = await api.startAssetBatch("OUTFIT", outfitId, "OUTFIT");
      return api.generateAssetCandidate(batch.id, requireDrawModel(), "1K", "OUTFIT");
    },
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const createStyle = useMutation({
    mutationFn: () => api.createStyle(
      id,
      styleName.trim(),
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

  const generateStyleTest = useMutation({
    mutationFn: async (styleId: string) => {
      const batch = await api.startAssetBatch("STYLE", styleId, "STYLE_TEST");
      return api.generateAssetCandidate(batch.id, requireDrawModel(), "1K", "STYLE_TEST");
    },
    onSuccess: () => {
      router.push(projectPath("jobs"));
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const activateStyle = useMutation({
    mutationFn: (styleId: string) => api.activateStyle(id, styleId),
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ["styles", id] });
      queryClient.invalidateQueries({ queryKey: ["project", id] });
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
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] }),
  });

  const generate = useMutation({
    mutationFn: async () => {
      const issue = getPageGenerationIssue(selectedPage, activeDrawModel);
      if (issue) throw new Error(issue);
      const batch = currentBatch ?? await api.startBatch(selectedPage!.id);
      const queued = [];
      for (let index = 0; index < effectiveDrawCount; index += 1) {
        queued.push(await api.generateCandidate(batch.id, requireDrawModel(), drawResolution));
      }
      return queued;
    },
    onSuccess: () => {
      setDraft(null);
      queryClient.invalidateQueries({ queryKey: ["batches", selectedPage?.id] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["jobs", id] });
      queryClient.invalidateQueries({ queryKey: ["project", id] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
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
      resolution: reviewCandidate?.resolution ?? drawResolution,
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
    mutationFn: (candidateId: string) => api.selectCandidate(selectedPage!.id, candidateId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pages", activeChapterId] });
      queryClient.invalidateQueries({ queryKey: ["candidates"] });
      queryClient.invalidateQueries({ queryKey: ["library", id] });
    },
  });

  const goNext = useMutation({
    mutationFn: () => api.nextPage(selectedPage!.id),
    onSuccess: (next) => {
      setSelectedPageId(next.id);
      queryClient.invalidateQueries({ queryKey: ["batches", next.id] });
    },
  });

  const createExport = useMutation({
    mutationFn: (type: "PNG" | "PDF" | "JSON") => api.createExport(activeChapterId!, type),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["exports", id] }),
  });

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

      <div className="workspace-layout">
        <button className={navOpen ? "workspace-nav-backdrop show" : "workspace-nav-backdrop"} onClick={() => setNavOpen(false)} aria-label="关闭项目导航" />
        <aside className={navOpen ? "workspace-left open" : "workspace-left"}>
          <div className="workspace-project-title"><span>PROJECT / 01</span><h1>{draft.name}</h1><p>{needsChapters ? `${chapters.data?.length ?? 0} 章` : "漫画生产工作区"}{needsPages ? ` · ${pages.data?.length ?? 0} 页已规划` : ""}</p></div>
          <nav className="workspace-steps">
            {navigationItems.map(([target, label, description, index, Icon]) => <Link key={target} href={projectPath(target)} className={section === target ? "active" : ""} aria-current={section === target ? "page" : undefined} onClick={() => setNavOpen(false)}><Icon size={17} /><span>{label}<small>{description}</small></span><i>{index}</i></Link>)}
            <span className="workspace-nav-divider" />
            <Link href={projectPath("workflow")} onClick={() => setNavOpen(false)}><Workflow size={17} /><span>流程编排<small>查看与自定义 DAG</small></span><i>FL</i></Link>
            <Link href={projectPath("settings")} onClick={() => setNavOpen(false)}><Settings size={17} /><span>项目设置<small>模型、清晰度与检查</small></span><i>ST</i></Link>
          </nav>
          <div className="lock-note"><LockKeyhole size={16} /><p><strong>采用版本才影响后续</strong>收藏与采用互相独立，重新抽卡不会覆盖历史候选。</p></div>
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
              <header className="canvas-header"><div><span>CHARACTER BIBLE / 角色资产</span><h2>姓名、绰号与参考图绑定</h2></div><small>{characters.data?.length ?? 0} 个角色</small></header>
              <div className="character-create">
                <input className="text-input" value={characterName} onChange={(event) => setCharacterName(event.target.value)} placeholder="主要姓名（剧本默认使用）" />
                <input className="text-input" value={characterAliases} onChange={(event) => setCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" />
                <button className="button ink compact" disabled={!characterName.trim() || createCharacter.isPending} onClick={() => createCharacter.mutate()}><Plus size={14} />添加角色</button>
              </div>
              <div className="character-strip">
                {characters.data?.map((character) => <button key={character.id} className={bindCharacterId === character.id ? "character-chip active" : "character-chip"} onClick={() => { setBindCharacterId(character.id); setEditCharacterName(character.primary_name); setEditCharacterAliases(character.aliases.join("，")); setEditLockedFeatures(character.locked_features.join("，")); setEditForbiddenChanges(character.forbidden_changes.join("，")); }}><strong>{character.primary_name}</strong><span>{character.aliases.length ? `又名 ${character.aliases.join(" / ")}` : "无绰号"}</span>{character.alias_conflict && <em>称呼冲突待确认</em>}<small>{character.references.length} 张参考图 · {character.locked_features.length} 项已锁定</small></button>)}
              </div>
              {boundCharacter && <div className="character-editor"><div><strong>规范姓名与一致性锁</strong><span>剧本统一使用主要姓名；固定特征和禁止改变项会进入每次生图提示。</span></div><input aria-label="编辑主要姓名" className="text-input" value={editCharacterName} onChange={(event) => setEditCharacterName(event.target.value)} /><input aria-label="编辑角色绰号" className="text-input" value={editCharacterAliases} onChange={(event) => setEditCharacterAliases(event.target.value)} placeholder="绰号，用逗号分隔" /><button className="button outline compact" disabled={!editCharacterName.trim() || updateCharacter.isPending} onClick={() => updateCharacter.mutate()}>{updateCharacter.isPending ? <LoaderCircle className="spin" size={13} /> : <Pencil size={13} />}保存角色规范</button><div className="character-lock-fields"><input aria-label="角色固定特征" className="text-input" value={editLockedFeatures} onChange={(event) => setEditLockedFeatures(event.target.value)} placeholder="固定特征：黑色长发、左眼泪痣…" /><input aria-label="角色禁止改变项" className="text-input" value={editForbiddenChanges} onChange={(event) => setEditForbiddenChanges(event.target.value)} placeholder="禁止改变：发色、瞳色、身高关系…" /></div>{boundCharacter.alias_conflict && <em><CircleAlert size={12} />当前称呼与其他角色冲突，请修改后保存</em>}</div>}
              <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} label="本次素材生成模型（两个模型完全平级，每次进入本页均需重新选择）" />
              {bindCharacterId && <div className="asset-quickgen"><span>为选中角色补全标准形象（使用当前模型，受项目并发上限控制）</span><div><button className="complete-sheet" disabled={generateCompleteSheet.isPending || !boundCharacter?.references.length || !activeDrawModel} onClick={() => generateCompleteSheet.mutate()}><Sparkles size={13} />一键生成全套 4 张</button>{(["FRONT", "SIDE", "BACK", "EXPRESSION"] as const).map((variant) => <button key={variant} disabled={generateCharacterAsset.isPending || !boundCharacter?.references.length || !activeDrawModel} onClick={() => generateCharacterAsset.mutate(variant)}>{{ FRONT: "正面", SIDE: "侧面", BACK: "背面", EXPRESSION: "表情" }[variant]}</button>)}</div></div>}
              <div className="asset-workbench">
                <section><header><Shirt size={16} /><div><strong>服装档案</strong><span>把服装参考绑定到选中角色</span></div></header><div className="workbench-form"><div className="workbench-fields"><input aria-label="服装档案名称" className="text-input" value={outfitName} onChange={(event) => setOutfitName(event.target.value)} placeholder="例如：校服 / 冬季便装" /><input aria-label="服装锁定项" className="text-input" value={outfitLockedFields} onChange={(event) => setOutfitLockedFields(event.target.value)} placeholder="锁定项：颜色、鞋、配饰…" /></div><button disabled={!bindCharacterId || !outfitName.trim() || !selectedOutfitAssets.length || createOutfit.isPending} onClick={() => createOutfit.mutate()}>建立档案（{selectedOutfitAssets.length} 图）</button></div><div className="profile-chips">{outfits.data?.map((outfit) => <span key={outfit.id}>{characters.data?.find((item) => item.id === outfit.character_id)?.primary_name} · {outfit.name}<small>{outfit.reference_asset_ids.length} 图 · {outfit.locked_fields.length} 项锁定</small><button disabled={generateOutfitPreview.isPending || !activeDrawModel} onClick={() => generateOutfitPreview.mutate(outfit.id)}>生成穿着图</button></span>)}</div></section>
                <section><header><Palette size={16} /><div><strong>漫画风格档案</strong><span>Gemini 总结参考页，再供生图模仿</span></div></header><div className="workbench-form"><div className="workbench-fields"><input aria-label="漫画风格档案名称" className="text-input" value={styleName} onChange={(event) => setStyleName(event.target.value)} placeholder="风格档案名称" /><input aria-label="漫画风格锁定项" className="text-input" value={styleLockedFields} onChange={(event) => setStyleLockedFields(event.target.value)} placeholder="锁定项：线稿、网点、构图…" /></div><button disabled={!styleName.trim() || !selectedStyleAssets.length || createStyle.isPending} onClick={() => createStyle.mutate()}>创建并分析（{selectedStyleAssets.length} 图）</button></div><div className="profile-chips">{styles.data?.map((style) => <span className={draft.default_style_id === style.id ? "active" : ""} key={style.id}>{style.name}<small>{style.status} · {style.locked_fields.length} 项锁定</small><button onClick={() => analyzeStyle.mutate(style.id)}>重新分析</button><button disabled={generateStyleTest.isPending || !activeDrawModel} onClick={() => generateStyleTest.mutate(style.id)}>生成测试图</button><button onClick={() => activateStyle.mutate(style.id)}>{draft.default_style_id === style.id ? "使用中" : "设为当前"}</button></span>)}</div></section>
              </div>
              <div className="intake-toolbar"><div className="kind-switch">{kinds.map(([value, label]) => <button key={value} className={assetKind === value ? "active" : ""} onClick={() => setAssetKind(value)}>{label}</button>)}</div><span>{assetKind === "CHARACTER_REFERENCE" ? (bindCharacterId ? "将绑定到选中的角色" : "请先选择要绑定的角色") : assetKind === "OUTFIT_REFERENCE" ? "用于锁定角色服装、配饰和状态" : "用于锁定黑白网点、线条和构图风格"}</span></div>
              <label className={upload.isPending ? "upload-stage busy" : "upload-stage"}><input type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseFile} disabled={upload.isPending} /><span className="upload-icon">{upload.isPending ? <LoaderCircle className="spin" /> : <Upload />}</span><strong>{upload.isPending ? "正在安全上传…" : `上传${kinds.find(([value]) => value === assetKind)?.[1]}`}</strong><p>人物图会和选中的主要姓名绑定，不会只依赖文件名猜测身份。</p></label>
              {uploadError && <p className="form-error"><CircleAlert size={15} />{uploadError}</p>}
              {(bindExistingCharacterReference.isError || unbindExistingCharacterReference.isError) && <p className="form-error"><CircleAlert size={15} />{(bindExistingCharacterReference.error ?? unbindExistingCharacterReference.error)?.message}</p>}
              {kinds.map(([kind, label]) => {
                const grouped = assets.data?.filter((asset) => asset.kind === kind) ?? [];
                return <section className="asset-purpose-group" key={kind}>
                  <div className="asset-list-header"><span>{label}</span><small>{grouped.length} FILES</small></div>
                  <p className="purpose-explain">{{ CHARACTER_REFERENCE: "绑定主要姓名与绰号，用于保持脸、发型和体型一致。", OUTFIT_REFERENCE: "选择后建立角色服装档案，并在场景中指定穿着。", STYLE_REFERENCE: "选择后由 Gemini 总结线稿、网点、对比和构图语言。" }[kind]}</p>
                  <div className="asset-grid">{grouped.map((asset, index) => {
                    const characterReference = kind === "CHARACTER_REFERENCE" ? boundCharacter?.references.find((reference) => reference.asset_id === asset.id) : undefined;
                    const selected = kind === "CHARACTER_REFERENCE" ? Boolean(characterReference) : kind === "OUTFIT_REFERENCE" ? selectedOutfitAssets.includes(asset.id) : selectedStyleAssets.includes(asset.id);
                    return <article className={selected ? "asset-card selected" : "asset-card"} key={asset.id}><div className={`asset-thumb thumb-${(index % 3) + 1}`}>{asset.content_url ? <Image src={publicUrl(asset.content_url)!} alt={asset.original_name} width={74} height={74} unoptimized /> : <FileImage size={27} />}<span>{asset.width && asset.height ? `${asset.width}×${asset.height}` : asset.mime_type}</span></div><div><strong>{asset.original_name}</strong><p>{label} · {formatBytes(asset.byte_size)}</p><span className="tiny-status"><Check size={11} />{asset.status}</span>{kind === "CHARACTER_REFERENCE" ? <button className={characterReference ? "bind-purpose bound" : "bind-purpose"} disabled={!boundCharacter || bindExistingCharacterReference.isPending || unbindExistingCharacterReference.isPending} onClick={() => characterReference ? unbindExistingCharacterReference.mutate(characterReference.id) : bindExistingCharacterReference.mutate(asset.id)}>{!boundCharacter ? "先选择角色" : characterReference ? `解除与 ${boundCharacter.primary_name} 的绑定` : `绑定到 ${boundCharacter.primary_name}`}</button> : <button className="bind-purpose" onClick={() => kind === "OUTFIT_REFERENCE" ? setSelectedOutfitAssets((values) => values.includes(asset.id) ? values.filter((item) => item !== asset.id) : [...values, asset.id]) : setSelectedStyleAssets((values) => values.includes(asset.id) ? values.filter((item) => item !== asset.id) : [...values, asset.id])}>{selected ? "已选入档案" : "选入新档案"}</button>}<div className="asset-actions"><select aria-label="修改素材用途" value={asset.kind} onChange={(event) => reclassifyAsset.mutate({ assetId: asset.id, kind: event.target.value as AssetPurpose })}>{kinds.map(([value, option]) => <option key={value} value={value}>{option}</option>)}</select><button title="删除素材" onClick={() => { if (window.confirm("删除该导入素材并解除人物绑定？")) deleteAsset.mutate(asset.id); }}><Trash2 size={13} /></button></div></div></article>;
                  })}</div>
                  {!grouped.length && <div className="purpose-empty">尚无{label}</div>}
                </section>;
              })}
            </>
          )}

          {section === "script" && (
            <>
              <header className="canvas-header"><div><span>SCREENPLAY / 漫画剧本</span><h2>先写场景与情节拍，再进入分页</h2></div><div className="chapter-stage-control"><select aria-label="选择要编辑剧本的章节" value={activeChapterId ?? ""} onChange={(event) => setSelectedChapterId(event.target.value)}>{chapters.data?.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.ordinal}. {chapter.title}</option>)}</select><small>{script.data?.scenes.length ?? 0} 个场景</small></div></header>
              {!activeChapterId ? <div className="asset-empty tall"><Clapperboard size={28} /><strong>请先导入原作</strong></div> : !script.data?.scenes.length ? <div className="script-empty"><Clapperboard size={30} /><strong>本章还没有漫画剧本</strong><p>点击“生成漫画剧本”，Gemini 会逐段补充可视化动作、场景、对白、旁白、情绪和翻页悬念，不会压缩原文。</p><button className="button ink" disabled={parseChapter.isPending} onClick={() => parseChapter.mutate()}><Sparkles size={15} />生成漫画剧本</button></div> : <ScriptEditor chapterId={activeChapterId} script={script.data} characters={characters.data ?? []} outfits={outfits.data ?? []} onAssignOutfit={(sceneId, assignments) => assignOutfit.mutate({ sceneId, assignments })} />}
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
                <div className="draw-toolbar"><div className="page-picker">{pages.data?.map((page) => <button key={page.id} className={selectedPage.id === page.id ? "active" : ""} onClick={() => setSelectedPageId(page.id)}>{page.page_number}</button>)}</div><button className="button ghost compact" disabled={startBatch.isPending || Boolean(selectedPageStructureIssue)} onClick={() => startBatch.mutate()}><Plus size={14} />新批次</button></div>
                <div className="draw-context"><div><span>PAGE LOAD</span><strong>{selectedPage.estimated_text_chars} 字</strong><small>{selectedPage.panel_count} 格 / {selectedPage.estimated_bubbles} 气泡</small></div><p>{selectedPage.source_coverage.ranges?.map((item) => item.text).join("").slice(0, 180)}</p></div>
                <ImageModelPicker selected={activeDrawModel} onSelect={setDrawModel} label="本次页面生成模型（项目上次使用记录不会自动代替本次选择）" />
                <div className="generation-bar"><div className="generation-options"><div><span>清晰度</span><div className="resolution-row small">{(["1K", "2K", "4K"] as Resolution[]).map((value) => <button key={value} className={drawResolution === value ? "selected" : ""} onClick={() => setDrawResolution(value)}>{value}{value === "4K" && <small>P</small>}</button>)}</div></div><div><span>本次候选数</span><div className="resolution-row small count-row">{Array.from({ length: draft.default_concurrency }, (_, index) => index + 1).map((value) => <button key={value} className={effectiveDrawCount === value ? "selected" : ""} onClick={() => setDrawCount(value)}>{value}</button>)}</div></div></div><button className="button ink generate-one" disabled={generate.isPending || Boolean(selectedPageGenerationIssue)} onClick={() => generate.mutate()}>{generate.isPending ? <LoaderCircle className="spin" size={17} /> : <Star size={17} />}{generate.isPending ? `正在加入 ${effectiveDrawCount} 个任务` : selectedPageStructureIssue ? "请先补全剧本与分镜" : activeDrawModel ? `生成 ${effectiveDrawCount} 个候选` : "请先选择模型"}</button></div>
                {(generate.isError || startBatch.isError) && <p className="form-error"><CircleAlert size={14} />{(generate.error ?? startBatch.error)?.message}</p>}
                <div className="batch-heading"><div><span>BATCH</span><strong>{currentBatch ? `批次 ${currentBatch.ordinal}` : "尚未开始批次"}</strong></div><small>可跨模型比较 · 收藏不等于采用</small></div>
                <div className="candidate-grid">{candidates.data?.map((candidate) => <article className={candidate.is_selected ? "candidate-card selected" : "candidate-card"} key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} label={`候选 ${candidate.ordinal}`} /><div className="candidate-meta"><span>候选 {String(candidate.ordinal).padStart(2, "0")}</span><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><small>{candidate.resolution} · {candidate.status}</small></div><div className="candidate-actions"><button className={candidate.is_favorite ? "favorited" : ""} onClick={() => favorite.mutate({ candidateId: candidate.id, value: !candidate.is_favorite })}><Heart size={14} fill={candidate.is_favorite ? "currentColor" : "none"} />收藏</button><button disabled={!candidate.asset_id || candidate.is_selected} onClick={() => selectCandidate.mutate(candidate.id)}><Check size={14} />{candidate.is_selected ? "已采用" : "采用"}</button><button className={reviewCandidateId === candidate.id ? "reviewing" : ""} disabled={!candidate.asset_id || inspectCandidate.isPending} onClick={() => { setReviewCandidateId(candidate.id); inspectCandidate.mutate(candidate.id); }}><CircleAlert size={14} />检查</button>{candidate.asset_id && candidate.resolution === "1K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "2K" })}>升至 2K</button>}{candidate.asset_id && candidate.resolution !== "4K" && <button disabled={upscaleCandidate.isPending || !activeDrawModel} onClick={() => upscaleCandidate.mutate({ candidateId: candidate.id, resolution: "4K" })}>升至 4K</button>}<button className="danger-action" disabled={candidate.is_selected} onClick={() => { if (window.confirm("删除这个候选？收藏状态也会一并移除。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={14} />删除</button></div></article>)}</div>
                {reviewCandidateId && <section className="inspection-panel"><header><div><span>AI QUALITY CHECK</span><strong>候选成片检查</strong></div><button onClick={() => setReviewCandidateId(null)}>关闭</button></header>{!latestInspections.length ? <div className="inspection-wait"><LoaderCircle className={reviewJob && !["COMPLETED", "FAILED"].includes(reviewJob.status) ? "spin" : ""} size={18} /><span>{reviewJob ? `检查任务 ${reviewJob.status} · ${reviewJob.progress}%` : "正在读取检查结果"}</span></div> : <div className="inspection-results">{latestInspections.map((inspection) => { const passed = ["PASS", "ACCEPTABLE", "MATCH"].includes(inspection.outcome); const repairType = recommendedRepairType(inspection.category); return <article className={passed ? "passed" : "failed"} key={inspection.id}><div><span>{inspectionLabels[inspection.category] ?? inspection.category}</span><strong>{inspection.outcome}</strong><em>{inspection.score === null ? "—" : `${Math.round(inspection.score * 100)}%`}</em></div><p>{inspectionSummary(inspection.details)}</p>{!passed && <button disabled={repairCandidate.isPending || !activeDrawModel} onClick={() => repairCandidate.mutate(inspection)}><Sparkles size={13} />修复{repairTypeLabels[repairType]}</button>}</article>; })}</div>}{(inspectCandidate.isError || repairCandidate.isError || upscaleCandidate.isError) && <p className="form-error"><CircleAlert size={14} />{(inspectCandidate.error ?? repairCandidate.error ?? upscaleCandidate.error)?.message}</p>}</section>}
                {!candidates.data?.length && <div className="asset-empty"><ImagePlus size={25} /><strong>这个批次还没有候选</strong><p>选择任一平级模型，生成一张再决定是否收藏或采用。</p></div>}
                <div className="next-page-row"><span>{selectedPage.selected_candidate_id ? "当前页已有采用版本，可以继续或单独导出" : "采用一个满意候选后才能进入下一页"}</span><div>{selectedPage.selected_candidate_id && <a className="button ghost compact" href={api.selectedPagePngUrl(selectedPage.id)!}><Download size={14} />单页 PNG</a>}<button className="button outline" disabled={!selectedPage.selected_candidate_id || goNext.isPending} onClick={() => goNext.mutate()}>生成下一页 <ArrowRight size={15} /></button></div></div>
              </> : <div className="asset-empty tall"><Sparkles size={28} /><strong>没有可抽卡页面</strong><p>先完成动态分页。</p></div>}
            </>
          )}

          {section === "library" && (
            <>
              <header className="canvas-header"><div><span>LIBRARY / 批次素材库</span><h2>保存每一次值得比较的结果</h2></div><small>{library.data?.total_candidates ?? 0} 个候选</small></header>
              <div className="library-toolbar"><div className="library-filter-grid"><button className={favoriteOnly ? "active" : ""} onClick={() => { setFavoriteOnly(!favoriteOnly); setLibraryCursor(""); setLibraryHistory([]); }}><Heart size={14} />只看收藏（{library.data?.favorite_count ?? 0}）</button><select aria-label="按角色筛选素材" value={libraryCharacter} onChange={(event) => { setLibraryCharacter(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部角色</option>{characters.data?.map((character) => <option key={character.id} value={character.id}>{character.primary_name}</option>)}</select><select aria-label="按生成类型筛选素材" value={libraryKind} onChange={(event) => { setLibraryKind(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部类型</option>{Object.entries(generationKindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><select aria-label="按模型筛选素材" value={libraryModel} onChange={(event) => { setLibraryModel(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部模型</option>{modelOptions.map((model) => <option key={model.alias} value={model.alias}>{model.name}</option>)}</select><select aria-label="按分辨率筛选素材" value={libraryResolution} onChange={(event) => { setLibraryResolution(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }}><option value="">全部清晰度</option>{(["1K", "2K", "4K"] as const).map((value) => <option key={value} value={value}>{value}</option>)}</select><label>从<input aria-label="素材开始日期" type="date" value={libraryDateFrom} onChange={(event) => { setLibraryDateFrom(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label><label>至<input aria-label="素材结束日期" type="date" value={libraryDateTo} onChange={(event) => { setLibraryDateTo(event.target.value); setLibraryCursor(""); setLibraryHistory([]); }} /></label><button onClick={() => { setFavoriteOnly(false); setLibraryCharacter(""); setLibraryKind(""); setLibraryModel(""); setLibraryResolution(""); setLibraryDateFrom(""); setLibraryDateTo(""); setLibraryCursor(""); setLibraryHistory([]); }}><RotateCcw size={13} />重置</button></div><span>按章节 → 页面 → 批次排列</span></div>
              <div className="library-groups">{library.data?.groups.map((group) => <section className="library-group" key={group.batch.id}><header><div><span>BATCH {String(group.batch.ordinal).padStart(3, "0")}</span><strong>{generationKindLabels[group.batch.generation_kind] ?? group.batch.generation_kind}</strong></div><small>{new Date(group.batch.created_at).toLocaleString("zh-CN")} · {group.candidates.length} 张</small></header><div className="library-candidates">{group.candidates.map((candidate) => <article key={candidate.id}><CandidateArtwork contentUrl={candidate.content_url} label={`批次候选 ${candidate.ordinal}`} /><div><strong>{modelOptions.find((item) => item.alias === candidate.model_alias)?.name}</strong><span>{candidate.resolution} · {candidate.status}</span>{candidate.is_favorite && <Heart size={13} fill="currentColor" />}{candidate.is_selected && <em>采用中</em>}<button className="library-delete" title="从素材库软删除" disabled={candidate.is_selected || deleteCandidate.isPending} onClick={() => { if (window.confirm("从素材库隐藏这个候选？生成文件和任务记录会保留。")) deleteCandidate.mutate(candidate.id); }}><Trash2 size={12} /></button></div></article>)}</div></section>)}</div>
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
              <div className="job-toolbar"><div><button className={!showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(false); setJobNotice(""); }}><ListTodo size={13} />近期任务</button><button className={showArchivedJobs ? "active" : ""} onClick={() => { setShowArchivedJobs(true); setJobNotice(""); }}><History size={13} />历史记录</button></div>{!showArchivedJobs && <button disabled={archiveCompletedJobs.isPending} onClick={() => { if (window.confirm("将所有已完成、失败和已取消任务移入历史记录？生成候选与溯源信息不会删除。")) archiveCompletedJobs.mutate(); }}><Archive size={13} />清空已结束任务</button>}</div>
              {jobNotice && <p className="job-notice"><CircleAlert size={13} />{jobNotice}</p>}
              <div className="job-list">{jobs.data?.map((job) => <article className={`job-row status-${job.status.toLowerCase()}`} key={job.id}><div className="job-type"><span>{jobLabels[job.job_type] ?? job.job_type}</span><strong>{job.status}</strong></div><div className="job-progress"><i><b style={{ width: `${job.progress}%` }} /></i><span>{job.progress}% · 尝试 {job.attempt_count}/{job.max_attempts}</span></div><div className="job-detail"><span>{job.workflow_node_id ? `节点 ${job.workflow_node_id}` : job.model_alias ? modelOptions.find((item) => item.alias === job.model_alias)?.name ?? job.model_alias : "系统任务"}</span><small>{new Date(job.created_at).toLocaleString("zh-CN")}</small>{job.error_message && <em>{job.error_message}</em>}</div><div className="job-actions">{!showArchivedJobs && ["WAITING", "QUEUED", "PREPARING", "GENERATING"].includes(job.status) && <button onClick={() => cancelJob.mutate(job.id)}>取消</button>}{!showArchivedJobs && (["FAILED", "CANCELLED", "TIMED_OUT"].includes(job.status) || (job.status === "WAITING" && Boolean(job.error_code))) && <button onClick={() => retryJob.mutate(job.id)}><RotateCcw size={12} />重试</button>}{!showArchivedJobs && ["COMPLETED", "FAILED", "CANCELLED"].includes(job.status) && <button onClick={() => archiveJob.mutate(job.id)}><Archive size={12} />归档</button>}{showArchivedJobs && <button onClick={() => restoreJob.mutate(job.id)}><RotateCcw size={12} />恢复</button>}{showArchivedJobs && ["FAILED", "CANCELLED"].includes(job.status) && <button className="danger-action" onClick={() => { if (window.confirm("仅无候选、生成记录、工作流或任务依赖的失败任务可以彻底删除。继续吗？")) deleteJob.mutate(job.id); }}><Trash2 size={12} />彻底删除</button>}</div></article>)}</div>
              {!jobs.data?.length && <div className="asset-empty tall">{showArchivedJobs ? <History size={28} /> : <ListTodo size={28} />}<strong>{showArchivedJobs ? "还没有历史任务" : "当前没有任务"}</strong><p>{showArchivedJobs ? "归档后的已结束任务会保留在这里，可随时恢复。" : "剧本解析、页面生成、检查和修复都会列在这里。"}</p></div>}
            </>
          )}
        </section>

      </div>

      <Link className="queue-dock" href={projectPath("jobs")}><div><span className={queueStats.waiting ? "queue-light active" : "queue-light"} /><strong>打开任务中心</strong><small>{jobs.data?.[0] ? `${jobLabels[jobs.data[0].job_type] ?? jobs.data[0].job_type} · ${jobs.data[0].status}` : section === "jobs" || section === "generate" ? "当前没有任务" : "查看生成、解析与检查进度"}</small></div>{(section === "jobs" || section === "generate") && <div><span>并发上限 {draft.default_concurrency}</span><i /><span>{queueStats.waiting} 等待</span><i /><span>{queueStats.failed} 失败</span></div>}</Link>
    </AppShell>
  );
}
