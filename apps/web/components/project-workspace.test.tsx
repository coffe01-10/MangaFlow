import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type Character, type MangaPage, type Outfit, type Project, type Script } from "@/lib/api";

import ProjectWorkspace from "./project-workspace";

// useSearchParams 的返回值需要按用例切换（?page= 深链回退提示），用
// vi.hoisted 提供可变宿主，避免模块级 mock 工厂引用未初始化变量。
const mockSearchParams = vi.hoisted(() => ({ current: new URLSearchParams() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "project-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => mockSearchParams.current,
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
const charactersApi = vi.spyOn(api, "characters");
const outfitsApi = vi.spyOn(api, "outfits");
const pagesApi = vi.spyOn(api, "pages");
const storyboardApi = vi.spyOn(api, "storyboard");
const sceneAssetsAllApi = vi.spyOn(api, "sceneAssetsAll");
const assignSceneOutfitsApi = vi.spyOn(api, "assignSceneOutfits");

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

describe("ProjectWorkspace ?page= 跨章深链回退提示", () => {
  function pageFixture(id: string, pageNumber: number): MangaPage {
    return {
      id,
      chapter_id: "chapter-1",
      page_number: pageNumber,
      revision_no: 1,
      page_function: "dialogue",
      panel_count: 4,
      reading_direction: "rtl",
      resolution: "1K",
      status: "PLANNED",
      estimated_text_chars: 40,
      estimated_bubbles: 2,
      source_coverage: { complete: true, ranges: [] },
      selected_candidate_id: null,
      storyboard_version: 1,
      selected_candidate_ack_version: 1,
      continuity_status: "PASSED",
      scene_ids: [],
      beat_ids: [],
      version: 1,
    };
  }

  function renderStoryboardWorkspace() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    return render(
      <QueryClientProvider client={client}>
        <ProjectWorkspace section="storyboard" />
      </QueryClientProvider>,
    );
  }

  beforeEach(() => {
    mockSearchParams.current = new URLSearchParams();
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
      page_count: 2,
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
    charactersApi.mockReset().mockResolvedValue([]);
    outfitsApi.mockReset().mockResolvedValue([]);
    pagesApi.mockReset().mockResolvedValue([pageFixture("page-1", 1), pageFixture("page-2", 2)]);
    storyboardApi.mockReset().mockResolvedValue({
      page: pageFixture("page-1", 1),
      panels: [],
      candidate_count: 0,
    });
  });

  it("?page= 指向他章页面时给出可见回退提示，而不是静默错页", async () => {
    mockSearchParams.current = new URLSearchParams({ page: "page-other-chapter" });
    renderStoryboardWorkspace();

    const banner = await screen.findByText("深链目标页不在当前章节");
    expect(banner).toBeInTheDocument();
    expect(screen.getByText(/已显示本章第 1 页/)).toBeInTheDocument();
  });

  it("回退提示可关闭，且同一深链不再重复打扰", async () => {
    mockSearchParams.current = new URLSearchParams({ page: "page-other-chapter" });
    renderStoryboardWorkspace();

    await screen.findByText("深链目标页不在当前章节");
    fireEvent.click(screen.getByRole("button", { name: "知道了" }));
    await waitFor(() => {
      expect(screen.queryByText("深链目标页不在当前章节")).not.toBeInTheDocument();
    });
  });

  it("?page= 命中当前章节页面时不渲染回退提示", async () => {
    mockSearchParams.current = new URLSearchParams({ page: "page-2" });
    renderStoryboardWorkspace();

    // 等编辑器真正落位（pages 数据已就绪）再断言无提示。
    await screen.findByTestId("canvas-page");
    expect(screen.queryByText("深链目标页不在当前章节")).not.toBeInTheDocument();
  });

  // #384：有页章节但目标页不存在（失效/已删链接）→ 横幅必须出现。
  it("#384 有页章节缺目标页时横幅出现（就绪判定不依赖目标存在）", async () => {
    mockSearchParams.current = new URLSearchParams({ page: "page-deleted" });
    renderStoryboardWorkspace();

    expect(await screen.findByText("深链目标页不在当前章节")).toBeInTheDocument();
    expect(screen.getByText(/已显示本章第 1 页/)).toBeInTheDocument();
  });

  // #384：旧的 pages.data.length > 0 前置把零页章节的横幅整个吞掉——
  // 零页 + 深链同样是"目标页不在当前章节"，必须给出可见反馈。
  it("#384 零页章节 + 深链时横幅同样出现，且文案不谎称已显示第 1 页", async () => {
    pagesApi.mockResolvedValue([]);
    mockSearchParams.current = new URLSearchParams({ page: "page-anywhere" });
    renderStoryboardWorkspace();

    const banner = await screen.findByText("深链目标页不在当前章节");
    expect(banner).toBeInTheDocument();
    expect(screen.getByText(/本章还没有任何页面/)).toBeInTheDocument();
    expect(screen.queryByText(/已显示第 1 页/)).toBeNull();
  });
});

describe("ProjectWorkspace assignOutfit 失效集合（#544）", () => {
  it("指定场景服装成功后失效 script/pages/generation-workbench/candidates", async () => {
    const character: Character = {
      id: "character-1",
      project_id: "project-1",
      primary_name: "林澈",
      aliases: [],
      alias_conflict: false,
      canonical_description: "",
      locked_features: [],
      forbidden_changes: [],
      status: "ACTIVE",
      version: 1,
      references: [],
    };
    const outfit: Outfit = {
      id: "outfit-1",
      project_id: "project-1",
      character_id: "character-1",
      name: "校服",
      components: {},
      state_rules: {},
      locked_fields: [],
      reference_asset_ids: [],
      status: "ACTIVE",
      version: 1,
    };
    mockSearchParams.current = new URLSearchParams();
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
      page_count: 1,
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
      scenes: [{
        id: "scene-1",
        ordinal: 1,
        location: "学校天台",
        scene_asset_id: null,
        scene_asset_variant_id: null,
        time_label: "黄昏",
        weather: "雨",
        purpose: "相遇",
        emotional_arc: "平静到紧张",
        source_range: {},
        outfit_assignments: {},
        locked_fields: [],
        version: 1,
        beats: [],
      }],
    } satisfies Script);
    jobsApi.mockReset().mockResolvedValue([]);
    charactersApi.mockReset().mockResolvedValue([character]);
    outfitsApi.mockReset().mockResolvedValue([outfit]);
    pagesApi.mockReset().mockResolvedValue([]);
    storyboardApi.mockReset();
    sceneAssetsAllApi.mockReset().mockResolvedValue([]);
    assignSceneOutfitsApi.mockReset().mockResolvedValue({ ok: true } as never);

    const client = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    render(
      <QueryClientProvider client={client}>
        <ProjectWorkspace section="script" />
      </QueryClientProvider>,
    );

    const wardrobeSelect = await screen.findByLabelText("林澈");
    fireEvent.change(wardrobeSelect, { target: { value: "outfit-1" } });
    await waitFor(() => {
      expect(assignSceneOutfitsApi).toHaveBeenCalledWith("scene-1", { "character-1": "outfit-1" });
    });
    // 后端会 bump storyboard_version：候选卡的 version_state 标签由
    // ["candidates"] 查询渲染，缺失失效会让卡片标签落后于已失效的工作台横幅。
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["candidates"] });
    });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["script", "chapter-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["pages", "chapter-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["generation-workbench"] });
  });
});

// #659：跨分区返回的滚动恢复与 dynamic 分区 chunk 加载竞态。jsdom 没有
// 布局：scrollY/scrollTo 用可调 maxScroll 的夹具模拟「矮文档钳制 → 内容
// 撑高」，rAF 用手动 flush 的桩驱动，验证键只在滚动确认落地后消费。
describe("ProjectWorkspace 跨分区滚动恢复（#659）", () => {
  function installScrollModel(maxScroll: number) {
    const state = { maxScroll, y: 0 };
    Object.defineProperty(window, "scrollY", { configurable: true, get: () => state.y });
    const scrollTo = vi.spyOn(window, "scrollTo").mockImplementation(((options?: ScrollToOptions) => {
      state.y = Math.max(0, Math.min(options?.top ?? 0, state.maxScroll));
    }) as never);
    return { state, scrollTo };
  }

  function installRaf() {
    const pending = new Map<number, FrameRequestCallback>();
    let nextHandle = 1;
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      const handle = nextHandle++;
      pending.set(handle, callback);
      return handle;
    });
    const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation((handle) => {
      pending.delete(handle);
    });
    const flush = (frames: number) => {
      for (let index = 0; index < frames; index += 1) {
        const callbacks = [...pending.values()];
        pending.clear();
        for (const callback of callbacks) callback(16 * (index + 1));
      }
    };
    return { flush, pendingCount: () => pending.size, raf, cancel };
  }

  let scrollStub: ReturnType<typeof installScrollModel>;
  let rafStub: ReturnType<typeof installRaf>;

  beforeEach(() => {
    mockSearchParams.current = new URLSearchParams();
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
    window.sessionStorage.clear();
    scrollStub = installScrollModel(400);
    rafStub = installRaf();
  });

  afterEach(() => {
    rafStub.raf.mockRestore();
    rafStub.cancel.mockRestore();
    scrollStub.scrollTo.mockRestore();
    delete (window as { scrollY?: number }).scrollY;
    window.sessionStorage.clear();
  });

  it("矮文档钳制时保留恢复键；文档撑高后恢复到位并消费键", async () => {
    window.sessionStorage.setItem("mangaflow.workspace-scroll.project-1", "3000");
    renderWorkspace();
    // 数据就绪后恢复 effect 把首帧排上 rAF（旧实现里这帧会无条件删键）。
    await waitFor(() => expect(rafStub.pendingCount()).toBe(1));
    // 分区 chunk 未就绪：文档矮，scrollTo 被钳制，键必须保留待重试。
    rafStub.flush(30);
    expect(scrollStub.state.y).toBe(400);
    expect(scrollStub.scrollTo).toHaveBeenCalledWith({ top: 3000, behavior: "auto" });
    expect(window.sessionStorage.getItem("mangaflow.workspace-scroll.project-1")).toBe("3000");
    // chunk 落地、内容撑高：下一帧恢复到位，落地后才消费键。
    scrollStub.state.maxScroll = 5000;
    rafStub.flush(1);
    expect(scrollStub.state.y).toBe(3000);
    expect(window.sessionStorage.getItem("mangaflow.workspace-scroll.project-1")).toBeNull();
  });

  it("目标始终无法到达时按重试上限放弃并消费键，避免恢复键永驻", async () => {
    window.sessionStorage.setItem("mangaflow.workspace-scroll.project-1", "3000");
    renderWorkspace();
    await waitFor(() => expect(rafStub.pendingCount()).toBe(1));
    // 远超上限的帧数：文档最终也没有长高，恢复放弃（停在钳制位置），
    // 键被消费，不会在后续无关刷新时跳到陈旧位置。
    rafStub.flush(200);
    expect(scrollStub.state.y).toBe(400);
    expect(window.sessionStorage.getItem("mangaflow.workspace-scroll.project-1")).toBeNull();
    expect(rafStub.pendingCount()).toBe(0);
  });

  it("卸载时取消挂起的恢复帧，cleanup 后不再触发 scrollTo，键未消费", async () => {
    scrollStub.state.maxScroll = 5000;
    window.sessionStorage.setItem("mangaflow.workspace-scroll.project-1", "1200");
    const view = renderWorkspace();
    await waitFor(() => expect(rafStub.pendingCount()).toBe(1));
    view.unmount();
    expect(rafStub.cancel).toHaveBeenCalled();
    expect(rafStub.pendingCount()).toBe(0);
    rafStub.flush(5);
    expect(scrollStub.scrollTo).not.toHaveBeenCalled();
    expect(window.sessionStorage.getItem("mangaflow.workspace-scroll.project-1")).toBe("1200");
  });
});
