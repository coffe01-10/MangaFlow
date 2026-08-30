const CONFIDENCE_LABELS: Record<string, string> = {
  MANUAL: "待验证",
  DECLARED: "待验证",
  INFERRED: "推断/待验证",
  PARTIAL: "部分验证",
  VERIFIED: "已验证",
};

const HEALTH_LABELS: Record<string, string> = {
  HEALTHY: "健康",
  DEGRADED: "降级",
  UNKNOWN: "未知",
  UNCONFIGURED: "未配置",
  CHECKING: "检查中",
  OFFLINE: "离线",
};

const CATEGORY_LABELS: Record<string, string> = {
  OFFICIAL: "官方",
  GATEWAY: "网关",
  THIRD_PARTY: "第三方",
  CUSTOM: "自定义",
  compatible: "兼容协议",
};

const RISK_LABELS: Record<string, string> = {
  LOW: "低风险",
  OFFICIAL: "官方",
  GATEWAY: "网关",
  THIRD_PARTY: "第三方",
  CUSTOM: "自定义",
  HIGH: "高风险",
};

const OPERATION_LABELS: Record<string, string> = {
  structured_text: "结构化文本",
  multimodal_analysis: "视觉理解",
  image_generate: "图片生成",
  image_edit: "图片编辑",
};

export function mapConfidence(value: string | null | undefined): string {
  if (!value) return "未知";
  return CONFIDENCE_LABELS[value] ?? "未知";
}

export function mapHealth(value: string | null | undefined): string {
  if (!value) return "未知";
  return HEALTH_LABELS[value] ?? "未知";
}

export function mapCategory(value: string | null | undefined): string {
  if (!value) return "";
  return CATEGORY_LABELS[value] ?? value;
}

export function mapRisk(value: string | null | undefined): string {
  if (!value) return "";
  return RISK_LABELS[value] ?? value;
}

export function mapOperation(value: string): string {
  return OPERATION_LABELS[value] ?? value;
}

export function mapOptimisticConflict(
  message: string,
  entity: "provider" | "connection",
): string {
  if (message.includes("已更新") || message.includes("刷新后重试")) {
    return entity === "provider"
      ? "供应商已在别处更新，请重新加载"
      : "连接已在别处更新，请重新加载";
  }
  return message;
}

export function classifyCreateError(message: string): "name" | "url" {
  if (/URL|地址|HTTPS|http/i.test(message)) return "url";
  return "name";
}
