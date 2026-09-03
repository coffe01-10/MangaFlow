import { describe, expect, it } from "vitest";

import type { ModelCapability } from "@/lib/api";
import {
  buildRegionRegenerateEnvelope,
  candidateMatchesCommand,
  derivedCandidatePhase,
  eraseRegions,
  extendStroke,
  formatMaskArea,
  LOCAL_EDIT_HISTORY_LIMIT,
  LOCAL_EDIT_MAX_POINTS,
  LOCAL_EDIT_MAX_REGIONS,
  localEditGate,
  maskAreaRatio,
  maskCapableModels,
  maskCapabilityNotice,
  pointInPolygon,
  pushMaskHistory,
  rectRegion,
  redoMask,
  simplifyStroke,
  strokeToRegion,
  undoMask,
  type MaskHistoryState,
  type MaskPoint,
  type MaskRegion,
} from "./local-edit-rules";

function modelFixture(overrides: Partial<ModelCapability> = {}): ModelCapability {
  return {
    catalog_id: "catalog-1",
    connection_id: "connection-1",
    provider: "Gemini 3.7",
    protocol: "gemini",
    model_id: "gemini-image",
    logical_alias: "gemini_image",
    display_name: "Gemini Image",
    model_type: "IMAGE",
    input_modalities: ["TEXT", "IMAGE"],
    output_modalities: ["IMAGE"],
    operations: ["image_gen", "image_edit"],
    resolutions: ["1K", "2K"],
    preview_resolutions: [],
    max_reference_images: 3,
    regions: ["global"],
    accepts_explicit_mask: true,
    confidence: "VERIFIED",
    enabled: true,
    display_enabled: true,
    auto_eligible: true,
    priority: 50,
    ...overrides,
  };
}

const rect: MaskRegion = {
  points: [[10, 10], [110, 10], [110, 110], [10, 110]],
};

describe("local-edit-rules mask 数学", () => {
  it("矩形拖拽产出 4 点多边形且钳制在图像内", () => {
    const region = rectRegion([20, 30], [-5, 2000], 1024, 1024);
    expect(region.points).toHaveLength(4);
    expect(region.points[0]).toEqual([0, 30]);
    expect(region.points[2]).toEqual([20, 1024]);
  });

  it("Shift 正方形：矩形按长边取方", () => {
    const region = rectRegion([0, 0], [100, 40], 1024, 1024, true);
    const width = region.points[2][0] - region.points[0][0];
    const height = region.points[2][1] - region.points[0][1];
    expect(width).toBe(100);
    expect(height).toBe(100);
  });

  it("画笔 stroke：≥3 点成为带笔径的闭合多边形，单击退化为画笔直径小方块", () => {
    const stroke: MaskPoint[] = [[10, 10], [50, 10], [50, 50], [10, 50]];
    const region = strokeToRegion(stroke, 20, 1024, 1024);
    expect(region?.points.length).toBeGreaterThanOrEqual(6);
    expect(maskAreaRatio([region!], 1024, 1024)).toBeGreaterThan(0);
    const dab = strokeToRegion([[500, 500]], 20, 1024, 1024);
    expect(dab).not.toBeNull();
    const area = maskAreaRatio([dab!], 1024, 1024) * 1024 * 1024;
    expect(area).toBeCloseTo(400, 0);
  });

  it("stroke 采样不超过 64 点上限", () => {
    const long: MaskPoint[] = Array.from({ length: 500 }, (_, index) => [index, index % 7] as MaskPoint);
    expect(simplifyStroke(long)).toHaveLength(LOCAL_EDIT_MAX_POINTS);
    expect(simplifyStroke(long).at(-1)).toEqual(long.at(-1));
  });

  it("擦除只移除笔画覆盖到的选区块", () => {
    const far: MaskRegion = { points: [[800, 800], [900, 800], [900, 900], [800, 900]] };
    const stroke: MaskPoint[] = [[50, 50], [60, 60]];
    const next = eraseRegions([rect, far], stroke);
    expect(next).toEqual([far]);
    expect(eraseRegions([rect, far], [])).toHaveLength(2);
  });

  it("点在多边形内判定", () => {
    expect(pointInPolygon([50, 50], rect.points)).toBe(true);
    expect(pointInPolygon([500, 50], rect.points)).toBe(false);
  });

  it("面积比例与格式化", () => {
    const ratio = maskAreaRatio([rect], 1000, 1000);
    expect(ratio).toBeCloseTo(0.01, 5);
    expect(formatMaskArea([rect], 1000, 1000)).toBe("1.0");
  });

  it("撤销/重做栈深度 ≥ 20（实现为 50）", () => {
    let state: MaskHistoryState = { past: [], present: [], future: [] };
    for (let index = 0; index < LOCAL_EDIT_HISTORY_LIMIT + 10; index += 1) {
      state = pushMaskHistory(state, [rectRegion([index, 0], [index + 5, 5], 1024, 1024)]);
    }
    expect(state.past.length).toBeLessThanOrEqual(LOCAL_EDIT_HISTORY_LIMIT);
    expect(state.past.length).toBeGreaterThanOrEqual(20);
    const undone = undoMask(state);
    expect(undone.future).toHaveLength(1);
    expect(undoMask({ past: [], present: [], future: [] })).toEqual({ past: [], present: [], future: [] });
    const redone = redoMask(undone);
    expect(redone.present).toEqual(state.present);
    expect(redoMask({ past: [], present: [], future: [] }).future).toHaveLength(0);
  });

  it("extendStroke 去重", () => {
    expect(extendStroke([[1, 1]], [1, 1])).toHaveLength(1);
    expect(extendStroke([[1, 1]], [2, 1])).toHaveLength(2);
  });
});

describe("local-edit-rules 能力门禁", () => {
  it("只有目录声明 accepts_explicit_mask 且启用的 image_edit 模型可用", () => {
    const capable = modelFixture();
    const list = [
      capable,
      modelFixture({ logical_alias: "no_mask", accepts_explicit_mask: false }),
      modelFixture({ logical_alias: "disabled", enabled: false }),
      modelFixture({ logical_alias: "gen_only", operations: ["image_gen"] }),
    ];
    expect(maskCapableModels(list).map((model) => model.logical_alias)).toEqual(["gemini_image"]);
    expect(maskCapabilityNotice(list)).toBeNull();
  });

  it("无可用模型时给出换模型/取消解释，且 UNKNOWN/缺失按不支持处理", () => {
    const notice = maskCapabilityNotice([]);
    expect(notice).toContain("当前模型不能按选区重绘");
    expect(notice).toContain("accepts_explicit_mask");
    const unknownBit = modelFixture({ accepts_explicit_mask: undefined as unknown as boolean });
    expect(maskCapableModels([unknownBit])).toHaveLength(0);
  });

  it("gate：空 mask / 无指令 / 源非采用候选均拒绝并解释", () => {
    const capableModels = [modelFixture()];
    const base = {
      hasMask: true,
      instruction: "把雨改成晴天",
      capableModels,
      sourceIsAdopted: true,
      sourceLabel: "候选 07",
      adoptedLabel: "候选 07",
    };
    expect(localEditGate(base).ok).toBe(true);
    expect(localEditGate({ ...base, hasMask: false }).reason).toContain("空选区不能生成");
    expect(localEditGate({ ...base, instruction: "  " }).reason).toContain("局部重绘指令");
    const mismatch = localEditGate({ ...base, sourceIsAdopted: false, adoptedLabel: "候选 03" });
    expect(mismatch.ok).toBe(false);
    expect(mismatch.reason).toContain("当前采用候选");
    expect(mismatch.reason).toContain("候选 07");
    const blocked = localEditGate({ ...base, capableModels: [] });
    expect(blocked.reason).toContain("不能按选区重绘");
  });
});

describe("local-edit-rules regenerate_region envelope", () => {
  it("编入 instruction + mask 多边形 + 模型，operation 固定，不发整页命令", () => {
    let counter = 0;
    const envelope = buildRegionRegenerateEnvelope({
      projectId: "project-1",
      page: { id: "page-1", version: 4, page_number: 12 },
      regions: [rect],
      instruction: " 把雨改成晴天 ",
      modelAlias: "gemini_image",
      resolution: "1K",
      newId: () => `id-${(counter += 1).toString().padStart(8, "0")}-abcd-abcd-abcd-abcdabcdabcd`.slice(0, 36),
      now: () => "2026-09-03T00:00:00.000Z",
    });
    expect(envelope.operation).toBe("regenerate_region");
    expect(envelope.target).toEqual({ project_id: "project-1", page_id: "page-1" });
    expect(envelope.expected_version).toEqual({ scope: "page", value: 4 });
    expect(envelope.payload.instruction).toBe("把雨改成晴天");
    expect(envelope.payload.mask).toEqual([{ points: [[10, 10], [110, 10], [110, 110], [10, 110]] }]);
    expect(envelope.payload.model_alias).toBe("gemini_image");
    expect(envelope.payload.resolution).toBe("1K");
    expect(envelope.source.raw_output_id).toBe("local_edit_v1");
  });

  it("区域数截断到 8、坐标四舍五入到 2 位小数控制 payload 大小", () => {
    const regions = Array.from({ length: 12 }, (_, index) => ({
      points: [[index + 0.123456, 0], [index + 1, 5], [index + 0.5, 6]] as MaskPoint[],
    }));
    const envelope = buildRegionRegenerateEnvelope({
      projectId: "project-1",
      page: { id: "page-1", version: 1, page_number: 1 },
      regions,
      instruction: "test",
      modelAlias: "gemini_image",
    });
    expect((envelope.payload.mask as MaskRegion[])).toHaveLength(LOCAL_EDIT_MAX_REGIONS);
    expect((envelope.payload.mask as MaskRegion[])[0].points[0]).toEqual([0.12, 0]);
    expect(envelope.payload.resolution).toBeUndefined();
  });
});

describe("local-edit-rules 派生候选识别", () => {
  it("按 prompt_snapshot.lineage.source_command_id 挂接结果", () => {
    const candidate = {
      status: "QUEUED",
      asset_id: null,
      prompt_snapshot: { lineage: { source_command_id: "cmd-1" } },
    };
    expect(candidateMatchesCommand(candidate, "cmd-1")).toBe(true);
    expect(candidateMatchesCommand(candidate, "cmd-2")).toBe(false);
    expect(candidateMatchesCommand({ prompt_snapshot: {} }, "cmd-1")).toBe(false);
  });

  it("派生候选阶段映射：终态失败/取消/完成/进行中", () => {
    expect(derivedCandidatePhase(null)).toBe("none");
    expect(derivedCandidatePhase({ status: "QUEUED", asset_id: null })).toBe("pending");
    expect(derivedCandidatePhase({ status: "GENERATING", asset_id: null })).toBe("pending");
    expect(derivedCandidatePhase({ status: "COMPLETED", asset_id: "asset-9" })).toBe("done");
    expect(derivedCandidatePhase({ status: "FAILED", asset_id: null })).toBe("failed");
    expect(derivedCandidatePhase({ status: "CANCELLED", asset_id: null })).toBe("canceled");
  });
});
