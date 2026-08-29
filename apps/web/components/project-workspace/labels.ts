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
  ["assets", "参考资产", "人物 / 服装 / 风格", "02", Users],
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
] as const;

export const assetKindByView: Record<Exclude<AssetWorkspaceView, "references">, AssetPurpose> = {
  characters: "CHARACTER_REFERENCE",
  outfits: "OUTFIT_REFERENCE",
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
