"use client";

// Cross-page spread preview (跨页对开预览): the neighbouring page renders as
// a panel-only layout skeleton flanking the live canvas, positioned like a
// printed book spread — for RTL (manga) the earlier page sits on the right,
// the next page on the left; LTR mirrors it. Purely presentational: geometry
// comes from the already-cached `api.storyboard` query and never enters the
// page save payload.
import type { CanvasInfo, MangaPage, StoryboardPanel } from "@/lib/api";

import { panelGeometry, panelRect } from "./geometry";
import { storyboardCopy } from "./storyboard-copy";

export function SpreadPage({
  page,
  panels,
  canvas,
  label,
  loading,
  width,
  onJump,
}: {
  page: MangaPage;
  /** 邻页已加载的格子；加载中/失败时传空数组，骨架仍占位以稳住布局。 */
  panels: StoryboardPanel[];
  canvas: CanvasInfo;
  label: string;
  loading?: boolean;
  /** 骨架页宽度（px），随缩放比例走。 */
  width: number;
  onJump: () => void;
}) {
  const ordered = [...panels].sort((a, b) => a.reading_order - b.reading_order);
  return <div
    className="spread-page"
    data-testid={`spread-page-${page.id}`}
    style={{ width: `${Math.max(120, width)}px` }}
  >
    <div className="spread-caption">
      <span>P.{String(page.page_number).padStart(3, "0")} · {label}</span>
      <button type="button" onClick={onJump}>{storyboardCopy.spreadJump}</button>
    </div>
    <div
      className="spread-sheet"
      style={{ aspectRatio: `${canvas.width_mm} / ${canvas.height_mm}` }}
    >
      {ordered.map((panel) => {
        const geometry = panelGeometry(panel);
        // 骨架只画版面轮廓：矩形取 geometry.rect（含位移草稿），多边形与其余
        // 情况退到 bounds 包围盒——与原生 PanelNode.From 的取径一致。
        const rect = geometry?.type === "rect" && geometry.rect ? geometry.rect : panelRect(panel);
        return <div
          key={panel.id}
          className="spread-panel"
          style={{
            left: `${rect.x * 100}%`,
            top: `${rect.y * 100}%`,
            width: `${rect.width * 100}%`,
            height: `${rect.height * 100}%`,
          }}
        >{panel.reading_order}</div>;
      })}
      {loading && <span className="spread-loading">{storyboardCopy.spreadLoading}</span>}
    </div>
  </div>;
}
