"use client";

import {
  api,
  type ModelCapability,
  type ProviderConnection,
} from "@/lib/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  CircleAlert,
  Coins,
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

export function ConnectionPanel({
  connection,
  models,
  modelsStatus,
  modelType,
  capability,
  verifiedOnly,
  autoFocusKey,
  onKeyFocused,
  onRetryModels,
}: {
  connection: ProviderConnection;
  models: ModelCapability[];
  modelsStatus: "loading" | "error" | "ready";
  modelType: ModelTypeFilter;
  capability: CapabilityFilter;
  verifiedOnly: boolean;
  autoFocusKey: boolean;
  onKeyFocused: () => void;
  onRetryModels: () => void;
}) {
  const queryClient = useQueryClient();
  const [keyLabel, setKeyLabel] = useState("default");
  const [apiKey, setApiKey] = useState("");
  const [manualId, setManualId] = useState("");
  const [manualName, setManualName] = useState("");
  const [manualType, setManualType] = useState<"TEXT" | "IMAGE">("TEXT");
  const [notice, setNotice] = useState("");
  const [confirm, setConfirm] = useState<
    | { type: "image"; model: ModelCapability }
    | { type: "key"; keyId: string; label: string }
    | null
  >(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const keyRef = useRef<HTMLInputElement>(null);
  const manualRef = useRef<HTMLInputElement>(null);
  const connectionModels = models.filter((model) => model.connection_id === connection.id);
  const visibleModels = filterModels(connectionModels, { modelType, capability, verifiedOnly });

  useEffect(() => {
    if (!autoFocusKey) return;
    keyRef.current?.focus();
    onKeyFocused();
  }, [autoFocusKey, onKeyFocused]);

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["providers"] });
    queryClient.invalidateQueries({ queryKey: ["models"] });
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
  const discover = useMutation({
    mutationFn: () => api.discoverProviderModels(connection.id),
    onSuccess: (items) => {
      setNotice(`已同步 ${items.length} 个模型；名称推断结果需验证后才参与自动路由`);
      refresh();
    },
  });
  const test = useMutation({
    mutationFn: ({ model, testType }: {
      model?: ModelCapability;
      testType: "CREDENTIALS" | "TEXT" | "VISION" | "IMAGE";
    }) => api.testProviderConnection(
      connection.id,
      model
        ? {
            test_type: testType,
            model_id: model.catalog_id,
            acknowledge_cost: testType === "IMAGE",
          }
        : { test_type: testType },
    ),
    onSuccess: (probe) => {
      setNotice(`${probe.probe_type}：${probe.status} · ${probe.latency_ms ?? "—"} ms`);
      refresh();
    },
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
  const removeKey = useMutation({
    mutationFn: (keyId: string) => api.deleteProviderKey(connection.id, keyId),
    onSuccess: refresh,
  });

  const pending = saveKey.isPending || discover.isPending || test.isPending
    || balance.isPending || addModel.isPending || removeKey.isPending;
  const keyError = saveKey.error ?? removeKey.error;
  const actionError = test.error ?? discover.error ?? balance.error;
  const manualError = addModel.error;

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
      {connection.protocol !== "VERTEX_NATIVE" && (
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
              disabled={!connection.credential_writable || !apiKey.trim() || !keyLabel.trim() || pending}
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
      )}
      <div className="provider-actions">
        <button
          type="button"
          disabled={!connection.configured || pending}
          onClick={() => test.mutate({ testType: "CREDENTIALS" })}
        >
          <ShieldCheck size={14} />验证凭据
        </button>
        <button
          type="button"
          disabled={!connection.configured || pending}
          onClick={() => discover.mutate()}
        >
          <RefreshCw size={14} />同步模型
        </button>
        <button
          type="button"
          disabled={!connection.configured || pending}
          onClick={() => balance.mutate()}
        >
          <Coins size={14} />查询余额
        </button>
      </div>
      {actionError && (
        <p id={`connection-${connection.id}-action-error`} className="form-error" role="alert">
          <CircleAlert size={14} />{actionError.message}
        </p>
      )}
      <ConnectionAdvanced connection={connection} busy={pending} />
      <div className="provider-models">
        {modelsStatus === "loading" && <p>正在读取模型目录…</p>}
        {modelsStatus === "error" && (
          <p className="form-error" role="alert">
            模型目录读取失败
            <button type="button" onClick={onRetryModels}>重试</button>
          </p>
        )}
        {modelsStatus === "ready" && connectionModels.length === 0 && (
          <p>还没有模型。同步目录，或手工添加上游 ID。</p>
        )}
        {modelsStatus === "ready" && connectionModels.length > 0 && visibleModels.length === 0 && (
          <p>
            没有符合筛选的模型
            <span>可清除类型、能力或仅已验证筛选。</span>
          </p>
        )}
        {modelsStatus === "ready" && visibleModels.map((model) => {
          const operations = model.operations.map(mapOperation);
          const extra = operations.length > 3 ? operations.length - 3 : 0;
          const showImage = model.model_type === "IMAGE" && connection.protocol !== "ANTHROPIC";
          const showText = model.model_type === "TEXT";
          const showVision = model.model_type === "TEXT" && model.operations.includes("multimodal_analysis");
          return (
            <article key={model.catalog_id}>
              <div>
                <strong>{model.display_name}</strong>
                <span title={model.model_id}>{model.model_type === "IMAGE" ? "图片" : "文字"} · {model.model_id}</span>
                <small>
                  {mapConfidence(model.confidence)}
                  {operations.slice(0, 3).map((label) => ` · ${label}`).join("")}
                  {extra > 0 ? ` · 更多 ${extra}` : ""}
                </small>
              </div>
              <div className="provider-model-actions">
                <button type="button" disabled title="创作界面展示偏好尚未就绪">显示</button>
                {showText && (
                  <button
                    type="button"
                    disabled={!model.enabled || !connection.configured || pending}
                    onClick={() => test.mutate({ model, testType: "TEXT" })}
                  >
                    <Activity size={13} />测试文本
                  </button>
                )}
                {showVision && (
                  <button
                    type="button"
                    disabled={!connection.configured || pending}
                    onClick={() => test.mutate({ model, testType: "VISION" })}
                  >
                    <Activity size={13} />测试视觉
                  </button>
                )}
                {showImage && (
                  <button
                    type="button"
                    className="paid-check"
                    disabled={!model.enabled || !connection.configured || pending}
                    onClick={(event) => {
                      triggerRef.current = event.currentTarget;
                      setConfirm({ type: "image", model });
                    }}
                  >
                    <Activity size={13} />测试图片
                  </button>
                )}
              </div>
            </article>
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
          {connection.protocol !== "ANTHROPIC" && <option value="IMAGE">图片模型</option>}
        </select>
        <button type="submit" disabled={!manualId.trim() || pending}>
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
          <LoaderCircle className="spin" size={14} />正在访问供应商，请稍候…
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
          message={`将向当前连接发起一次 1K 图片调用。模型：${confirm.model.display_name}。请确认已了解供应商计费规则。`}
          confirmLabel="确认测试"
          onCancel={closeConfirm}
          onConfirm={() => {
            const model = confirm.model;
            closeConfirm();
            test.mutate({ model, testType: "IMAGE" });
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
