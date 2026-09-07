"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArchiveRestore,
  CircleAlert,
  GitCompareArrows,
  Layers,
  LoaderCircle,
  Plus,
  ShieldCheck,
  Trash2,
  Users,
} from "lucide-react";
import Image from "next/image";
import { useMemo, useRef, useState, type KeyboardEvent } from "react";

import {
  api,
  isConflictError,
  publicUrl,
  type Asset,
  type Character,
  type CharacterModelPackage,
  type PackageOutfit,
  type PackageReference,
  type PackageRole,
  type PackageSpecPayload,
  type PackageVersion,
} from "@/lib/api";

import { PackageCompletenessGauge } from "./package-completeness-gauge";
import { PackageDiffModal } from "./package-diff-modal";
import { PackageExpressionMatrix } from "./package-expression-matrix";
import { packageVersionStatusMeta } from "./package-meta";
import { PackageViewMatrix } from "./package-view-matrix";
import { SceneConfirmDialog } from "./scene-modal";

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

function conflictMessage(error: unknown, fallback: string) {
  if (isConflictError(error)) {
    // ae0c6ee 之后 ApiError.message 即后端 409 detail（字符串或 detail.message）。
    // 语义化冲突（「已有草稿版本，请先发布或删除该草稿」「角色模型包已存在」）
    // 必须原样透出——通用刷新提示会让用户刷新后撞进同一个冲突（#156）。
    const message = error instanceof Error ? error.message : "";
    return message && message !== "请求数据不符合要求" ? message : "数据已变化，请刷新后重试";
  }
  return error instanceof Error ? error.message : fallback;
}

const IDENTITY_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "age_appearance", label: "年龄段外观", placeholder: "例如：17 岁高中生" },
  { key: "gender", label: "性别", placeholder: "例如：女" },
  { key: "personality", label: "核心性格", placeholder: "例如：冷静、话少、观察力强" },
  { key: "identity_notes", label: "身份备注", placeholder: "剧本中需要保持一致的身份设定" },
];

const VISUAL_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "hair", label: "发型", placeholder: "例如：黑色碎短发" },
  { key: "hair_color", label: "发色", placeholder: "例如：深黑" },
  { key: "face", label: "面部", placeholder: "例如：右耳银色耳钉" },
  { key: "eyes", label: "瞳色", placeholder: "例如：琥珀色" },
  { key: "body", label: "体型", placeholder: "例如：瘦高" },
  { key: "distinguishing_marks", label: "标识性特征", placeholder: "伤疤 / 眼镜 / 胎记…" },
];

/** Read-only spec display for frozen versions (§9.2: locked versions are readable). */
function FrozenSpecReadout({ spec }: { spec: PackageVersion["spec_snapshot"] }) {
  const rows = [
    ...Object.entries(spec.identity_spec ?? {}),
    ...Object.entries(spec.visual_spec ?? {}),
  ]
    .filter(([, value]) => value)
    .map(([key, value]) => ({
      label: [...IDENTITY_FIELDS, ...VISUAL_FIELDS].find((field) => field.key === key)?.label ?? key,
      value: String(value),
    }));
  const constraints = (spec.negative_constraints ?? []).filter(Boolean);
  return (
    <div className="pkg-frozen-spec">
      {rows.length ? (
        <dl>
          {rows.map((row) => (
            <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>
          ))}
        </dl>
      ) : (
        <p className="pkg-matrix-empty">该版本未填写规格文字。</p>
      )}
      {constraints.length > 0 && <p className="pkg-matrix-hint">负面约束：{constraints.join("；")}</p>}
    </div>
  );
}

/** Draft workspec editor: identity + visual specs and negative constraints. */
function PackageSpecEditor({
  pkg,
  pending,
  onSave,
}: {
  pkg: CharacterModelPackage;
  pending: boolean;
  onSave: (payload: PackageSpecPayload) => void;
}) {
  const [identity, setIdentity] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(pkg.identity_spec ?? {}).map(([key, value]) => [key, value ?? ""])),
  );
  const [visual, setVisual] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(pkg.visual_spec ?? {}).map(([key, value]) => [key, value ?? ""])),
  );
  const [constraints, setConstraints] = useState((pkg.negative_constraints ?? []).join("\n"));

  function clean(fields: Record<string, string>) {
    return Object.fromEntries(Object.entries(fields).map(([key, value]) => [key, value.trim() || null]));
  }

  return (
    <form
      className="pkg-spec-editor"
      aria-label="角色包规格工作集"
      onSubmit={(event) => {
        event.preventDefault();
        onSave({
          identity_spec: clean(identity),
          visual_spec: clean(visual),
          negative_constraints: constraints.split("\n").map((line) => line.trim()).filter(Boolean),
        });
      }}
    >
      <fieldset>
        <legend>身份锚点（20 分：每项 5 分）</legend>
        {IDENTITY_FIELDS.map((field) => (
          <label key={field.key}>
            <span>{field.label}</span>
            <input
              aria-label={`身份锚点 ${field.label}`}
              value={identity[field.key] ?? ""}
              placeholder={field.placeholder}
              onChange={(event) => setIdentity({ ...identity, [field.key]: event.target.value })}
            />
          </label>
        ))}
      </fieldset>
      <fieldset>
        <legend>视觉规格</legend>
        {VISUAL_FIELDS.map((field) => (
          <label key={field.key}>
            <span>{field.label}</span>
            <input
              aria-label={`视觉规格 ${field.label}`}
              value={visual[field.key] ?? ""}
              placeholder={field.placeholder}
              onChange={(event) => setVisual({ ...visual, [field.key]: event.target.value })}
            />
          </label>
        ))}
      </fieldset>
      <label className="wide">
        <span>负面约束与防崩词（每行一条，≤20 条）</span>
        <textarea
          aria-label="负面约束"
          rows={3}
          value={constraints}
          placeholder={"禁止改变发色\n禁止添加饰品"}
          onChange={(event) => setConstraints(event.target.value)}
        />
      </label>
      <div className="pkg-spec-actions">
        <button type="submit" className="button ink compact" disabled={pending}>
          {pending ? <LoaderCircle className="spin" size={13} /> : <ShieldCheck size={13} />}保存草稿规格
        </button>
        <small>规格属于包工作集，发布时冻结进版本快照。</small>
      </div>
    </form>
  );
}

/**
 * Character model package workspace (V02-23B): one-to-one package per
 * character addressed by character_id (contract §9.1). Renders the package
 * list + empty state, the DRAFT workspec (spec editor, view/expression
 * matrices, outfits, cover), publish/derive/archive flows and the version
 * diff modal. Completeness is advisory and always read from the API.
 */
export function CharacterPackageWorkspace({
  projectId,
  characters,
  assets,
}: {
  projectId: string;
  characters: Character[];
  assets: Asset[];
}) {
  const queryClient = useQueryClient();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [showDiff, setShowDiff] = useState(false);
  const [publishTarget, setPublishTarget] = useState<PackageVersion | null>(null);
  const [createCharacterId, setCreateCharacterId] = useState("");

  const list = useQuery({
    queryKey: ["character-packages", projectId],
    queryFn: () => api.characterPackagesAll(projectId),
  });
  const packages = useMemo(() => list.data ?? [], [list.data]);
  const resolvedCharacterId = selectedCharacterId ?? packages[0]?.character_id ?? null;
  const summary = packages.find((item) => item.character_id === resolvedCharacterId) ?? null;
  const characterName = summary?.character.primary_name
    ?? characters.find((item) => item.id === resolvedCharacterId)?.primary_name
    ?? "角色";

  const detail = useQuery({
    queryKey: ["character-package", projectId, resolvedCharacterId],
    queryFn: () => api.characterPackage(projectId, resolvedCharacterId!),
    enabled: Boolean(resolvedCharacterId),
  });
  const pkg = detail.data ?? null;
  const draft = pkg?.versions.find((item) => item.status === "DRAFT") ?? null;
  const published = pkg?.published_version_id
    ? pkg.versions.find((item) => item.id === pkg.published_version_id) ?? null
    : null;

  const outfits = useQuery({
    queryKey: ["outfits", projectId],
    queryFn: () => api.outfits(projectId),
    enabled: Boolean(pkg),
  });

  const characterOutfits = useMemo(
    () => (outfits.data ?? []).filter((item) => item.character_id === resolvedCharacterId),
    [outfits.data, resolvedCharacterId],
  );

  const bindableAssets = useMemo(() => {
    const boundToCharacter = new Set(
      characters.find((item) => item.id === resolvedCharacterId)?.references.map((ref) => ref.asset_id) ?? [],
    );
    const boundToAny = new Set(characters.flatMap((item) => item.references.map((ref) => ref.asset_id)));
    return assets.filter((asset) =>
      asset.kind === "CHARACTER_REFERENCE"
      && (boundToCharacter.has(asset.id) || !boundToAny.has(asset.id)));
  }, [assets, characters, resolvedCharacterId]);

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["character-packages", projectId] });
    if (resolvedCharacterId) {
      queryClient.invalidateQueries({ queryKey: ["character-package", projectId, resolvedCharacterId] });
    }
    // Uploaded slot images must show up in the asset library thumbnails too.
    queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
  }

  const createPackage = useMutation({
    mutationFn: (characterId: string) => {
      if (!characterId) throw new Error("请先选择要创建模型包的角色");
      return api.createCharacterPackage(projectId, characterId);
    },
    onSuccess: (_pkg, characterId) => {
      setNotice("");
      setSelectedCharacterId(characterId);
      setCreateCharacterId("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "创建角色模型包失败")),
  });

  const saveSpec = useMutation({
    mutationFn: (payload: PackageSpecPayload) => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return api.updateCharacterPackage(projectId, pkg.character_id, { ...payload, version: pkg.version });
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "保存草稿规格失败")),
  });

  const deriveVersion = useMutation({
    mutationFn: () => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return api.deriveCharacterPackageVersion(projectId, pkg.character_id, pkg.published_version_id);
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "派生新版本失败")),
  });

  const publishVersion = useMutation({
    mutationFn: (version: PackageVersion) => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return api.publishCharacterPackageVersion(projectId, pkg.character_id, version.id);
    },
    onSuccess: () => {
      setPublishTarget(null);
      setNotice("");
      refresh();
    },
    onError: (error) => {
      setPublishTarget(null);
      setNotice(conflictMessage(error, "发布版本失败"));
    },
  });

  const deleteDraft = useMutation({
    mutationFn: (version: PackageVersion) => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return api.deleteCharacterPackageVersion(projectId, pkg.character_id, version.id);
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "删除草稿版本失败")),
  });

  const switchPackageStatus = useMutation({
    mutationFn: (action: "archive" | "restore") => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return action === "archive"
        ? api.archiveCharacterPackage(projectId, pkg.character_id)
        : api.restoreCharacterPackage(projectId, pkg.character_id);
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "更新模型包状态失败")),
  });

  const activateVersion = useMutation({
    mutationFn: (version: PackageVersion) => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return api.activateCharacterPackageVersion(projectId, pkg.character_id, {
        version_id: version.id,
        expected_published_version_id: pkg.published_version_id,
      });
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "切换发布版本失败")),
  });

  const switchVersionStatus = useMutation({
    mutationFn: ({ version, action }: { version: PackageVersion; action: "archive" | "restore" }) => {
      if (!pkg) throw new Error("请先选择角色模型包");
      return action === "archive"
        ? api.archiveCharacterPackageVersion(projectId, pkg.character_id, version.id)
        : api.restoreCharacterPackageVersion(projectId, pkg.character_id, version.id);
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "更新版本状态失败")),
  });

  const bindReference = useMutation({
    mutationFn: ({ role, label, assetId }: { role: PackageRole; label?: string; assetId: string }) => {
      if (!draft) throw new Error("只有草稿版本可以修改矩阵");
      return api.bindCharacterPackageReference(projectId, pkg!.character_id, draft.id, {
        asset_id: assetId,
        role,
        label: label ?? "",
        sort_order: 0,
        version: draft.version,
      });
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "绑定参考图失败")),
  });

  const uploadReference = useMutation({
    mutationFn: async ({ role, label, file }: { role: PackageRole; label?: string; file: File }) => {
      if (!draft) throw new Error("只有草稿版本可以修改矩阵");
      const uploaded = await api.uploadAsset(projectId, "CHARACTER_REFERENCE", file);
      return api.bindCharacterPackageReference(projectId, pkg!.character_id, draft.id, {
        asset_id: uploaded.id,
        role,
        label: label ?? "",
        sort_order: 0,
        version: draft.version,
      });
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "上传或绑定参考图失败")),
  });

  const unbindReference = useMutation({
    mutationFn: (reference: PackageReference) => {
      if (!draft) throw new Error("只有草稿版本可以修改矩阵");
      return api.unbindCharacterPackageReference(projectId, pkg!.character_id, draft.id, reference.id, draft.version);
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "解绑参考图失败")),
  });

  const setCover = useMutation({
    mutationFn: ({ assetId, file }: { assetId?: string; file?: File }) => {
      if (!draft) throw new Error("只有草稿版本可以修改封面");
      const characterId = pkg!.character_id;
      const versionId = draft.id;
      const versionToken = draft.version;
      async function run() {
        let assetIdValue = assetId;
        if (!assetIdValue && file) {
          const uploaded = await api.uploadAsset(projectId, "CHARACTER_REFERENCE", file);
          assetIdValue = uploaded.id;
        }
        if (!assetIdValue) throw new Error("请选择封面素材");
        return api.setCharacterPackageCover(projectId, characterId, versionId, {
          asset_id: assetIdValue,
          version: versionToken,
        });
      }
      return run();
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "设置封面失败")),
  });

  const bindOutfit = useMutation({
    mutationFn: ({ outfitId, isDefault }: { outfitId: string; isDefault?: boolean }) => {
      if (!draft) throw new Error("只有草稿版本可以修改服装集");
      return api.bindCharacterPackageOutfit(projectId, pkg!.character_id, draft.id, {
        outfit_id: outfitId,
        is_default: isDefault ?? false,
        sort_order: draft.outfits.length,
        version: draft.version,
      });
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "关联服装失败")),
  });

  const setDefaultOutfit = useMutation({
    mutationFn: (relation: PackageOutfit) => {
      if (!draft) throw new Error("只有草稿版本可以修改服装集");
      return api.setCharacterPackageOutfitDefault(projectId, pkg!.character_id, draft.id, relation.outfit_id, {
        is_default: true,
        version: draft.version,
      });
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "设置默认服装失败")),
  });

  const unbindOutfit = useMutation({
    mutationFn: (relation: PackageOutfit) => {
      if (!draft) throw new Error("只有草稿版本可以修改服装集");
      return api.unbindCharacterPackageOutfit(projectId, pkg!.character_id, draft.id, relation.outfit_id, draft.version);
    },
    onSuccess: () => {
      setNotice("");
      refresh();
    },
    onError: (error) => setNotice(conflictMessage(error, "解绑服装失败")),
  });

  const hasDraft = Boolean(draft);
  // §9.2: locked versions stay readable. With no DRAFT, show the published
  // pointer (or newest locked version) as a read-only frozen view.
  const frozenVersion = !hasDraft && pkg
    ? published ?? pkg.versions.find((item) => item.status !== "DRAFT") ?? null
    : null;
  const frozenMeta = packageVersionStatusMeta(frozenVersion?.status ?? "DRAFT");
  const charactersWithoutPackage = characters.filter(
    (item) => !packages.some((entry) => entry.character_id === item.id),
  );

  function chooseFile(event: React.ChangeEvent<HTMLInputElement>, apply: (file: File) => void) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!IMAGE_TYPES.includes(file.type)) {
      setNotice("只支持 PNG、JPEG 或 WebP 图片");
      return;
    }
    apply(file);
  }

  function onListKey(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp"].includes(event.key) || !packages.length) return;
    event.preventDefault();
    const current = Math.max(0, packages.findIndex((item) => item.character_id === resolvedCharacterId));
    const next = event.key === "ArrowDown"
      ? Math.min(packages.length - 1, current + 1)
      : Math.max(0, current - 1);
    setSelectedCharacterId(packages[next].character_id);
    headingRef.current?.focus();
  }

  const coverReference = draft?.references.find((item) => item.role === "cover") ?? null;
  const coverFile = coverReference ? assets.find((asset) => asset.id === coverReference.asset_id) : undefined;
  const gaugeCompleteness = draft?.completeness ?? published?.completeness ?? pkg?.completeness ?? null;
  const gaugeCaption = draft
    ? `草稿 V${draft.version_number} 完整度（发布前补全引导，不阻断发布）`
    : published
      ? `已发布版本 V${published.version_number} 完整度`
      : undefined;
  const busy = bindReference.isPending || uploadReference.isPending || unbindReference.isPending
    || setCover.isPending || bindOutfit.isPending || setDefaultOutfit.isPending || unbindOutfit.isPending;

  return (
    <div className="scene-workspace pkg-workspace">
      <header className="canvas-header">
        <div>
          <span>CHARACTER MODEL PACKAGE / 角色模型包</span>
          <h2>版本化角色资产：规格、矩阵与服装集</h2>
        </div>
        <small>{packages.length} 个模型包</small>
      </header>

      <div className="pkg-create-row">
        <select
          aria-label="选择要创建模型包的角色"
          value={createCharacterId}
          onChange={(event) => setCreateCharacterId(event.target.value)}
        >
          <option value="">选择角色（还没有模型包）</option>
          {charactersWithoutPackage.map((item) => (
            <option key={item.id} value={item.id}>{item.primary_name}</option>
          ))}
        </select>
        <button
          type="button"
          className="button ink compact"
          disabled={!createCharacterId || createPackage.isPending}
          onClick={() => createPackage.mutate(createCharacterId)}
        >
          {createPackage.isPending ? <LoaderCircle className="spin" size={13} /> : <Plus size={14} />}创建角色模型包
        </button>
        <small>新建角色不会自动创建模型包；只有发布过的版本才会进入生成默认继承。</small>
      </div>

      {notice && (
        <p className="form-error">
          <CircleAlert size={14} />{notice}
          {notice.includes("请刷新") ? (
            <button type="button" className="button outline compact" onClick={() => { setNotice(""); refresh(); }}>刷新</button>
          ) : null}
        </p>
      )}

      {list.isLoading ? (
        <div className="asset-empty" role="status"><LoaderCircle className="spin" /><strong>正在载入角色模型包…</strong></div>
      ) : list.isError ? (
        <div className="asset-empty">
          <CircleAlert />
          <strong>角色模型包无法载入</strong>
          <p>{list.error.message}</p>
          <button type="button" className="button outline compact" onClick={() => list.refetch()}>重试</button>
        </div>
      ) : (
        <div className="scene-workspace-split">
          <div className="scene-list-pane">
            <div className="scene-card-list" role="listbox" aria-label="角色模型包列表" tabIndex={0} onKeyDown={onListKey}>
              {packages.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="option"
                  aria-selected={resolvedCharacterId === item.character_id}
                  className={resolvedCharacterId === item.character_id ? "scene-card active" : "scene-card"}
                  onClick={() => { setSelectedCharacterId(item.character_id); headingRef.current?.focus(); }}
                >
                  <strong>{item.character.primary_name}</strong>
                  <span>
                    {item.published_version_number ? `已发布 V${item.published_version_number}` : "尚未发布"}
                    {" · "}
                    {item.published_completeness ? `完整度 ${item.published_completeness.score}%` : "完整度 —"}
                  </span>
                  <em className={`scene-status-tone ${item.status === "ACTIVE" ? "ready" : "archived"}`}>
                    {item.status === "ACTIVE" ? "启用中" : "已归档"}
                  </em>
                </button>
              ))}
            </div>
            {!packages.length ? (
              <div className="asset-empty">
                <Users size={25} />
                <strong>尚未创建角色模型包</strong>
                <p>为角色创建模型包后，可以维护四视图矩阵、表情集与默认服装，并发布不可变版本用于生成。</p>
              </div>
            ) : null}
          </div>

          {pkg ? (
            <section className="scene-detail-pane">
              <header className="scene-detail-header">
                <div>
                  <span>PACKAGE DETAIL</span>
                  <h3 ref={headingRef} tabIndex={-1}>{characterName} 的角色模型包</h3>
                  <p aria-label={`模型包状态 ${pkg.status === "ACTIVE" ? "启用中" : "已归档"}`} className={`scene-status-tone ${pkg.status === "ACTIVE" ? "ready" : "archived"}`}>
                    {pkg.status === "ACTIVE" ? "启用中 · 已发布版本进入默认继承" : "已归档 · 退出默认继承，Character 不受影响"}
                  </p>
                </div>
                <div className="scene-detail-actions">
                  <button
                    type="button"
                    className="button outline compact"
                    disabled={hasDraft || deriveVersion.isPending}
                    title={hasDraft ? "已有草稿版本" : "从已发布版本派生新的可编辑草稿"}
                    onClick={() => deriveVersion.mutate()}
                  >
                    <Plus size={13} />派生新版本
                  </button>
                  <button
                    type="button"
                    className="button outline compact"
                    disabled={pkg.versions.length < 2}
                    onClick={() => setShowDiff(true)}
                  >
                    <GitCompareArrows size={13} />对比历史
                  </button>
                  {pkg.status === "ACTIVE" ? (
                    <button
                      type="button"
                      className="button ghost compact"
                      disabled={switchPackageStatus.isPending}
                      onClick={() => { if (window.confirm("归档角色模型包后，该角色将退出生成默认继承，既有分镜与候选不受影响。确认归档？")) switchPackageStatus.mutate("archive"); }}
                    >
                      <Layers size={13} />归档角色包
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="button outline compact"
                      disabled={switchPackageStatus.isPending}
                      onClick={() => switchPackageStatus.mutate("restore")}
                    >
                      <ArchiveRestore size={13} />恢复角色包
                    </button>
                  )}
                </div>
              </header>

              {!hasDraft && frozenVersion && (
                <section className="pkg-draft-block" aria-label={`已冻结版本 V${frozenVersion.version_number}`}>
                  <header>
                    <div>
                      <strong>已冻结版本 V{frozenVersion.version_number}</strong>
                      <em className={`scene-status-tone ${frozenMeta.tone}`}>{frozenMeta.label} · 只读</em>
                    </div>
                    <small>发布版本冻结不可编辑；如需修改请派生新版本。</small>
                  </header>
                  <FrozenSpecReadout spec={frozenVersion.spec_snapshot} />
                  <PackageViewMatrix
                    version={frozenVersion}
                    characterName={characterName}
                    assets={assets}
                    bindableAssets={bindableAssets}
                    editable={false}
                    busy={false}
                    onBindSlot={() => undefined}
                    onUnbind={() => undefined}
                    onUploadSlot={() => undefined}
                  />
                  <PackageExpressionMatrix
                    version={frozenVersion}
                    characterName={characterName}
                    assets={assets}
                    bindableAssets={bindableAssets}
                    editable={false}
                    busy={false}
                    onBindSlot={() => undefined}
                    onUnbind={() => undefined}
                    onUploadSlot={() => undefined}
                  />
                  <section className="pkg-matrix" aria-label="关联服装档案（已冻结）">
                    <header><strong>关联服装档案（{frozenVersion.outfits.length}）</strong><small>服装集与默认位随版本冻结</small></header>
                    {frozenVersion.outfits.length ? (
                      <ul className="pkg-outfit-list">
                        {frozenVersion.outfits.map((relation) => {
                          const outfit = characterOutfits.find((item) => item.id === relation.outfit_id);
                          return (
                            <li key={relation.id}>
                              <strong>{outfit?.name ?? relation.outfit_id}</strong>
                              <span>
                                {relation.is_default ? "默认服装" : "已关联"}
                                {outfit ? ` · ${outfit.reference_asset_ids.length} 张参考图` : " · 服装档案缺失"}
                              </span>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="pkg-matrix-empty">该版本未关联服装。</p>
                    )}
                  </section>
                </section>
              )}
              {!hasDraft && !frozenVersion && (
                <p className="pkg-hint">
                  当前没有草稿版本。规格与矩阵只能在草稿中修改；如需调整，请先派生新版本。
                </p>
              )}

              <PackageCompletenessGauge completeness={gaugeCompleteness} caption={gaugeCaption} />

              {draft && (
                <section className="pkg-draft-block" aria-label={`草稿版本 V${draft.version_number}`}>
                  <header>
                    <div>
                      <strong>草稿 V{draft.version_number}</strong>
                      <em className="scene-status-tone pending">DRAFT · 可编辑</em>
                    </div>
                    <div className="scene-detail-actions">
                      <button
                        type="button"
                        className="button ink compact"
                        disabled={!draft.references.length || publishVersion.isPending}
                        title={draft.references.length ? undefined : "发布前至少绑定 1 张参考图（完整度不设门槛）"}
                        onClick={() => setPublishTarget(draft)}
                      >
                        <ShieldCheck size={13} />发布当前版本 V{draft.version_number}
                      </button>
                      {pkg.versions.length > 1 && (
                        <button
                          type="button"
                          className="button ghost compact"
                          disabled={deleteDraft.isPending}
                          onClick={() => { if (window.confirm(`删除草稿 V${draft.version_number} 及其矩阵绑定？该操作不可撤销。`)) deleteDraft.mutate(draft); }}
                        >
                          <Trash2 size={13} />删除草稿
                        </button>
                      )}
                    </div>
                  </header>
                  {!draft.references.length && (
                    <p className="pkg-hint">发布前至少绑定 1 张参考图；完整度只作建议，不阻断发布。</p>
                  )}
                  <PackageSpecEditor
                    key={`spec:${pkg.id}:${pkg.version}`}
                    pkg={pkg}
                    pending={saveSpec.isPending}
                    onSave={(payload) => saveSpec.mutate(payload)}
                  />

                  <section className="pkg-cover-block" aria-label="封面">
                    <header><strong>封面图</strong><small>发布版本的门面参考，可用于默认继承兜底</small></header>
                    <div className="pkg-cover-row">
                      <div className="pkg-slot-thumb">
                        {coverFile?.content_url ? (
                          <Image src={publicUrl(coverFile.thumbnail_url ?? coverFile.content_url)!} alt={`${characterName} 封面参考图`} width={96} height={96} unoptimized />
                        ) : (
                          <span>未设置封面</span>
                        )}
                      </div>
                      <div className="pkg-slot-actions">
                        <label className="pkg-upload-label">
                          <Plus size={11} />上传封面
                          <input
                            aria-label="上传封面参考图"
                            type="file"
                            accept="image/png,image/jpeg,image/webp"
                            hidden
                            disabled={busy}
                            onChange={(event) => chooseFile(event, (file) => setCover.mutate({ file }))}
                          />
                        </label>
                        <select
                          aria-label="绑定封面素材"
                          value=""
                          disabled={busy || !bindableAssets.length}
                          onChange={(event) => { if (event.target.value) setCover.mutate({ assetId: event.target.value }); }}
                        >
                          <option value="">{bindableAssets.length ? "绑定已有素材…" : "暂无可绑定素材"}</option>
                          {bindableAssets.map((asset) => (
                            <option key={asset.id} value={asset.id}>{asset.display_name?.trim() || asset.original_name}</option>
                          ))}
                        </select>
                      </div>
                    </div>
                  </section>

                  <PackageViewMatrix
                    version={draft}
                    characterName={characterName}
                    assets={assets}
                    bindableAssets={bindableAssets}
                    editable
                    busy={busy}
                    onBindSlot={(role, assetId) => bindReference.mutate({ role, assetId })}
                    onUnbind={(reference) => unbindReference.mutate(reference)}
                    onUploadSlot={(role, file) => uploadReference.mutate({ role, file })}
                  />

                  <PackageExpressionMatrix
                    version={draft}
                    characterName={characterName}
                    assets={assets}
                    bindableAssets={bindableAssets}
                    editable
                    busy={busy}
                    onBindSlot={(role, label, assetId) => bindReference.mutate({ role, label, assetId })}
                    onUnbind={(reference) => unbindReference.mutate(reference)}
                    onUploadSlot={(role, label, file) => uploadReference.mutate({ role, label, file })}
                  />

                  <section className="pkg-matrix" aria-label="关联服装档案">
                    <header>
                      <strong>关联服装档案（{draft.outfits.length}）</strong>
                      <small>默认服装在生成分镜未指派服装时可替代满足门禁</small>
                    </header>
                    {draft.outfits.length ? (
                      <ul className="pkg-outfit-list">
                        {draft.outfits.map((relation) => {
                          const outfit = characterOutfits.find((item) => item.id === relation.outfit_id);
                          return (
                            <li key={relation.id}>
                              <strong>{outfit?.name ?? relation.outfit_id}</strong>
                              <span>
                                {relation.is_default ? "默认服装" : "已关联"}
                                {outfit ? ` · ${outfit.reference_asset_ids.length} 张参考图` : " · 服装档案缺失"}
                                {!outfit?.reference_asset_ids.length ? " · 警告：该服装没有可用参考图" : ""}
                              </span>
                              <div className="pkg-slot-actions">
                                {!relation.is_default && (
                                  <button type="button" disabled={busy} onClick={() => setDefaultOutfit.mutate(relation)}>设为默认</button>
                                )}
                                <button type="button" disabled={busy} onClick={() => unbindOutfit.mutate(relation)}>解绑</button>
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <p className="pkg-matrix-empty">还没有关联服装。服装绑定 15 分，设置默认再加 5 分。</p>
                    )}
                    {characterOutfits.filter((item) => !draft.outfits.some((relation) => relation.outfit_id === item.id)).length ? (
                      <select
                        aria-label="添加服装到版本"
                        value=""
                        disabled={busy}
                        onChange={(event) => { if (event.target.value) bindOutfit.mutate({ outfitId: event.target.value }); }}
                      >
                        <option value="">添加已有服装档案…</option>
                        {characterOutfits
                          .filter((item) => !draft.outfits.some((relation) => relation.outfit_id === item.id))
                          .map((item) => (
                            <option key={item.id} value={item.id}>{item.name} · {item.reference_asset_ids.length} 张参考图</option>
                          ))}
                      </select>
                    ) : (
                      <p className="pkg-matrix-hint">该角色的服装档案都已关联；新服装请先在“服装档案”中建立。</p>
                    )}
                  </section>
                </section>
              )}

              {pkg.versions.filter((item) => item.status !== "DRAFT").length ? (
                <section className="pkg-version-history" aria-label="版本历史">
                  <header><strong>版本历史</strong><small>已发布版本不可变；修改只能通过派生新版本</small></header>
                  <ul>
                    {pkg.versions.filter((item) => item.status !== "DRAFT").map((version) => {
                      const meta = packageVersionStatusMeta(version.status);
                      const isPointer = version.id === pkg.published_version_id;
                      return (
                        <li key={version.id}>
                          <strong>V{version.version_number}</strong>
                          <em className={`scene-status-tone ${meta.tone}`}>{meta.label}{isPointer ? " · 当前发布版本" : ""}</em>
                          <span>{version.completeness ? `完整度 ${version.completeness.score}%` : "完整度 —"} · {version.references.length} 张参考图 · {version.outfits.length} 套服装</span>
                          <div className="pkg-slot-actions">
                            {!isPointer && version.status !== "ARCHIVED" && (
                              <button type="button" disabled={activateVersion.isPending} onClick={() => activateVersion.mutate(version)}>设为发布版本</button>
                            )}
                            {!isPointer && version.status !== "ARCHIVED" && (
                              <button type="button" disabled={switchVersionStatus.isPending} onClick={() => switchVersionStatus.mutate({ version, action: "archive" })}>归档</button>
                            )}
                            {version.status === "ARCHIVED" && (
                              <button type="button" disabled={switchVersionStatus.isPending} onClick={() => switchVersionStatus.mutate({ version, action: "restore" })}>恢复</button>
                            )}
                            {draft && (
                              <button type="button" onClick={() => setShowDiff(true)}>与草稿对比</button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ) : null}
            </section>
          ) : null}
        </div>
      )}

      {publishTarget && (
        <SceneConfirmDialog
          title={`发布版本 V${publishTarget.version_number}？`}
          message="发布后该版本将固化为不可变版本，用于后续分镜与生图；历史候选与已发布版本不受影响。确认发布？"
          confirmLabel="确认发布"
          pending={publishVersion.isPending}
          onCancel={() => setPublishTarget(null)}
          onConfirm={() => publishVersion.mutate(publishTarget)}
        />
      )}

      {showDiff && pkg && (
        <PackageDiffModal
          projectId={projectId}
          characterId={pkg.character_id}
          characterName={characterName}
          pkg={pkg}
          onClose={() => setShowDiff(false)}
        />
      )}
    </div>
  );
}
