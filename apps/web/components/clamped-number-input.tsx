"use client";

import { useState } from "react";

// 数字输入的钳制只在失焦时发生:按 keystroke 钳制会把 "45"(区间 30–3600)
// 的首键 "4" 立即改写成 "30",用户永远无法通过键盘输入低于当前值的数;
// 清空字段也会被立即夹回最小值。输入期间原样展示,失焦时才提交钳制结果,
// 空/非法输入回退到上一个有效值。
export function ClampedNumberInput({
  value,
  min,
  max,
  onCommit,
  ariaLabel,
}: {
  value: number;
  min: number;
  max: number;
  onCommit: (value: number) => void;
  ariaLabel?: string;
}) {
  const [raw, setRaw] = useState<string | null>(null);
  return <input
    type="number"
    min={min}
    max={max}
    aria-label={ariaLabel}
    value={raw ?? String(value)}
    onChange={(event) => setRaw(event.target.value)}
    onBlur={() => {
      if (raw === null) return;
      const parsed = Number(raw);
      if (raw.trim() !== "" && Number.isFinite(parsed)) {
        onCommit(Math.min(max, Math.max(min, Math.round(parsed))));
      }
      // 空/非法输入放弃修改,回显已提交的值。
      setRaw(null);
    }}
  />;
}
