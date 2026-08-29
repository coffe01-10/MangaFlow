import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Project } from "@/lib/api";
import { EditorTopbar } from "./editor-topbar";

const projects: Project[] = [
  { id: "p1", name: "项目一" },
  { id: "p2", name: "项目二" },
] as Project[];

function renderTopbar(overrides: Partial<Parameters<typeof EditorTopbar>[0]> = {}) {
  const chooseProject = vi.fn();
  const exportFlow = vi.fn();
  const saveFlow = vi.fn();
  const runWorkflow = vi.fn();
  render(
    <EditorTopbar
      projects={projects}
      projectsLoading={false}
      resolvedProjectId="p1"
      chooseProject={chooseProject}
      projectDataError={false}
      projectDataLoading={false}
      chapterCount={5}
      assetCount={3}
      saved={true}
      isRunning={false}
      exportFlow={exportFlow}
      saveFlow={saveFlow}
      runWorkflow={runWorkflow}
      {...overrides}
    />,
  );
  return { chooseProject, exportFlow, saveFlow, runWorkflow };
}

describe("EditorTopbar", () => {
  it("展示项目上下文与已保存状态", () => {
    renderTopbar();
    expect(screen.getByText("5 章 · 3 项资产")).toBeInTheDocument();
    expect(screen.getByText("所有更改已保存")).toBeInTheDocument();
  });

  it("未保存与项目加载状态正确显示", () => {
    renderTopbar({ saved: false, projectsLoading: true, projects: [] });
    expect(screen.getByText("有未保存更改")).toBeInTheDocument();
    expect(screen.getByText("正在读取项目…")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "当前工作流项目" })).toBeDisabled();
  });

  it("项目服务错误时显示未连接提示", () => {
    renderTopbar({ projectDataError: true });
    expect(screen.getByText("项目服务未连接")).toBeInTheDocument();
  });

  it("切换项目回调 chooseProject", () => {
    const { chooseProject } = renderTopbar();
    fireEvent.change(screen.getByRole("combobox", { name: "当前工作流项目" }), { target: { value: "p2" } });
    expect(chooseProject).toHaveBeenCalledWith("p2");
  });

  it("运行中禁用运行按钮且点击不触发", () => {
    const { runWorkflow, exportFlow, saveFlow } = renderTopbar({ isRunning: true });

    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    expect(exportFlow).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(saveFlow).toHaveBeenCalledTimes(1);

    const runButton = screen.getByRole("button", { name: /运行中/ });
    expect(runButton).toBeDisabled();
    fireEvent.click(runButton);
    expect(runWorkflow).not.toHaveBeenCalled();
  });

  it("空闲时点击运行触发 runWorkflow", () => {
    const { runWorkflow } = renderTopbar({ isRunning: false });

    fireEvent.click(screen.getByRole("button", { name: /运行工作流/ }));
    expect(runWorkflow).toHaveBeenCalledTimes(1);
  });

  it("导出与保存按钮触发对应回调", () => {
    const { exportFlow, saveFlow } = renderTopbar({ isRunning: false });

    fireEvent.click(screen.getByRole("button", { name: /导出/ }));
    expect(exportFlow).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(saveFlow).toHaveBeenCalledTimes(1);
  });
});
