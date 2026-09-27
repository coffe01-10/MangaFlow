"use client";

// Session replay controls (V02-33): play/pause the recorded command
// timeline, scrub to any frame, step through it, pick a speed. The bar sits
// inside the canvas viewport; geometry tweening comes free from the command
// transitions already on the page.
import { Pause, Play, SkipBack, SkipForward, X } from "lucide-react";

import { storyboardCopy } from "./storyboard-copy";

const SPEEDS = [0.5, 1, 2, 4];

export function ReplayBar({
  index,
  count,
  label,
  playing,
  speed,
  onPlayPause,
  onSeek,
  onStep,
  onSpeed,
  onExit,
}: {
  index: number;
  count: number;
  /** Label of the command that produced the current frame. */
  label: string;
  playing: boolean;
  speed: number;
  onPlayPause: () => void;
  onSeek: (index: number) => void;
  onStep: (delta: number) => void;
  onSpeed: (speed: number) => void;
  onExit: () => void;
}) {
  return <div className="replay-bar" role="group" aria-label={storyboardCopy.replayBar} onPointerDown={(event) => event.stopPropagation()}>
    <button type="button" aria-label={storyboardCopy.replayStepBack} disabled={index <= 0} onClick={() => onStep(-1)}><SkipBack size={13} /></button>
    <button type="button" aria-label={playing ? storyboardCopy.replayPause : storyboardCopy.replayPlay} onClick={onPlayPause}>
      {playing ? <Pause size={13} /> : <Play size={13} />}
    </button>
    <button type="button" aria-label={storyboardCopy.replayStepForward} disabled={index >= count - 1} onClick={() => onStep(1)}><SkipForward size={13} /></button>
    <input
      type="range"
      aria-label={storyboardCopy.replayTimeline}
      min={0}
      max={Math.max(count - 1, 0)}
      value={index}
      onChange={(event) => onSeek(Number(event.target.value))}
    />
    <span className="replay-position">{index + 1}/{count}</span>
    <span className="replay-label" title={label}>{label}</span>
    <select aria-label={storyboardCopy.replaySpeed} value={speed} onChange={(event) => onSpeed(Number(event.target.value))}>
      {SPEEDS.map((value) => <option key={value} value={value}>×{value}</option>)}
    </select>
    <button type="button" className="replay-exit" onClick={onExit}>{<X size={13} />}{storyboardCopy.replayExit}</button>
  </div>;
}
