import {
  BookOpenText,
  Clapperboard,
  LibraryBig,
  ListTodo,
  PanelTop,
  Sparkles,
  Users,
} from "lucide-react";

import type { AssetPurpose } from "@/lib/api";

import type { AssetWorkspaceView } from "./types";

export const navigationItems = [
  ["source", "原作与修订", "导入、修改、撤回", "01", BookOpenText],
  ["assets", "参考资产", "人物 / 服装 / 场景 / 风格", "02", Users],
  ["script", "漫画剧本", "场景、情节拍、对白", "03", Clapperboard],
  ["storyboard", "分页与分镜", "场景切页、格子脚本", "04", PanelTop],
  ["generate", "单页生成", "抽卡、收藏、采用", "05", Sparkles],
  ["library", "生成素材库", "按类型和批次归档", "06", LibraryBig],
  ["jobs", "任务中心", "进度、失败、取消重试", "07", ListTodo],
] as const;

export const kinds = [
  ["CHARACTER_REFERENCE", "人物参考"],
  ["OUTFIT_REFERENCE", "服装参考"],
  ["STYLE_REFERENCE", "漫画风格"],
  ["SCENE_REFERENCE", "场景参考"],
] as const;

export const assetKindByView: Record<Exclude<AssetWorkspaceView, "references">, AssetPurpose> = {
  characters: "CHARACTER_REFERENCE",
  outfits: "OUTFIT_REFERENCE",
  scenes: "SCENE_REFERENCE",
  style: "STYLE_REFERENCE",
};

export const jobLabels: Record<string, string> = {
  SOURCE_PARSE: "解析剧本", PAGE_GENERATE: "生成页面", PAGE_REPAIR: "修复页面",
  PAGE_UPSCALE: "保持结构升清", ASSET_GENERATE: "生成角色/服装素材",
  PAGE_INSPECT: "检查页面", STYLE_ANALYZE: "分析漫画风格",
  WORKFLOW_NODE: "执行工作流节点",
};

export const generationKindLabels: Record<string, string> = {
  PAGE: "页面抽卡",
  REPAIR: "页面修复",
  CHARACTER: "角色形象补全",
  OUTFIT: "角色服装形象",
  STYLE_TEST: "漫画风格测试",
  UPSCALE: "保持结构升清",
};

export const inspectionLabels: Record<string, string> = {
  TEXT: "文字",
  SPEAKER: "说话人",
  CHARACTER: "角色",
  OUTFIT: "服装",
  PROP: "道具",
  CONTINUITY: "连续性",
};

export const repairTypeLabels = {
  BUBBLE_REGION: "气泡区域",
  PANEL: "单格",
  PAGE: "整页",
} as const;

// Status enum → Chinese label (values verified against apps/api domain/states.py
// and status writers). Unknown values fall back to the raw enum at the call
// site (`label ?? value`) so new backend statuses stay debuggable.
export const jobStatusLabels: Record<string, string> = {
  WAITING: "等待中",
  QUEUED: "排队中",
  PREPARING: "准备中",
  UPLOADING_REFERENCES: "上传参考图",
  GENERATING: "生成中",
  OCR_CHECKING: "文字检查",
  CONSISTENCY_CHECKING: "连续性检查",
  REPAIRING: "修复中",
  COMPLETED: "已完成",
  FAILED: "已失败",
  CANCELLED: "已取消",
  NEEDS_REVIEW: "待复核",
};

export const candidateStatusLabels: Record<string, string> = {
  QUEUED: "排队中",
  GENERATING: "生成中",
  READY: "已就绪",
  STALE: "已过期",
  INSPECTED: "已检查",
  NEEDS_REVIEW: "待复核",
  FAILED: "已失败",
  CANCELLED: "已取消",
};

export const candidateVersionStateLabels: Record<string, string> = {
  CURRENT: "当前版本",
  STALE: "分镜已更新",
  STALE_ACCEPTED: "已采用但分镜过期",
  LEGACY_UNKNOWN: "版本未知",
};

/** Workflow run / node-run statuses. Wording stays aligned with
 * `jobStatusLabels` for overlapping values so the workflow footer and the
 * jobs panel never label the same state two different ways. */
export const workflowRunStatusLabels: Record<string, string> = {
  WAITING: "等待中",
  RUNNING: "运行中",
  COMPLETED: "已完成",
  WAITING_APPROVAL: "等待确认",
  FAILED: "已失败",
  SKIPPED: "已跳过",
  CANCELLED: "已取消",
};

/** Provider / job error codes surfaced raw by the API; the label keeps the
 * code prefix visible for debugging while adding the human explanation.
 * Codes verified against apps/api ProviderAdapterError writers. */
export const errorCodeLabels: Record<string, string> = {
  MODEL_ROUTE_UNAVAILABLE: "没有已验证且满足任务能力的模型",
  MODEL_NOT_FOUND: "模型不存在或已下线",
  AUTHENTICATION: "供应商凭据验证失败",
  UNAUTHENTICATED: "CLI 登录已失效，请重新登录",
  CLI_LOGIN_UNKNOWN: "CLI 登录状态未知，请先运行登录",
  CLI_VERSION_FAILED: "CLI 版本检查失败",
  CLI_VERSION_UNKNOWN: "无法确认 CLI 版本",
  PERMISSION: "供应商账号权限不足",
  RATE_LIMIT: "供应商限流，请稍后重试",
  TIMEOUT: "请求超时",
  CONCURRENCY_LIMIT: "并发上限已满，请稍后重试",
  UNAVAILABLE: "供应商连接不可用",
  NOT_CONFIGURED: "供应商凭据尚未配置",
  CREDENTIAL_FILE_MISSING: "服务端凭据文件缺失",
  CONFIGURATION: "连接配置有误",
  UNSUPPORTED: "当前连接不支持该操作",
  INVALID_INPUT: "请求参数不符合供应商要求",
  INVALID_OUTPUT: "供应商响应格式异常",
  INVALID_NODE_OUTPUT: "工作流节点输出不符合要求",
  PARTIAL_OUTPUT: "供应商只返回了部分结果",
  UNKNOWN_RESULT: "任务结果未知，请重试",
  CRASH: "执行进程异常退出",
  LEASE_EXPIRED: "任务租约过期，已回到队列",
  MISSING_CANDIDATE: "找不到对应的生成候选",
  UPSTREAM: "上游服务异常",
  QUEUE_UNAVAILABLE: "队列服务不可用",
  QUEUE_DISABLED: "队列已停用",
  OUTPUT_ATTACHMENT_FAILED: "生成结果保存失败",
  LOCAL_WORKER: "本地执行器",
};

export const chapterStatusLabels: Record<string, string> = {
  IMPORTED: "已导入",
  SCRIPT_READY: "剧本已生成",
  SCRIPT_INCOMPLETE: "剧本不完整",
  PAGES_PLANNED: "已分页",
};

export const assetStatusLabels: Record<string, string> = {
  UPLOADED: "已上传",
  ANALYZED: "已分析",
  GENERATED: "已生成",
  NEEDS_CONFIRMATION: "待确认",
  CANONICAL: "已定稿",
  ARCHIVED: "已归档",
};

export const styleStatusLabels: Record<string, string> = {
  ANALYZING: "分析中",
  DRAFT: "草稿",
  TEST_GENERATED: "测试图已生成",
  CONFIRMED: "已确认",
  ACTIVE: "使用中",
};

export const scriptStatusLabels: Record<string, string> = {
  NOT_CREATED: "未创建",
  DRAFT: "生成中",
  PROCESSING: "生成中",
  READY: "已生成",
  INCOMPLETE: "不完整",
};

export const inspectionOutcomeLabels: Record<string, string> = {
  PASS: "通过",
  ACCEPTABLE: "可接受",
  MATCH: "匹配",
  MISMATCH: "不匹配",
  MISSING: "画面缺失",
  EXTRA: "画面多余",
};

export const productionStateLabels: Record<string, string> = {
  READY: "已通过",
  NEEDS_REPAIR: "待修复",
  STALE: "分镜已更新",
  AWAITING_INSPECTION: "待视觉检查",
  AWAITING_SELECTION: "待暂选",
};
