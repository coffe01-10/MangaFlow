import Image from "next/image";
import { Check, CircleAlert, LoaderCircle, Maximize2, Pencil, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";

import { originUrl, publicUrl, type Asset, type ImageModelAlias, type StyleProfile } from "@/lib/api";

export function ImageModelPicker({
  selected,
  onSelect,
  options,
  label,
}: {
  selected: ImageModelAlias | null;
  onSelect: (model: ImageModelAlias) => void;
  options: { alias: ImageModelAlias; name: string; id: string; provider: string }[];
  label?: string;
}) {
  return <div className="model-picker">
    {label && <p>{label}</p>}
    <div className="model-duel">{options.map((option) => <button type="button" aria-pressed={selected === option.alias} key={option.alias} className={selected === option.alias ? "model-choice active" : "model-choice"} onClick={() => onSelect(option.alias)}><Sparkles size={18} /><span><strong>{option.name}</strong><small>{option.provider} · {option.id}</small></span>{selected === option.alias && <Check size={15} />}</button>)}</div>
    {!options.length && <p className="form-error"><CircleAlert size={14} />暂无已启用且支持参考图编辑的图片模型，请先到系统设置配置供应商。</p>}
  </div>;
}

export function ComicModeSwitch({
  value,
  onChange,
  compact = false,
  disabled = false,
}: {
  value: StyleProfile["color_mode"];
  onChange: (mode: StyleProfile["color_mode"]) => void;
  compact?: boolean;
  disabled?: boolean;
}) {
  return <div className={compact ? "comic-mode-switch compact" : "comic-mode-switch"} role="group" aria-label="漫画色彩模式">
    <button type="button" aria-pressed={value === "monochrome"} className={value === "monochrome" ? "active monochrome" : "monochrome"} disabled={disabled} onClick={() => onChange("monochrome")}><i />黑白漫画</button>
    <button type="button" aria-pressed={value === "color"} className={value === "color" ? "active color" : "color"} disabled={disabled} onClick={() => onChange("color")}><i />彩色漫画</button>
  </div>;
}

export function AssetNameEditor({ asset, pending, error, onSave }: { asset: Asset; pending: boolean; error?: Error | null; onSave: (displayName: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(asset.display_name ?? asset.original_name);
  const [submitted, setSubmitted] = useState(false);
  const visibleName = asset.display_name?.trim() || asset.original_name;

  // The form stays open until the rename settles: closing it immediately made
  // a failed rename look like a successful save (the name later "reverted").
  useEffect(() => {
    if (!submitted || pending) return;
    if (error) {
      setSubmitted(false);
      return;
    }
    setEditing(false);
    setSubmitted(false);
  }, [submitted, pending, error]);

  if (editing) {
    return <form className="asset-name-edit" onSubmit={(event) => {
      event.preventDefault();
      const next = value.trim();
      if (!next) return;
      onSave(next);
      setSubmitted(true);
    }}>
      <input aria-label={`重命名 ${visibleName}`} maxLength={120} autoFocus value={value} onChange={(event) => setValue(event.target.value)} disabled={submitted && pending} />
      <button type="submit" aria-label="保存素材名称" disabled={pending || !value.trim()}><Check size={14} /></button>
      <button type="button" aria-label="取消重命名" onClick={() => { setValue(visibleName); setEditing(false); }}><X size={14} /></button>
      {submitted && error && <em className="asset-name-error"><CircleAlert size={11} />{error.message}</em>}
    </form>;
  }

  return <div className="asset-name-row">
    <strong title={`原始文件名：${asset.original_name}`}>{visibleName}</strong>
    <button type="button" aria-label={`重命名 ${visibleName}`} title="自定义素材名称" onClick={() => { setValue(visibleName); setEditing(true); }}><Pencil size={13} /></button>
  </div>;
}

export function CandidateArtwork({ contentUrl, thumbnailUrl, label, onOpen, eager = false }: { contentUrl: string | null; thumbnailUrl?: string | null; label: string; onOpen?: (url: string, label: string) => void; eager?: boolean }) {
  const url = publicUrl(thumbnailUrl ?? contentUrl);
  const fullUrl = originUrl(contentUrl ?? thumbnailUrl ?? null);
  return (
    <button type="button" className="candidate-artwork" aria-label={url ? `放大查看${label}` : label} onClick={() => fullUrl && onOpen?.(fullUrl, label)}>
      {url ? (
        <Image className="candidate-image" src={url} alt={label} fill sizes="(max-width: 900px) 46vw, 280px" priority={eager} fetchPriority={eager ? "high" : "auto"} loading={eager ? "eager" : "lazy"} unoptimized />
      ) : (
        <span className="candidate-placeholder"><LoaderCircle size={22} /><span>等待 Worker 生成</span></span>
      )}
      {url ? <span className="candidate-zoom"><Maximize2 size={15} />放大</span> : null}
    </button>
  );
}
