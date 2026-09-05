"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArchiveRestore,
  Check,
  CircleAlert,
  ImagePlus,
  Landmark,
  LoaderCircle,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import Image from "next/image";
import {
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import {
  api,
  isConflictError,
  originUrl,
  publicUrl,
  type Asset,
  type SceneAsset,
  type SceneAssetStatus,
  type SceneAssetStructured,
} from "@/lib/api";

import { formatBytes } from "./display";
import { SceneConfirmDialog, SceneModal } from "./scene-modal";
import {
  countPersistedSceneBindings,
  emptyStructured,
  joinList,
  pickStructured,
  pickVariantOverrides,
  splitList,
  TIME_OF_DAY_OPTIONS,
} from "./scene-structured";
import { interiorLabel, sceneAssetStatusMeta, SCENE_ASSET_STATUSES } from "./scene-status";

const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];

function conflictMessage(error: unknown, fallback: string) {
  if (isConflictError(error)) {
    // ae0c6ee 之后 ApiError.message 即后端 409 detail（字符串或 detail.message）。
    // 语义化冲突（乐观锁提示等）原样透出，只有无语义 body 才退回通用文案（#156）。
    const message = error instanceof Error ? error.message : "";
    return message && message !== "请求数据不符合要求" ? message : "数据已变化，请刷新后重试";
  }
  return error instanceof Error ? error.message : fallback;
}

function StructuredFields({
  value,
  onChange,
}: {
  value: SceneAssetStructured;
  onChange: (next: SceneAssetStructured) => void;
}) {
  return (
    <div className="scene-structured-grid">
      <label>
        <span>地点 / 场所</span>
        <input
          aria-label="地点或场所"
          value={value.place ?? ""}
          onChange={(event) => onChange({ ...value, place: event.target.value })}
        />
      </label>
      <label>
        <span>室内外</span>
        <select
          aria-label="室内或室外"
          value={value.interior === true ? "true" : value.interior === false ? "false" : ""}
          onChange={(event) => onChange({
            ...value,
            interior: event.target.value === "true" ? true : event.target.value === "false" ? false : null,
          })}
        >
          <option value="">未指定</option>
          <option value="true">室内</option>
          <option value="false">室外</option>
        </select>
      </label>
      <label>
        <span>时间</span>
        <select
          aria-label="时间段"
          value={value.time_of_day ?? ""}
          onChange={(event) => onChange({ ...value, time_of_day: event.target.value as SceneAssetStructured["time_of_day"] })}
        >
          {TIME_OF_DAY_OPTIONS.map(([id, label]) => <option key={id || "none"} value={id}>{label}</option>)}
        </select>
      </label>
      <label>
        <span>天气</span>
        <input aria-label="天气" value={value.weather ?? ""} onChange={(event) => onChange({ ...value, weather: event.target.value })} />
      </label>
      <label>
        <span>季节</span>
        <input aria-label="季节" value={value.season ?? ""} onChange={(event) => onChange({ ...value, season: event.target.value })} />
      </label>
      <label>
        <span>光照</span>
        <input aria-label="光照" value={value.lighting ?? ""} onChange={(event) => onChange({ ...value, lighting: event.target.value })} />
      </label>
      <label className="wide">
        <span>子区域</span>
        <input
          aria-label="子区域"
          value={joinList(value.subareas)}
          onChange={(event) => onChange({ ...value, subareas: splitList(event.target.value) })}
          placeholder="用逗号分隔"
        />
      </label>
      <label className="wide">
        <span>固定物件</span>
        <input
          aria-label="固定物件"
          value={joinList(value.fixed_props)}
          onChange={(event) => onChange({ ...value, fixed_props: splitList(event.target.value) })}
          placeholder="用逗号分隔"
        />
      </label>
      <label>
        <span>主色</span>
        <input
          aria-label="主色"
          value={joinList(value.palette?.dominant)}
          onChange={(event) => onChange({
            ...value,
            palette: { ...value.palette, dominant: splitList(event.target.value) },
          })}
          placeholder="#f2efe9"
        />
      </label>
      <label>
        <span>色调情绪</span>
        <input
          aria-label="色调情绪"
          value={value.palette?.mood ?? ""}
          onChange={(event) => onChange({
            ...value,
            palette: { ...value.palette, mood: event.target.value },
          })}
        />
      </label>
      <label className="wide">
        <span>空间关系（每行：起点 &gt; 关系 &gt; 终点）</span>
        <textarea
          aria-label="空间关系"
          rows={3}
          value={(value.spatial_relations ?? []).map((item) => [item.from, item.relation, item.to].filter(Boolean).join(" > ")).join("\n")}
          onChange={(event) => onChange({
            ...value,
            spatial_relations: event.target.value.split("\n").map((line) => {
              const parts = line.split(">").map((part) => part.trim());
              return { from: parts[0] ?? "", relation: parts[1] ?? "", to: parts.slice(2).join(" > ") };
            }).filter((item) => item.from || item.to || item.relation),
          })}
        />
      </label>
    </div>
  );
}

export function SceneWorkspace({
  projectId,
  assets,
  openPreview,
}: {
  projectId: string;
  assets: Asset[];
  openPreview: (url: string, label: string) => void;
}) {
  const queryClient = useQueryClient();
  const createTriggerRef = useRef<HTMLButtonElement>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement>(null);
  const variantTriggerRef = useRef<HTMLButtonElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [placeFilter, setPlaceFilter] = useState("");
  const [interiorFilter, setInteriorFilter] = useState("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  // 模态内的表单错误必须渲染在 backdrop 之上：写入 notice 会被全屏遮罩盖住，
  // 保存失败对用户完全不可见。
  const [formError, setFormError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState(false);
  const [showVariant, setShowVariant] = useState(false);
  const [editingVariantId, setEditingVariantId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SceneAsset | null>(null);
  const [bindingCount, setBindingCount] = useState<number | null | "loading">("loading");
  const [draft, setDraft] = useState({
    name: "",
    description: "",
    location_hint: "",
    structured: emptyStructured(),
  });
  const [variantDraft, setVariantDraft] = useState({
    name: "",
    time_of_day: "",
    weather: "",
    season: "",
    lighting: "",
    palette_dominant: "",
    palette_mood: "",
    is_canonical: false,
  });

  const list = useQuery({
    queryKey: ["scene-assets", projectId, { statusFilter, placeFilter, interiorFilter, includeDeleted }],
    queryFn: () => api.sceneAssets(projectId, {
      status: statusFilter || undefined,
      place: placeFilter.trim() || undefined,
      interior: interiorFilter === "" ? undefined : interiorFilter === "true",
      include_deleted: includeDeleted || undefined,
      limit: 200,
    }),
  });

  const visible = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    return (list.data ?? []).filter((item) => {
      if (!keyword) return true;
      return [item.name, item.location_hint, item.structured.place ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(keyword);
    });
  }, [list.data, search]);

  const resolvedId = selectedId ?? visible[0]?.id ?? null;
  const selected = (resolvedId
    ? visible.find((item) => item.id === resolvedId) ?? list.data?.find((item) => item.id === resolvedId)
    : null) ?? null;

  function refreshLists() {
    queryClient.invalidateQueries({ queryKey: ["scene-assets", projectId] });
    queryClient.invalidateQueries({ queryKey: ["assets", projectId] });
    queryClient.invalidateQueries({ queryKey: ["script"] });
  }

  const createAsset = useMutation({
    mutationFn: () => api.createSceneAsset(projectId, {
      name: draft.name.trim(),
      description: draft.description,
      location_hint: draft.location_hint,
      structured: pickStructured(draft.structured),
    }),
    onSuccess: (asset) => {
      setShowCreate(false);
      setSelectedId(asset.id);
      setNotice("");
      setFormError("");
      refreshLists();
    },
    onError: (error) => setFormError(conflictMessage(error, "创建场景资产失败")),
  });

  const updateAsset = useMutation({
    mutationFn: () => {
      if (!selected) throw new Error("请先选择场景资产");
      return api.updateSceneAsset(projectId, selected.id, {
        version: selected.version,
        name: draft.name.trim(),
        description: draft.description,
        structured: pickStructured(draft.structured),
      });
    },
    onSuccess: (asset) => {
      setShowEdit(false);
      setSelectedId(asset.id);
      setNotice("");
      refreshLists();
    },
    onError: (error) => setFormError(conflictMessage(error, "保存场景资产失败")),
  });

  const setStatus = useMutation({
    mutationFn: (status: SceneAssetStatus) => {
      if (!selected) throw new Error("请先选择场景资产");
      return api.updateSceneAsset(projectId, selected.id, { version: selected.version, status });
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "更新确认状态失败")),
  });

  const removeAsset = useMutation({
    mutationFn: (asset: SceneAsset) => api.deleteSceneAsset(projectId, asset.id),
    onSuccess: () => {
      setDeleteTarget(null);
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "归档场景资产失败")),
  });

  const restoreAsset = useMutation({
    mutationFn: (asset: SceneAsset) => api.restoreSceneAsset(projectId, asset.id),
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "恢复场景资产失败")),
  });

  const saveVariant = useMutation({
    mutationFn: async () => {
      if (!selected) throw new Error("请先选择场景资产");
      const structured_overrides = pickVariantOverrides({
        time_of_day: variantDraft.time_of_day,
        weather: variantDraft.weather,
        season: variantDraft.season,
        lighting: variantDraft.lighting,
        palette: {
          dominant: splitList(variantDraft.palette_dominant),
          mood: variantDraft.palette_mood,
        },
      });
      if (editingVariantId) {
        const current = selected.variants.find((item) => item.id === editingVariantId);
        if (!current) throw new Error("场景变体不存在，请刷新后重试");
        return api.updateSceneAssetVariant(projectId, selected.id, editingVariantId, {
          version: current.version,
          name: variantDraft.name.trim(),
          structured_overrides,
          is_canonical: variantDraft.is_canonical,
        });
      }
      return api.createSceneAssetVariant(projectId, selected.id, {
        name: variantDraft.name.trim(),
        structured_overrides,
        is_canonical: variantDraft.is_canonical,
      });
    },
    onSuccess: () => {
      setShowVariant(false);
      setEditingVariantId(null);
      setNotice("");
      setFormError("");
      refreshLists();
    },
    onError: (error) => setFormError(conflictMessage(error, "保存环境变体失败")),
  });

  const removeVariant = useMutation({
    mutationFn: (variantId: string) => {
      if (!selected) throw new Error("请先选择场景资产");
      return api.deleteSceneAssetVariant(projectId, selected.id, variantId);
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "删除环境变体失败")),
  });

  const setDefaultVariant = useMutation({
    mutationFn: (variant: SceneAsset["variants"][number]) => {
      if (!selected) throw new Error("请先选择场景资产");
      return api.updateSceneAssetVariant(projectId, selected.id, variant.id, {
        version: variant.version,
        is_canonical: true,
      });
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "设置默认变体失败")),
  });

  const uploadReference = useMutation({
    mutationFn: async ({ file, variantId }: { file: File; variantId?: string }) => {
      if (!selected) throw new Error("请先选择场景资产");
      const uploaded = await api.uploadAsset(projectId, "SCENE_REFERENCE", file);
      if (variantId) {
        await api.bindSceneAssetVariantReference(projectId, selected.id, variantId, {
          asset_id: uploaded.id,
          role: "main",
        });
      } else {
        await api.bindSceneAssetReference(projectId, selected.id, {
          asset_id: uploaded.id,
          role: "main",
          is_canonical: selected.references.length === 0,
        });
      }
      return uploaded;
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "上传或绑定参考图失败")),
  });

  const unbindReference = useMutation({
    mutationFn: (assetId: string) => {
      if (!selected) throw new Error("请先选择场景资产");
      return api.unbindSceneAssetReference(projectId, selected.id, assetId);
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "解绑参考图失败")),
  });

  const setCanonicalReference = useMutation({
    mutationFn: async (reference: SceneAsset["references"][number]) => {
      if (!selected) throw new Error("请先选择场景资产");
      await api.unbindSceneAssetReference(projectId, selected.id, reference.asset_id);
      return api.bindSceneAssetReference(projectId, selected.id, {
        asset_id: reference.asset_id,
        role: reference.role,
        is_canonical: true,
      });
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => {
      // #162: unbind 已提交、bind 失败——原规范参考已被真实解除，服务端处于
      // 零引用状态。立即刷新列表让 UI 回到真实（未绑定）状态而不是继续显示
      // 旧缓存里的「已绑定」，并明确告知重试方向。
      refreshLists();
      setNotice(`原规范参考已被解除，重新绑定失败（${conflictMessage(error, "设定规范参考失败")}），请重试`);
    },
  });

  const unbindVariantReference = useMutation({
    mutationFn: ({ variantId, assetId }: { variantId: string; assetId: string }) => {
      if (!selected) throw new Error("请先选择场景资产");
      return api.unbindSceneAssetVariantReference(projectId, selected.id, variantId, assetId);
    },
    onSuccess: () => {
      setNotice("");
      refreshLists();
    },
    onError: (error) => setNotice(conflictMessage(error, "解绑变体参考图失败")),
  });

  function openCreate() {
    setFormError("");
    setDraft({ name: "", description: "", location_hint: "", structured: emptyStructured() });
    setShowCreate(true);
  }

  function openEdit() {
    setFormError("");
    if (!selected) return;
    setDraft({
      name: selected.name,
      description: selected.description,
      location_hint: selected.location_hint,
      structured: pickStructured(selected.structured),
    });
    setShowEdit(true);
  }

  function openVariant(variant?: SceneAsset["variants"][number]) {
    const overrides = pickVariantOverrides(variant?.structured_overrides);
    const palette = (overrides.palette ?? {}) as { dominant?: string[]; mood?: string };
    setEditingVariantId(variant?.id ?? null);
    setVariantDraft({
      name: variant?.name ?? "",
      time_of_day: typeof overrides.time_of_day === "string" ? overrides.time_of_day : "",
      weather: typeof overrides.weather === "string" ? overrides.weather : "",
      season: typeof overrides.season === "string" ? overrides.season : "",
      lighting: typeof overrides.lighting === "string" ? overrides.lighting : "",
      palette_dominant: joinList(palette.dominant),
      palette_mood: palette.mood ?? "",
      is_canonical: variant?.is_canonical ?? false,
    });
    setFormError("");
    setShowVariant(true);
  }

  async function openDelete(asset: SceneAsset) {
    setDeleteTarget(asset);
    setBindingCount("loading");
    const count = await countPersistedSceneBindings(
      projectId,
      asset.id,
      () => api.chapters(projectId),
      (chapterId) => api.script(chapterId),
      (error) => error instanceof Error && /不存在/.test(error.message),
    );
    setBindingCount(count);
  }

  function chooseReference(event: ChangeEvent<HTMLInputElement>, variantId?: string) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || uploadReference.isPending) return;
    if (!IMAGE_TYPES.includes(file.type)) {
      setNotice("只支持 PNG、JPEG 或 WebP 图片");
      return;
    }
    uploadReference.mutate({ file, variantId });
  }

  function onListKey(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowDown", "ArrowUp"].includes(event.key) || !visible.length) return;
    event.preventDefault();
    const current = Math.max(0, visible.findIndex((item) => item.id === selected?.id));
    const next = event.key === "ArrowDown"
      ? Math.min(visible.length - 1, current + 1)
      : Math.max(0, current - 1);
    setSelectedId(visible[next].id);
    headingRef.current?.focus();
  }

  const statusMeta = sceneAssetStatusMeta(selected?.deleted_at ? "ARCHIVED" : selected?.status);
  const liveVariants = selected?.variants.filter((item) => item.deleted_at == null) ?? [];

  return (
    <div className="scene-workspace">
      <header className="canvas-header">
        <div>
          <span>SCENE BIBLE / 场景资产</span>
          <h2>地点档案、环境变体与参考图绑定</h2>
        </div>
        <small>{list.data?.length ?? 0} 个场景</small>
      </header>

      <div className="scene-toolbar">
        <button ref={createTriggerRef} type="button" className="button ink compact" onClick={openCreate}>
          <Plus size={14} />新建场景
        </button>
        <label>
          <span className="sr-only">搜索场景</span>
          <input aria-label="搜索场景或地点" placeholder="搜索场景 / 地点" value={search} onChange={(event) => setSearch(event.target.value)} />
        </label>
        <label>
          <span>状态</span>
          <select aria-label="按状态筛选场景" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            {SCENE_ASSET_STATUSES.map((status) => (
              <option key={status} value={status}>{sceneAssetStatusMeta(status).label}</option>
            ))}
          </select>
        </label>
        <label>
          <span>地点前缀</span>
          <input aria-label="按地点前缀筛选" value={placeFilter} onChange={(event) => setPlaceFilter(event.target.value)} />
        </label>
        <label>
          <span>室内外</span>
          <select aria-label="按室内外筛选" value={interiorFilter} onChange={(event) => setInteriorFilter(event.target.value)}>
            <option value="">全部</option>
            <option value="true">室内</option>
            <option value="false">室外</option>
          </select>
        </label>
        <label className="scene-archive-toggle">
          <input type="checkbox" checked={includeDeleted} onChange={(event) => setIncludeDeleted(event.target.checked)} />
          显示已归档
        </label>
      </div>

      {notice && (
        <p className="form-error" role="alert">
          <CircleAlert size={14} />{notice}
          {notice.includes("请刷新") ? (
            <button type="button" className="button outline compact" onClick={() => { setNotice(""); refreshLists(); }}>刷新</button>
          ) : null}
        </p>
      )}

      {list.isLoading ? (
        <div className="asset-empty" role="status"><LoaderCircle className="spin" /><strong>正在载入场景资产…</strong></div>
      ) : list.isError ? (
        <div className="asset-empty">
          <CircleAlert />
          <strong>场景资产无法载入</strong>
          <p>{list.error.message}</p>
          <button type="button" className="button outline compact" onClick={() => list.refetch()}>重试</button>
        </div>
      ) : (
        <div className="scene-workspace-split">
          <div className="scene-list-pane">
            <div
              className="scene-card-list"
              role="listbox"
              aria-label="场景资产列表"
              tabIndex={0}
              onKeyDown={onListKey}
            >
              {visible.map((asset) => {
                const meta = sceneAssetStatusMeta(asset.deleted_at ? "ARCHIVED" : asset.status);
                const variantCount = asset.variants.filter((item) => item.deleted_at == null).length;
                return (
                  <button
                    key={asset.id}
                    type="button"
                    role="option"
                    aria-selected={resolvedId === asset.id}
                    className={resolvedId === asset.id ? "scene-card active" : "scene-card"}
                    onClick={() => { setSelectedId(asset.id); headingRef.current?.focus(); }}
                  >
                    <strong>{asset.name}</strong>
                    <span>{interiorLabel(asset.structured.interior)} · {variantCount} 变体</span>
                    <em className={`scene-status-tone ${meta.tone}`}>{meta.label}</em>
                  </button>
                );
              })}
            </div>
            {!list.data?.length ? (
              <div className="asset-empty">
                <Landmark size={25} />
                <strong>尚未创建场景资产</strong>
                <p>新建地点档案后，才能把参考图和环境变体绑定到剧本场景。</p>
              </div>
            ) : !visible.length ? (
              <div className="asset-empty">
                <strong>未找到相关场景资产</strong>
                <button type="button" className="button outline compact" onClick={() => { setSearch(""); setStatusFilter(""); setPlaceFilter(""); setInteriorFilter(""); }}>清除搜索</button>
              </div>
            ) : null}
          </div>

          {selected ? (
            <section className="scene-detail-pane">
              <header className="scene-detail-header">
                <div>
                  <span>SCENE DETAIL</span>
                  <h3 ref={headingRef} tabIndex={-1}>{selected.name}（{interiorLabel(selected.structured.interior)}）</h3>
                  <p aria-label={`场景状态 ${statusMeta.label}`} className={`scene-status-tone ${statusMeta.tone}`}>{statusMeta.label}</p>
                </div>
                <div className="scene-detail-actions">
                  <button type="button" className="button outline compact" onClick={openEdit}><Pencil size={13} />编辑基本信息</button>
                  {selected.deleted_at ? (
                    <button type="button" className="button outline compact" disabled={restoreAsset.isPending} onClick={() => restoreAsset.mutate(selected)}>
                      <ArchiveRestore size={13} />恢复
                    </button>
                  ) : (
                    <button ref={deleteTriggerRef} type="button" className="button ghost compact" onClick={() => openDelete(selected)}>
                      <Trash2 size={13} />归档
                    </button>
                  )}
                </div>
              </header>
              <p className="scene-fixed-features">
                固定特征：{(selected.structured.fixed_props ?? []).join("、") || selected.description || "尚未填写"}
              </p>
              {selected.location_hint ? <p className="scene-location-hint">来源地点：{selected.location_hint}</p> : null}

              {!selected.deleted_at && (
                <div className="scene-status-actions">
                  <button
                    type="button"
                    className="button ink compact"
                    disabled={setStatus.isPending || selected.status === "CANONICAL" || !selected.references.some((item) => item.is_canonical)}
                    onClick={() => setStatus.mutate("CANONICAL")}
                  >
                    <Check size={13} />设为规范参考
                  </button>
                  <button
                    type="button"
                    className="button outline compact"
                    disabled={setStatus.isPending || selected.status === "NEEDS_CONFIRMATION"}
                    onClick={() => setStatus.mutate("NEEDS_CONFIRMATION")}
                  >
                    标为待确认
                  </button>
                </div>
              )}

              <section className="scene-reference-block">
                <header>
                  <strong>主参考图</strong>
                  <label className="button outline compact">
                    <Upload size={13} />上传参考图
                    <input aria-label="上传场景参考图" type="file" accept="image/png,image/jpeg,image/webp" hidden disabled={uploadReference.isPending || Boolean(selected.deleted_at)} onChange={(event) => chooseReference(event)} />
                  </label>
                </header>
                <div className="scene-reference-grid">
                  {selected.references.map((reference) => {
                    const file = assets.find((item) => item.id === reference.asset_id);
                    const label = `${selected.name} 主空间参考图${reference.role !== "main" ? ` - ${reference.role}` : ""}`;
                    return (
                      <article key={reference.id} className={reference.is_canonical ? "canonical" : undefined}>
                        {file?.content_url ? (
                          <button type="button" className="scene-reference-thumb" onClick={() => openPreview(originUrl(file.content_url)!, label)}>
                            <Image src={publicUrl(file.thumbnail_url ?? file.content_url)!} alt={label} width={160} height={160} unoptimized />
                          </button>
                        ) : <div className="scene-reference-missing">参考图文件不可用</div>}
                        <span>{reference.is_canonical ? "规范参考" : reference.role}{file ? ` · ${formatBytes(file.byte_size)}` : ""}</span>
                        <div>
                          {!reference.is_canonical && (
                            <button type="button" disabled={setCanonicalReference.isPending} onClick={() => setCanonicalReference.mutate(reference)}>设为规范参考</button>
                          )}
                          <button type="button" disabled={unbindReference.isPending} onClick={() => unbindReference.mutate(reference.asset_id)}>解绑</button>
                        </div>
                      </article>
                    );
                  })}
                  {!selected.references.length && <p className="purpose-empty">尚未绑定场景参考图</p>}
                </div>
              </section>

              <section className="scene-variant-gallery" role="region" aria-label="环境变体列表">
                <header>
                  <strong>环境变体与专属参考（{liveVariants.length}）</strong>
                  <button ref={variantTriggerRef} type="button" className="button outline compact" disabled={Boolean(selected.deleted_at)} onClick={() => openVariant()}>
                    <Plus size={13} />添加变体
                  </button>
                </header>
                <div className="scene-variant-grid">
                  {liveVariants.map((variant) => {
                    const override = pickVariantOverrides(variant.structured_overrides);
                    return (
                      <article key={variant.id} className={variant.is_canonical ? "canonical" : undefined}>
                        <strong>{variant.name}{variant.is_canonical ? "（默认）" : ""}</strong>
                        <span>
                          {[
                            TIME_OF_DAY_OPTIONS.find(([id]) => id === override.time_of_day)?.[1],
                            typeof override.weather === "string" ? override.weather : "",
                            typeof override.lighting === "string" ? override.lighting : "",
                          ].filter(Boolean).join(" · ") || "沿用主场景基调"}
                        </span>
                        <div className="scene-variant-refs">
                          {variant.references.map((reference) => {
                            const file = assets.find((item) => item.id === reference.asset_id);
                            const label = `${selected.name} 环境变体 - ${variant.name}`;
                            return (
                              <div key={reference.id}>
                                {file?.content_url ? (
                                  <button type="button" onClick={() => openPreview(originUrl(file.content_url)!, label)}>
                                    <Image src={publicUrl(file.thumbnail_url ?? file.content_url)!} alt={label} width={72} height={72} unoptimized />
                                  </button>
                                ) : <span>变体参考不可用</span>}
                                <button type="button" disabled={unbindVariantReference.isPending} onClick={() => unbindVariantReference.mutate({ variantId: variant.id, assetId: reference.asset_id })}>解绑</button>
                              </div>
                            );
                          })}
                          <label className="button ghost compact">
                            <ImagePlus size={12} />绑定变体图
                            <input aria-label={`${variant.name}绑定变体图`} type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={(event) => chooseReference(event, variant.id)} />
                          </label>
                        </div>
                        <div className="scene-variant-actions">
                          {!variant.is_canonical && (
                            <button type="button" disabled={setDefaultVariant.isPending} onClick={() => setDefaultVariant.mutate(variant)}>设为默认</button>
                          )}
                          <button type="button" onClick={() => openVariant(variant)}>编辑</button>
                          <button type="button" disabled={removeVariant.isPending} onClick={() => { if (window.confirm(`删除环境变体“${variant.name}”？其专属参考图将解除绑定，绑定该变体的剧本场景回退到资产默认参考。`)) removeVariant.mutate(variant.id); }}>删除</button>
                        </div>
                      </article>
                    );
                  })}
                  {!liveVariants.length && <p className="purpose-empty">还没有环境变体。变体只覆盖时间、天气、季节、光照和色调。</p>}
                </div>
              </section>
            </section>
          ) : null}
        </div>
      )}

      {(showCreate || showEdit) && (
        <SceneModal
          title={showCreate ? "新建场景资产" : "编辑场景资产"}
          wide
          onClose={() => { setShowCreate(false); setShowEdit(false); }}
          triggerRef={showCreate ? createTriggerRef : undefined}
        >
          <form
            className="scene-form"
            onSubmit={(event: FormEvent) => {
              event.preventDefault();
              if (!draft.name.trim()) return;
              if (showCreate) createAsset.mutate();
              else updateAsset.mutate();
            }}
          >
            <label>
              <span>名称</span>
              <input required aria-label="场景名称" value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
            </label>
            {showCreate ? (
              <label>
                <span>来源地点提示</span>
                <input aria-label="来源地点提示" value={draft.location_hint} onChange={(event) => setDraft({ ...draft, location_hint: event.target.value })} />
              </label>
            ) : draft.location_hint ? (
              <p className="scene-location-hint">来源地点（只读）：{draft.location_hint}</p>
            ) : null}
            <label className="wide">
              <span>描述</span>
              <textarea aria-label="场景描述" rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} />
            </label>
            <StructuredFields value={draft.structured} onChange={(structured) => setDraft({ ...draft, structured })} />
            {formError && <p className="form-error" role="alert"><CircleAlert size={14} />{formError}</p>}
            <div className="provider-dialog-actions">
              <button type="button" onClick={() => { setShowCreate(false); setShowEdit(false); setFormError(""); }}>取消</button>
              <button type="submit" className="button ink compact" disabled={!draft.name.trim() || createAsset.isPending || updateAsset.isPending}>
                {(createAsset.isPending || updateAsset.isPending) ? <LoaderCircle className="spin" size={13} /> : null}
                保存
              </button>
            </div>
          </form>
        </SceneModal>
      )}

      {showVariant && (
        <SceneModal
          title={editingVariantId ? "编辑环境变体" : "添加环境变体"}
          onClose={() => { setShowVariant(false); setEditingVariantId(null); }}
          triggerRef={variantTriggerRef}
        >
          <form
            className="scene-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!variantDraft.name.trim()) return;
              saveVariant.mutate();
            }}
          >
            <label>
              <span>变体名称</span>
              <input required aria-label="变体名称" value={variantDraft.name} onChange={(event) => setVariantDraft({ ...variantDraft, name: event.target.value })} />
            </label>
            <label>
              <span>时间</span>
              <select aria-label="变体时间" value={variantDraft.time_of_day} onChange={(event) => setVariantDraft({ ...variantDraft, time_of_day: event.target.value })}>
                {TIME_OF_DAY_OPTIONS.map(([id, label]) => <option key={id || "none"} value={id}>{label}</option>)}
              </select>
            </label>
            <label>
              <span>天气</span>
              <input aria-label="变体天气" value={variantDraft.weather} onChange={(event) => setVariantDraft({ ...variantDraft, weather: event.target.value })} />
            </label>
            <label>
              <span>季节</span>
              <input aria-label="变体季节" value={variantDraft.season} onChange={(event) => setVariantDraft({ ...variantDraft, season: event.target.value })} />
            </label>
            <label>
              <span>光照</span>
              <input aria-label="变体光照" value={variantDraft.lighting} onChange={(event) => setVariantDraft({ ...variantDraft, lighting: event.target.value })} />
            </label>
            <label>
              <span>主色</span>
              <input aria-label="变体主色" value={variantDraft.palette_dominant} onChange={(event) => setVariantDraft({ ...variantDraft, palette_dominant: event.target.value })} />
            </label>
            <label>
              <span>色调情绪</span>
              <input aria-label="变体色调情绪" value={variantDraft.palette_mood} onChange={(event) => setVariantDraft({ ...variantDraft, palette_mood: event.target.value })} />
            </label>
            <label className="scene-archive-toggle">
              <input type="checkbox" checked={variantDraft.is_canonical} onChange={(event) => setVariantDraft({ ...variantDraft, is_canonical: event.target.checked })} />
              设为默认变体
            </label>
            {formError && <p className="form-error" role="alert"><CircleAlert size={14} />{formError}</p>}
            <div className="provider-dialog-actions">
              <button type="button" onClick={() => { setShowVariant(false); setEditingVariantId(null); setFormError(""); }}>取消</button>
              <button type="submit" className="button ink compact" disabled={!variantDraft.name.trim() || saveVariant.isPending}>保存变体</button>
            </div>
          </form>
        </SceneModal>
      )}

      {deleteTarget && (
        <SceneConfirmDialog
          title={`归档场景“${deleteTarget.name}”？`}
          message={
            bindingCount === "loading"
              ? "正在确认剧本引用…"
              : bindingCount == null
                ? "无法确认引用数量。归档后相关剧本场景将无法继续把该资产当作已就绪参考，地点文本仍会保留。"
                : bindingCount > 0
                  ? `当前项目中有 ${bindingCount} 个剧本场景绑定了该资产。归档后它们会失去场景参考消费，地点文本仍会保留。`
                  : "当前已加载的剧本中没有发现绑定。归档后可从归档列表恢复。"
          }
          confirmLabel="确认归档"
          pending={removeAsset.isPending || bindingCount === "loading"}
          triggerRef={deleteTriggerRef}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => { if (bindingCount !== "loading") removeAsset.mutate(deleteTarget); }}
        />
      )}
    </div>
  );
}
