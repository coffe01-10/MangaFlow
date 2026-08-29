import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { initialNodes } from "./graph-model";
import type { FlowNode } from "./types";
import { RunMonitor } from "./run-monitor";

function MonitorHarness({ nodes, isRunning }: { nodes: FlowNode[]; isRunning: boolean }) {
  const [logOpen, setLogOpen] = useState(true);
  return <RunMonitor nodes={nodes} isRunning={isRunning} logOpen={logOpen} setLogOpen={setLogOpen} />;
}

describe("RunMonitor", () => {
  it("全部完成时显示进度与完成文案", () => {
    const doneNodes = initialNodes.map((node) => ({ ...node, status: "done" as const }));
    render(<MonitorHarness nodes={doneNodes} isRunning={false} />);

    expect(screen.getByText("IDLE")).toBeInTheDocument();
    expect(screen.getByText("流程已完成")).toBeInTheDocument();
    expect(screen.getByText("全部节点通过")).toBeInTheDocument();
  });

  it("运行中显示 LIVE 与当前节点，未运行显示等待", () => {
    const runningNodes = initialNodes.map((node) => node.id === "parser-1" ? { ...node, status: "running" as const } : node);
    const { rerender } = render(<MonitorHarness nodes={runningNodes} isRunning={true} />);

    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("剧情解析")).toBeInTheDocument();

    rerender(<MonitorHarness nodes={initialNodes} isRunning={false} />);
    expect(screen.getByText("等待运行")).toBeInTheDocument();
    expect(screen.getByText("从原作章节开始")).toBeInTheDocument();
  });

  it("点击折叠后隐藏统计，再次点击展开", () => {
    render(<MonitorHarness nodes={initialNodes} isRunning={false} />);

    const toggle = screen.getByTitle("收起运行监视器");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const statLine = (label: string) => screen.getByText((_, element) => element?.textContent === label);
    expect(statLine("成功 3")).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByTitle("展开运行监视器")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText((_, element) => element?.textContent === "成功 3")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTitle("展开运行监视器"));
    expect(statLine("成功 3")).toBeInTheDocument();
  });
});
