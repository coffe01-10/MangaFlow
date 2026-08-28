import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

async function createProject(request: APIRequestContext, name: string): Promise<string> {
  const created = await request.post("http://127.0.0.1:8000/api/v1/projects", {
    data: { name },
  });
  expect(created.ok()).toBeTruthy();
  return (await created.json() as { id: string }).id;
}

async function projectByName(request: APIRequestContext, name: string): Promise<string> {
  const listed = await request.get("http://127.0.0.1:8000/api/v1/projects");
  expect(listed.ok()).toBeTruthy();
  const match = ((await listed.json()) as { id: string; name: string }[]).find((item) => item.name === name);
  expect(match, name).toBeTruthy();
  return match!.id;
}

async function publishedGraph(request: APIRequestContext, projectId: string) {
  const workflows = await request.get(`http://127.0.0.1:8000/api/v1/projects/${projectId}/workflows`);
  expect(workflows.ok()).toBeTruthy();
  const items = await workflows.json() as { id: string }[];
  expect(items.length).toBeGreaterThan(0);
  const versions = await request.get(`http://127.0.0.1:8000/api/v1/workflows/${items[0].id}/versions`);
  expect(versions.ok()).toBeTruthy();
  const published = await versions.json() as { graph: { nodes: Array<{ id: string; position: { x: number; y: number } }> } }[];
  return published[0] ?? null;
}

function assertGraphMatches(
  published: { nodes: Array<{ id: string; position: { x: number; y: number } }> } | undefined,
  draft: { nodes?: Array<{ id: string; position: { x: number; y: number } }> } | undefined,
) {
  expect(published?.nodes?.length).toBeGreaterThan(0);
  for (const node of draft?.nodes ?? []) {
    const match = published?.nodes.find((item) => item.id === node.id);
    expect(match?.position).toEqual(node.position);
  }
}

function fakeJob(projectId: string, status: string) {
  const now = new Date().toISOString();
  return {
    id: "e2e-job-1",
    project_id: projectId,
    target_type: "PAGE",
    target_id: "e2e-page-1",
    job_type: "PAGE_INSPECT",
    priority: 0,
    status,
    progress: status === "COMPLETED" ? 100 : 40,
    attempt_count: 1,
    max_attempts: 3,
    model_alias: "text.fast",
    error_code: null,
    error_message: null,
    created_at: now,
    updated_at: now,
    started_at: now,
    finished_at: status === "COMPLETED" ? now : null,
    archived_at: null,
    usage_summary: {},
    estimated_cost: null,
    result: null,
    duration_ms: 1200,
  };
}

async function waitForStudio(page: Page) {
  await expect(page.getByText("流程编排", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "发布", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: /剧情解析/ })).toBeVisible();
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
}

async function nudgeDraft(page: Page, dx: number) {
  const node = page.locator(".react-flow__node").first();
  await node.waitFor();
  const box = await node.boundingBox();
  if (!box) throw new Error("工作流节点尚未渲染");
  await page.mouse.move(box.x + 36, box.y + 12);
  await page.mouse.down();
  await page.mouse.move(box.x + 36 + dx, box.y + 12, { steps: 6 });
  await page.mouse.up();
}

test("慢保存时继续编辑并发布最新草稿", async ({ page, request }) => {
  const id = await createProject(request, "慢保存发布");
  let releaseFirst: (() => void) | undefined;
  const firstHeld = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  let holdFirst = true;
  const patchBodies: Array<{ draft_graph?: { nodes?: unknown[] } }> = [];
  const publishUrls: string[] = [];

  await page.route("**/api/v1/workflows/*", async (route) => {
    const incoming = route.request();
    if (incoming.method() !== "PATCH") {
      await route.continue();
      return;
    }
    patchBodies.push(JSON.parse(incoming.postData() || "{}"));
    if (holdFirst) {
      holdFirst = false;
      await firstHeld;
    }
    await route.continue();
  });
  page.on("request", (outgoing) => {
    if (outgoing.method() === "POST" && /\/api\/v1\/workflows\/[^/]+\/publish$/.test(outgoing.url())) {
      publishUrls.push(outgoing.url());
    }
  });

  await page.goto(`/projects/${id}/workflow`);
  await waitForStudio(page);
  await nudgeDraft(page, 80);
  await expect.poll(() => patchBodies.length, { timeout: 8_000 }).toBe(1);
  expect(publishUrls).toEqual([]);

  await nudgeDraft(page, 160);
  await page.getByRole("button", { name: "发布", exact: true }).click();
  expect(publishUrls).toEqual([]);
  expect(patchBodies.length).toBe(1);

  releaseFirst?.();
  await expect.poll(() => patchBodies.length, { timeout: 15_000 }).toBeGreaterThanOrEqual(2);
  await expect.poll(() => publishUrls.length, { timeout: 15_000 }).toBe(1);
  const firstPositions = JSON.stringify(patchBodies[0]?.draft_graph);
  const latestPositions = JSON.stringify(patchBodies.at(-1)?.draft_graph);
  expect(latestPositions).not.toEqual(firstPositions);
  await expect(page.getByText("已发布不可变版本")).toBeVisible();
  await expect(page.getByText("尚未发布")).toHaveCount(0);
  const published = await publishedGraph(request, id);
  assertGraphMatches(published?.graph, patchBodies.at(-1)?.draft_graph as { nodes?: Array<{ id: string; position: { x: number; y: number } }> });
});

test("保存失败后恢复且不发布旧稿", async ({ page, request }) => {
  const id = await createProject(request, "保存失败恢复");
  let failNextPatch = true;
  const publishUrls: string[] = [];

  await page.route("**/api/v1/workflows/*", async (route) => {
    const incoming = route.request();
    if (incoming.method() !== "PATCH") {
      await route.continue();
      return;
    }
    if (failNextPatch) {
      failNextPatch = false;
      await route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ detail: "网络中断" }),
      });
      return;
    }
    await route.continue();
  });
  page.on("request", (outgoing) => {
    if (outgoing.method() === "POST" && /\/api\/v1\/workflows\/[^/]+\/publish$/.test(outgoing.url())) {
      publishUrls.push(outgoing.url());
    }
  });

  await page.goto(`/projects/${id}/workflow`);
  await waitForStudio(page);
  await nudgeDraft(page, 90);
  await expect(page.getByText("保存失败")).toBeVisible();

  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect(page.getByText("草稿保存失败，未发布")).toBeVisible();
  expect(publishUrls).toEqual([]);
  expect(await publishedGraph(request, id)).toBeNull();

  const recoveredPatches: Array<{ draft_graph?: { nodes?: Array<{ id: string; position: { x: number; y: number } }> } }> = [];
  page.on("request", (outgoing) => {
    if (outgoing.method() === "PATCH" && /\/api\/v1\/workflows\/[^/]+$/.test(outgoing.url())) {
      recoveredPatches.push(JSON.parse(outgoing.postData() || "{}"));
    }
  });
  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect.poll(() => publishUrls.length, { timeout: 15_000 }).toBe(1);
  await expect(page.getByText("已发布不可变版本")).toBeVisible();
  const published = await publishedGraph(request, id);
  assertGraphMatches(published?.graph, recoveredPatches.at(-1)?.draft_graph);
});

test("任务活动阶段持续轮询，进入终态后停止", async ({ page, request }) => {
  const id = await createProject(request, "任务轮询");
  let status: "CONSISTENCY_CHECKING" | "REPAIRING" | "COMPLETED" = "CONSISTENCY_CHECKING";
  const jobPolls: number[] = [];

  await page.route(/\/api\/v1\/projects\/[^/]+\/jobs/, async (route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    jobPolls.push(Date.now());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([fakeJob(id, status)]),
    });
  });

  await page.goto(`/projects/${id}/jobs`);
  await expect(page.getByText("CONSISTENCY_CHECKING", { exact: true })).toBeVisible();
  await expect.poll(() => jobPolls.length, { timeout: 12_000 }).toBeGreaterThanOrEqual(2);

  status = "REPAIRING";
  await expect(page.getByText("REPAIRING", { exact: true })).toBeVisible({ timeout: 12_000 });
  const afterRepair = jobPolls.length;
  await expect.poll(() => jobPolls.length, { timeout: 12_000 }).toBeGreaterThan(afterRepair);

  status = "COMPLETED";
  await expect(page.getByRole("link", { name: /检查页面 · COMPLETED/ })).toBeVisible({
    timeout: 12_000,
  });
  await expect(page.getByText("正在运行")).toHaveCount(0);
  await page.waitForTimeout(3500);
  const stoppedAt = jobPolls.length;
  await page.waitForTimeout(6500);
  expect(jobPolls.length).toBeLessThanOrEqual(stoppedAt + 1);
});

async function assertProductionGate(
  page: Page,
  id: string,
  expected: { ready: boolean; message?: string; exportEnabled: boolean },
) {
  await page.goto(`/projects/${id}/generate`);
  await expect(page.locator(".production-gate")).toBeVisible();
  if (expected.ready) {
    await expect(page.getByText("当前页已通过，可以进入下一页")).toBeVisible();
    await expect(page.getByRole("button", { name: /生成下一页/ })).toBeEnabled();
  } else {
    await expect(page.getByText("当前页尚未生产通过")).toBeVisible();
    await expect(page.getByRole("button", { name: /生成下一页/ })).toBeDisabled();
    if (expected.message) {
      await expect(page.getByText(expected.message).first()).toBeVisible();
    }
  }
  await page.goto(`/projects/${id}/library`);
  const png = page.getByRole("button", { name: "PNG", exact: true });
  if (expected.exportEnabled) await expect(png).toBeEnabled();
  else await expect(png).toBeDisabled();
}

test("存在候选时按缺项/失败/过期阻断，全通过才放行导出", async ({ page, request }) => {
  const missing = await projectByName(request, "e2e-gate-missing");
  const failed = await projectByName(request, "e2e-gate-failed");
  const stale = await projectByName(request, "e2e-gate-stale");
  const ready = await projectByName(request, "e2e-gate-ready");

  await assertProductionGate(page, missing, {
    ready: false,
    message: "暂选候选尚未完成视觉质量检查",
    exportEnabled: false,
  });
  await assertProductionGate(page, failed, {
    ready: false,
    message: "视觉检查未通过，请修复或重新生成后再次检查",
    exportEnabled: false,
  });
  await assertProductionGate(page, stale, {
    ready: false,
    message: "分镜已经变化，请明确沿用旧候选或按当前分镜重新生成",
    exportEnabled: false,
  });
  await assertProductionGate(page, ready, { ready: true, exportEnabled: true });
});
