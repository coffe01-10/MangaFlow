/**
 * PUB-01A 手机阅读预览的条带几何计算（纯函数，可单测）。
 *
 * 坐标一律在"目标宽度图像像素"空间：每页按原始宽高比缩放到目标宽度后的
 * 高度、页间距、切片最大高度都是图像像素；渲染时再乘 viewport/target
 * 的比例换算成屏幕像素。
 */

export interface PreviewStripItem {
  /** 页面在条带中的唯一 id（前端用 page_id）。 */
  id: string;
  /** 缩放后高度（图像像素）；占位页由调用方给固定值。 */
  heightPx: number;
  /** 参与切片的分页语义：占位诊断卡同样占据条带位置。 */
}

export interface PreviewSlice {
  /** 片序号（从 1 开始）。 */
  index: number;
  /** 片起点在条带中的像素位置。 */
  start: number;
  /** 片终点（不含），最后一片等于条带总高。 */
  end: number;
  /** 单页超过最大片高时独占一片并标记。 */
  oversized: boolean;
  /** 该片覆盖的条目 id（闭区间含义：完全落在片内的条目）。 */
  itemIds: string[];
}

export interface PreviewStrip {
  /** 条带总高（图像像素）。 */
  totalHeight: number;
  /** 各条目顶边位置与高度。 */
  items: { id: string; top: number; height: number }[];
  /** 切片结果；页间缝处切开，片内禁止半页。 */
  slices: PreviewSlice[];
}

/** 源图按目标宽度缩放后的高度；缺尺寸时回退 4:3 竖版占位高。 */
export function scaledPageHeight(
  width: number | null,
  height: number | null,
  targetWidth: number,
): number {
  if (width && height && width > 0 && height > 0) {
    return Math.round((height * targetWidth) / width);
  }
  return Math.round((targetWidth * 4) / 3);
}

/**
 * 计算竖排条带的切片边界。
 *
 * 策略（页间优先）：
 * - 条目按给定顺序排列，条目之间有 gapPx 间距（间距计入条带高度，且
 *   每个间距挂在前一个条目之后，切片边界落在间距末尾=下一条目顶边）。
 * - 顺序累加条目；当下一个条目的底边会使当前片超过 maxSlicePx 时，
 *   在该条目顶边的页间缝处收刀（当前片不含该条目）。
 * - 单个条目自身高度超过 maxSlicePx 时，该条目独占一片并标记
 *   oversized（PUB-01B 再定义真实导出的超限处理）。
 */
export function computeStrip(
  items: PreviewStripItem[],
  gapPx: number,
  maxSlicePx: number,
): PreviewStrip {
  const tops: { id: string; top: number; height: number }[] = [];
  let cursor = 0;
  items.forEach((item, index) => {
    tops.push({ id: item.id, top: cursor, height: Math.max(0, item.heightPx) });
    cursor += Math.max(0, item.heightPx) + (index < items.length - 1 ? gapPx : 0);
  });
  const totalHeight = cursor;
  const slices: PreviewSlice[] = [];
  if (!tops.length || maxSlicePx <= 0) {
    return { totalHeight, items: tops, slices };
  }

  let sliceStart = 0;
  let sliceItems: string[] = [];
  tops.forEach((entry) => {
    const bottom = entry.top + entry.height;
    if (entry.top > sliceStart && bottom - sliceStart > maxSlicePx) {
      slices.push({
        index: slices.length + 1,
        start: sliceStart,
        end: entry.top,
        oversized: false,
        itemIds: sliceItems,
      });
      sliceStart = entry.top;
      sliceItems = [];
    }
    sliceItems.push(entry.id);
    if (entry.height > maxSlicePx) {
      slices.push({
        index: slices.length + 1,
        start: sliceStart,
        end: bottom,
        oversized: true,
        itemIds: sliceItems,
      });
      sliceStart = bottom;
      sliceItems = [];
    }
  });
  if (sliceStart < totalHeight || !slices.length) {
    slices.push({
      index: slices.length + 1,
      start: sliceStart,
      end: totalHeight,
      oversized: false,
      itemIds: sliceItems,
    });
  }
  return { totalHeight, items: tops, slices };
}
