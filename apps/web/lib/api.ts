const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";
const API_ORIGIN = API_URL.replace(/\/api\/v1\/?$/, "");

export type Resolution = "1K" | "2K" | "4K";
export type WorkflowMode = "AUTO" | "DIRECTOR" | "SEMI_AUTO";
export type ImageModelAlias = string;

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
  consistency_check_enabled: boolean;
  text_model_alias: string;
  last_image_model_alias: ImageModelAlias | null;
  default_text_model_id: string | null;
  last_image_model_id: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ModelCapability {
  catalog_id: string;
  connection_id: string;
  provider: string;
  protocol: string;
  model_id: string;
  logical_alias: string;
  display_name: string;
  model_type: "TEXT" | "IMAGE";
  input_modalities: string[];
  output_modalities: string[];
  operations: string[];
  resolutions: string[];
  preview_resolutions: string[];
  max_reference_images: number;
  regions: string[];
  confidence: string;
  enabled: boolean;
  auto_eligible: boolean;
  priority: number;
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

export interface ProviderKeySummary {
  id: string;
  label: string;
  key_hint: string;
  enabled: boolean;
  health_state: string;
  cooldown_until: string | null;
  last_used_at: string | null;
  last_error_code: string | null;
}

export interface ProviderConnection {
  id: string;
  provider_id: string;
  name: string;
  protocol: "OPENAI" | "ANTHROPIC" | "VERTEX_NATIVE" | "GOOGLE_NATIVE";
  base_url: string;
  enabled: boolean;
  configured: boolean;
  credential_writable: boolean;
  use_responses_api: boolean;
  endpoint_templates: Record<string, string>;
  extra_headers: Record<string, string>;
  balance_config: Record<string, unknown>;
  nonsecret_config: Record<string, unknown>;
  health_state: string;
  last_checked_at: string | null;
  last_success_at: string | null;
  latency_ms: number | null;
  error_code: string | null;
  message: string;
  key_count: number;
  model_count: number;
  keys: ProviderKeySummary[];
  version: number;
}

export interface ProviderProfile {
  id: string;
  preset_key: string | null;
  name: string;
  category: string;
  description: string;
  built_in: boolean;
  enabled: boolean;
  risk_label: string;
  documentation_url: string | null;
  connections: ProviderConnection[];
  version: number;
}

export interface ProviderModel {
  id: string;
  connection_id: string;
  provider_model_id: string;
  display_name: string;
  legacy_alias: string | null;
  model_type: "TEXT" | "IMAGE";
  input_modalities: string[];
  output_modalities: string[];
  operations: string[];
  api_surfaces: string[];
  capabilities: Record<string, unknown>;
  enabled: boolean;
  priority: number;
  confidence: string;
  source: string;
  pricing: Record<string, unknown>;
  success_rate: number | null;
  median_latency_ms: number | null;
  last_verified_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface ModelProbe {
  id: string;
  connection_id: string;
  model_id: string | null;
  probe_type: string;
  status: string;
  latency_ms: number | null;
  metrics: Record<string, unknown>;
  error_code: string | null;
  message: string;
  created_at: string;
}

export interface Diagnostics {
  checks: DiagnosticCheck[];
  checked_at: string;
  queue: {
    current_mode: string;
    actual_executor: string;
    redis_state: string;
    can_execute_new_jobs: boolean;
  };
}

export interface Asset {
  id: string;
  project_id: string;
  kind: string;
  original_name: string;
  display_name: string | null;
  mime_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  status: string;
  created_at: string;
  content_url: string | null;
  thumbnail_url: string | null;
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
  scene_id: string;
  ordinal: number;
  action: string;
  speaker_name: string;
  dialogue: string;
  narration: string;
  subtext: string;
  emotion: string;
  importance: number;
  must_visualize: boolean;
  mergeable: boolean;
  page_turn_hook: boolean;
  source_range: { segment_ids?: string[] };
  version: number;
}

export interface ScriptScene {
  id: string;
  ordinal: number;
  location: string;
  time_label: string;
  weather: string;
  purpose: string;
  emotional_arc: string;
  source_range: { segment_ids?: string[] };
  outfit_assignments: Record<string, string>;
  locked_fields: string[];
  version: number;
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
  color_mode: "monochrome" | "color";
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
  source_coverage: { complete?: boolean; layout_mode?: "dynamic" | "balanced"; ranges?: { text: string }[] };
  selected_candidate_id: string | null;
  storyboard_version: number;
  selected_candidate_ack_version: number | null;
  continuity_status: string;
  scene_ids: string[];
  beat_ids: string[];
  version: number;
}

export interface PanelDialogue {
  id: string;
  panel_id: string;
  speaker_character_id: string | null;
  target_text: string;
  reading_order: number;
  text_direction: "vertical" | "horizontal";
  region: Record<string, unknown>;
  rewrite_forbidden: boolean;
}

export interface StoryboardPanel {
  id: string;
  page_id: string;
  reading_order: number;
  bounds: Record<string, number>;
  shot_type: string;
  camera_angle: string;
  camera_height: string;
  characters: string[];
  character_presence: Record<string, CharacterPresence>;
  props: string[];
  outfits: Record<string, string>;
  actions: Record<string, string>;
  expressions: Record<string, string>;
  background: string;
  bubble_regions: Array<Record<string, unknown>>;
  sound_effects: Array<Record<string, unknown> | string>;
  bleed: boolean;
  borderless: boolean;
  locked_fields: string[];
  version: number;
  dialogues: PanelDialogue[];
}

export interface Storyboard {
  page: MangaPage;
  panels: StoryboardPanel[];
  candidate_count: number;
}

export type CharacterPresence = "VISIBLE" | "OFFSCREEN" | "MENTIONED";

export interface PageReadinessBlocker {
  code: string;
  message: string;
  stage: string;
  target_id: string | null;
  severity: string;
}

export interface PageReadinessCharacter {
  character_id: string;
  primary_name: string;
  presence: CharacterPresence;
  character_reference_ids: string[];
  outfit_id: string | null;
  outfit_name: string | null;
  outfit_reference_ids: string[];
}

export interface PageReadiness {
  page_id: string;
  ready: boolean;
  source_complete: boolean;
  script_complete: boolean;
  visible_characters: PageReadinessCharacter[];
  mentioned_characters: PageReadinessCharacter[];
  props: string[];
  style: {
    style_id: string | null;
    name: string | null;
    color_mode: string | null;
    status: string | null;
    palette_confirmed: boolean;
    test_image_approved: boolean;
  };
  provider: {
    configured: boolean;
    health_state: string;
    text_model_access: string;
    image_model_access: string;
    image_model_alias: string;
    usable_image_model_count: number;
    auto_image_model_count: number;
  };
  worker: {
    queue_mode: string;
    executor: string;
    can_execute: boolean;
    redis_state: string;
  };
  blockers: PageReadinessBlocker[];
  estimated_image_calls: number;
  estimated_cost_note: string;
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
  based_on_storyboard_version: number | null;
  version_state: "CURRENT" | "STALE" | "STALE_ACCEPTED" | "LEGACY_UNKNOWN";
  staleness_reasons: string[];
  created_at: string;
  variant: string | null;
  prompt_snapshot: Record<string, unknown>;
  content_url: string | null;
  thumbnail_url: string | null;
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
  workflow_run_id: string | null;
  workflow_node_id: string | null;
  duration_ms: number | null;
  usage_summary: Record<string, unknown>;
  estimated_cost: number | null;
  result: {
    kind: "IMAGE";
    label: string;
    candidate_id: string | null;
    page_id: string | null;
    content_url: string;
    thumbnail_url: string | null;
  } | null;
  created_at: string;
  archived_at: string | null;
}

export interface DashboardProject {
  project: Project;
  chapter_count: number;
  page_count: number;
  selected_page_count: number;
  review_page_count: number;
  stale_selected_page_count: number;
  candidate_count: number;
  pending_job_count: number;
  failed_job_count: number;
  next_action: { section: string; label: string; reason: string };
}

export interface ProjectDashboard {
  totals: {
    project_count: number;
    page_count: number;
    selected_page_count: number;
    review_page_count: number;
    pending_job_count: number;
  };
  projects: DashboardProject[];
}

export interface GenerationWorkbench {
  page: MangaPage;
  storyboard: Storyboard;
  readiness: PageReadiness;
  current_batch: GenerationBatch | null;
  candidates: PageCandidate[];
  selected_candidate: PageCandidate | null;
  selected_candidate_state: PageCandidate["version_state"] | "NONE";
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
  next_cursor: string | null;
  limit: number;
}

export interface LibraryFilters {
  favorite?: boolean;
  chapter_id?: string;
  character_id?: string;
  generation_kind?: string;
  model_alias?: ImageModelAlias;
  resolution?: Resolution;
  date_from?: string;
  date_to?: string;
  cursor?: string;
  limit?: number;
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

export type WorkflowPortDataType = "text" | "json" | "image" | "asset" | "report" | "boolean";

export interface WorkflowPort {
  id: string;
  label: string;
  data_type: WorkflowPortDataType;
  required: boolean;
}

export interface WorkflowNodeConfig {
  model_alias: string | null;
  prompt_template: string;
  system_instruction: string;
  temperature: number;
  timeout_seconds: number;
  max_attempts: number;
  concurrency: number;
  resolution: Resolution | null;
  locked: boolean;
  notes: string;
  condition: Record<string, unknown>;
  requires_approval: boolean;
}

export interface WorkflowGraphNode {
  id: string;
  type: string;
  name: string;
  position: { x: number; y: number };
  inputs: WorkflowPort[];
  outputs: WorkflowPort[];
  config: WorkflowNodeConfig;
}

export interface WorkflowGraphEdge {
  id: string;
  source_node: string;
  source_port: string;
  target_node: string;
  target_port: string;
}

export interface WorkflowGraph {
  schema_version: 2;
  nodes: WorkflowGraphNode[];
  edges: WorkflowGraphEdge[];
}

export interface WorkflowDefinition {
  id: string;
  project_id: string;
  name: string;
  description: string;
  draft_graph: WorkflowGraph;
  draft_version: number;
  published_version_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface WorkflowValidationIssue {
  severity: "ERROR" | "WARNING";
  code: string;
  message: string;
  node_id: string | null;
  edge_id: string | null;
}

export interface WorkflowValidation {
  valid: boolean;
  issues: WorkflowValidationIssue[];
  topological_order: string[];
}

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  revision: number;
  graph: WorkflowGraph;
  graph_checksum: string;
  validation_report: WorkflowValidation;
  published_at: string;
}

export interface WorkflowNodeType {
  type: string;
  label: string;
  category: "INPUT" | "AGENT" | "CONTROL" | "OUTPUT" | string;
  description: string;
  inputs: WorkflowPort[];
  outputs: WorkflowPort[];
  configurable_fields: string[];
}

export interface WorkflowNodeRun {
  id: string;
  workflow_run_id: string;
  node_id: string;
  node_type: string;
  status: string;
  job_id: string | null;
  input_snapshot: Record<string, unknown>;
  output_refs: Record<string, unknown>;
  attempt_count: number;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface WorkflowRun {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  project_id: string;
  scope_type: "PROJECT" | "CHAPTER" | "PAGE" | "CANDIDATE";
  scope_id: string | null;
  status: string;
  start_node_ids: string[];
  stop_node_ids: string[];
  node_runs: WorkflowNodeRun[];
  created_at: string;
  updated_at: string;
  version: number;
}

export function publicUrl(path: string | null) {
  if (!path) return null;
  const previewPath = path.replace(
    /\/api\/v1\/assets\/([^/]+)\/content$/,
    "/api/v1/assets/$1/thumbnail/640",
  );
  return previewPath.startsWith("http") ? previewPath : `${API_ORIGIN}${previewPath}`;
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
  dashboard: () => request<ProjectDashboard>("/projects/dashboard"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  createProject: (payload: Partial<Project> & { name: string }) =>
    request<Project>("/projects", { method: "POST", body: JSON.stringify(payload) }),
  updateProject: (id: string, payload: Partial<Project> & { version: number }) =>
    request<Project>(`/projects/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteProject: (id: string, confirmName: string) =>
    request<void>(`/projects/${id}?confirm_name=${encodeURIComponent(confirmName)}`, { method: "DELETE" }),
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
  providers: () => request<ProviderProfile[]>("/providers"),
  createProvider: (payload: { name: string; protocol: "OPENAI" | "ANTHROPIC"; base_url: string; use_responses_api: boolean }) =>
    request<ProviderProfile>("/providers", { method: "POST", body: JSON.stringify(payload) }),
  updateProvider: (id: string, payload: { version: number; name?: string; enabled?: boolean }) =>
    request<ProviderProfile>(`/providers/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  updateProviderConnection: (id: string, payload: Partial<Pick<ProviderConnection, "name" | "base_url" | "enabled" | "use_responses_api" | "endpoint_templates" | "extra_headers" | "balance_config" | "nonsecret_config">> & { version: number }) =>
    request<ProviderConnection>(`/providers/connections/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  saveProviderKey: (connectionId: string, label: string, apiKey: string) =>
    request<ProviderKeySummary>(`/providers/connections/${connectionId}/keys`, { method: "PUT", body: JSON.stringify({ label, api_key: apiKey }) }),
  deleteProviderKey: (connectionId: string, keyId: string) =>
    request<void>(`/providers/connections/${connectionId}/keys/${keyId}`, { method: "DELETE" }),
  discoverProviderModels: (connectionId: string) =>
    request<ProviderModel[]>(`/providers/connections/${connectionId}/discover`, { method: "POST" }),
  createProviderModel: (connectionId: string, payload: { provider_model_id: string; display_name?: string; model_type: "TEXT" | "IMAGE"; input_modalities: string[]; output_modalities: string[]; operations: string[]; api_surfaces: string[]; capabilities: Record<string, unknown> }) =>
    request<ProviderModel>(`/providers/connections/${connectionId}/models`, { method: "POST", body: JSON.stringify(payload) }),
  testProviderConnection: (connectionId: string, payload: { test_type: "CREDENTIALS" | "TEXT" | "VISION" | "IMAGE" | "BENCHMARK"; model_id?: string; acknowledge_cost?: boolean; runs?: number }) =>
    request<ModelProbe>(`/providers/connections/${connectionId}/test`, { method: "POST", body: JSON.stringify(payload) }),
  providerBalance: (connectionId: string) =>
    request<{ configured: boolean; value: string | number | null; usage?: string | number | null; currency?: string | null; message: string }>(`/providers/connections/${connectionId}/balance`),
  providerProbes: (connectionId: string) => request<ModelProbe[]>(`/providers/probes?connection_id=${encodeURIComponent(connectionId)}`),
  assets: (projectId: string) => request<Asset[]>(`/assets?project_id=${encodeURIComponent(projectId)}`),
  uploadAsset: (projectId: string, kind: string, file: File) => {
    const data = new FormData();
    data.append("project_id", projectId);
    data.append("kind", kind);
    data.append("file", file);
    return request<Asset>("/assets/upload", { method: "POST", body: data });
  },
  updateAsset: (assetId: string, payload: { kind?: AssetPurpose; display_name?: string | null }) => request<Asset>(`/assets/${assetId}`, {
    method: "PATCH", body: JSON.stringify(payload),
  }),
  adoptGeneratedAssetAsReference: (assetId: string) =>
    request<Asset>(`/assets/${assetId}/adopt-reference`, { method: "POST" }),
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
  deleteScript: (chapterId: string) => request<void>(`/chapters/${chapterId}/script`, { method: "DELETE" }),
  updateScene: (sceneId: string, payload: Partial<Pick<ScriptScene, "location" | "time_label" | "weather" | "purpose" | "emotional_arc">> & { version: number }) =>
    request<ScriptScene>(`/scenes/${sceneId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  updateBeat: (beatId: string, payload: Partial<Pick<ScriptBeat, "action" | "speaker_name" | "dialogue" | "narration" | "subtext" | "emotion" | "importance" | "must_visualize" | "mergeable" | "page_turn_hook">> & { version: number }) =>
    request<ScriptBeat>(`/beats/${beatId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  parseChapter: (chapterId: string) => request<Job>(`/chapters/${chapterId}/parse`, { method: "POST" }),
  planChapter: (chapterId: string, fromPageNumber?: number) => request<{ pages: MangaPage[]; coverage_ratio: number }>(`/chapters/${chapterId}/plan`, {
    method: "POST",
    body: JSON.stringify({ replace_existing: true, from_page_number: fromPageNumber }),
  }),
  pages: (chapterId: string) => request<MangaPage[]>(`/chapters/${chapterId}/pages`),
  pageReadiness: (pageId: string) => request<PageReadiness>(`/pages/${pageId}/readiness`),
  generationWorkbench: (pageId: string) =>
    request<GenerationWorkbench>(`/pages/${pageId}/generation-workbench`),
  storyboard: (pageId: string) => request<Storyboard>(`/pages/${pageId}/storyboard`),
  updatePanel: (panelId: string, payload: Partial<Pick<StoryboardPanel, "shot_type" | "camera_angle" | "camera_height" | "characters" | "character_presence" | "props" | "outfits" | "actions" | "expressions" | "background" | "sound_effects" | "bleed" | "borderless">> & { version: number }) =>
    request<StoryboardPanel>(`/panels/${panelId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  createDialogue: (panelId: string, payload: Pick<PanelDialogue, "target_text" | "speaker_character_id" | "text_direction" | "rewrite_forbidden"> & { panel_version: number }) =>
    request<PanelDialogue>(`/panels/${panelId}/dialogues`, { method: "POST", body: JSON.stringify(payload) }),
  updateDialogue: (dialogueId: string, payload: Partial<Pick<PanelDialogue, "target_text" | "speaker_character_id" | "text_direction" | "rewrite_forbidden">> & { panel_version: number }) =>
    request<PanelDialogue>(`/dialogues/${dialogueId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteDialogue: (dialogueId: string, panelVersion: number) =>
    request<void>(`/dialogues/${dialogueId}`, { method: "DELETE", body: JSON.stringify({ panel_version: panelVersion }) }),
  characters: (projectId: string) => request<Character[]>(`/projects/${projectId}/characters`),
  outfits: (projectId: string) => request<Outfit[]>(`/projects/${projectId}/outfits`),
  createOutfit: (projectId: string, payload: { character_id: string; name: string; reference_asset_ids: string[]; locked_fields: string[] }) =>
    request<Outfit>(`/projects/${projectId}/outfits`, {
      method: "POST", body: JSON.stringify({ ...payload, components: {}, state_rules: {} }),
    }),
  updateOutfit: (outfitId: string, payload: { version: number; name: string; reference_asset_ids: string[]; locked_fields: string[] }) =>
    request<Outfit>(`/outfits/${outfitId}`, {
      method: "PATCH", body: JSON.stringify(payload),
    }),
  deleteOutfit: (outfitId: string) =>
    request<void>(`/outfits/${outfitId}`, { method: "DELETE" }),
  styles: (projectId: string) => request<StyleProfile[]>(`/projects/${projectId}/styles`),
  createStyle: (projectId: string, name: string, colorMode: StyleProfile["color_mode"], referenceAssetIds: string[], lockedFields: string[]) =>
    request<StyleProfile>(`/projects/${projectId}/styles`, {
      method: "POST",
      body: JSON.stringify({ name, color_mode: colorMode, profile: {}, reference_asset_ids: referenceAssetIds, locked_fields: lockedFields }),
    }),
  updateStyleMode: (styleId: string, version: number, colorMode: StyleProfile["color_mode"]) =>
    request<StyleProfile>(`/styles/${styleId}`, {
      method: "PATCH", body: JSON.stringify({ version, color_mode: colorMode }),
    }),
  draftStylePalette: (styleId: string, atmosphere: string) => request<Job>(`/styles/${styleId}/palette-draft`, {
    method: "POST", body: JSON.stringify({ atmosphere }),
  }),
  approveStylePalette: (styleId: string, version: number, palette: Record<string, unknown>) => request<StyleProfile>(`/styles/${styleId}/palette-approve`, {
    method: "POST", body: JSON.stringify({ version, palette }),
  }),
  approveStyleTest: (styleId: string, version: number, candidateId: string, approved = true) => request<StyleProfile>(`/styles/${styleId}/style-test-approve`, {
    method: "POST", body: JSON.stringify({ version, candidate_id: candidateId, approved }),
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
  unbindCharacterReference: (referenceId: string) =>
    request<void>(`/character-references/${referenceId}`, { method: "DELETE" }),
  startAssetBatch: (targetType: "CHARACTER" | "OUTFIT" | "STYLE", targetId: string, generationKind: "CHARACTER" | "OUTFIT" | "STYLE_TEST") =>
    request<GenerationBatch>("/asset-generation-batches", {
      method: "POST",
      body: JSON.stringify({ target_type: targetType, target_id: targetId, generation_kind: generationKind }),
    }),
  assetBatches: (targetType: "CHARACTER" | "OUTFIT" | "STYLE", targetId: string, limit = 10) =>
    request<GenerationBatch[]>(`/asset-generation-batches?target_type=${targetType}&target_id=${targetId}&limit=${limit}`),
  generateAssetCandidate: (batchId: string, model_alias: ImageModelAlias, resolution: Resolution, variant: "FRONT" | "SIDE" | "BACK" | "EXPRESSION" | "SHEET" | "OUTFIT" | "OUTFIT_SHEET" | "STYLE_TEST") =>
    request<{ job_id: string; job_status: string; candidate: PageCandidate }>(`/asset-generation-batches/${batchId}/candidates`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution, variant, instruction: "" }),
    }),
  generateCompleteCharacterSheet: (characterId: string, model_alias: ImageModelAlias, resolution: Resolution, concept?: { appearance_description: string; outfit_name: string; outfit_description: string }) =>
    request<{ job_id: string; job_status: string; candidate: PageCandidate }>(`/characters/${characterId}/complete-sheet`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution, generation_mode: concept ? "CONCEPT" : "REFERENCE", ...concept }),
    }),
  approveAssetReference: (candidateId: string, payload: { character_id: string; bind_character_reference?: boolean; set_canonical?: boolean; outfit_name?: string; outfit_description?: string; outfit_locked_fields?: string[] }) =>
    request<{ candidate_id: string; asset_id: string; character_id: string; outfit_id?: string | null }>(`/asset-candidates/${candidateId}/approve-reference`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  retractAssetReference: (candidateId: string) =>
    request<{ candidate_id: string; approved: boolean }>(`/asset-candidates/${candidateId}/approve-reference`, {
      method: "DELETE",
    }),
  updatePageLayout: (pageId: string, panelCount: number, layoutMode: "dynamic" | "balanced") =>
    request<Storyboard>(`/pages/${pageId}/layout`, {
      method: "PATCH",
      body: JSON.stringify({ panel_count: panelCount, layout_mode: layoutMode }),
    }),
  batches: (pageId: string) => request<GenerationBatch[]>(`/pages/${pageId}/batches`),
  startBatch: (pageId: string) => request<GenerationBatch>(`/pages/${pageId}/batches`, { method: "POST" }),
  candidates: (batchId: string) => request<PageCandidate[]>(`/batches/${batchId}/candidates`),
  generateCandidate: (batchId: string, model_alias: ImageModelAlias, resolution: Resolution, storyboard_version: number, reference_selections: Record<string, { character_asset_id: string | null; outfit_id: string | null; outfit_asset_id: string | null }>) =>
    request<{ job_id: string; job_status: string; candidate: PageCandidate }>(`/batches/${batchId}/candidates`, {
      method: "POST",
      body: JSON.stringify({ model_alias, resolution, storyboard_version, reference_selections }),
    }),
  favoriteCandidate: (candidateId: string, isFavorite: boolean) =>
    request<PageCandidate>(`/candidates/${candidateId}/favorite`, {
      method: "PATCH",
      body: JSON.stringify({ is_favorite: isFavorite }),
    }),
  deleteCandidate: (candidateId: string) => request<void>(`/candidates/${candidateId}`, { method: "DELETE" }),
  selectCandidate: (pageId: string, candidateId: string, manualTextConfirmed = false, acceptStale = false) =>
    request<MangaPage>(`/pages/${pageId}/select-candidate`, {
      method: "POST",
      body: JSON.stringify({ candidate_id: candidateId, manual_text_confirmed: manualTextConfirmed, accept_stale: acceptStale }),
    }),
  keepSelectedCandidate: (pageId: string, candidateId: string, storyboardVersion: number) =>
    request<MangaPage>(`/pages/${pageId}/selected-candidate/keep`, {
      method: "POST",
      body: JSON.stringify({
        candidate_id: candidateId,
        storyboard_version: storyboardVersion,
        manual_text_confirmed: true,
      }),
    }),
  retractSelectedCandidate: (pageId: string) =>
    request<MangaPage>(`/pages/${pageId}/selected-candidate`, { method: "DELETE" }),
  nextPage: (pageId: string) => request<MangaPage>(`/pages/${pageId}/next`, { method: "POST" }),
  library: (projectId: string, filters: LibraryFilters = {}) => {
    const query = new URLSearchParams({ group_by: "batch" });
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== undefined && value !== "") query.set(key, String(value));
    });
    return request<Library>(`/projects/${projectId}/library?${query.toString()}`);
  },
  jobs: (projectId: string, archived = false) => request<Job[]>(`/projects/${projectId}/jobs?archived=${archived}`),
  cancelJob: (jobId: string) => request<Job>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  retryJob: (jobId: string) => request<Job>(`/jobs/${jobId}/retry`, { method: "POST" }),
  archiveJob: (jobId: string) => request<Job>(`/jobs/${jobId}/archive`, { method: "POST" }),
  restoreJob: (jobId: string) => request<Job>(`/jobs/${jobId}/restore`, { method: "POST" }),
  archiveCompletedJobs: (projectId: string) => request<{ archived_count: number }>(`/projects/${projectId}/jobs/archive-completed`, { method: "POST" }),
  bulkArchiveJobs: (projectId: string, jobIds: string[]) =>
    request<{ archived_count: number }>(`/projects/${projectId}/jobs/bulk-archive`, {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds }),
    }),
  deleteJob: (jobId: string) => request<void>(`/jobs/${jobId}`, { method: "DELETE" }),
  inspectCandidate: (candidateId: string) => request<Job>(`/candidates/${candidateId}/inspect`, {
    method: "POST",
    body: JSON.stringify({ categories: ["SPEAKER", "CHARACTER", "OUTFIT", "PROP", "CONTINUITY"] }),
  }),
  inspections: (candidateId: string) => request<InspectionResult[]>(`/candidates/${candidateId}/inspections`),
  repairCandidate: (
    candidateId: string,
    payload: {
      inspection_result_id: string;
      repair_type: "BUBBLE_REGION" | "PANEL" | "PAGE";
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
  workflowNodeTypes: () => request<WorkflowNodeType[]>("/workflow-node-types"),
  workflows: (projectId: string) => request<WorkflowDefinition[]>(`/projects/${projectId}/workflows`),
  createWorkflow: (projectId: string, name = "默认漫画工作流", template: "manga_default" | "blank" = "manga_default") =>
    request<WorkflowDefinition>(`/projects/${projectId}/workflows`, {
      method: "POST",
      body: JSON.stringify({ name, template, description: "" }),
    }),
  importWorkflow: (projectId: string, payload: { name: string; description?: string; graph: WorkflowGraph }) =>
    request<WorkflowDefinition>(`/projects/${projectId}/workflows/import`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateWorkflow: (workflowId: string, version: number, payload: Partial<Pick<WorkflowDefinition, "name" | "description" | "draft_graph" | "is_active">>) =>
    request<WorkflowDefinition>(`/workflows/${workflowId}`, {
      method: "PATCH",
      body: JSON.stringify({ ...payload, version }),
    }),
  validateWorkflow: (workflowId: string) => request<WorkflowValidation>(`/workflows/${workflowId}/validate`, { method: "POST" }),
  publishWorkflow: (workflowId: string) => request<WorkflowVersion>(`/workflows/${workflowId}/publish`, { method: "POST" }),
  workflowVersions: (workflowId: string) => request<WorkflowVersion[]>(`/workflows/${workflowId}/versions`),
  restoreWorkflowVersion: (versionId: string, version: number) => request<WorkflowDefinition>(`/workflow-versions/${versionId}/restore`, {
    method: "POST",
    body: JSON.stringify({ version }),
  }),
  workflowRuns: (workflowId: string) => request<WorkflowRun[]>(`/workflows/${workflowId}/runs`),
  startWorkflowRun: (workflowId: string, payload: { scope_type: WorkflowRun["scope_type"]; scope_id: string | null; start_node_ids?: string[]; stop_node_ids?: string[] }) =>
    request<WorkflowRun>(`/workflows/${workflowId}/runs`, {
      method: "POST",
      body: JSON.stringify({ ...payload, start_node_ids: payload.start_node_ids ?? [], stop_node_ids: payload.stop_node_ids ?? [] }),
    }),
  workflowRun: (runId: string) => request<WorkflowRun>(`/workflow-runs/${runId}`),
  cancelWorkflowRun: (runId: string) => request<WorkflowRun>(`/workflow-runs/${runId}/cancel`, { method: "POST" }),
  retryWorkflowRun: (runId: string) => request<WorkflowRun>(`/workflow-runs/${runId}/retry`, { method: "POST" }),
  approveWorkflowNode: (
    runId: string,
    nodeId: string,
    payload: {
      candidate_id?: string | null;
      image_model_alias?: ImageModelAlias | null;
      resolution?: Resolution | null;
    } = {},
  ) => request<WorkflowRun>(`/workflow-runs/${runId}/nodes/${nodeId}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  }),
};
