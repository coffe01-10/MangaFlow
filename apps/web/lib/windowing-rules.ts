// V02-51B fixture for V02-52 (audit §7): the item counts at which a grid or
// list must move from plain DOM to windowed rendering. The interim strategy is
// CSS `content-visibility: auto` (see .candidate-grid in globals.css); the
// V02-52 performance gate decides the final mechanism against real samples.
// Nothing consumes these thresholds yet — they are the contract seam, not a
// behavior change.
export const WINDOWING_THRESHOLDS = {
  generateCandidates: 24,
  libraryThumbnails: 60,
  taskRows: 80,
} as const;

export function shouldWindowize(itemCount: number, threshold: number): boolean {
  return itemCount > threshold;
}
