"use client";

import {
  api,
  type ModelVisibilityBatchResult,
  type ProviderConnection,
  type ProviderModel,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CircleAlert,
  Coins,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "./confirm-dialog";
import { ConnectionAdvanced } from "./connection-advanced";
import { mapConfidence, mapHealth, mapOperation } from "./provider-copy";
import type { CapabilityFilter, ModelTypeFilter } from "./provider-filters";
import { filterModels } from "./provider-filters";

const EMPTY_MODELS: ProviderModel[] = [];

export function ConnectionPanel({
  connection,
  modelType,
  capability,
  verifiedOnly,
  showHidden,
  autoFocusKey,
  onKeyFocused,
}: {
  connection: ProviderConnection;
  modelType: ModelTypeFilter;
  capability: CapabilityFilter;
  verifiedOnly: boolean;
  showHidden: boolean;
  autoFocusKey: boolean;
  onKeyFocused: () => void;
}) {
  const queryClient = useQueryClient();
  const models = useQuery({
    queryKey: ["provider-models", connection.id],
    queryFn: () => api.providerModels(connection.id),
  });
  const [keyLabel, setKeyLabel] = useState("default");
  const [apiKey, setApiKey] = useState("");
  const [manualId, setManualId] = useState("");
  const [manualName, setManualName] = useState("");
  const [manualType, setManualType] = useState<"TEXT" | "IMAGE">("TEXT");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [visibilityFailures, setVisibilityFailures] = useState<
    ModelVisibilityBatchResult["failed"]
  >([]);
  const [confirm, setConfirm] = useState<
    | { type: "image"; model: ProviderModel }
    | { type: "key"; keyId: string; label: string }
    | null
  >(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const keyRef = useRef<HTMLInputElement>(null);
  const manualRef = useRef<HTMLInputElement>(null);
  const managementModels = models.data ?? EMPTY_MODELS;
  const selectedModels = managementModels.filter((model) => selected.has(model.id));
  const visibleModels = filterModels(managementModels, {
    modelType,
    capability,
    verifiedOnly,
    showHidden,
  });

  useEffect(() => {
    if (!autoFocusKey) return;
    keyRef.current?.focus();
    onKeyFocused();
  }, [autoFocusKey, onKeyFocused]);

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["providers"] });
    queryClient.invalidateQueries({ queryKey: ["models"] });
    queryClient.invalidateQueries({ queryKey: ["provider-models", connection.id] });
  }

  function closeConfirm() {
    const trigger = triggerRef.current;
    setConfirm(null);
    trigger?.focus();
  }

  const saveKey = useMutation({
    mutationFn: () => api.saveProviderKey(connection.id, keyLabel.trim(), apiKey.trim()),
    onSuccess: () => {
      setApiKey("");
      setNotice("密钥已保存");
      refresh();
    },
  });
  const verify = useMutation({
    mutationFn: (model?: ProviderModel) => api.verifyProviderConnection(
      connection.id,
      model
        ? {
            level: "MODEL_SMOKE",
            catalog_model_id: model.id,
            acknowledge_cost: model.output_modalities.includes("IMAGE"),
          }
        : { level: "CREDENTIALS" },
    ),
    onSuccess: (result) => {
      setNotice(
        result.probe.probe_type === "CREDENTIALS"
          ? `连接测试完成 · ${result.probe.latency_ms ?? "—"} ms`
          : `${result.probe.status === "PASSED" ? "模型测试通过" : "模型测试完成"} · ${result.probe.latency_ms ?? "—"} ms`,
      );
      refresh();
    },
  });
  const discover = useMutation({
    mutationFn: () => api.discoverProviderModels(connection.id),
    onSuccess: (result) => {
      setNotice(`模型目录已同步 · ${result.length} 个模型`);
      refresh();
    },
  });
  const toggleConnection = useMutation({
    mutationFn: () => api.updateProviderConnection(connection.id, {
      version: connection.version,
      enabled: !connection.enabled,
    }),
    onSuccess: refresh,
  });
  const balance = useMutation({
    mutationFn: () => api.providerBalance(connection.id),
    onSuccess: (result) => setNotice(
      result.configured
        ? `余额：${result.value ?? "—"} ${result.currency ?? ""}`
        : result.message,
    ),
  });
  const addModel = useMutation({
    mutationFn: () => api.createProviderModel(connection.id, {
      provider_model_id: manualId.trim(),
      display_name: (manualName.trim() || manualId.trim()),
      model_type: manualType,
      input_modalities: manualType === "IMAGE" ? ["TEXT", "IMAGE"] : ["TEXT"],
      output_modalities: manualType === "IMAGE" ? ["IMAGE"] : ["TEXT"],
      operations: manualType === "IMAGE"
        ? ["image_generate", "image_edit"]
        : ["structured_text"],
      api_surfaces: manualType === "IMAGE" ? ["IMAGES"] : [connection.use_responses_api ? "RESPONSES" : "CHAT"],
      capabilities: manualType === "IMAGE"
        ? { resolutions: ["1K"], max_reference_images: 1 }
        : { structured_output_mode: "JSON_MODE" },
    }),
    onSuccess: () => {
      setManualId("");
      setManualName("");
      setNotice("已添加。测试通过前不会进入自动路由。");
      refresh();
      queueMicrotask(() => manualRef.current?.focus());
    },
  });
  const visibility = useMutation({
    mutationFn: ({ model, displayEnabled }: { model: ProviderModel; displayEnabled: boolean }) => (
      api.updateProviderModelVisibility(model.id, displayEnabled, model.version)
    ),
    onSuccess: (model) => {
      setVisibilityFailures([]);
      setNotice(model.display_enabled ? "模型已显示在创作界面" : "模型已从创作界面隐藏");
      refresh();
    },
  });
  const bulkVisibility = useMutation({
    mutationFn: (displayEnabled: boolean) => api.updateProviderModelVisibilityBatch(
      selectedModels.map((model) => ({ model_id: model.id, expected_version: model.version })),
      displayEnabled,
    ),
    onMutate: () => setVisibilityFailures([]),
    onSuccess: (result) => {
      const failedIds = new Set(result.failed.map((item) => item.model_id));
      setSelected(failedIds);
      setVisibilityFailures(result.failed);
      setNotice(
        result.failed.length
          ? `已更新 ${result.updated.length} 个模型，${result.failed.length} 个失败并保留选择`
          : `已更新 ${result.updated.length} 个模型的展示偏好`,
      );
      refresh();
    },
  });
  const removeKey = useMutation({
    mutationFn: (keyId: string) => api.deleteProviderKey(connection.id, keyId),
    onSuccess: refresh,
  });

  const connectionActionPending = verify.isPending || discover.isPending
    || balance.isPending || toggleConnection.isPending;
  const modelActionPending = verify.isPending || visibility.isPending || bulkVisibility.isPending;
  const pending = saveKey.isPending || removeKey.isPending || connectionActionPending
    || addModel.isPending || visibility.isPending || bulkVisibility.isPending;
  const keyError = saveKey.error ?? removeKey.error;
  const actionError = verify.error ?? discover.error ?? balance.error ?? toggleConnection.error;
  const modelError = visibility.error ?? bulkVisibility.error;
  const manualError = addModel.error;
  const progress = verify.isPending
    ? "正在执行连接或模型验证…"
    : discover.isPending
      ? "正在同步模型目录…"
      : bulkVisibility.isPending || visibility.isPending
        ? "正在保存模型展示偏好…"
        : "正在访问供应商，请稍候…";

  return (
    <section className="provider-connection" aria-busy={pending}>
      <header>
        <div>
          <strong>{connection.name}</strong>
          <span title={connection.base_url}>{connection.protocol} · {connection.base_url}</span>
        </div>
        <em className={`provider-state ${connection.health_state.toLowerCase()}`}>
          {mapHealth(connection.health_state)}
        </em>
      </header>
      <div className="provider-metrics">
        <span>{connection.key_count} 个密钥</span>
        <span>{connection.model_count} 个模型</span>
        <span>{connection.latency_ms === null ? "延迟未测" : `${connection.latency_ms} ms`}</span>
        <span>{connection.message}</span>
      </div>
      {connection.credential_source === "CONNECTION_KEY" ? (
        <>
          <div className="provider-key-form">
            <input
              aria-label="密钥标签"
              value={keyLabel}
              onChange={(event) => setKeyLabel(event.target.value)}
              placeholder="密钥标签"
            />
            <input
              ref={keyRef}
              id={`connection-${connection.id}-api-key`}
              aria-label="API Key"
              aria-describedby={keyError ? `connection-${connection.id}-key-error` : undefined}
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder={connection.credential_writable ? "输入 API Key（不会回显）" : "服务端未配置凭据主密钥"}
              disabled={!connection.credential_writable}
            />
            <button
              type="button"
              title={!connection.credential_writable ? "服务端未配置凭据主密钥" : undefined}
              disabled={!connection.credential_writable || !apiKey.trim() || !keyLabel.trim() || saveKey.isPending}
              onClick={() => saveKey.mutate()}
            >
              <KeyRound size={14} />保存密钥
            </button>
          </div>
          {!connection.credential_writable && (
            <p className="provider-field-hint">服务端未配置凭据主密钥</p>
          )}
          <div className="provider-key-list">
            {connection.keys.map((key) => (
              <span key={key.id}>
                <KeyRound size={12} />
                {key.label} {key.key_hint} · {mapHealth(key.health_state)}
                {key.cooldown_until && (
                  <em>冷却至 {new Date(key.cooldown_until).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</em>
                )}
                <button
                  type="button"
                  aria-label={`删除 ${key.label}`}
                  disabled={removeKey.isPending}
                  onClick={(event) => {
                    triggerRef.current = event.currentTarget;
                    setConfirm({ type: "key", keyId: key.id, label: key.label });
                  }}
                >
                  <Trash2 size={12} />
                </button>
              </span>
            ))}
          </div>
          {keyError && (
            <p id={`connection-${connection.id}-key-error`} className="form-error" role="alert">
              <CircleAlert size={14} />{keyError.message}
            </p>
          )}
        </>
      ) : (
        <p className="provider-field-hint">
          凭据由服务端环境管理；当前{connection.configured ? "已就绪" : "未就绪"}，不会显示凭据路径或内容。
        </p>
      )}
      <div className="provider-actions">
        <button
          type="button"
          disabled={!connection.configured || connectionActionPending}
          onClick={() => verify.mutate(undefined)}
        >
          <ShieldCheck size={14} />测试连接
        </button>
        {connection.supports_model_discovery && (
          <button
            type="button"
            disabled={!connection.configured || connectionActionPending}
            onClick={() => discover.mutate()}
          >
            <RefreshCw size={14} />同步模型
          </button>
        )}
        <button
          type="button"
          disabled={connectionActionPending}
          onClick={() => toggleConnection.mutate()}
        >
          <RefreshCw size={14} />{connection.enabled ? "停用连接" : "启用连接"}
        </button>
        {connection.supports_balance && (
          <button
            type="button"
            disabled={!connection.configured || connectionActionPending}
            onClick={() => balance.mutate()}
          >
            <Coins size={14} />查询余额
          </button>
        )}
      </div>
      {actionError && (
        <p id={`connection-${connection.id}-action-error`} className="form-error" role="alert">
          <CircleAlert size={14} />{actionError.message}
        </p>
      )}
      <ConnectionAdvanced connection={connection} busy={pending} />
      <div className="provider-models">
        {models.isPending && <p>正在读取模型目录…</p>}
        {models.isError && (
          <p className="form-error" role="alert">
            模型目录读取失败
            <button type="button" onClick={() => models.refetch()}>重试</button>
          </p>
        )}
        {!models.isPending && !models.isError && managementModels.length === 0 && (
          <p>
            {connection.supports_model_discovery
              ? "还没有模型。先同步模型列表，或手工添加上游 ID。"
              : "此连接不支持自动发现模型，请手工添加上游 ID。"}
          </p>
        )}
        {!models.isPending && !models.isError && managementModels.length > 0 && visibleModels.length === 0 && (
          <p>
            没有符合筛选的模型
            <span>可清除类型、能力、仅已验证筛选，或开启“显示已隐藏”。</span>
          </p>
        )}
        {selectedModels.length > 0 && (
          <div className="provider-model-batch" role="toolbar" aria-label="批量模型展示设置">
            <strong>已选 {selectedModels.length} 个模型</strong>
            <button
              type="button"
              disabled={bulkVisibility.isPending}
              onClick={() => bulkVisibility.mutate(false)}
            >
              <EyeOff size={13} />隐藏所选
            </button>
            <button
              type="button"
              disabled={bulkVisibility.isPending}
              onClick={() => bulkVisibility.mutate(true)}
            >
              <Eye size={13} />显示所选
            </button>
            <button type="button" disabled={bulkVisibility.isPending} onClick={() => setSelected(new Set())}>
              取消选择
            </button>
          </div>
        )}
        {visibleModels.map((model) => {
          const operations = model.operations.map(mapOperation);
          const extra = operations.length > 3 ? operations.length - 3 : 0;
          const isImage = model.output_modalities.includes("IMAGE")
            && (model.operations.includes("image_generate") || model.operations.includes("image_edit"));
          const probeLabel = isImage
            ? "测试图片"
            : model.operations.includes("multimodal_analysis")
              ? "测试视觉"
              : "测试文本";
          return (
            <article key={model.id} className={model.display_enabled ? "" : "provider-model-hidden"}>
              <label className="provider-model-select">
                <input
                  type="checkbox"
                  aria-label={`选择 ${model.display_name}`}
                  checked={selected.has(model.id)}
                  disabled={bulkVisibility.isPending}
                  onChange={(event) => {
                    setSelected((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(model.id);
                      else next.delete(model.id);
                      return next;
                    });
                  }}
                />
              </label>
              <div>
                <strong>{model.display_name}</strong>
                <span title={model.provider_model_id}>
                  {model.model_type === "IMAGE" ? "图片" : "文字"} · {model.provider_model_id}
                  {!model.display_enabled ? " · 已隐藏" : ""}
                  {!model.enabled ? " · 不可调用" : ""}
                </span>
                <small>
                  {mapConfidence(model.confidence)} · 来源 {model.source}
                  {operations.slice(0, 3).map((label) => ` · ${label}`).join("")}
                  {extra > 0 ? ` · 更多 ${extra}` : ""}
                </small>
              </div>
              <div className="provider-model-actions">
                <button
                  type="button"
                  disabled={modelActionPending}
                  onClick={() => visibility.mutate({ model, displayEnabled: !model.display_enabled })}
                >
                  {model.display_enabled ? <EyeOff size={13} /> : <Eye size={13} />}
                  {model.display_enabled ? "隐藏" : "显示"}
                </button>
                <button
                  type="button"
                  className={isImage ? "paid-check" : undefined}
                  disabled={!model.enabled || !connection.configured || modelActionPending}
                  onClick={(event) => {
                    if (isImage) {
                      triggerRef.current = event.currentTarget;
                      setConfirm({ type: "image", model });
                    } else {
                      verify.mutate(model);
                    }
                  }}
                >
                  <Activity size={13} />{probeLabel}
                </button>
              </div>
            </article>
          );
        })}
        {modelError && (
          <p className="form-error" role="alert">
            <CircleAlert size={14} />{modelError.message}
          </p>
        )}
        {visibilityFailures.map((failure) => {
          const model = managementModels.find((item) => item.id === failure.model_id);
          return (
            <p key={failure.model_id} className="form-error" role="alert">
              <CircleAlert size={14} />
              {model?.display_name ?? failure.model_id}：{failure.message}
            </p>
          );
        })}
      </div>
      <form
        className="provider-manual-model"
        onSubmit={(event) => {
          event.preventDefault();
          addModel.mutate();
        }}
      >
        <input
          ref={manualRef}
          aria-label="上游模型 ID"
          aria-describedby={manualError ? `connection-${connection.id}-manual-error` : undefined}
          value={manualId}
          onChange={(event) => setManualId(event.target.value)}
          placeholder="上游模型 ID"
        />
        <input
          aria-label="显示名"
          value={manualName}
          onChange={(event) => setManualName(event.target.value)}
          placeholder="显示名"
        />
        <select
          aria-label="模型类型"
          value={manualType}
          onChange={(event) => setManualType(event.target.value as "TEXT" | "IMAGE")}
        >
          <option value="TEXT">文字模型</option>
          {connection.supported_model_types.includes("IMAGE") && <option value="IMAGE">图片模型</option>}
        </select>
        <button type="submit" disabled={!manualId.trim() || addModel.isPending}>
          <Plus size={14} />添加模型
        </button>
      </form>
      {manualError && (
        <p id={`connection-${connection.id}-manual-error`} className="form-error" role="alert">
          <CircleAlert size={14} />{manualError.message}
        </p>
      )}
      {pending && (
        <p className="settings-progress" role="status">
          <LoaderCircle className="spin" size={14} />{progress}
        </p>
      )}
      {notice && (
        <p className="save-success" role="status">
          <Sparkles size={14} />{notice}
        </p>
      )}
      {confirm?.type === "image" && (
        <ConfirmDialog
          title="图片能力测试可能产生费用"
          message={`将向当前连接发起一次图片模型冒烟调用。模型：${confirm.model.display_name}。请确认已了解供应商计费规则。`}
          confirmLabel="确认测试"
          onCancel={closeConfirm}
          onConfirm={() => {
            const model = confirm.model;
            closeConfirm();
            verify.mutate(model);
          }}
        />
      )}
      {confirm?.type === "key" && (
        <ConfirmDialog
          title="删除密钥"
          message={`删除密钥「${confirm.label}」？已保存的密文将无法恢复。`}
          confirmLabel="确认删除"
          danger
          onCancel={closeConfirm}
          onConfirm={() => {
            const keyId = confirm.keyId;
            closeConfirm();
            removeKey.mutate(keyId);
          }}
        />
      )}
    </section>
  );
}
