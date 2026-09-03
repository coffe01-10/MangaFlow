// Storyboard 100-node fixed-window measurement (V02-32, optional tool).
//
// Renders the client-side `?stress=100` fixture (20 panels + 80 bubbles, never
// persisted) through the real page canvas and samples requestAnimationFrame
// frame times over a fixed 10-second window per round. Every round and every
// raw sample is written to output/storyboard-fps/ — results are never cherry-
// picked and there is deliberately NO FPS threshold: this environment has no
// Windows trackpad gate machine, so the script only reports (NOT RUN in the
// PR body). It is not part of `npm run check` or CI.
//
// Usage: node scripts/storyboard-stress-fps-gate.mjs  (requires web+API dev
// servers and Chrome; MANGAFLOW_WEB_ORIGIN / MANGAFLOW_API_ORIGIN override).
import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const webOrigin = process.env.MANGAFLOW_WEB_ORIGIN ?? "http://127.0.0.1:3000";
const apiOrigin = process.env.MANGAFLOW_API_ORIGIN ?? "http://127.0.0.1:8000/api/v1";
const WINDOW_MS = 10_000;
const ROUNDS = 2;

async function json(path, init = {}) {
  const response = await fetch(`${apiOrigin}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

const project = await json("/projects", { method: "POST", body: JSON.stringify({ name: "分镜 100 节点压力测量" }) });
let browser;

function summarize(frameTimes) {
  const sorted = [...frameTimes].sort((left, right) => left - right);
  const measuredMs = frameTimes.reduce((sum, value) => sum + value, 0);
  return {
    samples: frameTimes.length,
    measured_ms: measuredMs,
    average_fps: 1000 / (measuredMs / frameTimes.length),
    p95_frame_ms: sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))],
    p99_frame_ms: sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.99))],
    max_frame_ms: sorted.at(-1),
  };
}

try {
  browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await page.goto(`${webOrigin}/projects/${project.id}/storyboard?stress=100`, { waitUntil: "networkidle" });
  await page.locator('[data-testid="stress-canvas"]').waitFor();
  const rendered = await page.locator(".canvas-object-layer rect").count();
  if (rendered !== 100) throw new Error(`压力画布仅渲染 ${rendered}/100 个矢量对象`);

  const pane = page.locator('[data-testid="canvas-page"]');
  const bounds = await pane.boundingBox();
  if (!bounds) throw new Error("找不到页画布");
  const centerX = bounds.x + bounds.width / 2;
  const centerY = bounds.y + bounds.height / 2;
  let direction = 1;
  const exerciseCanvas = async (durationMs) => {
    const startedAt = Date.now();
    while (Date.now() - startedAt < durationMs) {
      // 选中一个对象并拖拽，配合 Ctrl+滚轮缩放，覆盖拖动预览与视口手势。
      await page.mouse.click(centerX, centerY);
      await page.mouse.move(centerX, centerY);
      await page.mouse.down();
      await page.mouse.move(centerX + direction * 120, centerY + direction * 60, { steps: 10 });
      await page.mouse.up();
      await page.keyboard.down("Control");
      await page.mouse.wheel(0, direction * 240);
      await page.keyboard.up("Control");
      direction *= -1;
    }
  };

  // Warm browser/JIT and the gesture handlers before the measured windows; the
  // measured window itself is a full, fixed 10s per round.
  await exerciseCanvas(2_000);
  await page.waitForTimeout(250);

  const rounds = [];
  for (let round = 0; round < ROUNDS; round++) {
    await page.evaluate(() => {
      window.__mangaflowFrameTimes = [];
      window.__mangaflowSampling = true;
      let previous = performance.now();
      const sample = (time) => {
        if (!window.__mangaflowSampling) return;
        window.__mangaflowFrameTimes.push(time - previous);
        previous = time;
        requestAnimationFrame(sample);
      };
      requestAnimationFrame(sample);
    });
    await exerciseCanvas(WINDOW_MS);
    await page.evaluate(() => {
      window.__mangaflowSampling = false;
    });
    const frameTimes = await page.evaluate(() => window.__mangaflowFrameTimes.slice(10));
    // 保留全部样本与全部轮次：不挑最好一次，也不设阈值。
    rounds.push({ round: round + 1, window_ms: WINDOW_MS, frame_times_ms: frameTimes, ...summarize(frameTimes) });
  }

  const report = {
    fixture: "storyboard ?stress=100 (20 panels × 4 bubbles, client-only)",
    rendered_objects: rendered,
    viewport: { width: 1440, height: 900 },
    rounds,
    threshold: "none (report-only; no gate in this environment)",
  };
  const outputDir = "output/storyboard-fps";
  mkdirSync(outputDir, { recursive: true });
  const outputPath = `${outputDir}/stress-${new Date().toISOString().replace(/[:.]/g, "-")}.json`;
  writeFileSync(outputPath, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report.rounds.map(({ frame_times_ms, ...summary }) => ({ ...summary, samples_kept: frame_times_ms.length })), null, 2));
  console.log(`全部 ${ROUNDS} 轮原始样本已写入 ${outputPath}`);
} finally {
  if (browser) await browser.close();
  const deletedProject = await fetch(
    `${apiOrigin}/projects/${project.id}?confirm_name=${encodeURIComponent(project.name)}`,
    { method: "DELETE" },
  );
  if (![200, 204, 404].includes(deletedProject.status)) {
    throw new Error(`cleanup project failed: ${deletedProject.status} ${await deletedProject.text()}`);
  }
}
