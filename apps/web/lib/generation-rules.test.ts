import { describe, expect, it } from "vitest";

import { getPageGenerationIssue, getPageStructureIssue } from "./generation-rules";

const validPage = {
  source_coverage: { complete: true },
  scene_ids: ["scene-1"],
  beat_ids: ["beat-1"],
};

describe("页面生成规则", () => {
  it("不会把项目上次使用的模型当成本次选择", () => {
    expect(getPageGenerationIssue(validPage, null)).toBe(
      "请先选择 Nano Banana 2 或 Nano Banana Pro",
    );
  });

  it("阻止没有剧本和分镜来源的旧版分页进入生图", () => {
    const issue = getPageStructureIssue({
      ...validPage,
      scene_ids: [],
      beat_ids: [],
    });

    expect(issue).toContain("旧版规划");
    expect(issue).toContain("重新分页");
  });

  it("完整来源且显式选模后允许生成", () => {
    expect(getPageGenerationIssue(validPage, "image.nano_banana_2")).toBeNull();
    expect(getPageGenerationIssue(validPage, "image.nano_banana_pro")).toBeNull();
  });
});
