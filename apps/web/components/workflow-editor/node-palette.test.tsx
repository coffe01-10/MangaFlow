import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { NodePalette } from "./node-palette";

function PaletteHarness({ addNode }: { addNode: (templateKey: string) => void }) {
  const [search, setSearch] = useState("");
  const [collapsedGroups, setCollapsedGroups] = useState<string[]>([]);
  return (
    <NodePalette
      search={search}
      setSearch={setSearch}
      collapsedGroups={collapsedGroups}
      togglePaletteGroup={(label) => setCollapsedGroups((current) => current.includes(label)
        ? current.filter((item) => item !== label)
        : [...current, label])}
      addNode={addNode}
    />
  );
}

describe("NodePalette", () => {
  it("渲染全部调色板分组与模板", () => {
    render(<PaletteHarness addNode={vi.fn()} />);

    expect(screen.getByText("输入 / INPUT")).toBeInTheDocument();
    expect(screen.getByText("智能体 / AGENTS")).toBeInTheDocument();
    expect(screen.getByText("生成 / OUTPUT")).toBeInTheDocument();
    expect(screen.getByText("原作章节")).toBeInTheDocument();
    expect(screen.getByText("连续导出")).toBeInTheDocument();
  });

  it("搜索按标题与描述过滤节点模板", () => {
    render(<PaletteHarness addNode={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("搜索节点"), { target: { value: "剧情" } });

    expect(screen.getByText("剧情解析")).toBeInTheDocument();
    expect(screen.queryByText("漫画改编")).not.toBeInTheDocument();
    expect(screen.queryByText("质量检查")).not.toBeInTheDocument();
  });

  it("点击分组头折叠展开，搜索命中时忽略折叠状态", () => {
    render(<PaletteHarness addNode={vi.fn()} />);

    const agentsHeader = screen.getByRole("button", { name: /智能体 \/ AGENTS/ });
    expect(agentsHeader).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(agentsHeader);
    expect(agentsHeader).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("剧情解析")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("搜索节点"), { target: { value: "剧情" } });
    expect(screen.getByText("剧情解析")).toBeInTheDocument();

    fireEvent.click(agentsHeader);
    fireEvent.change(screen.getByLabelText("搜索节点"), { target: { value: "" } });
    expect(screen.getByText("剧情解析")).toBeInTheDocument();
  });

  it("点击模板与快速添加按钮调用 addNode", () => {
    const addNode = vi.fn();
    render(<PaletteHarness addNode={addNode} />);

    fireEvent.click(screen.getByText("分镜导演"));
    expect(addNode).toHaveBeenCalledWith("director");

    fireEvent.click(screen.getByTitle("添加节点"));
    expect(addNode).toHaveBeenCalledWith("parser");
  });
});
