import { chromium } from "playwright";

const webOrigin = process.env.MANGAFLOW_WEB_ORIGIN ?? "http://127.0.0.1:3000";
const apiOrigin = process.env.MANGAFLOW_API_ORIGIN ?? "http://127.0.0.1:8000/api/v1";

async function json(path, init = {}) {
  const response = await fetch(`${apiOrigin}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${init.method ?? "GET"} ${path}: ${response.status} ${await response.text()}`);
  return response.status === 204 ? null : response.json();
}

const project = await json("/projects", { method: "POST", body: JSON.stringify({ name: "工作流 100 节点性能门禁" }) });
let workflow;
let browser;

try {
  workflow = await json(`/projects/${project.id}/workflows`, {
    method: "POST",
    body: JSON.stringify({ name: "100 节点基准", template: "manga_default", description: "自动性能门禁，运行后删除" }),
  });
  const seeds = workflow.draft_graph.nodes;
  const nodes = Array.from({ length: 100 }, (_, index) => {
    const seed = seeds[index % seeds.length];
    return {
      ...seed,
      id: `benchmark-${String(index + 1).padStart(3, "0")}`,
      name: `${seed.name} ${index + 1}`,
      position: { x: (index % 10) * 270, y: Math.floor(index / 10) * 160 },
      inputs: seed.inputs.map((port) => ({ ...port })),
      outputs: seed.outputs.map((port) => ({ ...port })),
      config: { ...seed.config, condition: { ...seed.config.condition } },
    };
  });
  await json(`/workflows/${workflow.id}`, {
    method: "PATCH",
    body: JSON.stringify({ version: workflow.version, draft_graph: { schema_version: 2, nodes, edges: [] } }),
  });

  browser = await chromium.launch({ channel: "chrome", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  await page.goto(`${webOrigin}/projects/${project.id}/workflow`, { waitUntil: "networkidle" });
  await page.locator(".react-flow__node").first().waitFor();
  const rendered = await page.locator(".react-flow__node").count();
  if (rendered !== 100) throw new Error(`工作流画布仅渲染 ${rendered}/100 个节点`);

  const pane = page.locator(".react-flow__pane");
  const bounds = await pane.boundingBox();
  if (!bounds) throw new Error("找不到工作流画布");
  await page.evaluate(() => {
    window.__mangaflowFrameTimes = [];
    let previous = performance.now();
    const sample = (time) => {
      window.__mangaflowFrameTimes.push(time - previous);
      previous = time;
      if (window.__mangaflowFrameTimes.length < 1200) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
  });

  const start = Date.now();
  let direction = 1;
  while (Date.now() - start < 10_000) {
    const centerX = bounds.x + bounds.width / 2;
    const centerY = bounds.y + bounds.height / 2;
    await page.mouse.move(centerX, centerY);
    await page.mouse.down();
    await page.mouse.move(centerX + direction * 90, centerY + direction * 45, { steps: 8 });
    await page.mouse.up();
    await page.mouse.wheel(0, direction * 220);
    direction *= -1;
  }

  const frameTimes = await page.evaluate(() => window.__mangaflowFrameTimes.slice(10));
  const sorted = [...frameTimes].sort((left, right) => left - right);
  const averageFps = 1000 / (frameTimes.reduce((sum, value) => sum + value, 0) / frameTimes.length);
  const p99FrameTime = sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.99))];
  const onePercentLowFps = 1000 / p99FrameTime;
  console.log(JSON.stringify({ nodes: rendered, seconds: 10, average_fps: averageFps, one_percent_low_fps: onePercentLowFps }, null, 2));
  if (averageFps < 55 || onePercentLowFps < 45) {
    throw new Error(`工作流 FPS 未达标：平均 ${averageFps.toFixed(1)}，1% low ${onePercentLowFps.toFixed(1)}`);
  }
} finally {
  if (browser) await browser.close();
  if (workflow) {
    const deletedWorkflow = await fetch(`${apiOrigin}/workflows/${workflow.id}`, { method: "DELETE" });
    if (![200, 204, 404].includes(deletedWorkflow.status)) {
      throw new Error(`cleanup workflow failed: ${deletedWorkflow.status} ${await deletedWorkflow.text()}`);
    }
  }
  const deletedProject = await fetch(
    `${apiOrigin}/projects/${project.id}?confirm_name=${encodeURIComponent(project.name)}`,
    { method: "DELETE" },
  );
  if (![200, 204, 404].includes(deletedProject.status)) {
    throw new Error(`cleanup project failed: ${deletedProject.status} ${await deletedProject.text()}`);
  }
}
