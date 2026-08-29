import { describe, expect, it } from "vitest";

import {
  createNode,
  edge,
  initialEdges,
  initialNodes,
  paletteGroups,
  statusLabel,
  templateMap,
} from "./graph-model";

describe("edge", () => {
  it("用端点拼出稳定且唯一的连线 id", () => {
    const item = edge("source-1", "source", "parser-1", "source");
    expect(item).toEqual({
      id: "source-1:source-parser-1:source",
      source: "source-1",
      sourcePort: "source",
      target: "parser-1",
      targetPort: "source",
    });
  });

  it("交换端点会得到不同的 id", () => {
    expect(edge("a", "out", "b", "in").id).not.toBe(edge("b", "in", "a", "out").id);
  });
});

describe("createNode", () => {
  it("按模板生成节点并填充默认设置", () => {
    const node = createNode("parser", "parser-9", 100, 200);
    expect(node).toMatchObject({
      id: "parser-9",
      kind: "agent",
      title: "剧情解析",
      eyebrow: "AGENT",
      x: 100,
      y: 200,
      status: "idle",
    });
    expect(node.settings).toEqual({
      model: "Gemini 3.5 Flash",
      resolution: "1K 草稿",
      concurrency: 2,
      locked: false,
      notes: "",
    });
  });

  it("生成器节点默认使用 Nano Banana 2", () => {
    expect(createNode("generator", "generator-9", 0, 0).settings.model).toBe("Nano Banana 2");
  });

  it("overrides 覆盖模板默认值", () => {
    const node = createNode("source", "source-9", 0, 0, { status: "done", title: "自定义" });
    expect(node.status).toBe("done");
    expect(node.title).toBe("自定义");
  });

  it("未知模板会报错而不是静默生成空节点", () => {
    expect(() => createNode("missing", "missing-1", 0, 0)).toThrow();
  });
});

describe("初始图", () => {
  it("调色板每个模板都能创建节点，键不重复", () => {
    const keys = paletteGroups.flatMap((group) => group.items.map((item) => item.key));
    expect(new Set(keys).size).toBe(keys.length);
    for (const key of keys) {
      expect(createNode(key, `probe-${key}`, 0, 0).id).toBe(`probe-${key}`);
    }
  });

  it("初始节点与初始边一一对应且互相连通", () => {
    const nodeIds = new Set(initialNodes.map((node) => node.id));
    expect(nodeIds).toEqual(new Set(["source-1", "parser-1", "adapter-1", "director-1", "assets-1", "generator-1", "quality-1", "export-1"]));
    for (const item of initialEdges) {
      expect(nodeIds.has(item.source)).toBe(true);
      expect(nodeIds.has(item.target)).toBe(true);
    }
    expect(initialEdges.map((item) => item.id)).toEqual([
      "source-1:source-parser-1:source",
      "parser-1:story-adapter-1:story",
      "adapter-1:script-director-1:script",
      "director-1:panels-generator-1:panels",
      "assets-1:assets-generator-1:assets",
      "generator-1:page-quality-1:page",
      "quality-1:approved-export-1:page",
    ]);
  });

  it("每个状态都有中文标签", () => {
    for (const status of ["idle", "ready", "running", "done", "warning"] as const) {
      expect(statusLabel[status]).toBeTruthy();
    }
  });

  it("templateMap 与调色板条目一致", () => {
    expect(templateMap.size).toBe(paletteGroups.reduce((sum, group) => sum + group.items.length, 0));
  });
});
