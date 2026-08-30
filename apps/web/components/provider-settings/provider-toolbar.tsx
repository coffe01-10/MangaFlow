"use client";

import { ArrowDownAZ, Plus } from "lucide-react";

import type { CapabilityFilter, ModelTypeFilter, ProviderSort } from "./provider-filters";

export function ProviderToolbar({
  filter,
  onFilterChange,
  onJump,
  matchCount,
  modelType,
  onModelTypeChange,
  capability,
  onCapabilityChange,
  verifiedOnly,
  onVerifiedOnlyChange,
  sort,
  onSortChange,
  createOpen,
  onToggleCreate,
}: {
  filter: string;
  onFilterChange: (value: string) => void;
  onJump: () => void;
  matchCount: number;
  modelType: ModelTypeFilter;
  onModelTypeChange: (value: ModelTypeFilter) => void;
  capability: CapabilityFilter;
  onCapabilityChange: (value: CapabilityFilter) => void;
  verifiedOnly: boolean;
  onVerifiedOnlyChange: (value: boolean) => void;
  sort: ProviderSort;
  onSortChange: (value: ProviderSort) => void;
  createOpen: boolean;
  onToggleCreate: () => void;
}) {
  return (
    <div className="provider-toolbar">
      <input
        className="provider-toolbar-search"
        aria-label="筛选供应商"
        value={filter}
        onChange={(event) => onFilterChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            if (matchCount > 0) onJump();
          }
        }}
        placeholder="搜索供应商、协议或模型 ID"
      />
      <div className="provider-toolbar-filters">
        <div className="provider-filter-pills" role="group" aria-label="模型类型">
          <button
            type="button"
            aria-pressed={modelType === "TEXT"}
            onClick={() => onModelTypeChange(modelType === "TEXT" ? "ALL" : "TEXT")}
          >
            文字
          </button>
          <button
            type="button"
            aria-pressed={modelType === "IMAGE"}
            onClick={() => onModelTypeChange(modelType === "IMAGE" ? "ALL" : "IMAGE")}
          >
            图片
          </button>
        </div>
        <label>
          <span>能力</span>
          <select
            aria-label="能力筛选"
            value={capability}
            onChange={(event) => onCapabilityChange(event.target.value as CapabilityFilter)}
          >
            <option value="ALL">全部能力</option>
            <option value="structured_text">结构化文本</option>
            <option value="multimodal_analysis">视觉理解</option>
            <option value="image_generate">图片生成</option>
            <option value="image_edit">图片编辑</option>
          </select>
        </label>
        <label className="provider-check" title="自动路由只使用已验证模型">
          <input
            type="checkbox"
            checked={verifiedOnly}
            onChange={(event) => onVerifiedOnlyChange(event.target.checked)}
          />
          仅已验证
        </label>
        <label>
          <ArrowDownAZ size={14} />
          <span>排序</span>
          <select
            aria-label="供应商排序"
            title="已配置、健康、有模型的靠前"
            value={sort}
            onChange={(event) => onSortChange(event.target.value as ProviderSort)}
          >
            <option value="RECOMMENDED">推荐</option>
            <option value="NAME">名称</option>
            <option value="HEALTH">健康</option>
            <option value="MODELS">模型数量</option>
            <option value="LATENCY">延迟</option>
          </select>
        </label>
        <button type="button" disabled={matchCount === 0} onClick={onJump}>跳到结果</button>
        <button
          type="button"
          aria-expanded={createOpen}
          aria-controls="provider-create-panel"
          onClick={onToggleCreate}
        >
          <Plus size={14} />添加供应商
        </button>
      </div>
    </div>
  );
}
