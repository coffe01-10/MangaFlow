import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

async function createProject(request: APIRequestContext, name: string): Promise<string> {
  const created = await request.post("http://127.0.0.1:8000/api/v1/projects", {
    data: { name },
  });
  expect(created.ok()).toBeTruthy();
  return (await created.json() as { id: string }).id;
}

async function importSource(request: APIRequestContext, projectId: string) {
  const imported = await request.post(
    `http://127.0.0.1:8000/api/v1/projects/${projectId}/sources/import`,
    {
      data: {
        title: "第一章",
        text: "雨停之前，他把伞递给她。巷口的灯还亮着。",
        source_type: "PASTE",
      },
    },
  );
  expect(imported.ok(), await imported.text()).toBeTruthy();
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

  await page.getByRole("button", { name: "发布", exact: true }).click();
  await expect.poll(() => publishUrls.length, { timeout: 15_000 }).toBe(1);
  await expect(page.getByText("已发布不可变版本")).toBeVisible();
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

test("生产门禁未通过时阻断生成下一页和整章导出", async ({ page, request }) => {
  const id = await createProject(request, "门禁阻断");
  await importSource(request, id);

  await page.goto(`/projects/${id}/library`);
  await expect(page.getByText("整章导出门禁")).toBeVisible();
  await expect(page.getByRole("button", { name: "PNG", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "PDF", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "JSON", exact: true })).toBeDisabled();

  await page.goto(`/projects/${id}/generate`);
  await expect(page.getByText("没有可抽卡页面")).toBeVisible();
  await expect(page.getByRole("button", { name: /生成下一页/ })).toHaveCount(0);
});
