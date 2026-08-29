import { describe, expect, it } from "vitest";

import type { Asset } from "@/lib/api";

import {
  assetName,
  formatBytes,
  inspectionBubbleDiffs,
  inspectionSummary,
  promptPreview,
  queueStatsOf,
  recommendedRepairType,
} from "./display";

describe("recommendedRepairType", () => {
  it("说话人问题建议修复气泡区域", () => {
    expect(recommendedRepairType("SPEAKER")).toBe("BUBBLE_REGION");
  });

  it("角色、服装、道具问题建议修复单格", () => {
    expect(recommendedRepairType("CHARACTER")).toBe("PANEL");
    expect(recommendedRepairType("OUTFIT")).toBe("PANEL");
    expect(recommendedRepairType("PROP")).toBe("PANEL");
  });

  it("其余类别（含连续性）建议修复整页", () => {
    expect(recommendedRepairType("CONTINUITY")).toBe("PAGE");
    expect(recommendedRepairType("TEXT")).toBe("PAGE");
  });
});

describe("inspectionSummary", () => {
  it("优先拼接应为/实为说明", () => {
    expect(inspectionSummary({ expected: "苏清白", observed: "路人" })).toBe("应为：苏清白；实为：路人");
    expect(inspectionSummary({ expected: "", observed: "只有实为" })).toBe("实为：只有实为");
  });

  it("没有应为/实为时按条目列出，空对象给出兜底文案", () => {
    expect(inspectionSummary({ severity: "INFO" })).toBe("severity: INFO");
    expect(inspectionSummary({})).toBe("模型未补充说明");
  });
});

describe("inspectionBubbleDiffs", () => {
  it("返回气泡差异列表并过滤非对象条目", () => {
    const diffs = inspectionBubbleDiffs({
      bubble_diffs: [
        { balloon_index: 1, target_text: "你好", recognized_text: "你好", similarity: 0.98 },
        null,
        "noise",
      ],
    });
    expect(diffs).toHaveLength(1);
    expect(diffs[0]).toEqual({ balloon_index: 1, target_text: "你好", recognized_text: "你好", similarity: 0.98 });
  });

  it("缺少 bubble_diffs 字段时返回空数组", () => {
    expect(inspectionBubbleDiffs({})).toEqual([]);
    expect(inspectionBubbleDiffs({ bubble_diffs: "nope" })).toEqual([]);
  });
});

describe("formatBytes", () => {
  it("小于 1MB 以 KB 向上取整展示", () => {
    expect(formatBytes(0)).toBe("0 KB");
    expect(formatBytes(512 * 1024)).toBe("512 KB");
    expect(formatBytes(1024 * 1024 - 1)).toBe("1024 KB");
  });

  it("大于等于 1MB 以一位小数 MB 展示", () => {
    expect(formatBytes(1024 * 1024)).toBe("1.0 MB");
    expect(formatBytes(2.5 * 1024 * 1024)).toBe("2.5 MB");
  });
});

describe("assetName", () => {
  it("优先使用去除空白后的自定义名称，其次原始文件名", () => {
    expect(assetName({ display_name: "  主参考  ", original_name: "raw.png" } as Asset)).toBe("主参考");
    expect(assetName({ display_name: "   ", original_name: "raw.png" } as Asset)).toBe("raw.png");
  });

  it("两个名称都缺失时返回未命名素材", () => {
    expect(assetName(undefined)).toBe("未命名素材");
    expect(assetName({} as Asset)).toBe("未命名素材");
  });
});

describe("promptPreview", () => {
  it("快照里有字符串预览时原样返回", () => {
    expect(promptPreview({ prompt_snapshot: { prompt_preview: "生成提示词" } })).toBe("生成提示词");
  });

  it("快照缺失或非字符串时返回排队占位文案", () => {
    expect(promptPreview({ prompt_snapshot: {} })).toBe("任务排队后会在这里保存本次实际提示词。");
    expect(promptPreview({ prompt_snapshot: { prompt_preview: 42 } })).toBe("任务排队后会在这里保存本次实际提示词。");
  });
});

describe("queueStatsOf", () => {
  it("分别统计等待/排队与失败任务数量", () => {
    const stats = queueStatsOf([
      { status: "WAITING" },
      { status: "QUEUED" },
      { status: "RUNNING" },
      { status: "FAILED" },
      { status: "COMPLETED" },
    ]);
    expect(stats).toEqual({ waiting: 2, failed: 1 });
  });
});
