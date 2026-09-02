"use client";

import Image from "next/image";
import { Plus, Upload } from "lucide-react";
import { useState, type ChangeEvent } from "react";

import { publicUrl, type Asset, type PackageReference, type PackageVersion } from "@/lib/api";

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const SUGGESTED_LABELS = ["neutral", "joy", "anger", "sorrow"];

/**
 * Core expression matrix of one package version. Slots are addressed by
 * ``(role, label)`` — every expression slot needs a non-empty label
 * (contract §4.3); completeness counts distinct labels up to four (§7.3).
 */
export function PackageExpressionMatrix({
  version,
  characterName,
  assets,
  bindableAssets,
  editable,
  busy,
  onBindSlot,
  onUnbind,
  onUploadSlot,
}: {
  version: PackageVersion;
  characterName: string;
  assets: Asset[];
  bindableAssets: Asset[];
  editable: boolean;
  busy: boolean;
  onBindSlot: (role: "expression", label: string, assetId: string) => void;
  onUnbind: (reference: PackageReference) => void;
  onUploadSlot: (role: "expression", label: string, file: File) => void;
}) {
  const [label, setLabel] = useState("");
  const [labelError, setLabelError] = useState("");
  const expressions = version.references.filter((item) => item.role === "expression");

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    const trimmed = label.trim();
    if (!file) return;
    if (!IMAGE_TYPES.includes(file.type)) return;
    if (!trimmed) {
      setLabelError("先填写表情标签，例如 neutral / joy / anger / sorrow");
      return;
    }
    onUploadSlot("expression", trimmed, file);
  }

  function bindExisting(assetId: string) {
    const trimmed = label.trim();
    if (!trimmed) {
      setLabelError("先填写表情标签，例如 neutral / joy / anger / sorrow");
      return;
    }
    onBindSlot("expression", trimmed, assetId);
  }

  return (
    <section className="pkg-matrix" aria-label="核心表情集">
      <header>
        <strong>核心表情集</strong>
        <small>每个表情标签 5 分，最多计 4 个（推荐 neutral / joy / anger / sorrow）</small>
      </header>
      {expressions.length ? (
        <div className="pkg-matrix-grid">
          {expressions.map((reference) => {
            const file = assets.find((asset) => asset.id === reference.asset_id);
            const alt = `${characterName} V${version.version_number} - 表情 ${reference.label} 参考图`;
            return (
              <article key={reference.id} data-slot-card tabIndex={0} className="pkg-slot-card bound" aria-label={`表情 ${reference.label}槽位`}>
                <div className="pkg-slot-thumb">
                  {file?.content_url ? (
                    <Image src={publicUrl(file.thumbnail_url ?? file.content_url)!} alt={alt} width={132} height={132} unoptimized />
                  ) : (
                    <span>参考图文件不可用</span>
                  )}
                </div>
                <strong>{reference.label}</strong>
                <span>{file ? "已绑定" : "已绑定 · 文件缺失"}</span>
                {editable && (
                  <div className="pkg-slot-actions">
                    <button type="button" disabled={busy} onClick={() => onUnbind(reference)}>解绑</button>
                  </div>
                )}
              </article>
            );
          })}
        </div>
      ) : (
        <p className="pkg-matrix-empty">还没有表情参考。表情按标签计槽位，例如自然、喜悦、愤怒、悲伤。</p>
      )}
      {editable && (
        <div className="pkg-expression-compose">
          <label>
            <span>表情标签</span>
            <input
              aria-label="表情标签"
              value={label}
              placeholder="neutral / joy / anger / sorrow"
              maxLength={48}
              onChange={(event) => {
                setLabel(event.target.value);
                setLabelError("");
              }}
            />
          </label>
          <div className="pkg-suggested-labels" role="group" aria-label="推荐表情标签">
            {SUGGESTED_LABELS.map((suggestion) => (
              <button key={suggestion} type="button" disabled={busy} onClick={() => { setLabel(suggestion); setLabelError(""); }}>
                <Plus size={10} />{suggestion}
              </button>
            ))}
          </div>
          <label className="pkg-upload-label">
            <Upload size={11} />上传表情图
            <input aria-label="上传表情参考图" type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={busy} onChange={chooseFile} />
          </label>
          <select
            aria-label="为表情绑定已有素材"
            value=""
            disabled={busy || !bindableAssets.length}
            onChange={(event) => {
              if (event.target.value) bindExisting(event.target.value);
            }}
          >
            <option value="">{bindableAssets.length ? "绑定已有素材…" : "暂无可绑定素材"}</option>
            {bindableAssets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.display_name?.trim() || asset.original_name}
              </option>
            ))}
          </select>
        </div>
      )}
      {editable && labelError && <p className="form-error"><span>{labelError}</span></p>}
    </section>
  );
}
