"use client";

import { useQuery } from "@tanstack/react-query";
import { CircleAlert, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { api, type CharacterModelPackage } from "@/lib/api";

import { SceneModal } from "./scene-modal";
import { isCorePackageRole, packageRoleLabel } from "./package-meta";

function blockRows(
  block: { added: Record<string, string>; removed: Record<string, string>; changed: { field: string; base_value: string | null; target_value: string | null }[] },
) {
  const rows: { key: string; kind: "added" | "removed" | "changed"; field: string; base?: string | null; target?: string | null }[] = [];
  Object.entries(block.added).forEach(([field, value]) => rows.push({ key: `add:${field}`, kind: "added", field, target: value }));
  Object.entries(block.removed).forEach(([field, value]) => rows.push({ key: `del:${field}`, kind: "removed", field, base: value }));
  block.changed.forEach((item) => rows.push({ key: `chg:${item.field}`, kind: "changed", field: item.field, base: item.base_value, target: item.target_value }));
  return rows;
}

const SPEC_FIELD_LABELS: Record<string, string> = {
  age_appearance: "年龄段外观",
  gender: "性别",
  personality: "性格",
  identity_notes: "身份备注",
  hair: "发型",
  hair_color: "发色",
  face: "面部特征",
  eyes: "瞳色",
  body: "体型",
  distinguishing_marks: "标识性特征",
};

/**
 * Version diff viewer (contract §9.1 GET .../package/diff). The modal reuses
 * SceneModal, which provides the focus trap and Escape-to-close behaviour.
 */
export function PackageDiffModal({
  projectId,
  characterId,
  characterName,
  pkg,
  onClose,
}: {
  projectId: string;
  characterId: string;
  characterName: string;
  pkg: CharacterModelPackage;
  onClose: () => void;
}) {
  const versions = pkg.versions;
  const draft = versions.find((item) => item.status === "DRAFT") ?? null;
  const published = versions.find((item) => item.id === pkg.published_version_id)
    ?? versions.find((item) => item.status !== "DRAFT")
    ?? null;
  const [baseId, setBaseId] = useState(published?.id ?? versions[versions.length - 1]?.id ?? "");
  const [targetId, setTargetId] = useState(draft?.id ?? versions[0]?.id ?? "");

  const diff = useQuery({
    queryKey: ["character-package-diff", projectId, characterId, baseId, targetId],
    queryFn: () => api.characterPackageDiff(projectId, characterId, baseId, targetId),
    enabled: Boolean(baseId && targetId && baseId !== targetId),
  });

  const baseVersion = versions.find((item) => item.id === baseId);
  const targetVersion = versions.find((item) => item.id === targetId);

  return (
    <SceneModal title={`版本差异对比：${characterName}`} wide onClose={onClose}>
      <div className="pkg-diff-controls">
        <label>
          <span>基线版本</span>
          <select aria-label="选择基线版本" value={baseId} onChange={(event) => setBaseId(event.target.value)}>
            {versions.map((item) => (
              <option key={item.id} value={item.id}>V{item.version_number} · {item.status}</option>
            ))}
          </select>
        </label>
        <label>
          <span>对比版本</span>
          <select aria-label="选择对比版本" value={targetId} onChange={(event) => setTargetId(event.target.value)}>
            {versions.map((item) => (
              <option key={item.id} value={item.id}>V{item.version_number} · {item.status}</option>
            ))}
          </select>
        </label>
      </div>
      {baseId === targetId ? (
        <p className="pkg-diff-empty">请选择两个不同的版本进行对比。</p>
      ) : diff.isLoading ? (
        <p className="reference-check-loading"><LoaderCircle className="spin" size={15} />正在计算版本差异…</p>
      ) : diff.isError ? (
        <p className="form-error"><CircleAlert size={14} />{diff.error.message}</p>
      ) : diff.data ? (
        <div className="pkg-diff-columns">
          <div>
            <h4>基线：V{baseVersion?.version_number}（{baseVersion?.status}）</h4>
            <h4>变更：V{targetVersion?.version_number}（{targetVersion?.status}）</h4>
          </div>
          {([
            ["身份规格", diff.data.identity_spec],
            ["视觉规格", diff.data.visual_spec],
          ] as const).map(([title, block]) => {
            const rows = blockRows(block);
            return rows.length ? (
              <section key={title} className="pkg-diff-block">
                <strong>{title}</strong>
                <ul>
                  {rows.map((row) => (
                    <li key={row.key} className={`pkg-diff-${row.kind}`}>
                      <em>{row.kind === "added" ? "新增" : row.kind === "removed" ? "移除" : "变更"}</em>
                      <span>{SPEC_FIELD_LABELS[row.field] ?? row.field}</span>
                      <small>
                        {row.kind === "added"
                          ? String(row.target ?? "")
                          : row.kind === "removed"
                            ? String(row.base ?? "")
                            : `${row.base ?? "（空）"} → ${row.target ?? "（空）"}`}
                      </small>
                    </li>
                  ))}
                </ul>
              </section>
            ) : null;
          })}
          {(diff.data.negative_constraints.added.length || diff.data.negative_constraints.removed.length) ? (
            <section className="pkg-diff-block">
              <strong>负面约束</strong>
              <ul>
                {diff.data.negative_constraints.added.map((item) => (
                  <li key={`n-add:${item}`} className="pkg-diff-added"><em>新增</em><span>{item}</span></li>
                ))}
                {diff.data.negative_constraints.removed.map((item) => (
                  <li key={`n-del:${item}`} className="pkg-diff-removed"><em>移除</em><span>{item}</span></li>
                ))}
              </ul>
            </section>
          ) : null}
          {(diff.data.references.added.length || diff.data.references.removed.length || diff.data.references.changed.length) ? (
            <section className="pkg-diff-block">
              <strong>参考图矩阵</strong>
              <ul>
                {diff.data.references.added.map((slot) => (
                  <li key={`r-add:${slot.role}:${slot.label}`} className="pkg-diff-added">
                    <em>新增</em><span>{packageRoleLabel(slot.role, isCorePackageRole(slot.role) ? undefined : slot.label)}</span>
                    <small>{slot.asset_deleted ? "素材已失效" : slot.asset_id}</small>
                  </li>
                ))}
                {diff.data.references.removed.map((slot) => (
                  <li key={`r-del:${slot.role}:${slot.label}`} className="pkg-diff-removed">
                    <em>移除</em><span>{packageRoleLabel(slot.role, isCorePackageRole(slot.role) ? undefined : slot.label)}</span>
                    <small>{slot.asset_deleted ? "素材已失效" : slot.asset_id}</small>
                  </li>
                ))}
                {diff.data.references.changed.map((slot) => (
                  <li key={`r-chg:${slot.role}:${slot.label}`} className="pkg-diff-changed">
                    <em>变更</em><span>{packageRoleLabel(slot.role, isCorePackageRole(slot.role) ? undefined : slot.label)}</span>
                    <small>{slot.base_asset_id ?? "（空）"} → {slot.target_asset_id ?? "（空）"}</small>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {(diff.data.outfits.added.length || diff.data.outfits.removed.length || diff.data.outfits.changed.length) ? (
            <section className="pkg-diff-block">
              <strong>服装集</strong>
              <ul>
                {diff.data.outfits.added.map((item) => (
                  <li key={`o-add:${item.outfit_id}`} className="pkg-diff-added"><em>新增</em><span>{item.outfit_id}</span>{item.is_default ? <small>默认服装</small> : null}</li>
                ))}
                {diff.data.outfits.removed.map((item) => (
                  <li key={`o-del:${item.outfit_id}`} className="pkg-diff-removed"><em>移除</em><span>{item.outfit_id}</span></li>
                ))}
                {diff.data.outfits.changed.map((item) => (
                  <li key={`o-chg:${item.outfit_id}`} className="pkg-diff-changed"><em>变更</em><span>{item.outfit_id}</span><small>默认位 {item.is_default ? "开" : "关"} · 顺序 {item.sort_order}</small></li>
                ))}
              </ul>
            </section>
          ) : null}
          {(() => {
            const d = diff.data;
            const hasChanges = Object.keys(d.identity_spec.added).length > 0
              || Object.keys(d.identity_spec.removed).length > 0
              || d.identity_spec.changed.length > 0
              || Object.keys(d.visual_spec.added).length > 0
              || Object.keys(d.visual_spec.removed).length > 0
              || d.visual_spec.changed.length > 0
              || d.negative_constraints.added.length > 0
              || d.negative_constraints.removed.length > 0
              || d.references.added.length > 0
              || d.references.removed.length > 0
              || d.references.changed.length > 0
              || d.outfits.added.length > 0
              || d.outfits.removed.length > 0
              || d.outfits.changed.length > 0;
            return hasChanges ? null : <p className="pkg-diff-empty">两个版本的规格与矩阵完全一致。</p>;
          })()}
        </div>
      ) : null}
    </SceneModal>
  );
}
