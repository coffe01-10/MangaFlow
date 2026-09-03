"use client";

import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";

import { api } from "@/lib/api";

import { packageVersionStatusMeta } from "./package-meta";

/**
 * Per-character package version picker for the generate workbench (contract
 * §8.1/§4.6): the storyboard never stores a version pointer; selection only
 * happens at the candidate layer via `reference_selections.package_version_id`.
 * Empty value = default inheritance (latest published version resolved
 * server-side); characters without a package stay on the legacy path.
 */
export function CharacterPackagePicker({
  projectId,
  characterId,
  characterName,
  value,
  onChange,
}: {
  projectId: string;
  characterId: string;
  characterName: string;
  value: string | null;
  onChange: (versionId: string | null) => void;
}) {
  const summaries = useQuery({
    queryKey: ["character-packages", projectId],
    queryFn: () => api.characterPackagesAll(projectId),
  });
  const summary = (summaries.data ?? []).find((item) => item.character_id === characterId) ?? null;

  const detail = useQuery({
    queryKey: ["character-package", projectId, characterId],
    queryFn: () => api.characterPackage(projectId, characterId),
    enabled: Boolean(summary),
  });

  if (summaries.isLoading || (summary && detail.isLoading)) {
    return <span className="pkg-picker-loading"><LoaderCircle className="spin" size={12} />正在读取角色模型包…</span>;
  }
  if (!summary) {
    return <span className="pkg-picker-empty">未启用角色模型包（沿用人物参考图路径）</span>;
  }
  if (detail.isError || !detail.data) {
    return <span className="pkg-picker-empty">角色模型包暂不可用</span>;
  }
  const selectable = detail.data.versions.filter((item) => item.status !== "DRAFT");
  return (
    <label className="pkg-picker">
      <span>角色模型包版本</span>
      <select
        aria-label={`${characterName}的角色模型包版本`}
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">{summary.published_version_number ? `默认：使用最新发布版本（V${summary.published_version_number}）` : "不指定版本（沿用人物参考图路径）"}</option>
        {selectable.map((version) => (
          <option key={version.id} value={version.id}>
            V{version.version_number} · {packageVersionStatusMeta(version.status).label}{version.id === detail.data?.published_version_id ? " · 当前发布版本" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
