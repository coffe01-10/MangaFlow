import { describe, expect, it } from "vitest";

import { createNode, NODE_HEIGHT, NODE_WIDTH } from "./graph-model";
import type { FlowNode } from "./types";
import { clamp, getPortPoint, nodeTypeClass, pathBetween, portTypeClass } from "./geometry";

const probeNode: FlowNode = createNode("parser", "probe", 500, 300, {
  inputs: [
    { id: "a", label: "A", dataType: "text" },
    { id: "b", label: "B", dataType: "json" },
  ],
  outputs: [
    { id: "x", label: "X", dataType: "image" },
    { id: "y", label: "Y", dataType: "report" },
  ],
});

describe("clamp", () => {
  it("把数值限制在区间内", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(11, 0, 10)).toBe(10);
  });
});

describe("getPortPoint", () => {
  it("输入端口在节点左缘，按端口序号纵向排布", () => {
    expect(getPortPoint(probeNode, "a", "input")).toEqual({ x: 500, y: 300 + 112 });
    expect(getPortPoint(probeNode, "b", "input")).toEqual({ x: 500, y: 300 + 112 + 29 });
  });

  it("输出端口在节点右缘", () => {
    expect(getPortPoint(probeNode, "x", "output")).toEqual({ x: 500 + NODE_WIDTH, y: 300 + 112 });
    expect(getPortPoint(probeNode, "y", "output")).toEqual({ x: 500 + NODE_WIDTH, y: 300 + 112 + 29 });
  });

  it("未知端口回退到第一个端口的位置", () => {
    expect(getPortPoint(probeNode, "missing", "input")).toEqual(getPortPoint(probeNode, "a", "input"));
  });
});

describe("pathBetween", () => {
  it("生成包含两端点的三次贝塞尔曲线路径", () => {
    const d = pathBetween({ x: 0, y: 0 }, { x: 200, y: 100 });
    expect(d).toContain("M 0 0");
    expect(d).toContain("200 100");
    expect(d).toMatch(/^M .+ C .+, .+, .+$/);
  });

  it("水平距离过小时保持最小伸展", () => {
    const d = pathBetween({ x: 10, y: 5 }, { x: 12, y: 5 });
    expect(d).toBe(`M 10 5 C ${10 + 72} 5, ${12 - 72} 5, 12 5`);
  });
});

describe("样式类拼接", () => {
  it("节点类包含节点与种类类名", () => {
    expect(nodeTypeClass("generator")).toContain("node");
    expect(nodeTypeClass("generator")).toContain("node_generator");
  });

  it("端口类包含端口与数据类型类名", () => {
    expect(portTypeClass("image")).toContain("portHandle");
    expect(portTypeClass("image")).toContain("port_image");
  });

  it("画布节点尺寸常量保持不变", () => {
    expect(NODE_WIDTH).toBe(264);
    expect(NODE_HEIGHT).toBe(178);
  });
});
