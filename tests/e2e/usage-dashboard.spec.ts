import { expect, test } from "@playwright/test";

test.describe("用量与成本看板（离线种子数据）", () => {
  test("设置页提供看板入口并到达 /settings/usage", async ({ page }) => {
    await page.goto("/settings");
    const entry = page.getByRole("link", { name: "用量与成本看板" });
    await expect(entry).toBeVisible();
    await entry.click();
    await expect(page).toHaveURL(/\/settings\/usage$/);
    await expect(page.getByText("系统设置 / 用量与成本看板")).toBeVisible();
  });

  test("看板按原币种展示估算与账单，且两者永不相加", async ({ page }) => {
    await page.goto("/settings/usage");
    await expect(page.getByText("正在汇总 API 用量与成本数据…")).toBeHidden();

    const kpi = page.getByLabel("用量概览指标");
    await expect(kpi.getByText("¥0.15")).toBeVisible(); // ≈ 0.13 + 0.01 + 0.01 CNY 估算合计
    await expect(kpi.getByText("$1.32")).toBeVisible(); // USD 估算合计，独立展示
    await expect(kpi.getByText("¥66.00")).toBeVisible(); // 对账账单事实
    await expect(kpi.getByText("账单事实与估算永不相加")).toBeVisible();
    // CNY 估算 0.15 与账单 66.00 的和不得出现在 KPI 中
    await expect(kpi.getByText("¥67.15")).toHaveCount(0);
    await expect(kpi.getByText("无估算数据")).toHaveCount(0);

    const breakdown = page.getByLabel("供应商与模型分解");
    const cliRow = breakdown.locator("tbody tr").filter({ hasText: "e2e-gate-cli-codex" });
    await expect(cliRow.getByText("仅计量")).toBeVisible();
    await expect(cliRow.getByRole("cell", { name: "CLI", exact: true })).toBeVisible();

    // 未返回用量的一次调用在明细行显示“无用量返回”，绝不显示为 0 成本；
    // 分解表展示组级聚合语义（该组含估算 → 预估），两者层级不同
    const attempts = page.getByLabel("调用明细");
    await expect(attempts.getByText("无用量返回").first()).toBeVisible();
    await expect(breakdown.getByText("预估").first()).toBeVisible();
  });

  test("预算超出时显示提醒", async ({ page }) => {
    await page.goto("/settings/usage");
    await expect(page.getByLabel("预算提醒")).toBeVisible();
    await expect(page.getByText("尚未设置预算提醒")).toBeVisible();

    await page.getByRole("button", { name: "设置预算" }).click();
    await page.getByLabel("预算币种").fill("CNY");
    await page.getByLabel("预算金额").fill("0.01");
    await page.getByRole("button", { name: "保存" }).click();

    const status = page.locator(".usage-budget-status");
    await expect(status).toContainText("已超出预算 ¥0.01");
    await expect(status).toContainText("估算支出 ¥0.15");
  });

  test("明细行打开抽屉回显 Request ID 与换路标记，Esc 关闭", async ({ page }) => {
    await page.goto("/settings/usage");
    await expect(page.getByLabel("调用明细")).toBeVisible();

    const switchedRow = page
      .locator(".usage-table.attempts tbody tr")
      .filter({ hasText: "换路" })
      .first();
    await switchedRow.click();
    const dialog = page.getByRole("dialog", { name: "调用尝试详情" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText("req_e2e_image_0")).toBeVisible();
    await expect(dialog.getByText("换路")).toBeVisible();
    await expect(dialog.getByText(/单次尝试金额不在账本读取接口返回/)).toBeVisible();
    await expect(dialog.locator("button").first()).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });

  test("通道筛选只作用于调用明细，CLI 行保留且不显示免费", async ({ page }) => {
    await page.goto("/settings/usage");
    await expect(page.getByLabel("调用明细")).toBeVisible();

    const attempts = page.getByLabel("调用明细");
    await expect(attempts.getByText("e2e-gate-cli-codex")).toBeVisible();
    await expect(attempts.getByText("e2e-gate-image").first()).toBeVisible();

    await page.getByLabel("按通道筛选").selectOption("CLI");
    await expect(attempts.getByText("e2e-gate-cli-codex")).toBeVisible();
    await expect(attempts.getByText("e2e-gate-image")).toHaveCount(0);
    // 汇总层（供应商/模型分解）不受通道筛选影响：summary 契约无通道参数
    const breakdown = page.getByLabel("供应商与模型分解");
    await expect(breakdown.getByText("e2e-gate-image").first()).toBeVisible();
  });

  test("导出 CSV 生成按币种分行的文件", async ({ page }) => {
    await page.goto("/settings/usage");
    await expect(page.getByLabel("调用明细")).toBeVisible();

    const [download] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /导出 CSV/ }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^usage-\d{4}-\d{2}-\d{2}\.csv$/);
  });

  test("项目筛选后看板数据保留", async ({ page }) => {
    await page.goto("/settings/usage");
    await expect(page.getByLabel("调用明细")).toBeVisible();

    await page.getByLabel("按项目筛选").selectOption({ label: "e2e-gate-ready" });
    const kpi = page.getByLabel("用量概览指标");
    await expect(kpi.getByText("¥0.15")).toBeVisible();
    // 契约行为：对账记录无法归因到项目，项目筛选下 billed 恒为空
    await expect(kpi.getByText("暂无对账记录")).toBeVisible();
    await expect(page.getByText("未找到匹配的调用记录")).toHaveCount(0);
  });
});
