"use client";

import {
  api,
  type ModelCapability,
  type ProviderConnection,
  type ProviderProfile,
} from "@/lib/api";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDownAZ,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  Coins,
  KeyRound,
  LoaderCircle,
  Plus,
  RefreshCw,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";

function ConnectionPanel({
  connection,
  models,
}: {
  connection: ProviderConnection;
  models: ModelCapability[];
}) {
  const queryClient = useQueryClient();
  const [keyLabel, setKeyLabel] = useState("default");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(connection.base_url);
  const [responses, setResponses] = useState(connection.use_responses_api);
  const [endpointText, setEndpointText] = useState(
    JSON.stringify(connection.endpoint_templates, null, 2),
  );
  const [headerText, setHeaderText] = useState(
    JSON.stringify(connection.extra_headers, null, 2),
  );
  const [manualId, setManualId] = useState("");
  const [manualType, setManualType] = useState<"TEXT" | "IMAGE">("TEXT");
  const [notice, setNotice] = useState("");
  const connectionModels = models.filter(
    (model) => model.connection_id === connection.id,
  );
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["providers"] });
    queryClient.invalidateQueries({ queryKey: ["models"] });
  };
  const saveKey = useMutation({
    mutationFn: () => api.saveProviderKey(connection.id, keyLabel.trim(), apiKey.trim()),
    onSuccess: () => {
      setApiKey("");
      setNotice("API Key 已加密保存；浏览器不会读取明文");
      refresh();
    },
  });
  const saveConnection = useMutation({
    mutationFn: () => api.updateProviderConnection(connection.id, {
      version: connection.version,
      base_url: baseUrl.trim(),
      use_responses_api: responses,
      endpoint_templates: JSON.parse(endpointText) as Record<string, string>,
      extra_headers: JSON.parse(headerText) as Record<string, string>,
    }),
    onSuccess: () => {
      setNotice("连接与端点模板已保存");
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
      display_name: manualId.trim(),
      model_type: manualType,
      input_modalities: manualType === "IMAGE" ? ["TEXT", "IMAGE"] : ["TEXT"],
      output_modalities: manualType === "IMAGE" ? ["IMAGE"] : ["TEXT"],
      operations: manualType === "IMAGE"
        ? ["image_generate", "image_edit"]
        : ["structured_text"],
      api_surfaces: manualType === "IMAGE" ? ["IMAGES"] : [responses ? "RESPONSES" : "CHAT"],
      capabilities: manualType === "IMAGE"
        ? { resolutions: ["1K"], max_reference_images: 1 }
        : { structured_output_mode: "JSON_MODE" },
    }),
    onSuccess: () => {
      setManualId("");
      setNotice("手动模型已加入；请先测试能力再用于自动路由");
      refresh();
    },
  });
  const removeKey = useMutation({
    mutationFn: (keyId: string) => api.deleteProviderKey(connection.id, keyId),
    onSuccess: refresh,
  });
  const pending = saveKey.isPending || saveConnection.isPending || discover.isPending
    || test.isPending || balance.isPending || addModel.isPending;
  const error = saveKey.error ?? saveConnection.error ?? discover.error ?? test.error
    ?? balance.error ?? addModel.error ?? removeKey.error;

  return <section className="provider-connection">
    <header>
      <div><strong>{connection.name}</strong><span>{connection.protocol} · {connection.base_url}</span></div>
      <em className={`provider-state ${connection.health_state.toLowerCase()}`}>{connection.health_state}</em>
    </header>
    <div className="provider-metrics">
      <span>{connection.key_count} 个密钥</span><span>{connection.model_count} 个模型</span>
      <span>{connection.latency_ms === null ? "延迟未测" : `${connection.latency_ms} ms`}</span>
      <span>{connection.message}</span>
    </div>
    {connection.protocol !== "VERTEX_NATIVE" && <>
      <div className="provider-key-form">
        <input aria-label="密钥标签" value={keyLabel} onChange={(event) => setKeyLabel(event.target.value)} placeholder="密钥标签" />
        <input aria-label="API Key" type="password" autoComplete="new-password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={connection.credential_writable ? "输入 API Key（不会回显）" : "服务端未配置凭据主密钥"} />
        <button disabled={!connection.credential_writable || !apiKey.trim() || !keyLabel.trim() || pending} onClick={() => saveKey.mutate()}><KeyRound size={14} />保存密钥</button>
      </div>
      <div className="provider-key-list">{connection.keys.map((key) => <span key={key.id}><KeyRound size={12} />{key.label} {key.key_hint} · {key.health_state}<button aria-label={`删除 ${key.label}`} onClick={() => removeKey.mutate(key.id)}><Trash2 size={12} /></button></span>)}</div>
    </>}
    <div className="provider-actions">
      <button disabled={!connection.configured || pending} onClick={() => test.mutate({ testType: "CREDENTIALS" })}><ShieldCheck size={14} />验证凭据</button>
      <button disabled={!connection.configured || pending} onClick={() => discover.mutate()}><RefreshCw size={14} />同步模型</button>
      <button disabled={!connection.configured || pending} onClick={() => balance.mutate()}><Coins size={14} />查询余额</button>
    </div>
    <details className="provider-advanced">
      <summary>连接、端点模板与自定义请求头</summary>
      <label><span>Base URL</span><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /></label>
      {connection.protocol === "OPENAI" && <label className="provider-check"><input type="checkbox" checked={responses} onChange={(event) => setResponses(event.target.checked)} />文本优先使用 Responses API</label>}
      <label><span>端点模板（JSON）</span><textarea value={endpointText} onChange={(event) => setEndpointText(event.target.value)} /></label>
      <label><span>额外请求头（禁止 Authorization / x-api-key）</span><textarea value={headerText} onChange={(event) => setHeaderText(event.target.value)} /></label>
      <button disabled={pending} onClick={() => saveConnection.mutate()}><Save size={14} />保存连接</button>
    </details>
    <div className="provider-models">
      {connectionModels.map((model) => <article key={model.catalog_id}>
        <div><strong>{model.display_name}</strong><span>{model.model_type} · {model.model_id}</span><small>{model.confidence} · {model.operations.join(" / ")}</small></div>
        <button className={model.model_type === "IMAGE" ? "paid-check" : ""} disabled={!connection.configured || pending} onClick={() => {
          if (model.model_type === "IMAGE" && !window.confirm("图片能力测试会产生一次 1K 调用费用，是否继续？")) return;
          test.mutate({ model, testType: model.model_type === "IMAGE" ? "IMAGE" : "TEXT" });
        }}><Activity size={13} />测试{model.model_type === "IMAGE" ? "图片" : "文本"}</button>
        {model.model_type === "TEXT" && model.operations.includes("multimodal_analysis") && <button disabled={!connection.configured || pending} onClick={() => test.mutate({ model, testType: "VISION" })}><Activity size={13} />测试视觉</button>}
      </article>)}
      {!connectionModels.length && <p>尚无模型。先同步模型列表，或手动添加供应商模型 ID。</p>}
    </div>
    <form className="provider-manual-model" onSubmit={(event) => { event.preventDefault(); addModel.mutate(); }}>
      <input value={manualId} onChange={(event) => setManualId(event.target.value)} placeholder="手动模型 ID" />
      <select value={manualType} onChange={(event) => setManualType(event.target.value as "TEXT" | "IMAGE")}><option value="TEXT">文字模型</option><option value="IMAGE">图片模型</option></select>
      <button disabled={!manualId.trim() || pending}><Plus size={14} />添加</button>
    </form>
    {pending && <p className="settings-progress"><LoaderCircle className="spin" size={14} />正在访问供应商，请稍候…</p>}
    {notice && <p className="save-success"><Sparkles size={14} />{notice}</p>}
    {error && <p className="form-error"><CircleAlert size={14} />{error.message}</p>}
  </section>;
}

type ProviderSort = "RECOMMENDED" | "NAME" | "HEALTH" | "MODELS" | "LATENCY";

const healthOrder: Record<string, number> = {
  HEALTHY: 0,
  DEGRADED: 1,
  UNKNOWN: 2,
  UNCONFIGURED: 3,
  OFFLINE: 4,
};

function providerModelCount(provider: ProviderProfile) {
  return provider.connections.reduce((total, connection) => total + connection.model_count, 0);
}

function providerLatency(provider: ProviderProfile) {
  const measured = provider.connections
    .map((connection) => connection.latency_ms)
    .filter((latency): latency is number => latency !== null);
  return measured.length ? Math.min(...measured) : Number.POSITIVE_INFINITY;
}

function providerHealthRank(provider: ProviderProfile) {
  return Math.min(...provider.connections.map((connection) => healthOrder[connection.health_state] ?? 5));
}

function sortProviders(items: ProviderProfile[], sort: ProviderSort) {
  return [...items].sort((left, right) => {
    if (sort === "NAME") return left.name.localeCompare(right.name, "zh-CN");
    if (sort === "HEALTH") return providerHealthRank(left) - providerHealthRank(right) || left.name.localeCompare(right.name, "zh-CN");
    if (sort === "MODELS") return providerModelCount(right) - providerModelCount(left) || left.name.localeCompare(right.name, "zh-CN");
    if (sort === "LATENCY") return providerLatency(left) - providerLatency(right) || left.name.localeCompare(right.name, "zh-CN");
    const leftScore = Number(left.connections.some((connection) => connection.configured)) * 4
      + Number(providerHealthRank(left) === 0) * 2 + Number(providerModelCount(left) > 0);
    const rightScore = Number(right.connections.some((connection) => connection.configured)) * 4
      + Number(providerHealthRank(right) === 0) * 2 + Number(providerModelCount(right) > 0);
    return rightScore - leftScore || left.name.localeCompare(right.name, "zh-CN");
  });
}

function ProviderCard({ provider, models }: { provider: ProviderProfile; models: ModelCapability[] }) {
  const queryClient = useQueryClient();
  const [expanded, setExpanded] = useState(
    provider.connections.some((connection) => connection.configured),
  );
  const toggle = useMutation({
    mutationFn: () => api.updateProvider(provider.id, { version: provider.version, enabled: !provider.enabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["providers"] });
      queryClient.invalidateQueries({ queryKey: ["models"] });
    },
  });
  const connectionCount = provider.connections.length;
  const configuredCount = provider.connections.filter((connection) => connection.configured).length;
  const modelCount = providerModelCount(provider);
  return <article className={`provider-card ${provider.enabled ? "" : "disabled"}`}>
    <header>
      <button className="provider-card-toggle" aria-expanded={expanded} onClick={() => setExpanded((current) => !current)}>
        {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        <span className="provider-card-title"><span>{provider.category} · {provider.risk_label}</span><strong>{provider.name}</strong><small>{provider.description}</small></span>
        <span className="provider-card-counts"><small>{configuredCount}/{connectionCount} 连接</small><small>{modelCount} 模型</small></span>
      </button>
      <button className="provider-enable-toggle" disabled={toggle.isPending} onClick={() => toggle.mutate()}>{provider.enabled ? "停用" : "启用"}</button>
    </header>
    {expanded && <div className="provider-card-body">{provider.connections.map((connection) => <ConnectionPanel key={`${connection.id}:${connection.version}`} connection={connection} models={models} />)}</div>}
  </article>;
}

function ProviderGroup({
  label,
  providers,
  models,
  defaultExpanded,
  forceExpanded,
}: {
  label: string;
  providers: ProviderProfile[];
  models: ModelCapability[];
  defaultExpanded: boolean;
  forceExpanded: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const shown = forceExpanded || expanded;
  return <section className="provider-group">
    <header><button className="provider-group-toggle" aria-expanded={shown} onClick={() => setExpanded((current) => !current)}>{shown ? <ChevronDown size={14} /> : <ChevronRight size={14} />}<span>{label}</span><strong>{providers.length}</strong></button></header>
    {shown && (providers.length ? providers.map((provider) => <ProviderCard key={`${provider.id}:${provider.version}`} provider={provider} models={models} />) : <p className="provider-group-empty">当前筛选条件下没有供应商</p>)}
  </section>;
}

export function ProviderManagement() {
  const queryClient = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers });
  const models = useQuery({ queryKey: ["models"], queryFn: api.models });
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<ProviderSort>("RECOMMENDED");
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<"OPENAI" | "ANTHROPIC">("OPENAI");
  const [baseUrl, setBaseUrl] = useState("");
  const create = useMutation({
    mutationFn: () => api.createProvider({ name: name.trim(), protocol, base_url: baseUrl.trim(), use_responses_api: false }),
    onSuccess: () => {
      setName("");
      setBaseUrl("");
      queryClient.invalidateQueries({ queryKey: ["providers"] });
    },
  });
  const grouped = useMemo(() => {
    const normalized = filter.trim().toLowerCase();
    const visible = (providers.data ?? []).filter((provider) => !normalized || `${provider.name} ${provider.preset_key ?? ""}`.toLowerCase().includes(normalized));
    return {
      enabled: sortProviders(visible.filter((provider) => provider.enabled), sort),
      disabled: sortProviders(visible.filter((provider) => !provider.enabled), sort),
    };
  }, [filter, providers.data, sort]);

  return <article className="control-card provider-platform">
    <header><div><Sparkles size={18} /><span>AI PROVIDERS / MODEL PLATFORM</span></div><small>{providers.data?.length ?? 0} 个预设供应商</small></header>
    <div className="provider-toolbar"><input value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="搜索 OpenAI、Claude、DeepSeek、火山…" /><label><ArrowDownAZ size={14} /><span>排序</span><select aria-label="供应商排序" value={sort} onChange={(event) => setSort(event.target.value as ProviderSort)}><option value="RECOMMENDED">推荐顺序</option><option value="NAME">名称</option><option value="HEALTH">健康优先</option><option value="MODELS">模型数量</option><option value="LATENCY">延迟</option></select></label><span>OpenAI / Anthropic；Vertex 保留为原生连接。</span></div>
    <details className="provider-create">
      <summary><Plus size={14} />添加自定义供应商</summary>
      <form onSubmit={(event) => { event.preventDefault(); create.mutate(); }}><input value={name} onChange={(event) => setName(event.target.value)} placeholder="供应商名称" /><select value={protocol} onChange={(event) => setProtocol(event.target.value as "OPENAI" | "ANTHROPIC")}><option value="OPENAI">OpenAI 协议</option><option value="ANTHROPIC">Anthropic 协议</option></select><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.example.com/v1" /><button disabled={!name.trim() || !baseUrl.trim() || create.isPending}><Plus size={14} />创建</button></form>
      {create.error && <p className="form-error"><CircleAlert size={14} />{create.error.message}</p>}
    </details>
    {providers.isLoading || models.isLoading ? <div className="loading-panel"><LoaderCircle className="spin" />读取供应商与模型目录…</div> : <div className="provider-list"><ProviderGroup label="已启用" providers={grouped.enabled} models={models.data ?? []} defaultExpanded forceExpanded={Boolean(filter.trim())} /><ProviderGroup label="已停用" providers={grouped.disabled} models={models.data ?? []} defaultExpanded={false} forceExpanded={Boolean(filter.trim())} /></div>}
  </article>;
}
