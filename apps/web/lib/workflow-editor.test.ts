import { describe, expect, it } from "vitest";
import type { WorkflowGroup, WorkflowNodeType } from "./api";
import { arrangeNodes, cleanGroups, duplicateNodes, insertNode, makeNode, projectGroups, resolveHandle, type StudioEdge, type StudioNode } from "./workflow-editor";

const type: WorkflowNodeType = { type: "agent.test", label: "处理", category: "AGENT", description: "", configurable_fields: [],
  inputs: [{ id: "in", label: "输入", data_type: "json", required: true }],
  outputs: [{ id: "out", label: "输出", data_type: "json", required: false }] };
const node = (id: string, x = 0, y = 0): StudioNode => {
  const item = makeNode(type, { x, y });
  return { ...item, id, data: { graphNode: { ...item.data.graphNode, id } } };
};
const edge = (source: string, target: string): StudioEdge => ({ id: `${source}-${target}`, source, target, sourceHandle: "out", targetHandle: "in" });
const group: WorkflowGroup = { id: "g", name: "分组", color: "#397b68", notes: "备注", collapsed: true, node_ids: ["a", "b"] };

describe("workflow editor graph operations", () => {
  it("copies only internal edges and deeply isolates configs from originals", () => {
    const nodes = [node("a"), node("b"), node("c")];
    nodes[0].data.graphNode.config.condition = { nested: { key: true } };
    const result = duplicateNodes(nodes, [edge("a", "b"), edge("b", "c")], new Set(["a", "b"]));
    expect(result.edges).toHaveLength(3);
    expect(result.edges[2]).toMatchObject({ source: result.mapping.get("a"), target: result.mapping.get("b") });
    expect(result.nodes.slice(0, 3).every((item) => !item.selected)).toBe(true);
    result.copies[0].data.graphNode.config.condition.nested = {};
    expect(nodes[0].data.graphNode.config.condition).toEqual({ nested: { key: true } });
  });
  it("distributes equal empty space with unequal measured sizes and preserves extremes", () => {
    const nodes = [node("a", 10), node("b", 150), node("c", 610), node("outside", 900)];
    nodes[0].measured = { width: 100 }; nodes[1].measured = { width: 200 }; nodes[2].measured = { width: 150 };
    const result = arrangeNodes(nodes, new Set(["a", "b", "c"]), "horizontal");
    expect(result.map((item) => item.position.x)).toEqual([10, 260, 610, 900]);
    expect(arrangeNodes(nodes, new Set(["a", "b", "c"]), "right").map((item) => item.position.x)).toEqual([660, 560, 610, 900]);
  });
  it("inserts between endpoints atomically and preserves unrelated edges", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const result = insertNode(type, { position: { x: 200, y: 50 }, edgeId: "a-b", source: { nodeId: "a", handle: "out" }, target: { nodeId: "b", handle: "in" } }, nodes, [edge("a", "b"), edge("b", "c")])!;
    expect(result.edges).toHaveLength(3);
    expect(result.edges).toEqual(expect.arrayContaining([expect.objectContaining({ source: "a", target: result.node.id }), expect.objectContaining({ source: result.node.id, target: "b" }), edge("b", "c")]));
    expect(result.edges.some((item) => item.id === "a-b")).toBe(false);
    const incompatible = { ...type, inputs: [{ ...type.inputs[0], data_type: "image" as const }] };
    expect(insertNode(incompatible, { position: { x: 0, y: 0 }, source: { nodeId: "a", handle: "out" } }, nodes, [])).toBeNull();
    expect(insertNode(type, { position: { x: 0, y: 0 }, edgeId: "gone" }, nodes, [])).toBeNull();
  });
  it("supports adding upstream from an input handle", () => {
    const result = insertNode(type, { position: { x: 0, y: 0 }, target: { nodeId: "b", handle: "in" } }, [node("b")], [])!;
    expect(result.edges[0]).toMatchObject({ source: result.node.id, target: "b", sourceHandle: "out" });
  });
  it("projects collapsed groups without mutating real graph and retains mixed fan-out handles", () => {
    const nodes = [node("a"), node("b", 300), node("c", 600)];
    const edges = [edge("a", "b"), edge("a", "c")];
    const result = projectGroups(nodes, edges, [group]);
    expect(result.nodes.find((item) => item.id === "a")?.hidden).toBe(true);
    expect(result.edges[0].hidden).toBe(true);
    expect(result.edges[1]).toMatchObject({ source: "g", sourceHandle: "a:out", target: "c", hidden: false });
    expect(result.nodes[0].data.graphNode.outputs.some((port) => port.id === "a:out")).toBe(true);
    expect(resolveHandle("g", "a:out", [group])).toEqual({ nodeId: "a", handle: "out" });
    expect(nodes[0].hidden).toBeUndefined(); expect(edges[1].source).toBe("a");
    const expanded = projectGroups(nodes, edges, [{ ...group, collapsed: false }]);
    expect(expanded.edges.every((item) => !item.hidden)).toBe(true);
    expect(expanded.edges[1].source).toBe("a");
  });
  it("removes deleted members and empty groups", () => {
    expect(cleanGroups([group], [node("b")])[0].node_ids).toEqual(["b"]);
    expect(cleanGroups([group], [])).toEqual([]);
  });
});
