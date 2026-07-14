const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

export type Resolution = "1K" | "2K" | "4K";
export type WorkflowMode = "AUTO" | "DIRECTOR" | "SEMI_AUTO";

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
  ocr_enabled: boolean;
  consistency_check_enabled: boolean;
  text_model_alias: string;
  image_model_alias: string;
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
  credential_file_present: boolean;
  location: string;
  text_model: string;
  image_models: string[];
  verification: "not_run" | "verified";
  message: string;
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
    throw new Error(body.detail ?? "请求失败");
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
  vertexStatus: () => request<VertexStatus>("/models/vertex/status"),
  verifyVertex: () => request<VertexStatus>("/models/vertex/verify", { method: "POST" }),
  assets: (projectId: string) => request<Asset[]>(`/assets?project_id=${encodeURIComponent(projectId)}`),
  uploadAsset: (projectId: string, kind: string, file: File) => {
    const data = new FormData();
    data.append("project_id", projectId);
    data.append("kind", kind);
    data.append("file", file);
    return request<Asset>("/assets/upload", { method: "POST", body: data });
  },
};
