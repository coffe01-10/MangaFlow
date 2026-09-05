import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { originUrl, type PageCandidate } from "@/lib/api";
import {
  SIDEBAR_WIDTH_DEFAULT,
  SIDEBAR_WIDTH_MAX,
  SIDEBAR_WIDTH_MIN,
  clampSidebarWidth,
  storedSidebarWidth,
} from "@/lib/workspace-layout";
import { WINDOWING_THRESHOLDS, shouldWindowize } from "@/lib/windowing-rules";

import { CandidateArtwork } from "./shared";
import { WorkspaceInspectorSlot } from "./workspace-slots";
import { ImageLightbox, QueueDock, WorkspaceSidebar } from "./workspace-chrome";

const imageCapture = vi.hoisted(() => ({
  props: null as { alt: string; src: string; loading?: string } | null,
}));

vi.mock("next/image", () => ({
  default: (props: { alt: string; src: string; loading?: string }) => {
    imageCapture.props = props;
    return <span role="img" aria-label={props.alt} data-src={props.src} data-loading={props.loading} />;
  },
}));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode } & Record<string, unknown>) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

describe("U7 lightbox（audit §2.4/§7）", () => {
  function LightboxHarness({ onClose }: { onClose: () => void }) {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button type="button" onClick={() => setOpen(true)}>打开大图</button>
        {open && (
          <ImageLightbox
            preview={{ url: "http://127.0.0.1:8000/api/v1/assets/asset-1/content", label: "候选 1" }}
            onClose={() => { onClose(); setOpen(false); }}
          />
        )}
      </>
    );
  }

  it("打开后焦点进入对话框，＋/－ 键调整缩放，Esc 关闭", () => {
    const onClose = vi.fn();
    render(<LightboxHarness onClose={onClose} />);

    const trigger = screen.getByRole("button", { name: "打开大图" });
    trigger.focus();
    fireEvent.click(trigger);

    expect(screen.getByRole("dialog", { name: "候选 1" })).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: "关闭大图" })).toHaveFocus();

    fireEvent.keyDown(document, { key: "+" });
    expect(screen.getByRole("button", { name: "125%" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "-" });
    expect(screen.getByRole("button", { name: "100%" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("Esc 关闭后焦点回到触发缩略图（U7：关闭并焦点回触发图）", () => {
    const onClose = vi.fn();
    render(<LightboxHarness onClose={onClose} />);

    const trigger = screen.getByRole("button", { name: "打开大图" });
    trigger.focus();
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog", { name: "候选 1" })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(trigger).toHaveFocus();
  });

  it("Tab 焦点陷阱把键盘焦点留在 lightbox 内", () => {
    render(
      <ImageLightbox
        preview={{ url: "http://127.0.0.1:8000/api/v1/assets/asset-1/content", label: "候选 1" }}
        onClose={() => {}}
      />,
    );

    expect(screen.getByRole("button", { name: "关闭大图" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(screen.getByRole("button", { name: "放大图片" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "关闭大图" })).toHaveFocus();
  });

  it("lightbox 携带 candidate 时渲染局部修改按钮并回调", () => {
    const candidate = { id: "candidate-1" } as PageCandidate;
    const onLocalEdit = vi.fn();
    render(
      <ImageLightbox
        preview={{ url: "http://127.0.0.1:8000/api/v1/assets/asset-1/content", label: "候选 1", candidate }}
        onClose={() => {}}
        onLocalEdit={onLocalEdit}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "局部修改" }));
    expect(onLocalEdit).toHaveBeenCalledWith(candidate);
  });
});

describe("U8 queue-dock 隐藏/恢复 localStorage 往返", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  function renderDock() {
    return render(
      <QueueDock
        queueStats={{ waiting: 2, failed: 1 }}
        latestJob={undefined}
        latestJobLabel=""
        section="generate"
        concurrency={2}
        projectPath={(target) => `/projects/p1/${target}`}
      />,
    );
  }

  it("隐藏写入 localStorage", () => {
    renderDock();
    expect(window.localStorage.getItem("mangaflow.queue-dock-hidden")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "隐藏任务中心快捷栏" }));
    expect(window.localStorage.getItem("mangaflow.queue-dock-hidden")).toBe("true");
  });

  it("隐藏态重新挂载只显示恢复按钮，点击后回到 dock 并清除隐藏标记", () => {
    window.localStorage.setItem("mangaflow.queue-dock-hidden", "true");
    renderDock();

    expect(screen.queryByRole("link", { name: /打开任务中心/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "显示任务中心快捷栏" }));
    expect(window.localStorage.getItem("mangaflow.queue-dock-hidden")).toBe("false");
    expect(screen.getByRole("link", { name: /打开任务中心/ })).toBeInTheDocument();
  });
});

describe("V02-51B 通用右槽 API（workspace-slots）", () => {
  it("open 时渲染 drawer-open 槽与遮罩，关闭按钮回调 onClose", () => {
    const onClose = vi.fn();
    const { container, rerender } = render(
      <WorkspaceInspectorSlot title="检查器" open onClose={onClose}>
        <p>检查器内容</p>
      </WorkspaceInspectorSlot>,
    );

    const slot = screen.getByRole("complementary", { name: "检查器" });
    expect(slot).toHaveClass("workspace-inspector", "drawer-open");
    expect(screen.getByText("检查器内容")).toBeInTheDocument();
    expect(container.querySelector(".workspace-inspector-backdrop")).not.toBeNull();

    const closers = screen.getAllByRole("button", { name: "关闭检查器" });
    fireEvent.click(closers[closers.length - 1]);
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(
      <WorkspaceInspectorSlot title="检查器" open={false} onClose={onClose}>
        <p>检查器内容</p>
      </WorkspaceInspectorSlot>,
    );
    expect(screen.getByRole("complementary", { name: "检查器" })).not.toHaveClass("drawer-open");
    expect(container.querySelector(".workspace-inspector-backdrop")).toBeNull();
  });
});

describe("U9 侧栏拖宽 188–360（audit §13：与既有拖宽边界同类）", () => {
  it("clamp 拒绝越界，存储值越界/非法回退默认 214", () => {
    expect(SIDEBAR_WIDTH_MIN).toBe(188);
    expect(SIDEBAR_WIDTH_MAX).toBe(360);
    expect(clampSidebarWidth(100)).toBe(188);
    expect(clampSidebarWidth(214)).toBe(214);
    expect(clampSidebarWidth(999)).toBe(360);
    expect(storedSidebarWidth("300")).toBe(300);
    expect(storedSidebarWidth("100")).toBe(SIDEBAR_WIDTH_DEFAULT);
    expect(storedSidebarWidth("abc")).toBe(SIDEBAR_WIDTH_DEFAULT);
    expect(storedSidebarWidth(null)).toBe(SIDEBAR_WIDTH_DEFAULT);
  });

  it("project workspace 的拖宽与恢复消费同一 clamp 实现", () => {
    const source = readFileSync(resolve(process.cwd(), "components/project-workspace.tsx"), "utf8");
    expect(source).toContain("clampSidebarWidth(startWidth");
    expect(source).toContain("storedSidebarWidth(window.localStorage.getItem");
    expect(source).not.toContain("Math.min(360, Math.max(188");
  });
});

describe("左导航图标轨（模板 B）", () => {
  it("折叠状态把 rail 类传给侧边栏且当前步骤仍可辨识", () => {
    const props = {
      navOpen: false,
      navCollapsed: true,
      setNavOpen: () => {},
      projectName: "测试项目",
      summary: "1 章 · 0 页已规划",
      section: "source" as const,
      projectPath: (target: string) => `/projects/p1/${target}`,
      rememberWorkspaceScroll: () => {},
      onSidebarResize: () => {},
    };
    const { container, rerender } = render(<WorkspaceSidebar {...props} />);
    expect(container.querySelector("aside.workspace-left")).toHaveClass("rail");
    expect(container.querySelector("a.active")).toHaveAttribute("aria-current", "page");

    rerender(<WorkspaceSidebar {...props} navOpen navCollapsed={false} />);
    expect(container.querySelector("aside.workspace-left")).toHaveClass("open");
    expect(container.querySelector("aside.workspace-left")).not.toHaveClass("rail");
  });
});

describe("候选网格缩略图夹具（audit §7：网格一律走 thumbnail，lightbox 才用原图）", () => {
  function capturedImage() {
    return imageCapture.props as { alt: string; src: string; loading?: string } | null;
  }

  it("CandidateArtwork 优先使用 thumbnailUrl，点击把原图交给 lightbox", () => {
    imageCapture.props = null;
    const onOpen = vi.fn();
    const { container } = render(
      <CandidateArtwork
        contentUrl="/api/v1/assets/asset-1/content"
        thumbnailUrl="/api/v1/assets/asset-1/thumbnail/640"
        label="候选 1"
        eager
        onOpen={onOpen}
      />,
    );

    expect(capturedImage()?.src).toContain("/api/v1/assets/asset-1/thumbnail/640");
    expect(capturedImage()?.loading).toBe("eager");
    fireEvent.click(container.querySelector("button.candidate-artwork")!);
    expect(onOpen).toHaveBeenCalledWith(originUrl("/api/v1/assets/asset-1/content"), "候选 1");
    expect(onOpen.mock.calls[0][0]).not.toContain("thumbnail/640");
  });

  it("懒加载缩略图缺省时回退 content 改写为 /thumbnail/640", () => {
    imageCapture.props = null;
    render(<CandidateArtwork contentUrl="/api/v1/assets/asset-2/content" label="候选 2" />);

    expect(capturedImage()?.src).toContain("/api/v1/assets/asset-2/thumbnail/640");
    expect(capturedImage()?.loading).toBe("lazy");
  });
});

describe("窗口化策略夹具（V02-52 接缝）", () => {
  it("阈值来自审计 §7：候选 24 / 库 60 / 任务 80，超过才窗口化", () => {
    expect(WINDOWING_THRESHOLDS).toEqual({ generateCandidates: 24, libraryThumbnails: 60, taskRows: 80 });
    expect(shouldWindowize(24, WINDOWING_THRESHOLDS.generateCandidates)).toBe(false);
    expect(shouldWindowize(25, WINDOWING_THRESHOLDS.generateCandidates)).toBe(true);
  });
});
