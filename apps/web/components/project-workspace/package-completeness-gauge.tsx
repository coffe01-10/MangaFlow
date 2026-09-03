"use client";

import { Check, CircleAlert } from "lucide-react";

import type { PackageCompleteness } from "@/lib/api";

/**
 * Read-path completeness gauge (contract §7): the score always comes from the
 * API payload; the frontend never recomputes it. Advisory only — it never
 * gates publish or generation.
 */
export function PackageCompletenessGauge({
  completeness,
  caption,
}: {
  completeness: PackageCompleteness | null;
  caption?: string;
}) {
  const score = Math.min(100, Math.max(0, completeness?.score ?? 0));
  const missing = completeness?.missing ?? [];
  return (
    <section className="pkg-completeness" aria-label="角色包完整度">
      <div className="pkg-completeness-summary">
        <div
          className="pkg-completeness-bar"
          role="progressbar"
          aria-label="角色包完整度"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={score}
        >
          <span style={{ width: `${score}%` }} />
        </div>
        <strong>{score}%</strong>
      </div>
      {caption ? <small>{caption}</small> : null}
      {missing.length ? (
        <ul className="pkg-completeness-missing">
          {missing.map((item) => (
            <li key={`${item.code}:${item.field}`}>
              <CircleAlert size={12} />
              <span>{item.message}</span>
              <small>{item.suggestion}</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="pkg-completeness-ok"><Check size={12} />当前版本没有缺失项。完整度仅作补全建议，不会阻断生成。</p>
      )}
    </section>
  );
}
