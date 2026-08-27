import { describe, expect, it } from "vitest";

import {
  ACTIVE_TASK_STATUSES,
  TERMINAL_TASK_STATUSES,
  activePollInterval,
  hasActiveItem,
  isActiveTaskStatus,
  isTerminalTaskStatus,
} from "./task-status";

describe("共享任务状态语义", () => {
  it("覆盖任务执行链上的全部活动状态，含上传、检查与修复阶段", () => {
    for (const status of [
      "WAITING",
      "QUEUED",
      "PREPARING",
      "UPLOADING_REFERENCES",
      "GENERATING",
      "OCR_CHECKING",
      "CONSISTENCY_CHECKING",
      "REPAIRING",
      "RUNNING",
    ]) {
      expect(isActiveTaskStatus(status), `${status} 应为活动状态`).toBe(true);
    }
  });

  it("终态及非执行链状态不属于活动状态", () => {
    expect(isActiveTaskStatus("COMPLETED")).toBe(false);
    expect(isActiveTaskStatus("FAILED")).toBe(false);
    expect(isActiveTaskStatus("CANCELLED")).toBe(false);
    expect(isActiveTaskStatus("NEEDS_REVIEW")).toBe(false);
    expect(isActiveTaskStatus("READY")).toBe(false);
    expect(isActiveTaskStatus("NOT_A_STATUS")).toBe(false);
  });

  it("活动集合与终态集合互斥", () => {
    for (const status of TERMINAL_TASK_STATUSES) {
      expect(isActiveTaskStatus(status), `${status} 不应同时属于两个集合`).toBe(false);
    }
    for (const status of ACTIVE_TASK_STATUSES) {
      expect(TERMINAL_TASK_STATUSES).not.toContain(status);
    }
  });

  it("isTerminalTaskStatus 只认三种终态", () => {
    expect(isTerminalTaskStatus("COMPLETED")).toBe(true);
    expect(isTerminalTaskStatus("FAILED")).toBe(true);
    expect(isTerminalTaskStatus("CANCELLED")).toBe(true);
    expect(isTerminalTaskStatus("RUNNING")).toBe(false);
    expect(isTerminalTaskStatus("NEEDS_REVIEW")).toBe(false);
  });

  it("只要列表中还有任一活动条目就判定为需要继续轮询", () => {
    expect(hasActiveItem([{ status: "REPAIRING" }])).toBe(true);
    expect(hasActiveItem([{ status: "COMPLETED" }, { status: "CONSISTENCY_CHECKING" }])).toBe(true);
    expect(hasActiveItem([{ status: "COMPLETED" }, { status: "FAILED" }])).toBe(false);
    expect(hasActiveItem([])).toBe(false);
    expect(hasActiveItem(undefined)).toBe(false);
    expect(hasActiveItem(null)).toBe(false);
  });
});

describe("activePollInterval 轮询间隔门禁", () => {
  it("上传参考图与一致性检查阶段按传入间隔继续轮询", () => {
    expect(activePollInterval([{ status: "UPLOADING_REFERENCES" }], 2500)).toBe(2500);
    expect(activePollInterval([{ status: "CONSISTENCY_CHECKING" }], 3000)).toBe(3000);
    expect(activePollInterval([{ status: "OCR_CHECKING" }], 2000)).toBe(2000);
  });

  it("全部进入终态或列表为空时停止轮询", () => {
    expect(activePollInterval([{ status: "COMPLETED" }], 3000)).toBe(false);
    expect(activePollInterval([{ status: "FAILED" }], 3000)).toBe(false);
    expect(activePollInterval([{ status: "CANCELLED" }], 3000)).toBe(false);
    expect(activePollInterval([{ status: "COMPLETED" }, { status: "FAILED" }], 3000)).toBe(false);
    expect(activePollInterval([], 3000)).toBe(false);
    expect(activePollInterval(undefined, 3000)).toBe(false);
  });
});
