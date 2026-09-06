import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Project, type Script } from "@/lib/api";

import ProjectWorkspace from "./project-workspace";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "project-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/projects/project-1/source",
}));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => <a href={href}>{children}</a>,
}));

const projectApi = vi.spyOn(api, "project");
const modelsApi = vi.spyOn(api, "models");
const chaptersApi = vi.spyOn(api, "chapters");
const scriptApi = vi.spyOn(api, "script");
const jobsApi = vi.spyOn(api, "jobs");

function projectFixture(): Project {
  return {
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
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 1,
  };
}

function renderWorkspace() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProjectWorkspace section="source" />
    </QueryClientProvider>,
  );
}

describe("ProjectWorkspace 项目载入失败", () => {
  beforeEach(() => {
    projectApi.mockReset().mockResolvedValue(projectFixture());
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
      page_count: 0,
      coverage_ratio: 1,
      created_at: "2026-09-01T00:00:00Z",
      updated_at: "2026-09-01T00:00:00Z",
      version: 1,
    }]);
    scriptApi.mockReset().mockResolvedValue({
      chapter_id: "chapter-1",
      status: "READY",
      revision_no: 1,
      coverage: {},
      scenes: [],
    } satisfies Script);
    jobsApi.mockReset().mockResolvedValue([]);
  });

  it("项目查询 rejected 时展示错误与重试，而不是永久加载", async () => {
    projectApi.mockRejectedValue(new Error("项目服务暂时不可用"));
    renderWorkspace();
    expect(await screen.findByText("项目无法打开")).toBeInTheDocument();
    expect(screen.getByText("项目服务暂时不可用")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回项目列表" })).toBeInTheDocument();
    // 错误分支必须先于加载分支：spinner 文案不允许再出现。
    expect(screen.queryByText("加载项目工作区…")).not.toBeInTheDocument();
  });

  it("错误界面上的重试会重新发起项目请求", async () => {
    projectApi.mockRejectedValue(new Error("项目服务暂时不可用"));
    renderWorkspace();
    await screen.findByText("项目无法打开");
    const before = projectApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    await waitFor(() => {
      expect(projectApi.mock.calls.length).toBeGreaterThan(before);
    });
  });
});
