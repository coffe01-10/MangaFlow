import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ChapterProductionReadiness, type ExportBundle } from "@/lib/api";

import { LibrarySection } from "./library-section";
import { useLibraryWorkspace } from "./use-library-workspace";
import { useWorkspaceQueries } from "./use-workspace-queries";

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}));

const projectApi = vi.spyOn(api, "project");
const modelsApi = vi.spyOn(api, "models");
const chaptersApi = vi.spyOn(api, "chapters");
const charactersApi = vi.spyOn(api, "characters");
const pagesApi = vi.spyOn(api, "pages");
const libraryApi = vi.spyOn(api, "library");
const exportsApi = vi.spyOn(api, "exports");
const chapterProductionApi = vi.spyOn(api, "chapterProductionReadiness");
const createExport = vi.spyOn(api, "createExport");

const idleMutation = {
  isPending: false,
  mutate: vi.fn(),
};

function blockedProduction(): ChapterProductionReadiness {
  return {
    chapter_id: "chapter-1",
    ready: false,
    total_pages: 1,
    ready_pages: 0,
    pages: [{
      page_id: "page-1",
      state: "AWAITING_SELECTION",
      ready: false,
      selected_candidate_id: null,
      blockers: [{
        code: "CANDIDATE_NOT_SELECTED",
        message: "请先人工校对文字并暂选一张当前页候选",
        section: "generate",
        candidate_id: null,
      }],
    }],
  };
}

function readyProduction(): ChapterProductionReadiness {
  return {
    chapter_id: "chapter-1",
    ready: true,
    total_pages: 1,
    ready_pages: 1,
    pages: [{
      page_id: "page-1",
      state: "READY",
      ready: true,
      selected_candidate_id: "candidate-1",
      blockers: [],
    }],
  };
}

function LibraryHarness() {
  const queries = useWorkspaceQueries({
    id: "project-1",
    section: "library",
    assetView: "characters",
    selectedChapterId: "chapter-1",
  });
  const libraryWorkspace = useLibraryWorkspace({
    id: "project-1",
    section: "library",
    activeChapterId: queries.activeChapterId,
  });
  return (
    <LibrarySection
      pages={queries.pages}
      chapters={queries.chapters}
      characters={queries.characters}
      modelOptions={[]}
      openPreview={() => undefined}
      router={{ push: vi.fn() }}
      projectPath={(target) => `/projects/project-1/${target}`}
      rememberWorkspaceScroll={() => undefined}
      setSelectedPageId={() => undefined}
      libraryWorkspace={libraryWorkspace}
      generation={{
        deleteCandidate: idleMutation as never,
        retractSelectedCandidate: idleMutation as never,
      }}
    />
  );
}

function renderLibrary() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <LibraryHarness />
    </QueryClientProvider>,
  );
  return client;
}

describe("LibrarySection 导出阻塞", () => {
  beforeEach(() => {
    projectApi.mockReset().mockResolvedValue({
      id: "project-1",
      name: "演练",
      language: "zh-CN",
      reading_direction: "rtl",
      page_ratio: "b5_portrait",
      default_resolution: "2K",
      draft_resolution: "1K",
      workflow_mode: "SEMI_AUTO",
      default_concurrency: 4,
      default_style_id: null,
      consistency_check_enabled: true,
      text_model_alias: "text.fast",
      last_image_model_alias: null,
      default_text_model_id: null,
      last_image_model_id: null,
      created_at: "2026-08-29T10:00:00Z",
      updated_at: "2026-08-29T10:00:00Z",
      version: 1,
    });
    modelsApi.mockReset().mockResolvedValue([]);
    chaptersApi.mockReset().mockResolvedValue([{
      id: "chapter-1",
      project_id: "project-1",
      title: "一",
      ordinal: 1,
      status: "READY",
      current_source_revision_id: null,
      source_character_count: 0,
      segment_count: 0,
      page_count: 1,
      coverage_ratio: 1,
      created_at: "2026-08-29T10:00:00Z",
      updated_at: "2026-08-29T10:00:00Z",
      version: 1,
    }]);
    charactersApi.mockReset().mockResolvedValue([]);
    pagesApi.mockReset().mockResolvedValue([]);
    libraryApi.mockReset().mockResolvedValue({
      groups: [],
      total_candidates: 0,
      favorite_count: 0,
      next_cursor: null,
      limit: 30,
    });
    exportsApi.mockReset().mockResolvedValue([]);
    chapterProductionApi.mockReset().mockResolvedValue(blockedProduction());
    createExport.mockReset();
  });

  it("章节未生产通过时导出按钮禁用且不调用 createExport", async () => {
    renderLibrary();
    await waitFor(() => {
      expect(screen.getByText("0/1 页生产通过")).toBeInTheDocument();
    });
    expect(screen.getAllByText("请先人工校对文字并暂选一张当前页候选").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "PNG" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "PDF" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "JSON" })).toBeDisabled();
    expect(screen.getByText("素材库还是空的")).toBeInTheDocument();
    expect(createExport).not.toHaveBeenCalled();
  });

  it("章节就绪后导出成功会刷新 exports，失败展示用户可见错误", async () => {
    chapterProductionApi.mockResolvedValue(readyProduction());
    const bundle: ExportBundle = {
      id: "export-1",
      project_id: "project-1",
      chapter_id: "chapter-1",
      export_type: "PNG",
      byte_size: 2048,
      page_count: 1,
      created_at: "2026-08-29T10:00:00Z",
      download_url: "/api/v1/exports/export-1/download",
    };
    createExport.mockResolvedValueOnce(bundle);
    renderLibrary();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "PNG" })).toBeEnabled();
    });
    const before = exportsApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "PNG" }));
    await waitFor(() => {
      expect(createExport).toHaveBeenCalledWith("chapter-1", "PNG");
      expect(exportsApi.mock.calls.length).toBeGreaterThan(before);
    });

    createExport.mockRejectedValueOnce(new Error("第 1 页尚未达到生产通过状态"));
    fireEvent.click(screen.getByRole("button", { name: "PDF" }));
    await waitFor(() => {
      expect(screen.getByText("第 1 页尚未达到生产通过状态")).toBeInTheDocument();
    });
  });
});
