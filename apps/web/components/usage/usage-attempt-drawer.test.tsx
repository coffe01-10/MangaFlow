import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { ModelCallAttempt } from "@/lib/api";

import { UsageAttemptDrawer } from "./usage-attempt-drawer";

const attempt: ModelCallAttempt = {
  id: "attempt-1",
  job_id: "job-1",
  project_id: "project-1",
  job_attempt: 1,
  dispatch_no: 1,
  dispatch_request_id: "dispatch-1",
  route_switched: false,
  outcome: "SUCCEEDED",
  channel: "HTTP_API",
  provider: "usage-provider",
  model_id: "model-a",
  catalog_model_id: null,
  connection_id: null,
  selected_key_id: null,
  request_id: "req-1",
  probe_id: null,
  chapter_id: null,
  page_id: null,
  panel_id: null,
  candidate_id: null,
  started_at: "2026-09-01T10:00:00Z",
  finished_at: null,
  duration_ms: 1200,
  usage: null,
  usage_status: null,
  usage_source: null,
  unit_kind: null,
  input_tokens: null,
  output_tokens: null,
  cached_input_tokens: null,
  cache_hit: null,
  output_images: null,
  output_image_dims: null,
  output_asset_ids: null,
  route_reason: null,
  route_score: null,
  error_code: null,
  error_message: null,
};

// 宿主每次渲染都产生新的 onClose 闭包（与 usage-dashboard 的内联箭头一致）：
// 抽屉若把 onClose 放进 effect 依赖，父级任何重渲染都会重跑焦点捕获。
function DrawerHost() {
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  return (
    <>
      <button type="button" onClick={() => setTick((tick) => tick + 1)}>触发父级重渲染</button>
      <button type="button" id="opener" onClick={() => setOpen(true)}>触发按钮</button>
      {open ? <UsageAttemptDrawer attempt={attempt} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

describe("UsageAttemptDrawer 焦点还原", () => {
  it("抽屉打开期间父级重渲染（新 onClose 闭包）不抢焦点：Esc 关闭后焦点回到触发元素", () => {
    render(<DrawerHost />);
    const opener = document.getElementById("opener")!;
    opener.focus();
    fireEvent.click(opener);

    // 打开期间焦点在抽屉关闭按钮上。
    expect(screen.getByRole("button", { name: "关闭调用尝试详情" })).toHaveFocus();

    // 父级重渲染产生新的 onClose：mount-only 的焦点捕获不得重跑——旧实现
    // 的 cleanup 会在对话框中途把焦点短暂抢回触发元素再拉回（焦点抖动），
    // 读屏器用户会听到焦点跳出对话框。
    const openerFocusSpy = vi.spyOn(opener, "focus");
    fireEvent.click(screen.getByRole("button", { name: "触发父级重渲染" }));
    expect(screen.getByRole("button", { name: "关闭调用尝试详情" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "关闭调用尝试详情" })).toHaveFocus();
    expect(openerFocusSpy).not.toHaveBeenCalled();

    // Esc 关闭：焦点还原到触发按钮。
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});
