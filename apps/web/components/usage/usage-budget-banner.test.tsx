import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { UsageSummaryGroup } from "@/lib/api";

import { UsageBudgetBanner } from "./usage-budget-banner";

const groups: UsageSummaryGroup[] = [
  {
    day: "2026-09-15",
    provider: "usage-provider",
    model_id: "usage-model",
    channel: "HTTP_API",
    attempt_count: 2,
    succeeded_count: 2,
    failed_count: 0,
    pending_count: 0,
    input_tokens: null,
    output_tokens: null,
    cached_input_tokens: null,
    output_images: null,
    usage_status_counts: {},
    estimated_costs: [{ currency: "CNY", amount: "1.5" }],
  },
];

describe("UsageBudgetBanner 表单校验反馈", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });
  afterEach(() => {
    cleanup();
  });

  it("非法币种保存被拒绝并给出原因，而不是静默无效果", () => {
    render(<UsageBudgetBanner groups={groups} />);
    fireEvent.click(screen.getByRole("button", { name: "设置预算" }));
    fireEvent.change(screen.getByLabelText("预算币种"), { target: { value: "CN" } });
    fireEvent.change(screen.getByLabelText("预算金额"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(screen.getByRole("alert").textContent).toContain("币种须为 3 位字母");
    expect(window.localStorage.getItem("mangaflow.usage-budget")).toBeNull();
  });

  it("非正数金额保存被拒绝并给出原因", () => {
    render(<UsageBudgetBanner groups={groups} />);
    fireEvent.click(screen.getByRole("button", { name: "设置预算" }));
    fireEvent.change(screen.getByLabelText("预算币种"), { target: { value: "CNY" } });
    fireEvent.change(screen.getByLabelText("预算金额"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(screen.getByRole("alert").textContent).toContain("预算金额须为正数");
    expect(window.localStorage.getItem("mangaflow.usage-budget")).toBeNull();
  });

  it("修正输入并重新输入时错误提示消失，合法输入保存成功", () => {
    render(<UsageBudgetBanner groups={groups} />);
    fireEvent.click(screen.getByRole("button", { name: "设置预算" }));
    fireEvent.change(screen.getByLabelText("预算币种"), { target: { value: "CN" } });
    fireEvent.change(screen.getByLabelText("预算金额"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    expect(screen.getByRole("alert")).toBeTruthy();

    // 重新输入即清除错误；合法值保存后表单收起。
    fireEvent.change(screen.getByLabelText("预算币种"), { target: { value: "CNY" } });
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(window.localStorage.getItem("mangaflow.usage-budget")).toBe(
      JSON.stringify({ currency: "CNY", amount: "10" }),
    );
    expect(screen.getByRole("button", { name: "设置预算" })).toBeTruthy();
  });
});
