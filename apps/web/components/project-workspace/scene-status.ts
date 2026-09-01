export const SCENE_ASSET_STATUSES = [
  "UPLOADED",
  "ANALYZED",
  "GENERATED",
  "NEEDS_CONFIRMATION",
  "CANONICAL",
  "ARCHIVED",
] as const;

export type KnownSceneAssetStatus = (typeof SCENE_ASSET_STATUSES)[number];

export function isKnownSceneAssetStatus(status: string | null | undefined): status is KnownSceneAssetStatus {
  return Boolean(status && (SCENE_ASSET_STATUSES as readonly string[]).includes(status));
}

export function sceneAssetStatusMeta(status: string | null | undefined) {
  switch (status) {
    case "UPLOADED":
      return { label: "已上传", ready: false, tone: "pending" as const };
    case "ANALYZED":
      return { label: "已分析", ready: false, tone: "pending" as const };
    case "GENERATED":
      return { label: "已生成", ready: false, tone: "pending" as const };
    case "NEEDS_CONFIRMATION":
      return { label: "待确认 · 尚未设置规范参考图", ready: false, tone: "pending" as const };
    case "CANONICAL":
      return { label: "已就绪 · 可直接用于剧本与分镜", ready: true, tone: "ready" as const };
    case "ARCHIVED":
      return { label: "已归档", ready: false, tone: "archived" as const };
    default:
      return { label: "状态未知", ready: false, tone: "unknown" as const };
  }
}

export function interiorLabel(interior: boolean | null | undefined) {
  if (interior === true) return "室内";
  if (interior === false) return "室外";
  return "空间未指定";
}
