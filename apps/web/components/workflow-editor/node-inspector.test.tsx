import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { initialNodes } from "./graph-model";
import type { FlowNode } from "./types";
import { NodeInspector } from "./node-inspector";

const selectedNode = initialNodes.find((node) => node.id === "adapter-1") as FlowNode;

function renderInspector(overrides: Partial<Parameters<typeof NodeInspector>[0]> = {}) {
  const updateNode = vi.fn();
  const updateSettings = vi.fn();
  const deleteSelection = vi.fn();
  const setInspectorOpen = vi.fn();
  render(
    <NodeInspector
      selectedNode={selectedNode}
      activeProject={null}
      projectsLoading={false}
      projectDataError={false}
      assetCount={3}
      chapterCount={5}
      models={[
        {
          catalog_id: "text-model",
          connection_id: "connection-1",
          provider: "Example",
          protocol: "OPENAI",
          model_id: "example-text",
          logical_alias: "text.example",
          display_name: "Example Text",
          model_type: "TEXT",
          input_modalities: ["TEXT", "IMAGE"],
          output_modalities: ["TEXT"],
          operations: ["structured_text", "multimodal_analysis"],
          resolutions: [],
          preview_resolutions: [],
          max_reference_images: 1,
          regions: [],
          confidence: "VERIFIED",
          enabled: true,
          display_enabled: true,
          auto_eligible: true,
          priority: 100,
        },
      ]}
      modelsLoading={false}
      inspectorOpen={true}
      setInspectorOpen={setInspectorOpen}
      updateNode={updateNode}
      updateSettings={updateSettings}
      deleteSelection={deleteSelection}
      {...overrides}
    />,
  );
  return { updateNode, updateSettings, deleteSelection, setInspectorOpen };
}

describe("NodeInspector", () => {
  it("未选中节点时显示空状态", () => {
    renderInspector({ selectedNode: null });
    expect(screen.getByText("未选择节点")).toBeInTheDocument();
  });

  it("展示选中节点摘要与绑定说明", () => {
    renderInspector();
    expect(screen.getByText(/ADAPTER-1/)).toBeInTheDocument();
    expect(screen.getByText("尚未连接项目；该节点只能保留本地流程配置。")).toBeInTheDocument();
  });

  it("名称与说明输入回调 updateNode", () => {
    const { updateNode } = renderInspector();

    fireEvent.change(screen.getByLabelText("节点名称"), { target: { value: "新名称" } });
    expect(updateNode).toHaveBeenLastCalledWith({ title: "新名称" });

    fireEvent.change(screen.getByLabelText("说明"), { target: { value: "新说明" } });
    expect(updateNode).toHaveBeenLastCalledWith({ description: "新说明" });
  });

  it("模型、清晰度、并发数与锁定开关回调 updateSettings", () => {
    const { updateSettings } = renderInspector();

    fireEvent.change(screen.getByLabelText("使用模型"), { target: { value: "text-model" } });
    expect(updateSettings).toHaveBeenLastCalledWith({ model: "text-model" });

    fireEvent.change(screen.getByLabelText("清晰度"), { target: { value: "4K 高清" } });
    expect(updateSettings).toHaveBeenLastCalledWith({ resolution: "4K 高清" });

    fireEvent.change(screen.getByLabelText("并发数"), { target: { value: "4" } });
    expect(updateSettings).toHaveBeenLastCalledWith({ concurrency: 4 });

    fireEvent.click(screen.getByRole("button", { name: /锁定节点设定/ }));
    expect(updateSettings).toHaveBeenLastCalledWith({ locked: true });
  });

  it("备注编辑、重置状态与删除按钮触发对应回调", () => {
    const { updateSettings, updateNode, deleteSelection } = renderInspector();

    fireEvent.change(screen.getByLabelText("备注"), { target: { value: "检查台词顺序" } });
    expect(updateSettings).toHaveBeenLastCalledWith({ notes: "检查台词顺序" });

    fireEvent.click(screen.getByRole("button", { name: /重置状态/ }));
    expect(updateNode).toHaveBeenLastCalledWith({ status: "ready" });

    fireEvent.click(screen.getByRole("button", { name: /删除节点/ }));
    expect(deleteSelection).toHaveBeenCalledTimes(1);
  });

  it("关闭时仅显示打开按钮，点击恢复展开", () => {
    const { setInspectorOpen } = renderInspector({ inspectorOpen: false });
    expect(screen.queryByText("属性面板")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle("打开属性面板"));
    expect(setInspectorOpen).toHaveBeenCalledWith(true);
  });
});
