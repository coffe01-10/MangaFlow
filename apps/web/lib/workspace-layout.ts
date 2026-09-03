// V02-51B Template B sidebar width contract (audit §4.2/U9): the draggable
// left nav keeps the same 188–360px bounds the workspace has always used, with
// 214px as the default for fresh and out-of-range stored values.
export const SIDEBAR_WIDTH_MIN = 188;
export const SIDEBAR_WIDTH_MAX = 360;
export const SIDEBAR_WIDTH_DEFAULT = 214;

export function clampSidebarWidth(width: number): number {
  return Math.min(SIDEBAR_WIDTH_MAX, Math.max(SIDEBAR_WIDTH_MIN, width));
}

export function storedSidebarWidth(stored: string | null): number {
  const value = Number(stored);
  return stored !== null && Number.isFinite(value) && value >= SIDEBAR_WIDTH_MIN && value <= SIDEBAR_WIDTH_MAX
    ? value
    : SIDEBAR_WIDTH_DEFAULT;
}
