import type { ImageModelAlias, MangaPage } from "@/lib/api";

type PageGenerationSource = Pick<
  MangaPage,
  "scene_ids" | "beat_ids" | "source_coverage"
>;

export function getPageStructureIssue(page: PageGenerationSource | null): string | null {
  if (!page) return "请先选择要生成的页面";
  if (!page.source_coverage.complete) return "当前页没有完整覆盖原文，暂不能生成图片";
  if (!page.scene_ids.length || !page.beat_ids.length) {
    return "当前分页是旧版规划，缺少剧本与分镜来源。请先到漫画剧本页删除分页，再重新生成剧本并计算分页";
  }
  return null;
}

export function getPageGenerationIssue(
  page: PageGenerationSource | null,
  selectedModel: ImageModelAlias | null,
): string | null {
  return getPageStructureIssue(page) ?? (
    selectedModel ? null : "请先选择一个支持当前任务的图片模型"
  );
}
