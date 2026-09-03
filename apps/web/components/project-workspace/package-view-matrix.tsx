"use client";

import Image from "next/image";
import { ImagePlus, Upload } from "lucide-react";
import { useRef, type ChangeEvent, type KeyboardEvent } from "react";

import { publicUrl, type Asset, type PackageReference, type PackageRole, type PackageVersion } from "@/lib/api";

import { packageRoleLabel } from "./package-meta";

const VIEW_SLOTS: { role: PackageRole; label: string }[] = [
  { role: "front", label: "正面（主视）" },
  { role: "side", label: "右侧面" },
  { role: "back", label: "背面" },
  { role: "three_quarter", label: "3/4 侧面" },
];

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

/**
 * Multi-view matrix for one package version. DRAFT versions allow binding an
 * existing eligible asset or uploading a new image per core slot; READY+
 * versions render the frozen matrix read-only (contract §9.2).
 */
export function PackageViewMatrix({
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
  onBindSlot: (role: PackageRole, assetId: string) => void;
  onUnbind: (reference: PackageReference) => void;
  onUploadSlot: (role: PackageRole, file: File) => void;
}) {
  const gridRef = useRef<HTMLDivElement>(null);

  function onGridKey(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    const cards = Array.from(gridRef.current?.querySelectorAll<HTMLElement>("[data-slot-card]") ?? []);
    if (!cards.length) return;
    const current = cards.findIndex((card) => card.contains(document.activeElement));
    if (current < 0) return;
    event.preventDefault();
    const delta = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
    const next = (current + delta + cards.length) % cards.length;
    cards[next].focus();
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>, role: PackageRole) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!IMAGE_TYPES.includes(file.type)) return;
    onUploadSlot(role, file);
  }

  return (
    <section className="pkg-matrix" aria-label="多角度视角矩阵">
      <header>
        <strong>多角度视角矩阵</strong>
        <small>{editable ? "正面 15 · 侧面 10 · 背面 10 · 3/4 侧 5 分" : "已发布版本的四视图冻结展示"}</small>
      </header>
      <div className="pkg-matrix-grid" ref={gridRef} onKeyDown={onGridKey}>
        {VIEW_SLOTS.map(({ role, label }) => {
          const reference = version.references.find((item) => item.role === role);
          const file = reference ? assets.find((asset) => asset.id === reference.asset_id) : undefined;
          const alt = `${characterName} V${version.version_number} - ${label}参考图`;
          return (
            <article
              key={role}
              data-slot-card
              tabIndex={0}
              className={reference ? "pkg-slot-card bound" : "pkg-slot-card"}
              aria-label={`${label}槽位${reference ? "已绑定" : "空缺"}`}
            >
              <div className="pkg-slot-thumb">
                {file?.content_url ? (
                  <Image src={publicUrl(file.thumbnail_url ?? file.content_url)!} alt={alt} width={132} height={132} unoptimized />
                ) : reference ? (
                  <span>参考图文件不可用</span>
                ) : (
                  <ImagePlus size={22} />
                )}
              </div>
              <strong>{label}</strong>
              <span>{reference ? (file ? "已绑定" : "已绑定 · 文件缺失") : "空缺"}</span>
              {editable && (
                <div className="pkg-slot-actions">
                  {reference ? (
                    <button type="button" disabled={busy} onClick={() => onUnbind(reference)}>解绑</button>
                  ) : (
                    <>
                      <label className="pkg-upload-label">
                        <Upload size={11} />上传
                        <input
                          aria-label={`上传${label}参考图`}
                          type="file"
                          accept="image/png,image/jpeg,image/webp"
                          hidden
                          disabled={busy}
                          onChange={(event) => chooseFile(event, role)}
                        />
                      </label>
                      <select
                        aria-label={`为${label}绑定已有素材`}
                        value=""
                        disabled={busy || !bindableAssets.length}
                        onChange={(event) => {
                          if (event.target.value) onBindSlot(role, event.target.value);
                        }}
                      >
                        <option value="">{bindableAssets.length ? "绑定已有素材…" : "暂无可绑定素材"}</option>
                        {bindableAssets.map((asset) => (
                          <option key={asset.id} value={asset.id}>
                            {asset.display_name?.trim() || asset.original_name}
                          </option>
                        ))}
                      </select>
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
      {!version.references.length && !editable && (
        <p className="pkg-matrix-empty">该版本未绑定任何参考图（完整度矩阵为空）。</p>
      )}
      {editable && <p className="pkg-matrix-hint">换绑槽位前需先解绑；同一张图可同时作为封面与视图（{packageRoleLabel("front")}优先参与默认继承）。</p>}
    </section>
  );
}
