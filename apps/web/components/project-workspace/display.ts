import type { Asset } from "@/lib/api";

export function recommendedRepairType(category: string): "BUBBLE_REGION" | "PANEL" | "PAGE" {
  if (category === "SPEAKER") return "BUBBLE_REGION";
  if (["CHARACTER", "OUTFIT", "PROP"].includes(category)) return "PANEL";
  return "PAGE";
}

export function inspectionSummary(details: Record<string, unknown>) {
  const expected = typeof details.expected === "string" ? details.expected : "";
  const observed = typeof details.observed === "string" ? details.observed : "";
  if (expected || observed) return [expected && `应为：${expected}`, observed && `实为：${observed}`].filter(Boolean).join("；");
  return Object.entries(details).map(([key, value]) => `${key}: ${String(value)}`).join("；") || "模型未补充说明";
}

export function inspectionBubbleDiffs(details: Record<string, unknown>) {
  if (!Array.isArray(details.bubble_diffs)) return [];
  return details.bubble_diffs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
}

export function formatBytes(value: number) {
  if (value < 1024 * 1024) return `${Math.ceil(value / 1024)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function assetName(asset: Asset | undefined) {
  return asset?.display_name?.trim() || asset?.original_name || "未命名素材";
}

export function promptPreview(candidate: { prompt_snapshot: Record<string, unknown> }) {
  return typeof candidate.prompt_snapshot.prompt_preview === "string"
    ? candidate.prompt_snapshot.prompt_preview
    : "任务排队后会在这里保存本次实际提示词。";
}

export function queueStatsOf(jobs: { status: string }[]) {
  return {
    waiting: jobs.filter((item) => ["WAITING", "QUEUED"].includes(item.status)).length,
    failed: jobs.filter((item) => item.status === "FAILED").length,
  };
}
