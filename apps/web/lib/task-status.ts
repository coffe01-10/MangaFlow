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
  // RUNNING 不是 JobStatus；它是 WorkflowRun.status 的词汇
  // （workflow-studio 的运行状态轮询）。保留在此使共享的活跃判定对工作流
  // 运行条目同样成立，勿当作后端任务状态扩展依据。
  "RUNNING",
] as const;

/** 终态之后不会再有写入；晚到的旧结果不允许重新触发轮询。 */
// NEEDS_REVIEW 与后端四处终态定义一致（workflow/generation.py、workflow/jobs.py、
// asset_generation.py、job_service.py）：检查工作流停在 NEEDS_REVIEW 后不会自行迁移，
// 只有用户重试（回 WAITING）才会，属于轮询语义上的终态。
export const TERMINAL_TASK_STATUSES = ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"] as const;

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
 *
 * #367 分歧注记：web 端的轮询间隔由各消费点按视图硬编码传入本助手
 * （asset-production-panel 2000/3000、use-jobs-workspace 3000、
 * use-generation-workspace 2500/3000 等），运行设置 ui_poll_interval_seconds
 * 只由桌面客户端消费（apps/desktop/native/Services/PollInterval.cs）。
 * 若未来要把 web 接入该设置，这里就是唯一需要换算默认间隔的共享层，
 * 但需要同时处理各视图刻意调出的差异化间隔，不是单点替换。
 */
export function activePollInterval(
  items: ReadonlyArray<StatusedItem> | undefined,
  intervalMs: number,
): number | false {
  return hasActiveItem(items) ? intervalMs : false;
}
