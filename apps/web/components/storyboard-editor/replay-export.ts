// Replay export (制作回放导出): renders each recorded timeline frame onto an
// HTMLCanvasElement and captures it with MediaRecorder → .webm video. Drawing
// is pure canvas2d (no DOM dependency) so tests can drive it with a stubbed
// context. The encoder path needs a browser with canvas.captureStream +
// MediaRecorder; callers must check `canExportReplay()` first.
import type { CanvasInfo } from "@/lib/api";

import type { ReplayEntry } from "./storyboard-history";

const PAGE_BG = "#fffdf8";
const INK = "#262019";
const MUTED = "#8a8272";

/** One frame of the export: page background, panel outlines with reading
 * badges, bubble outlines, and a footer strip with the command label. */
export function drawReplayFrame(
  ctx: CanvasRenderingContext2D,
  frame: ReplayEntry,
  index: number,
  count: number,
  width: number,
  height: number,
) {
  const px = (rect: { x: number; y: number; width: number; height: number }) => ({
    x: rect.x * width,
    y: rect.y * height,
    w: rect.width * width,
    h: rect.height * height,
  });
  ctx.fillStyle = PAGE_BG;
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = INK;
  ctx.lineWidth = Math.max(1.5, width / 480);
  // 面板按 z_order 叠放（回放态 rect 已是归一化坐标）。
  const panels = Object.entries(frame.state.panels)
    .sort(([, a], [, b]) => a.z_order - b.z_order);
  panels.forEach(([, panel], order) => {
    const rect = px(panel.rect);
    ctx.fillStyle = "rgba(38,32,25,0.05)";
    ctx.fillRect(rect.x, rect.y, rect.w, rect.h);
    ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
    ctx.fillStyle = INK;
    ctx.font = `600 ${Math.max(10, width / 60)}px sans-serif`;
    ctx.fillText(String(order + 1), rect.x + 4, rect.y + Math.max(14, width / 55));
  });
  ctx.lineWidth = Math.max(1, width / 720);
  for (const bubble of Object.values(frame.state.bubbles)) {
    const rect = px(bubble.rect);
    ctx.beginPath();
    if (bubble.type === "ellipse") {
      ctx.ellipse(rect.x + rect.w / 2, rect.y + rect.h / 2, rect.w / 2, rect.h / 2, 0, 0, Math.PI * 2);
    } else {
      ctx.rect(rect.x, rect.y, rect.w, rect.h);
    }
    ctx.stroke();
  }
  // 拟声词状态不含文字（CanvasState.sfx 只有几何），以小菱形标出存在即可。
  ctx.fillStyle = MUTED;
  for (const byIndex of Object.values(frame.state.sfx))
    for (const sfx of Object.values(byIndex)) {
      const cx = sfx.x * width;
      const cy = sfx.y * height;
      const r = Math.max(3, sfx.size * width * 0.5);
      ctx.beginPath();
      ctx.moveTo(cx, cy - r);
      ctx.lineTo(cx + r, cy);
      ctx.lineTo(cx, cy + r);
      ctx.lineTo(cx - r, cy);
      ctx.closePath();
      ctx.fill();
    }
  // 底部帧标：帧号 + 命令标签，方便在分享件里辨认动作。
  ctx.fillStyle = "rgba(38,32,25,0.85)";
  ctx.font = `${Math.max(9, width / 80)}px sans-serif`;
  const footer = `${index + 1}/${count}  ${frame.label}`;
  ctx.fillText(footer, width * 0.02, height - Math.max(8, height * 0.012));
}

export function canExportReplay(): boolean {
  return typeof MediaRecorder !== "undefined"
    && typeof HTMLCanvasElement !== "undefined"
    && typeof HTMLCanvasElement.prototype.captureStream === "function";
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 把整段时间线录成 webm：captureStream(0) 手动要帧，每帧按回放节奏停留
 * 620ms/speed。返回 null 表示环境不支持（调用方已先用 canExportReplay 挡过，
 * 这里兜底返回 null 而不是抛错）。 */
export async function exportReplayVideo(input: {
  timeline: ReplayEntry[];
  canvas: CanvasInfo;
  speed: number;
  width?: number;
  onFrame?: (index: number, count: number) => void;
}): Promise<Blob | null> {
  const { timeline, canvas, speed } = input;
  if (!canExportReplay() || timeline.length === 0) return null;
  const width = input.width ?? 720;
  const height = Math.round(width * (canvas.height_mm / Math.max(1, canvas.width_mm)));
  const surface = document.createElement("canvas");
  surface.width = width;
  surface.height = height;
  const ctx = surface.getContext("2d");
  const stream = surface.captureStream(0);
  const track = stream.getVideoTracks()[0] as CanvasCaptureMediaStreamTrack | undefined;
  if (!ctx || !track) return null;
  const chunks: Blob[] = [];
  const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
  recorder.ondataavailable = (event) => {
    if (event.data.size > 0) chunks.push(event.data);
  };
  const done = new Promise<void>((resolve) => {
    recorder.onstop = () => resolve();
  });
  recorder.start();
  const frameMs = 620 / Math.max(0.25, speed);
  for (let index = 0; index < timeline.length; index += 1) {
    drawReplayFrame(ctx, timeline[index], index, timeline.length, width, height);
    track.requestFrame?.();
    input.onFrame?.(index, timeline.length);
    await sleep(frameMs);
  }
  recorder.stop();
  await done;
  stream.getTracks().forEach((item) => item.stop());
  return new Blob(chunks, { type: "video/webm" });
}

/** 触发浏览器下载（返回文件名便于测试断言）。 */
export function downloadBlob(blob: Blob, filename: string): string {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
  return filename;
}
