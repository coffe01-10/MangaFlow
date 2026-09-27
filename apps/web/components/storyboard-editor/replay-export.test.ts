// Replay export unit tests: frame painting is verified against a stubbed 2d
// context (jsdom has no real canvas), the MediaRecorder path is exercised with
// stubbed captureStream/MediaRecorder globals.
import { afterEach, describe, expect, it, vi } from "vitest";

import { canExportReplay, downloadBlob, drawReplayFrame, exportReplayVideo } from "./replay-export";
import type { CanvasState, ReplayEntry } from "./storyboard-history";

const canvas = { width_mm: 182, height_mm: 257, bleed_mm: 3, safe_mm: 5, unit: "mm" };

const state = (x: number): CanvasState => ({
  panels: {
    "p-1": { rect: { x, y: 0.1, width: 0.4, height: 0.3 }, rotation: 0, z_order: 1 },
    "p-2": { rect: { x: 0.5, y: 0.5, width: 0.3, height: 0.2 }, rotation: 0, z_order: 2 },
  },
  bubbles: {
    "d-1": { type: "ellipse", rect: { x: 0.6, y: 0.12, width: 0.2, height: 0.1 }, rotation: 0 },
  },
  sfx: { "p-1": { 0: { x: 0.3, y: 0.3, rotation: 0, size: 0.08 } } },
});

const timeline: ReplayEntry[] = [
  { label: "初始", at: 0, state: state(0.1) },
  { label: "拖动格子", at: 1, state: state(0.2) },
];

function stubContext() {
  const calls: Record<string, unknown[]> = { fillRect: [], strokeRect: [], ellipse: [], rect: [], fillText: [], fill: [] };
  const ctx = {
    calls,
    fillStyle: "", strokeStyle: "", lineWidth: 0, font: "",
    fillRect: (x: number, y: number, w: number, h: number) => calls.fillRect.push([x, y, w, h]),
    strokeRect: (x: number, y: number, w: number, h: number) => calls.strokeRect.push([x, y, w, h]),
    ellipse: (...args: number[]) => calls.ellipse.push(args),
    rect: (...args: number[]) => calls.rect.push(args),
    beginPath: () => undefined,
    moveTo: () => undefined,
    lineTo: () => undefined,
    closePath: () => undefined,
    stroke: () => undefined,
    fill: () => calls.fill.push([]),
    fillText: (...args: unknown[]) => calls.fillText.push(args),
  };
  return ctx as unknown as CanvasRenderingContext2D & { calls: typeof calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (HTMLCanvasElement.prototype as unknown as Record<string, unknown>).captureStream;
});

describe("replay-export", () => {
  it("drawReplayFrame 按归一化坐标画格子/气泡/拟声词/帧标", () => {
    const ctx = stubContext();
    drawReplayFrame(ctx, timeline[1], 1, 2, 720, 1018);
    // 两格：各自 fillRect+strokeRect，p-1 的 x=0.2 → 144px
    expect(ctx.calls.strokeRect.length).toBe(2);
    (ctx.calls.strokeRect[0] as number[]).forEach((value, index) =>
      expect(value).toBeCloseTo([144, 101.8, 288, 305.4][index], 4));
    // 椭圆气泡走 ellipse，中心点在 rect 中心。
    expect((ctx.calls.ellipse[0] as number[])[0]).toBeCloseTo(0.7 * 720, 3);
    // 拟声词菱形走 fill，帧标走 fillText。
    expect(ctx.calls.fill.length).toBeGreaterThan(0);
    expect(ctx.calls.fillText.at(-1)).toEqual(["2/2  拖动格子", expect.any(Number), expect.any(Number)]);
  });

  it("环境无 MediaRecorder 时 canExportReplay=false 且导出返回 null", async () => {
    vi.stubGlobal("MediaRecorder", undefined);
    expect(canExportReplay()).toBe(false);
    expect(await exportReplayVideo({ timeline, canvas, speed: 1 })).toBeNull();
  });

  it("exportReplayVideo 逐帧录制并产出 webm Blob", async () => {
    const ctx = stubContext();
    const requestFrame = vi.fn();
    const track = { requestFrame, stop: vi.fn() };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(ctx as never);
    (HTMLCanvasElement.prototype as unknown as Record<string, unknown>).captureStream = () => ({
      getVideoTracks: () => [track],
      getTracks: () => [track],
    });
    class FakeRecorder {
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      constructor(public stream: unknown, public options: unknown) {}
      start() {}
      stop() {
        this.ondataavailable?.({ data: new Blob(["chunk"], { type: "video/webm" }) });
        this.onstop?.();
      }
    }
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    const frames: number[] = [];
    const blob = await exportReplayVideo({ timeline, canvas, speed: 4, onFrame: (index) => frames.push(index) });
    expect(blob).not.toBeNull();
    expect(blob!.type).toBe("video/webm");
    expect(frames).toEqual([0, 1]);
    expect(requestFrame).toHaveBeenCalledTimes(2);
    expect(track.stop).toHaveBeenCalled();
  });

  it("downloadBlob 生成 a[download] 并回收 objectURL", () => {
    const urls: string[] = [];
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:fake"),
      revokeObjectURL: vi.fn((url: string) => urls.push(url)),
    });
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    });
    const name = downloadBlob(new Blob(["x"], { type: "video/webm" }), "分镜回放-P003.webm");
    expect(name).toBe("分镜回放-P003.webm");
    expect(clicked).toEqual(["分镜回放-P003.webm"]);
    expect(urls).toEqual(["blob:fake"]);
  });
});
