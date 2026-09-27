import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ChapterMobilePreview } from "@/lib/api";

import { MobilePreview } from "./mobile-preview";

const previewApi = vi.spyOn(api, "chapterMobilePreview");

function previewFixture(): ChapterMobilePreview {
  return {
    chapter_id: "chapter-1",
    title: "第一章",
    pages: [
      {
        page_id: "page-1",
        page_number: 1,
        state: "READY",
        ready: true,
        image_url: "/api/v1/assets/asset-1/thumbnail/640",
        full_image_url: "/api/v1/pages/page-1/export.png",
        width: 1440,
        height: 2160,
        blockers: [],
      },
      {
        page_id: "page-2",
        page_number: 2,
        state: "AWAITING_SELECTION",
        ready: false,
        image_url: null,
        full_image_url: null,
        width: null,
        height: null,
        blockers: [{
          code: "CANDIDATE_NOT_SELECTED",
          message: "请先人工校对文字并暂选一张当前页候选",
          section: "generate",
          candidate_id: null,
        }],
      },
    ],
  };
}

function renderPreview() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <MobilePreview chapterId="chapter-1" onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

describe("MobilePreview 章节手机预览", () => {
  beforeEach(() => {
    previewApi.mockReset().mockResolvedValue(previewFixture());
  });

  it("就绪页渲染图片，未达标页原位显示页码与阻塞原因", async () => {
    renderPreview();
    await waitFor(() => expect(screen.getByText("第 2 页")).toBeTruthy());
    expect(screen.getByText(/暂选一张当前页候选/)).toBeTruthy();
    const image = screen.getByAltText("第 1 页");
    expect(image.getAttribute("src")).toContain("thumbnail/640");
    expect(screen.getByText(/1 页未达标/)).toBeTruthy();
    expect(previewApi).toHaveBeenCalledWith("chapter-1");
  });

  it("最大片高变化会重算切片边界与片数", async () => {
    renderPreview();
    await waitFor(() => expect(screen.getByText(/页 · /)).toBeTruthy());
    // 默认 1080 目标宽度：第 1 页缩放高 1620，占位页 1440，间距 16，
    // 片高 4096 → 1620+16+1440=3076 一片。
    expect(screen.getByText(/1 片$/)).toBeTruthy();
    const selects = screen.getAllByRole("combobox");
    // 控件顺序：视口 / 目标宽度 / 页间距 / 最大片高。
    fireEvent.change(selects[3], { target: { value: "2048" } });
    await waitFor(() => expect(screen.getByText(/2 片$/)).toBeTruthy());
    expect(screen.getByText("片 2")).toBeTruthy();
  });

  it("加载失败时显示错误态", async () => {
    previewApi.mockReset().mockRejectedValue(new Error("boom"));
    renderPreview();
    await waitFor(() => expect(screen.getByText("预览加载失败，请稍后重试。")).toBeTruthy());
  });
});
