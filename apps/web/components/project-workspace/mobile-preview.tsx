"use client";

import { useQuery } from "@tanstack/react-query";
import Image from "next/image";
import { useMemo, useState } from "react";

import { api, publicUrl, type MobilePreviewPage } from "@/lib/api";
import { computeStrip, scaledPageHeight } from "@/lib/mobile-preview";

import { SceneModal } from "./scene-modal";

const VIEWPORT_WIDTHS = [360, 390, 414];
const TARGET_WIDTHS = [750, 1080, 1440];
const PAGE_GAPS = [0, 8, 16, 24, 32];
const SLICE_HEIGHTS = [2048, 4096, 8192];
/** 未达标页占位卡在图像像素空间的高度比例（4:3 竖版占位）。 */
const PLACEHOLDER_RATIO = 4 / 3;

/**
 * PUB-01A 章节手机阅读预览（只读）。
 * 条带按页码顺序渲染：通过页显示采用图，未达标页原位显示占位卡与
 * 阻塞原因；切片边界按目标宽度/页间距/最大片高在图像像素空间计算，
 * 再乘视口比例换算成屏幕像素，不做任何写操作。
 */
export function MobilePreview({
  chapterId,
  onClose,
}: {
  chapterId: string;
  onClose: () => void;
}) {
  const [viewportWidth, setViewportWidth] = useState(390);
  const [targetWidth, setTargetWidth] = useState(1080);
  const [gapPx, setGapPx] = useState(16);
  const [sliceHeight, setSliceHeight] = useState(4096);

  const preview = useQuery({
    queryKey: ["chapter-mobile-preview", chapterId],
    queryFn: () => api.chapterMobilePreview(chapterId),
  });

  const strip = useMemo(() => {
    const pages = preview.data?.pages ?? [];
    return computeStrip(
      pages.map((page) => ({
        id: page.page_id,
        heightPx: page.ready
          ? scaledPageHeight(page.width, page.height, targetWidth)
          : Math.round(targetWidth * PLACEHOLDER_RATIO),
      })),
      gapPx,
      sliceHeight,
    );
  }, [preview.data, targetWidth, gapPx, sliceHeight]);

  const scale = viewportWidth / targetWidth;
  const stripDisplayHeight = Math.round(strip.totalHeight * scale);
  const pages = preview.data?.pages ?? [];
  const blockedCount = pages.filter((page) => !page.ready).length;

  return (
    <SceneModal title={preview.data ? `手机预览 · ${preview.data.title}` : "手机预览"} onClose={onClose} wide>
      <div className="mobile-preview-toolbar">
        <label>
          视口
          <select value={viewportWidth} onChange={(event) => setViewportWidth(Number(event.target.value))}>
            {VIEWPORT_WIDTHS.map((value) => (
              <option key={value} value={value}>{value}px</option>
            ))}
          </select>
        </label>
        <label>
          目标宽度
          <select value={targetWidth} onChange={(event) => setTargetWidth(Number(event.target.value))}>
            {TARGET_WIDTHS.map((value) => (
              <option key={value} value={value}>{value}px</option>
            ))}
          </select>
        </label>
        <label>
          页间距
          <select value={gapPx} onChange={(event) => setGapPx(Number(event.target.value))}>
            {PAGE_GAPS.map((value) => (
              <option key={value} value={value}>{value}px</option>
            ))}
          </select>
        </label>
        <label>
          最大片高
          <select value={sliceHeight} onChange={(event) => setSliceHeight(Number(event.target.value))}>
            {SLICE_HEIGHTS.map((value) => (
              <option key={value} value={value}>{value}px</option>
            ))}
          </select>
        </label>
        <span className="mobile-preview-summary">
          {preview.data
            ? `${pages.length} 页 · ${blockedCount ? `${blockedCount} 页未达标 · ` : ""}${strip.slices.length} 片`
            : ""}
        </span>
      </div>
      {preview.isLoading && <p className="muted">正在加载章节预览…</p>}
      {preview.isError && <p className="muted">预览加载失败，请稍后重试。</p>}
      {preview.data && !pages.length && <p className="muted">该章节还没有分页。</p>}
      {pages.length > 0 && (
        <div className="mobile-preview-stage">
          <div className="mobile-preview-device" style={{ width: viewportWidth }}>
            <div className="mobile-preview-strip" style={{ height: stripDisplayHeight }}>
              {pages.map((page) => {
                const layout = strip.items.find((item) => item.id === page.page_id);
                if (!layout) return null;
                return (
                  <PreviewSlot
                    key={page.page_id}
                    page={page}
                    top={layout.top * scale}
                    height={layout.height * scale}
                    width={viewportWidth}
                  />
                );
              })}
              {strip.slices.slice(1).map((slice) => (
                <div
                  key={slice.index}
                  className="mobile-preview-slice-boundary"
                  style={{ top: slice.start * scale }}
                >
                  <em>片 {slice.index}</em>
                </div>
              ))}
              {strip.slices
                .filter((slice) => slice.oversized)
                .map((slice) => (
                  <div
                    key={`oversized-${slice.index}`}
                    className="mobile-preview-oversized"
                    style={{ top: slice.start * scale, height: (slice.end - slice.start) * scale }}
                  >
                    <em>片 {slice.index} · 单页超片高</em>
                  </div>
                ))}
            </div>
          </div>
        </div>
      )}
    </SceneModal>
  );
}

function PreviewSlot({
  page,
  top,
  height,
  width,
}: {
  page: MobilePreviewPage;
  top: number;
  height: number;
  width: number;
}) {
  const imageUrl = publicUrl(page.image_url);
  if (page.ready && imageUrl) {
    return (
      <div className="mobile-preview-page" style={{ top, height, width }}>
        <Image src={imageUrl} alt={`第 ${page.page_number} 页`} fill sizes={`${width}px`} unoptimized />
      </div>
    );
  }
  const reason = page.blockers[0]?.message ?? "未达到生产通过条件";
  const extra = page.blockers.length > 1 ? ` 等 ${page.blockers.length} 项` : "";
  return (
    <div className="mobile-preview-page blocked" style={{ top, height, width }}>
      <strong>第 {page.page_number} 页</strong>
      <span>{reason}{extra}</span>
    </div>
  );
}
