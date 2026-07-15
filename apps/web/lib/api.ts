const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
const API_ORIGIN = API_URL.replace(/\/api\/v1\/?$/, "");

export type Resolution = "1K" | "2K" | "4K";
export type WorkflowMode = "AUTO" | "DIRECTOR" | "SEMI_AUTO";
export type ImageModelAlias = "image.nano_banana_2" | "image.nano_banana_pro";

export interface Project {
  id: string;
  name: string;
  language: string;
  reading_direction: string;
  page_ratio: string;
  default_resolution: Resolution;
  draft_resolution: Resolution;
  workflow_mode: WorkflowMode;
  default_concurrency: number;
  default_style_id: string | null;
  ocr_enabled: boolean;
  consistency_check_enabled: boolean;
  text_model_alias: string;
  last_image_model_alias: ImageModelAlias | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ModelCapability {
  provider: string;
  model_id: string;
  logical_alias: string;
  display_name: string;
  operations: string[];
  resolutions: string[];
  preview_resolutions: string[];
  max_reference_images: number;
  regions: string[];
}

export interface VertexStatus {
  configured: boolean;
  health_state: "UNCONFIGURED" | "CHECKING" | "HEALTHY" | "DEGRADED" | "OFFLINE";
  credential_file_present: boolean;
  project: string | null;
  location: string;
  text_model: string;
  image_models: string[];
  last_checked_at: string | null;
  last_success_at: string | null;
  token_expires_at: string | null;
  consecutive_failures: number;
  latency_ms: number | null;
  error_code: string | null;
  message: string;
  text_model_access: string;
  image_model_access: Record<string, string>;
}

export interface RuntimeSettings {
  queue_mode: "AUTO" | "LOCAL" | "REDIS";
  job_timeout_seconds: number;
  max_auto_repairs: number;
  default_concurrency: number;
  health_check_interval_seconds: number;
  ui_poll_interval_seconds: number;
  workflow_autosave_ms: number;
  database_backend: string;
  storage_root: string;
  upload_root: string;
  redis_configured: boolean;
  version: number;
}

export interface DiagnosticCheck {
  id: string;
  label: string;
  status: "OK" | "WARNING" | "FAILED" | "NOT_CHECKED";
  message: string;
  latency_ms: number | null;
}

export interface Diagnostics {
  checks: DiagnosticCheck[];
  checked_at: string;
}

export interface Asset {
  id: string;
  project_id: string;
  kind: string;
  original_name: string;
  mime_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  status: string;
  created_at: string;
  content_url: string | null;
}

export type AssetPurpose = "CHARACTER_REFERENCE" | "OUTFIT_REFERENCE" | "STYLE_REFERENCE";

export interface Chapter {
  id: string;
  project_id: string;
  title: string;
  ordinal: number;
  status: string;
  current_source_revision_id: string | null;
  source_character_count: number;
  segment_count: number;
  page_count: number;
  coverage_ratio: number;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface SourceRevision {
  id: string;
  chapter_id: string;
  revision: number;
  source_type: string;
  original_text: string;
  character_count: number;
  imported_at: string;
}

export interface ScriptBeat {
  id: string;
  ordinal: number;
  action: string;
  speaker_name: string;
  dialogue: string;
  narration: string;
  emotion: string;
  source_range: { segment_ids?: string[] };
}

export interface ScriptScene {
  id: string;
  ordinal: number;
  location: string;
  time_label: string;
  purpose: string;
  emotional_arc: string;
  source_range: { segment_ids?: string[] };
  outfit_assignments: Record<string, string>;
  beats: ScriptBeat[];
}

export interface Outfit {
  id: string;
  project_id: string;
  character_id: string;
  name: string;
  components: Record<string, unknown>;
  state_rules: Record<string, unknown>;
  locked_fields: string[];
  reference_asset_ids: string[];
  status: string;
  version: number;
}

export interface StyleProfile {
  id: string;
  project_id: string;
  name: string;
  color_mode: string;
  profile: Record<string, unknown> & { prompt_summary?: string; reference_asset_ids?: string[] };
  locked_fields: string[];
  status: string;
  version: number;
}

export interface Script {
  chapter_id: string;
  status: string;
  revision_no: number | null;
  coverage: { ratio?: number; expected?: number; covered?: number; missing_segment_ids?: string[] };
  scenes: ScriptScene[];
}

export interface CharacterReference {
  id: string;
  character_id: string;
  asset_id: string;
  angle: string;
  is_canonical: boolean;
}

export interface Character {
  id: string;
  project_id: string;
  primary_name: string;
  aliases: string[];
  alias_conflict: boolean;
  canonical_description: string;
  locked_features: string[];
  forbidden_changes: string[];
  status: string;
  version: number;
  references: CharacterReference[];
}

export interface MangaPage {
  id: string;
  chapter_id: string;
  page_number: number;
  revision_no: number;
  page_function: string;
  panel_count: number;
  reading_direction: string;
  resolution: Resolution;
  status: string;
  estimated_text_chars: number;
  estimated_bubbles: number;
  source_coverage: { complete?: boolean; ranges?: { text: string }[] };
  selected_candidate_id: string | null;
  continuity_status: string;
  scene_ids: string[];
  beat_ids: string[];
}

export interface GenerationBatch {
  id: string;
  project_id: string;
  chapter_id: string | null;
  page_id: string | null;
  target_type: string | null;
  target_id: string | null;
  ordinal: number;
  generation_kind: string;
  status: string;
  created_at: string;
  closed_at: string | null;
}

export interface PageCandidate {
  id: string;
  batch_id: string;
  page_id: string | null;
  ordinal: number;
  model_alias: ImageModelAlias;
  resolution: Resolution;
  status: string;
  asset_id: string | null;
  job_id: string | null;
  is_favorite: boolean;
  is_selected: boolean;
  created_at: string;
  content_url: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  target_type: string;
  target_id: string;
  job_type: string;
  status: string;
  progress: number;
  attempt_count: number;
  max_attempts: number;
  model_alias: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
}

export interface InspectionResult {
  id: string;
  candidate_id: string | null;
  category: "TEXT" | "SPEAKER" | "CHARACTER" | "OUTFIT" | "PROP" | "CONTINUITY" | string;
  outcome: string;
  score: number | null;
  details: Record<string, unknown>;
  regions: Array<Record<string, unknown>>;
  severity: string;
  created_at: string;
}

export interface CandidateQueued {
  job_id: string;
  job_status: string;
  candidate: PageCandidate;
}

export interface LibraryGroup {
  batch: GenerationBatch;
  candidates: PageCandidate[];
}

export interface Library {
  groups: LibraryGroup[];
  total_candidates: number;
  favorite_count: number;
}

export interface LibraryFilters {
  favorite?: boolean;
  character_id?: string;
  generation_kind?: string;
  model_alias?: ImageModelAlias;
  resolution?: Resolution;
  date_from?: string;
  date_to?: string;
}

export interface ExportBundle {
  id: string;
  project_id: string;
  chapter_id: string | null;
  export_type: "PNG" | "PDF" | "JSON";
  byte_size: number;
  page_count: number;
  created_at: string;
  download_url: string;
}

export function publicUrl(path: string | null) {
  if (!path) return null;
  return path.startsWith("http") ? path : `${API_ORIGIN}${path}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: "请求失败" }));
    const detail = typeof body.detail === "string" ? body.detail : "请求数据不符合要求";
    throw new Error(detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (payload: Partial<Project> & { name: string }) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(payload) }),
  updateProject: (id: string, payload: Partial<Project> & { version: number }) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  models: () => request<ModelCapability[]>("/models"),
  vertexStatus: () => request<VertexStatus>("/settings/vertex/status"),
  verifyVertex: (level: "CREDENTIALS" | "TEXT_MODEL" | "IMAGE_MODEL" = "CREDENTIALS", imageModelAlias?: ImageModelAlias) =>
    request<VertexStatus>("/settings/vertex/verify", {
      method: "POST",
      body: JSON.stringify({ level, image_model_alias: imageModelAlias }),
    }),
  runtimeSettings: () => request<RuntimeSettings>("/settings/runtime"),
  updateRuntimeSettings: (payload: Partial<RuntimeSettings> & { version: number }) =>
    request<RuntimeSettings>("/settings/runtime", { method: "PATCH", body: JSON.stringify(payload) }),
  diagnostics: () => request<Diagnostics>("/settings/diagnostics"),
  assets: (projectId: string) => request<Asset[]>(`/assets?project_id=${encodeURIComponent(projectId)}`),
  uploadAsset: (projectId: string, kind: string, file: File) => {
    const data = new FormData();
    data.append("project_id", projectId);
    data.append("kind", kind);
    data.append("file", file);
    return request<Asset>("/assets/upload", { method: "POST", body: data });
  },
  updateAsset: (assetId: string, kind: AssetPurpose) => request<Asset>(`/assets/${assetId}`, {
    method: "PATCH", body: JSON.stringify({ kind }),
  }),
  deleteAsset: (assetId: string) => request<void>(`/assets/${assetId}`, { method: "DELETE" }),
  chapters: (projectId: string) => request<Chapter[]>(`/projects/${projectId}/chapters`),
  importSource: (projectId: string, title: string, text: string) =>
    request<{ chapters: Chapter[]; total_characters: number }>(`/projects/${projectId}/sources/import`, {
      method: "POST",
      body: JSON.stringify({ title, text, source_type: "PASTE" }),
    }),
  uploadSource: (projectId: string, title: string, file: File) => {
    const data = new FormData();
    data.append("title", title);
    data.append("file", file);
    return request<{ chapters: Chapter[]; total_characters: number }>(`/projects/${projectId}/sources/upload`, {
      method: "POST",
      body: data,
    });
  },
  revisions: (chapterId: string) => request<SourceRevision[]>(`/chapters/${chapterId}/revisions`),
  reviseSource: (chapterId: string, title: string, text: string) => request<SourceRevision>(`/chapters/${chapterId}/revisions`, {
    method: "POST", body: JSON.stringify({ title, text, source_type: "PASTE" }),
  }),
  deleteChapter: (chapterId: string) => request<void>(`/chapters/${chapterId}`, { method: "DELETE" }),
  restoreChapter: (chapterId: string) => request<Chapter>(`/chapters/${chapterId}/restore`, { method: "POST" }),
  script: (chapterId: string) => request<Script>(`/chapters/${chapterId}/script`),
  parseChapter: (chapterId: string) => request<Job>(`/chapters/${chapterId}/parse`, { method: "POST" }),
  planChapter: (chapterId: string, fromPageNumber?: number) => request<{ pages: MangaPage[]; coverage_ratio: number }>(`/chapters/${chapterId}/plan`, {
    method: "POST",
    body: JSON.stringify({ replace_existing: true, from_page_number: fromPageNumber }),
  }),
  pages: (chapterId: string) => request<MangaPage[]>(`/chapters/${chapterId}/pages`),
  characters: (projectId: string) => request<Character[]>(`/projects/${projectId}/characters`),
  outfits: (projectId: string) => request<Outfit[]>(`/projects/${projectId}/outfits`),
  createOutfit: (projectId: string, payload: { character_id: string; name: string; reference_asset_ids: string[]; locked_fields: string[] }) =>
    request<Outfit>(`/projects/${projectId}/outfits`, {
      method: "POST", body: JSON.stringify({ ...payload, components: {}, state_rules: {} }),
    }),
  styles: (projectId: string) => request<StyleProfile[]>(`/projects/${projectId}/styles`),
  createStyle: (projectId: string, name: string, referenceAssetIds: string[], lockedFields: string[]) =>
    request<StyleProfile>(`/projects/${projectId}/styles`, {
      method: "POST",
      body: JSON.stringify({ name, color_mode: "monochrome", profile: {}, reference_asset_ids: referenceAssetIds, locked_fields: lockedFields }),
    }),
  analyzeStyle: (styleId: string) => request<Job>(`/styles/${styleId}/analyze`, { method: "POST" }),
  activateStyle: (projectId: string, styleId: string) => request<StyleProfile>(`/projects/${projectId}/styles/${styleId}/activate`, { method: "POST" }),
  assignSceneOutfits: (sceneId: string, assignments: Record<string, string>) =>
    request<{ scene_id: string; assignments: Record<string, string> }>(`/scenes/${sceneId}/outfits`, {
      method: "PATCH", body: JSON.stringify({ assignments }),
    }),
  createCharacter: (projectId: string, primaryName: string, aliases: string[]) =>
    request<Character>(`/projects/${projectId}/characters`, {
      method: "POST",
      body: JSON.stringify({ primary_name: primaryName, aliases }),
    }),
  updateCharacter: (characterId: string, version: number, primaryName: string, aliases: string[], lockedFeatures: string[], forbiddenChanges: string[]) =>
    request<Character>(`/characters/${characterId}`, {
      method: "PATCH",
      body: JSON.stringify({ version, primary_name: primaryName, aliases, locked_features: lockedFeatures, forbidden_changes: forbiddenChanges }),
    }),
  bindCharacterReference: (characterId: string, assetId: string) =>
    request<CharacterReference>(`/characters/${characterId}/references`, {
      method: "POST",
      body: JSON.stringify({ asset_id: assetId, angle: "unspecified", is_canonical: true }),
    }),
  startAssetBatch: (targetType: "CHARACTER" | "OUTFIT" | "STYLE", targetId: string, generationKind: "CHARACTER" | "OUTFIT" | "STYLE_TEST") =>
    request<GenerationBatch>("/asset-generation-batches", {
      method: "POST",
      body: JSON.stringify({ target_type: targetType, target_id: targetId, generation_kind: generationKind }),
    }),
  generateAssetCandidate: (batchId: string, model_alias: ImageModelAlias, resolution: Resolution, variant: "FRONT" | "SIDE" | "BACK" | "EXPRESSION" | "OUTFIT" | "STYLE_TEST") =>
    request<{ job_id: string; job_status: string; candidate: PageCandidate }>(`/asset-generation-batches/${batchId}/candidates`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution, variant, instruction: "" }),
    }),
  generateCompleteCharacterSheet: (characterId: string, model_alias: ImageModelAlias, resolution: Resolution) =>
    request<Array<{ job_id: string; job_status: string; candidate: PageCandidate }>>(`/characters/${characterId}/complete-sheet`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution, variants: ["FRONT", "SIDE", "BACK", "EXPRESSION"] }),
    }),
  batches: (pageId: string) => request<GenerationBatch[]>(`/pages/${pageId}/batches`),
  startBatch: (pageId: string) => request<GenerationBatch>(`/pages/${pageId}/batches`, { method: "POST" }),
  candidates: (batchId: string) => request<PageCandidate[]>(`/batches/${batchId}/candidates`),
  generateCandidate: (batchId: string, model_alias: ImageModelAlias, resolution: Resolution) =>
    request<{ job_id: string; job_status: string; candidate: PageCandidate }>(`/batches/${batchId}/candidates`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution }),
    }),
  favoriteCandidate: (candidateId: string, isFavorite: boolean) =>
    request<PageCandidate>(`/candidates/${candidateId}/favorite`, {
      method: "PATCH",
      body: JSON.stringify({ is_favorite: isFavorite }),
    }),
  deleteCandidate: (candidateId: string) => request<void>(`/candidates/${candidateId}`, { method: "DELETE" }),
  selectCandidate: (pageId: string, candidateId: string) =>
    request<MangaPage>(`/pages/${pageId}/select-candidate`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId }),
    }),
  nextPage: (pageId: string) => request<MangaPage>(`/pages/${pageId}/next`, { method: "POST" }),
  library: (projectId: string, filters: LibraryFilters = {}) => {
    const query = new URLSearchParams({ group_by: "batch" });
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    return request<Library>(`/projects/${projectId}/library?${query.toString()}`);
  },
  jobs: (projectId: string) => request<Job[]>(`/projects/${projectId}/jobs`),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  retryJob: (jobId: string) => request<Job>(`/jobs/${jobId}/retry`, { method: "POST" }),
  inspectCandidate: (candidateId: string) => request<Job>(`/candidates/${candidateId}/inspect`, {
    method: "POST",
    body: JSON.stringify({ categories: ["TEXT", "SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"] }),
  }),
  inspections: (candidateId: string) => request<InspectionResult[]>(`/candidates/${candidateId}/inspections`),
  repairCandidate: (
    candidateId: string,
    payload: {
      inspection_result_id: string;
      repair_type: "TEXT_REGION" | "BUBBLE_REGION" | "PANEL" | "PAGE";
      target_regions: Array<Record<string, unknown>>;
      target_fields: string[];
      model_alias: ImageModelAlias;
      resolution: Resolution;
    },
  ) => request<CandidateQueued>(`/candidates/${candidateId}/repairs`, {
    method: "POST",
    body: JSON.stringify(payload),
  }),
  upscaleCandidate: (candidateId: string, model_alias: ImageModelAlias, resolution: "2K" | "4K") =>
    request<CandidateQueued>(`/candidates/${candidateId}/upscale`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution }),
    }),
  exports: (projectId: string) => request<ExportBundle[]>(`/projects/${projectId}/exports`),
  createExport: (chapterId: string, exportType: ExportBundle["export_type"]) =>
    request<ExportBundle>(`/chapters/${chapterId}/exports`, {
      method: "POST",
      body: JSON.stringify({ export_type: exportType }),
    }),
  selectedPagePngUrl: (pageId: string) => publicUrl(`/api/v1/pages/${pageId}/export.png`),
};
