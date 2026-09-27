"use client";

// A/B snapshot compare chooser (画布 A/B 版式对比): two snapshots overlay the
// canvas as tinted ghost outlines; this floating bar names both sides and
// adopts one of them as an ordinary undoable restore command.
import { X } from "lucide-react";

import { storyboardCopy } from "./storyboard-copy";

export function CompareBar({
  nameA,
  nameB,
  onAdopt,
  onExit,
}: {
  nameA: string;
  nameB: string;
  onAdopt: (side: "a" | "b") => void;
  onExit: () => void;
}) {
  return <div className="replay-bar compare-bar" role="group" aria-label={storyboardCopy.abCompare} onPointerDown={(event) => event.stopPropagation()}>
    <span className="compare-tag a" aria-hidden="true">A</span>
    <span className="compare-name" title={nameA}>{nameA}</span>
    <button type="button" onClick={() => onAdopt("a")}>{storyboardCopy.adoptSideA}</button>
    <span className="compare-tag b" aria-hidden="true">B</span>
    <span className="compare-name" title={nameB}>{nameB}</span>
    <button type="button" onClick={() => onAdopt("b")}>{storyboardCopy.adoptSideB}</button>
    <button type="button" className="replay-exit" onClick={onExit}><X size={13} />{storyboardCopy.exitCompare}</button>
  </div>;
}
