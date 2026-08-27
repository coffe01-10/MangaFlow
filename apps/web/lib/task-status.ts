/**
 * 任务与候选的共享状态语义。
 *
 * 任务进入上传参考图、检查或修复阶段后仍然属于活动状态，
 * 所有轮询方必须持续刷新到终态，否则页面会停留在过期数据上。
 */
export const ACTIVE_TASK_STATUSES = [
  "WAITING",
  "QUEUED",
  "PREPARING",
  "UPLOADING_REFERENCES",
  "GENERATING",
  "OCR_CHECKING", // 历史任务兼容
  "CONSISTENCY_CHECKING",
  "REPAIRING",
  "RUNNING",
] as const;

/** 终态之后不会再有写入；晚到的旧结果不允许重新触发轮询。 */
export const TERMINAL_TASK_STATUSES = ["COMPLETED", "FAILED", "CANCELLED"] as const;

export function isActiveTaskStatus(status: string): boolean {
  return (ACTIVE_TASK_STATUSES as readonly string[]).includes(status);
}

export function isTerminalTaskStatus(status: string): boolean {
  return (TERMINAL_TASK_STATUSES as readonly string[]).includes(status);
}

interface StatusedItem {
  status: string;
}

export function hasActiveItem(items: ReadonlyArray<StatusedItem> | undefined | null): boolean {
  return (items ?? []).some((item) => isActiveTaskStatus(item.status));
}

/**
 * useQuery 的 refetchInterval 回调：列表中仍有活动条目时按指定间隔轮询，
 * 全部进入终态后返回 false 停止轮询。
 */
export function activePollInterval(
  items: ReadonlyArray<StatusedItem> | undefined,
  intervalMs: number,
): number | false {
  return hasActiveItem(items) ? intervalMs : false;
}
