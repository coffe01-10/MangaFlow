import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { StoryboardEditor } from "./storyboard-editor";

const storyboard = vi.spyOn(api, "storyboard");

const page = {
  id: "page-1",
  page_number: 1,
  panel_count: 3,
  storyboard_version: 1,
  estimated_text_chars: 20,
  estimated_bubbles: 1,
  scene_ids: ["scene-1"],
  beat_ids: ["beat-1"],
  source_coverage: { layout_mode: "dynamic" },
  continuity_status: "READY",
  selected_candidate_id: null,
} as never;

describe("StoryboardEditor", () => {
  beforeEach(() => {
    window.localStorage.clear();
    storyboard.mockReset().mockResolvedValue({
      page,
      candidate_count: 0,
      panels: [{
        id: "panel-1",
        page_id: "page-1",
        reading_order: 1,
        bounds: { x: 0, y: 0, width: 1, height: 1 },
        shot_type: "establishing",
        camera_angle: "eye_level",
        camera_height: "eye_level",
        characters: [],
        character_presence: {},
        props: [],
        outfits: {},
        actions: { script_action: "角色推门进入" },
        expressions: {},
        background: "教室",
        bubble_regions: [],
        sound_effects: [],
        bleed: false,
        borderless: false,
        locked_fields: [],
        version: 1,
        dialogues: [],
      }],
    } as never);
  });

  it("捕获拖拽指针并持续调整属性面板宽度", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <StoryboardEditor
          chapterId="chapter-1"
          pages={[page]}
          characters={[]}
          outfits={[]}
          onReplan={() => undefined}
          replanPending={false}
        />
      </QueryClientProvider>,
    );

    const separator = await screen.findByRole("separator", { name: "调整属性面板宽度" });
    const worktable = separator.parentElement!;
    let captured = false;
    Object.defineProperties(separator, {
      setPointerCapture: { value: vi.fn(() => { captured = true; }) },
      hasPointerCapture: { value: vi.fn(() => captured) },
      releasePointerCapture: { value: vi.fn(() => { captured = false; }) },
    });
    vi.spyOn(worktable, "getBoundingClientRect").mockReturnValue({
      right: 1000,
    } as DOMRect);

    fireEvent.pointerDown(separator, { pointerId: 7, clientX: 610 });
    fireEvent.pointerMove(separator, { pointerId: 7, clientX: 500 });

    await waitFor(() => expect(separator).toHaveAttribute("aria-valuenow", "500"));
    expect(window.localStorage.getItem("mangaflow.storyboard-inspector-width")).toBe("500");
  });
});
