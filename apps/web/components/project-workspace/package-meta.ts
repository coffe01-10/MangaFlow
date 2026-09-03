import type { PackageRole, PackageVersionStatus } from "@/lib/api";

export function packageVersionStatusMeta(status: PackageVersionStatus | string): {
  label: string;
  tone: "ready" | "pending" | "archived" | "unknown";
} {
  switch (status) {
    case "DRAFT":
      return { label: "草稿", tone: "pending" };
    case "READY":
      return { label: "已发布", tone: "ready" };
    case "IN_PRODUCTION":
      return { label: "生产使用中", tone: "ready" };
    case "ARCHIVED":
      return { label: "已归档", tone: "archived" };
    default:
      return { label: status, tone: "unknown" };
  }
}

export const PACKAGE_ROLE_LABELS: Record<PackageRole | string, string> = {
  cover: "封面",
  front: "正面（主视）",
  side: "右侧面",
  back: "背面",
  three_quarter: "3/4 侧面",
  expression: "表情",
  pose: "姿态",
  extra: "补充",
};

/** Core five slots are the only label-free roles (contract §4.3). */
export function isCorePackageRole(role: string): boolean {
  return ["cover", "front", "side", "back", "three_quarter"].includes(role);
}

export function packageRoleLabel(role: string, label?: string): string {
  const base = PACKAGE_ROLE_LABELS[role] ?? role;
  return label ? `${base} · ${label}` : base;
}
