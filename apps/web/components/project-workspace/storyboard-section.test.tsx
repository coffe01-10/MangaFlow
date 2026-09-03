// V02-32: `?stress=100` swaps the storyboard section to the client-only
// stress fixture; without the param the product editor is untouched.
/* eslint-disable @typescript-eslint/no-explicit-any */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { beforeEach, vi } from "vitest";
import { describe, expect, it } from "vitest";

import { api } from "@/lib/api";

import { StoryboardSection } from "./storyboard-section";

vi.mock("next/link", () => ({ default: (props: { href: string; children: React.ReactNode }) => (
  <a href={props.href}>{props.children}</a>
) }));

const page = {
  id: "page-1",
  chapter_id: "chapter-1",
  page_number: 1,
  panel_count: 3,
  reading_direction: "rtl",
  estimated_text_chars: 20,
  estimated_bubbles: 1,
  source_coverage: { layout_mode: "dynamic" },
  selected_candidate_id: null,
  storyboard_version: 1,
  selected_candidate_ack_version: null,
  continuity_status: "READY",
  scene_ids: [],
  beat_ids: [],
  version: 1,
  canvas: null,
};

const pageProps = {
  chapters: { data: [] },
  characters: { data: [] },
  outfits: { data: [] },
  activeChapterId: null,
  setSelectedChapterId: () => undefined,
  replanPage: { isPending: false, error: null, mutate: () => undefined },
  projectPath: (target: string) => `/projects/p1/${target}`,
  initialPageId: null,
  focusCharacterId: null,
};

function renderSection() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <StoryboardSection
        {...(pageProps as any)}
        pages={{ data: [page] } as any}
      />
    </QueryClientProvider>,
  );
}

describe("storyboard section ?stress=100 gate", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/projects/p1/storyboard");
    vi.spyOn(api, "storyboard").mockReset();
  });

  it("带 stress=100：渲染 100 节点压力夹具，不请求分镜数据", async () => {
    window.history.replaceState(null, "", "/projects/p1/storyboard?stress=100");
    const storyboardSpy = vi.spyOn(api, "storyboard").mockResolvedValue({ page, panels: [], candidate_count: 0 } as never);
    renderSection();
    await screen.findByTestId("stress-canvas");
    expect(document.querySelectorAll(".canvas-object-layer rect")).toHaveLength(100);
    expect(screen.queryByRole("button", { name: "保存本页" })).toBeNull();
    expect(storyboardSpy).not.toHaveBeenCalled();
  });

  it("不带参数：仍渲染产品编辑器并正常请求分镜", async () => {
    const storyboardSpy = vi.spyOn(api, "storyboard").mockResolvedValue({ page, panels: [], candidate_count: 0 } as never);
    renderSection();
    await screen.findByTestId("canvas-page");
    expect(screen.queryByTestId("stress-canvas")).toBeNull();
    expect(storyboardSpy).toHaveBeenCalledWith("page-1");
  });
});
