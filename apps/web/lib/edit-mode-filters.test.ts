import { describe, expect, it } from "vitest";

import type { ModelCapability } from "@/lib/api";
import { filterModels } from "@/components/provider-settings/provider-filters";
import { editModeSupported, EDIT_MODE_LABELS } from "./local-edit-rules";

function modelFixture(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    catalog_id: "catalog-1",
    connection_id: "connection-1",
    provider: "p",
    protocol: "OPENAI",
    model_id: "m",
    logical_alias: "m",
    display_name: "m",
    model_type: "IMAGE",
    input_modalities: ["TEXT", "IMAGE"],
    output_modalities: ["IMAGE"],
    operations: ["image_generate", "image_edit"],
    resolutions: ["1K"],
    preview_resolutions: [],
    max_reference_images: 1,
    regions: ["global"],
    confidence: "DECLARED",
    enabled: true,
    display_enabled: true,
    auto_eligible: false,
    priority: 50,
    ...overrides,
  };
}

describe("P0-1 editModeSupported", () => {
  it("结构化 edit_modes 声明优先于旧布尔位", () => {
    const model = modelFixture({
      accepts_explicit_mask: true,
      edit_modes: { mask: { supported: false, source: "DECLARED" } },
    });
    expect(editModeSupported(model, "mask")).toBe(false);
    expect(editModeSupported(model, "whole_image_reference")).toBe(false);
  });

  it("缺省 edit_modes 回退到旧位，图中文字无旧位按不支持", () => {
    const model = modelFixture({
      accepts_explicit_mask: true,
      whole_image_reference_only: true,
    });
    expect(editModeSupported(model, "mask")).toBe(true);
    expect(editModeSupported(model, "whole_image_reference")).toBe(true);
    expect(editModeSupported(model, "in_image_text_edit")).toBe(false);
  });

  it("完全未声明的模型 fail-closed", () => {
    const model = modelFixture({ accepts_explicit_mask: undefined });
    expect(editModeSupported(model, "mask")).toBe(false);
    expect(editModeSupported(model, "whole_image_reference")).toBe(false);
    expect(EDIT_MODE_LABELS.mask).toContain("mask");
  });
});

describe("P0-1 filterModels 编辑模式筛选", () => {
  const maskModel = modelFixture({
    logical_alias: "mask-model",
    catalog_id: "mask-1",
    edit_modes: { mask: { supported: true, source: "DECLARED" } },
  });
  const textEditModel = modelFixture({
    logical_alias: "text-edit",
    catalog_id: "text-1",
    edit_modes: { in_image_text_edit: { supported: true, source: "VERIFIED" } },
  });
  const undeclared = modelFixture({ logical_alias: "plain", catalog_id: "plain-1" });
  const all = [maskModel, textEditModel, undeclared];

  it("edit_mask 只保留声明 mask 支持的模型", () => {
    const result = filterModels(all, {
      modelType: "ALL",
      capability: "edit_mask",
      verifiedOnly: false,
      showHidden: true,
    });
    expect(result.map((m) => m.logical_alias)).toEqual(["mask-model"]);
  });

  it("edit_in_image_text 筛图中文字能力，未声明被排除", () => {
    const result = filterModels(all, {
      modelType: "ALL",
      capability: "edit_in_image_text",
      verifiedOnly: false,
      showHidden: true,
    });
    expect(result.map((m) => m.logical_alias)).toEqual(["text-edit"]);
  });

  it("旧操作筛选不受影响", () => {
    const result = filterModels(all, {
      modelType: "ALL",
      capability: "image_edit",
      verifiedOnly: false,
      showHidden: true,
    });
    expect(result).toHaveLength(3);
  });
});
