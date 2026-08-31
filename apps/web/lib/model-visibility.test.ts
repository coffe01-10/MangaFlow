import { describe, expect, it } from "vitest";

import type { ModelCapability } from "@/lib/api";

import { creatorVisibleModels } from "./model-visibility";

function model(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    catalog_id: "catalog-visible",
    connection_id: "connection-1",
    provider: "provider",
    protocol: "OPENAI_COMPATIBLE",
    model_id: "provider-model",
    logical_alias: "text.visible",
    display_name: "Visible model",
    model_type: "TEXT",
    input_modalities: ["text"],
    output_modalities: ["text"],
    operations: ["structured_text"],
    resolutions: [],
    preview_resolutions: [],
    max_reference_images: 0,
    regions: [],
    confidence: "VERIFIED",
    enabled: true,
    display_enabled: true,
    auto_eligible: true,
    priority: 100,
    ...overrides,
  };
}

describe("creatorVisibleModels", () => {
  it("只把已启用且允许展示的模型作为新选择", () => {
    const hidden = model({ catalog_id: "hidden", logical_alias: "text.hidden", display_enabled: false });
    const unavailable = model({ catalog_id: "unavailable", logical_alias: "text.unavailable", enabled: false });

    expect(creatorVisibleModels([model(), hidden, unavailable]).map((item) => item.catalog_id)).toEqual([
      "catalog-visible",
    ]);
  });

  it("按目录 ID 或逻辑别名保留当前已选模型", () => {
    const hiddenProjectModel = model({ catalog_id: "hidden-project", logical_alias: "text.project", display_enabled: false });
    const hiddenWorkflowModel = model({ catalog_id: "hidden-workflow", logical_alias: "text.workflow", display_enabled: false });

    expect(creatorVisibleModels([hiddenProjectModel, hiddenWorkflowModel], {
      catalogIds: ["hidden-project"],
      logicalAliases: ["text.workflow"],
    })).toEqual([hiddenProjectModel, hiddenWorkflowModel]);
  });
});
