import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ModelCapability, type ProviderConnection, type ProviderProfile } from "@/lib/api";

import { ProviderManagement } from "./provider-management";
import { mapConfidence } from "./provider-settings/provider-copy";
import {
  filterModels,
  providerMatchesQuery,
  sortProviders,
} from "./provider-settings/provider-filters";
import { validateJsonRecord } from "./provider-settings/provider-json";

const providersApi = vi.spyOn(api, "providers");
const modelsApi = vi.spyOn(api, "models");
const createProvider = vi.spyOn(api, "createProvider");
const updateProvider = vi.spyOn(api, "updateProvider");
const deleteProvider = vi.spyOn(api, "deleteProvider");
const testConnection = vi.spyOn(api, "testProviderConnection");
const updateConnection = vi.spyOn(api, "updateProviderConnection");

const stylesheet = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");

function makeConnection(overrides: Partial<ProviderConnection> = {}): ProviderConnection {
  return {
    id: "conn-1",
    provider_id: "provider-1",
    name: "default",
    protocol: "OPENAI",
    base_url: "https://api.openai.com/v1",
    enabled: true,
    configured: true,
    credential_writable: true,
    use_responses_api: false,
    endpoint_templates: {},
    extra_headers: {},
    balance_config: {},
    nonsecret_config: {},
    health_state: "DEGRADED",
    last_checked_at: null,
    last_success_at: null,
    latency_ms: null,
    error_code: "PROVIDER_AUTH",
    message: "密钥无效",
    key_count: 1,
    model_count: 0,
    keys: [{
      id: "key-1",
      label: "default",
      key_hint: "sk-****1234",
      enabled: true,
      health_state: "OFFLINE",
      cooldown_until: null,
      last_used_at: null,
      last_error_code: "PROVIDER_AUTH",
    }],
    version: 1,
    ...overrides,
  };
}

function makeProvider(overrides: Partial<ProviderProfile> = {}): ProviderProfile {
  const connections = overrides.connections ?? [makeConnection({
    provider_id: overrides.id ?? "provider-1",
  })];
  return {
    id: "provider-1",
    preset_key: "openai",
    name: "OpenAI",
    category: "compatible",
    description: "",
    built_in: true,
    enabled: true,
    risk_label: "LOW",
    documentation_url: null,
    version: 1,
    ...overrides,
    connections,
  };
}

function makeModel(overrides: Partial<ModelCapability> = {}): ModelCapability {
  const modelId = overrides.model_id ?? "gpt-4.1-mini";
  return {
    catalog_id: overrides.catalog_id ?? "cat-1",
    connection_id: "conn-1",
    provider: "openai",
    protocol: "OPENAI",
    model_id: modelId,
    logical_alias: modelId,
    display_name: overrides.display_name ?? modelId,
    model_type: "TEXT",
    input_modalities: ["TEXT"],
    output_modalities: ["TEXT"],
    operations: ["structured_text"],
    resolutions: [],
    preview_resolutions: [],
    max_reference_images: 0,
    regions: [],
    confidence: "VERIFIED",
    enabled: true,
    auto_eligible: true,
    priority: 0,
    ...overrides,
  };
}

function renderPlatform() {
  return render(
    <QueryClientProvider client={new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })}>
      <ProviderManagement />
    </QueryClientProvider>,
  );
}

describe("ProviderManagement 错误展示", () => {
  beforeEach(() => {
    providersApi.mockReset().mockResolvedValue([makeProvider()]);
    modelsApi.mockReset().mockResolvedValue([]);
    createProvider.mockReset();
    updateProvider.mockReset();
    deleteProvider.mockReset();
    testConnection.mockReset();
    updateConnection.mockReset();
  });

  it("连接健康错误对用户可见，且不回显密钥明文", async () => {
    renderPlatform();
    await waitFor(() => {
      expect(screen.getByText("密钥无效")).toBeInTheDocument();
    });
    expect(screen.getByText(/sk-\*\*\*\*1234/)).toBeInTheDocument();
    expect(screen.queryByText(/sk-live-secret/)).not.toBeInTheDocument();
  });

  it("创建供应商失败时展示 form-error，成功后刷新 providers", async () => {
    createProvider.mockRejectedValueOnce(new Error("供应商名称已存在"));
    renderPlatform();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "添加供应商" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(createProvider).toHaveBeenCalled();
      expect(screen.getByText("供应商名称已存在")).toBeInTheDocument();
    });

    createProvider.mockResolvedValueOnce(makeProvider());
    const before = providersApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(providersApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("凭据测试失败展示用户可见错误，不出现输入密钥", async () => {
    testConnection.mockRejectedValue(new Error("上游返回 401"));
    renderPlatform();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "验证凭据" })).toBeEnabled();
    });
    fireEvent.change(screen.getByPlaceholderText("输入 API Key（不会回显）"), {
      target: { value: "sk-live-secret-should-not-render" },
    });
    fireEvent.click(screen.getByRole("button", { name: "验证凭据" }));
    await waitFor(() => {
      expect(testConnection).toHaveBeenCalledWith("conn-1", { test_type: "CREDENTIALS" });
      expect(screen.getByText("上游返回 401")).toBeInTheDocument();
    });
    expect(document.body.textContent).toContain("上游返回 401");
    expect(document.body.textContent).not.toContain("sk-live-secret-should-not-render");
  });
});

describe("供应商生命周期", () => {
  beforeEach(() => {
    providersApi.mockReset().mockResolvedValue([makeProvider()]);
    modelsApi.mockReset().mockResolvedValue([]);
    createProvider.mockReset();
    updateProvider.mockReset();
    deleteProvider.mockReset();
    testConnection.mockReset();
    updateConnection.mockReset();
  });

  it("P1 名称或 URL 为空时提交按钮禁用", async () => {
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    expect(screen.getByRole("button", { name: "创建" })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    expect(screen.getByRole("button", { name: "创建" })).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    expect(screen.getByRole("button", { name: "创建" })).toBeEnabled();
  });

  it("P2 创建 400 时 URL 字段关联错误且面板保持打开", async () => {
    createProvider.mockRejectedValueOnce(new Error("供应商 Base URL 必须是 HTTP(S) 地址"));
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("https://api.example.com/v1")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByPlaceholderText("https://api.example.com/v1")).toHaveAttribute(
        "aria-describedby",
        "provider-create-error",
      );
      expect(screen.getByText("供应商 Base URL 必须是 HTTP(S) 地址")).toBeInTheDocument();
      expect(screen.getByPlaceholderText("供应商名称")).toBeInTheDocument();
    });
  });

  it("P3 创建 409 时错误关联名称字段且不关闭面板", async () => {
    createProvider.mockRejectedValueOnce(new Error("供应商名称已存在"));
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("供应商名称")).toHaveAttribute("aria-invalid", "true");
      expect(screen.getByPlaceholderText("供应商名称")).toHaveFocus();
      expect(screen.getByText("供应商名称已存在")).toBeInTheDocument();
    });
  });

  it("P4 创建成功后刷新、清空表单、展开新卡并聚焦密钥输入", async () => {
    const created = makeProvider({
      id: "custom-1",
      name: "Custom",
      built_in: false,
      preset_key: null,
      connections: [makeConnection({
        id: "custom-conn",
        provider_id: "custom-1",
        configured: false,
        health_state: "UNCONFIGURED",
        message: "等待录入 API Key",
        key_count: 0,
        keys: [],
      })],
    });
    createProvider.mockImplementation(async () => {
      providersApi.mockResolvedValue([makeProvider(), created]);
      return created;
    });
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.click(screen.getByLabelText("文本优先使用 Responses API"));
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(createProvider).toHaveBeenCalledWith({
        name: "Custom",
        protocol: "OPENAI",
        base_url: "https://api.example.com/v1",
        use_responses_api: true,
      });
      expect(screen.queryByPlaceholderText("供应商名称")).not.toBeInTheDocument();
      expect(document.getElementById("connection-custom-conn-api-key")).toHaveFocus();
    });
  });

  it("P5 停用内置供应商会带 version 发送 PATCH 并进入已停用", async () => {
    updateProvider.mockImplementation(async (_id, payload) => {
      const next = makeProvider({ enabled: payload.enabled ?? false, version: 2 });
      providersApi.mockResolvedValue([next]);
      return next;
    });
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "停用" }));
    fireEvent.click(screen.getByRole("button", { name: "确认停用" }));
    await waitFor(() => {
      expect(updateProvider).toHaveBeenCalledWith("provider-1", { version: 1, enabled: false });
      expect(screen.getByRole("button", { name: /已停用/ })).toBeInTheDocument();
    });
  });

  it("P6 重命名成功后标题更新", async () => {
    updateProvider.mockImplementation(async (_id, payload) => {
      const next = makeProvider({ name: payload.name ?? "OpenAI 2", version: 2 });
      providersApi.mockResolvedValue([next]);
      return next;
    });
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByLabelText("供应商显示名"), { target: { value: "OpenAI 2" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await waitFor(() => {
      expect(updateProvider).toHaveBeenCalledWith("provider-1", { version: 1, name: "OpenAI 2" });
      expect(screen.getByRole("button", { name: /OpenAI 2/ })).toBeInTheDocument();
    });
  });

  it("P7 重命名 409 时保留本地输入并可放弃草稿", async () => {
    updateProvider.mockRejectedValueOnce(new Error("供应商设置已更新，请刷新后重试"));
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "重命名" }));
    fireEvent.change(screen.getByLabelText("供应商显示名"), { target: { value: "草稿名" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await waitFor(() => {
      expect(screen.getByText("供应商已在别处更新，请重新加载")).toBeInTheDocument();
      expect(screen.getByLabelText("供应商显示名")).toHaveValue("草稿名");
    });
    fireEvent.click(screen.getByRole("button", { name: "放弃草稿并重新加载" }));
    expect(screen.queryByLabelText("供应商显示名")).not.toBeInTheDocument();
  });

  it("P8 删除自定义供应商成功后卡片消失", async () => {
    const custom = makeProvider({
      id: "custom-1",
      name: "Custom",
      built_in: false,
      connections: [makeConnection({ id: "custom-conn", provider_id: "custom-1" })],
    });
    providersApi.mockResolvedValue([custom]);
    deleteProvider.mockImplementation(async () => {
      providersApi.mockResolvedValue([]);
    });
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => {
      expect(deleteProvider).toHaveBeenCalledWith("custom-1");
      expect(screen.queryByRole("button", { name: /Custom/ })).not.toBeInTheDocument();
    });
  });

  it("P9 内置供应商不显示删除按钮且不调用 DELETE", async () => {
    renderPlatform();
    await screen.findByRole("button", { name: "停用" });
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
    expect(deleteProvider).not.toHaveBeenCalled();
  });

  it("P10 删除 409 任务占用时展示服务端详情且卡片仍在", async () => {
    const custom = makeProvider({
      id: "custom-1",
      name: "Custom",
      built_in: false,
      connections: [makeConnection({ id: "custom-conn", provider_id: "custom-1" })],
    });
    providersApi.mockResolvedValue([custom]);
    deleteProvider.mockRejectedValueOnce(new Error("供应商仍被执行中或可重试的生成任务引用，请先处理相关任务"));
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => {
      expect(screen.getByText("供应商仍被执行中或可重试的生成任务引用，请先处理相关任务")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Custom/ })).toBeInTheDocument();
    });
  });

  it("P11 删除 409 内置误调时展示不能删除文案", async () => {
    const custom = makeProvider({
      id: "custom-1",
      name: "Custom",
      built_in: false,
      connections: [makeConnection({ id: "custom-conn", provider_id: "custom-1" })],
    });
    providersApi.mockResolvedValue([custom]);
    deleteProvider.mockRejectedValueOnce(new Error("内置供应商只能停用，不能删除"));
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => {
      expect(screen.getByText("内置供应商只能停用，不能删除")).toBeInTheDocument();
    });
  });
});

describe("搜索筛选排序与 confidence", () => {
  const models: ModelCapability[] = [
    makeModel({ catalog_id: "m-manual", model_id: "manual-1", confidence: "MANUAL" }),
    makeModel({ catalog_id: "m-declared", model_id: "declared-1", confidence: "DECLARED" }),
    makeModel({ catalog_id: "m-inferred", model_id: "inferred-1", confidence: "INFERRED" }),
    makeModel({ catalog_id: "m-partial", model_id: "partial-1", confidence: "PARTIAL" }),
    makeModel({ catalog_id: "m-verified", model_id: "gpt-4.1-mini", confidence: "VERIFIED" }),
    makeModel({ catalog_id: "m-unknown", model_id: "unknown-1", confidence: "FOO" }),
    makeModel({
      catalog_id: "m-image",
      model_id: "dall-e-3",
      display_name: "DALL-E 3",
      model_type: "IMAGE",
      confidence: "VERIFIED",
      operations: ["image_generate"],
    }),
    makeModel({
      catalog_id: "m-vision",
      model_id: "gpt-4o",
      confidence: "VERIFIED",
      operations: ["structured_text", "multimodal_analysis"],
    }),
  ];

  beforeEach(() => {
    providersApi.mockReset().mockResolvedValue([
      makeProvider(),
      makeProvider({
        id: "provider-2",
        name: "DeepSeek",
        preset_key: "deepseek",
        connections: [makeConnection({
          id: "conn-2",
          provider_id: "provider-2",
          protocol: "ANTHROPIC",
          health_state: "OFFLINE",
          message: "",
          keys: [],
          key_count: 0,
        })],
      }),
    ]);
    modelsApi.mockReset().mockResolvedValue(models);
    createProvider.mockReset();
    updateProvider.mockReset();
    deleteProvider.mockReset();
    testConnection.mockReset();
    updateConnection.mockReset();
  });

  it("F6b mapConfidence 覆盖全表", () => {
    expect(mapConfidence("MANUAL")).toBe("待验证");
    expect(mapConfidence("DECLARED")).toBe("待验证");
    expect(mapConfidence("INFERRED")).toBe("推断/待验证");
    expect(mapConfidence("PARTIAL")).toBe("部分验证");
    expect(mapConfidence("VERIFIED")).toBe("已验证");
    expect(mapConfidence("")).toBe("未知");
    expect(mapConfidence("FOO")).toBe("未知");
  });

  it("F1 名称命中只留匹配供应商并展开分组", async () => {
    renderPlatform();
    expect(await screen.findByRole("button", { name: /DeepSeek/ })).toBeInTheDocument();
    const search = screen.getByLabelText("筛选供应商");
    fireEvent.change(search, { target: { value: "OpenAI" } });
    expect(screen.getByRole("button", { name: /OpenAI/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /DeepSeek/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已配置/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("F2 模型 ID 命中时含该模型的供应商可见", async () => {
    renderPlatform();
    fireEvent.change(await screen.findByLabelText("筛选供应商"), { target: { value: "gpt-4.1-mini" } });
    expect(screen.getByRole("button", { name: /OpenAI/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /DeepSeek/ })).not.toBeInTheDocument();
  });

  it("F3 无命中时展示筛选空而不是加载失败", async () => {
    renderPlatform();
    fireEvent.change(await screen.findByLabelText("筛选供应商"), { target: { value: "不存在的供应商" } });
    expect(screen.getByText("没有符合当前搜索或筛选的供应商")).toBeInTheDocument();
    expect(screen.queryByText("供应商列表读取失败")).not.toBeInTheDocument();
  });

  it("F4 类型=图片时隐藏文字行", async () => {
    renderPlatform();
    expect(await screen.findByText("DALL-E 3")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "图片" }));
    expect(screen.getByText("DALL-E 3")).toBeInTheDocument();
    expect(screen.queryByText(/gpt-4\.1-mini/)).not.toBeInTheDocument();
  });

  it("F5 能力=视觉时仅 multimodal_analysis", async () => {
    renderPlatform();
    expect((await screen.findAllByText(/gpt-4o/)).length).toBeGreaterThan(0);
    fireEvent.change(screen.getByLabelText("能力筛选"), { target: { value: "multimodal_analysis" } });
    expect(screen.getAllByText(/gpt-4o/).length).toBeGreaterThan(0);
    expect(screen.queryByText("DALL-E 3")).not.toBeInTheDocument();
    expect(screen.queryByText(/gpt-4\.1-mini/)).not.toBeInTheDocument();
  });

  it("F6 仅已验证只显示 VERIFIED，芯片文案符合映射", async () => {
    renderPlatform();
    await screen.findByText(/推断\/待验证/);
    expect(screen.getAllByText(/待验证/).length).toBeGreaterThanOrEqual(3);
    expect(screen.getByText(/部分验证/)).toBeInTheDocument();
    expect(screen.getByText(/未知/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "仅已验证" }));
    expect(screen.getAllByText(/gpt-4\.1-mini/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/推断\/待验证/)).not.toBeInTheDocument();
    expect(screen.queryByText(/部分验证/)).not.toBeInTheDocument();
    expect(screen.queryByText(/unknown-1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/manual-1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/declared-1/)).not.toBeInTheDocument();
  });

  it("F7 类型、已验证和搜索同时生效", async () => {
    renderPlatform();
    fireEvent.change(await screen.findByLabelText("筛选供应商"), { target: { value: "openai" } });
    fireEvent.click(screen.getByRole("button", { name: "图片" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "仅已验证" }));
    expect(screen.getByText("DALL-E 3")).toBeInTheDocument();
    expect(screen.queryByText(/gpt-4\.1-mini/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /DeepSeek/ })).not.toBeInTheDocument();
  });

  it("F8 排序=健康时 HEALTHY 先于 OFFLINE", () => {
    const healthy = makeProvider({
      id: "a",
      name: "Beta",
      connections: [makeConnection({ health_state: "HEALTHY", latency_ms: 40 })],
    });
    const offline = makeProvider({
      id: "b",
      name: "Alpha",
      connections: [makeConnection({ id: "c2", health_state: "OFFLINE" })],
    });
    expect(sortProviders([offline, healthy], "HEALTH").map((item) => item.name)).toEqual(["Beta", "Alpha"]);
  });

  it("F9 排序=延迟时无延迟排在有延迟之后", () => {
    const measured = makeProvider({
      id: "a",
      name: "Slow",
      connections: [makeConnection({ latency_ms: 800 })],
    });
    const missing = makeProvider({
      id: "b",
      name: "Unknown",
      connections: [makeConnection({ id: "c2", latency_ms: null })],
    });
    expect(sortProviders([missing, measured], "LATENCY").map((item) => item.name)).toEqual(["Slow", "Unknown"]);
  });

  it("纯函数搜索覆盖协议与模型 ID", () => {
    const provider = makeProvider();
    expect(providerMatchesQuery(provider, models, "OPENAI")).toBe(true);
    expect(providerMatchesQuery(provider, models, "gpt-4.1-mini")).toBe(true);
    expect(providerMatchesQuery(provider, models, "nope")).toBe(false);
    expect(filterModels(models, {
      modelType: "IMAGE",
      capability: "ALL",
      verifiedOnly: true,
    }).map((item) => item.model_id)).toEqual(["dall-e-3"]);
  });
});

describe("键盘焦点与错误关联", () => {
  beforeEach(() => {
    providersApi.mockReset().mockResolvedValue([makeProvider({
      connections: [makeConnection({
        health_state: "HEALTHY",
        message: "可用",
      })],
    })]);
    modelsApi.mockReset().mockResolvedValue([
      makeModel({
        catalog_id: "img-1",
        model_id: "dall-e-3",
        display_name: "DALL-E 3",
        model_type: "IMAGE",
        operations: ["image_generate"],
      }),
    ]);
    createProvider.mockReset();
    updateProvider.mockReset();
    deleteProvider.mockReset();
    testConnection.mockReset();
    updateConnection.mockReset();
  });

  it("A1 Tab 到分组头后点击可切换 aria-expanded", async () => {
    renderPlatform();
    const group = await screen.findByRole("button", { name: /已配置/ });
    group.focus();
    expect(group).toHaveFocus();
    expect(group).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(group);
    expect(group).toHaveAttribute("aria-expanded", "false");
  });

  it("A2 打开添加区后焦点到名称", async () => {
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    expect(screen.getByPlaceholderText("供应商名称")).toHaveFocus();
  });

  it("A3 创建失败后焦点到无效字段", async () => {
    createProvider.mockRejectedValueOnce(new Error("供应商名称已存在"));
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "添加供应商" }));
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("供应商名称")).toHaveFocus();
      expect(screen.getByPlaceholderText("供应商名称")).toHaveAttribute("aria-describedby", "provider-create-error");
    });
  });

  it("A4 打开高级区后焦点到 Base URL", async () => {
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "连接与端点" }));
    expect(screen.getByLabelText("Base URL")).toHaveFocus();
  });

  it("A5 图片确认 Esc 后焦点回到测试图片", async () => {
    renderPlatform();
    const trigger = await screen.findByRole("button", { name: "测试图片" });
    fireEvent.click(trigger);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it("A6 删除密钥保留可访问名称并弹出确认", async () => {
    renderPlatform();
    const remove = await screen.findByRole("button", { name: "删除 default" });
    fireEvent.click(remove);
    expect(screen.getByRole("dialog")).toHaveTextContent("已保存的密文将无法恢复");
  });

  it("A7 搜索输入只过滤不抢焦点", async () => {
    renderPlatform();
    const search = await screen.findByLabelText("筛选供应商");
    search.focus();
    fireEvent.change(search, { target: { value: "OpenAI" } });
    expect(search).toHaveFocus();
    expect(screen.getByRole("button", { name: /已配置/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("A8 搜索框 Enter 且有匹配时焦点到第一张匹配卡标题", async () => {
    renderPlatform();
    const search = await screen.findByLabelText("筛选供应商");
    search.focus();
    fireEvent.change(search, { target: { value: "OpenAI" } });
    fireEvent.keyDown(search, { key: "Enter" });
    expect(document.getElementById("provider-card-toggle-provider-1")).toHaveFocus();
  });

  it("A9 无匹配时跳到结果禁用且不移焦", async () => {
    renderPlatform();
    const search = await screen.findByLabelText("筛选供应商");
    search.focus();
    fireEvent.change(search, { target: { value: "没有这家" } });
    const jump = screen.getByRole("button", { name: "跳到结果" });
    expect(jump).toBeDisabled();
    fireEvent.click(jump);
    expect(search).toHaveFocus();
  });
});

describe("加载空错误与 JSON 校验", () => {
  beforeEach(() => {
    providersApi.mockReset();
    modelsApi.mockReset();
    createProvider.mockReset();
    updateProvider.mockReset();
    deleteProvider.mockReset();
    testConnection.mockReset();
    updateConnection.mockReset();
  });

  it("L1 providers pending 时平台加载且不渲染空分组", () => {
    providersApi.mockReturnValue(new Promise(() => {}));
    modelsApi.mockReturnValue(new Promise(() => {}));
    renderPlatform();
    expect(screen.getByText("正在读取供应商与模型目录…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /已配置|未配置|已停用/ })).not.toBeInTheDocument();
  });

  it("L2 providers reject 时平台错误可重试", async () => {
    providersApi.mockRejectedValue(new Error("网络中断"));
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    expect(await screen.findByRole("alert")).toHaveTextContent("供应商列表读取失败");
    expect(screen.queryByText("没有符合当前搜索或筛选的供应商")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(providersApi.mock.calls.length).toBeGreaterThan(1);
  });

  it("L3 models reject 时连接头可见且模型区报错", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockRejectedValue(new Error("模型目录失败"));
    renderPlatform();
    expect(await screen.findByText("密钥无效")).toBeInTheDocument();
    expect(screen.getByText("模型目录读取失败")).toBeInTheDocument();
    expect(screen.queryByText("还没有模型。同步目录，或手工添加上游 ID。")).not.toBeInTheDocument();
  });

  it("L4 模型空数组时展示真空间态", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    expect(await screen.findByText("还没有模型。同步目录，或手工添加上游 ID。")).toBeInTheDocument();
  });

  it("L5 主密钥不可写时保存禁用并说明", async () => {
    providersApi.mockResolvedValue([makeProvider({
      connections: [makeConnection({ credential_writable: false })],
    })]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    expect(await screen.findByPlaceholderText("服务端未配置凭据主密钥")).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存密钥" })).toBeDisabled();
    expect(screen.getAllByText("服务端未配置凭据主密钥").length).toBeGreaterThan(0);
  });

  it("C9 JSON 语法非法时保存禁用且中文错误不含 SyntaxError", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "连接与端点" }));
    fireEvent.change(screen.getByLabelText("端点模板"), { target: { value: "{" } });
    expect(screen.getByText("端点模板不是合法 JSON")).toBeInTheDocument();
    expect(screen.getByLabelText("端点模板")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "保存连接" })).toBeDisabled();
    expect(document.body.textContent).not.toContain("SyntaxError");
  });

  it("C10 JSON 顶层为数组、null 或数字时提示必须是对象", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "连接与端点" }));
    const field = screen.getByLabelText("端点模板");
    fireEvent.change(field, { target: { value: "[]" } });
    expect(screen.getByText("必须是 JSON 对象，不能是数组或单值")).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "null" } });
    expect(screen.getByText("必须是 JSON 对象，不能是数组或单值")).toBeInTheDocument();
    fireEvent.change(field, { target: { value: "1" } });
    expect(screen.getByText("必须是 JSON 对象，不能是数组或单值")).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("C11 嵌套对象或非字符串值时提示键值必须是字符串", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "连接与端点" }));
    fireEvent.change(screen.getByLabelText("端点模板"), { target: { value: '{"models":{"path":"/models"}}' } });
    expect(screen.getByText("每个键和值都必须是字符串")).toBeInTheDocument();
  });

  it("C12 extra_headers 禁头大小写变体只在该字段报错", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "连接与端点" }));
    fireEvent.change(screen.getByLabelText("额外请求头"), { target: { value: '{"X-Api-Key":"n"}' } });
    expect(screen.getByText("不能设置 Authorization、x-api-key、Host 或 Content-Length")).toBeInTheDocument();
    expect(screen.getByLabelText("额外请求头")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("端点模板")).not.toHaveAttribute("aria-invalid", "true");
    fireEvent.change(screen.getByLabelText("额外请求头"), { target: { value: '{"Authorization":"n"}' } });
    expect(screen.getByText("不能设置 Authorization、x-api-key、Host 或 Content-Length")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("额外请求头"), { target: { value: '{"Host":"n"}' } });
    expect(screen.getByText("不能设置 Authorization、x-api-key、Host 或 Content-Length")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("额外请求头"), { target: { value: '{"Content-Length":"1"}' } });
    expect(screen.getByText("不能设置 Authorization、x-api-key、Host 或 Content-Length")).toBeInTheDocument();
  });

  it("C13 合法对象与空对象时保存启用且无错误", async () => {
    providersApi.mockResolvedValue([makeProvider()]);
    modelsApi.mockResolvedValue([]);
    renderPlatform();
    fireEvent.click(await screen.findByRole("button", { name: "连接与端点" }));
    fireEvent.change(screen.getByLabelText("端点模板"), { target: { value: '{"models":"/models"}' } });
    fireEvent.change(screen.getByLabelText("额外请求头"), { target: { value: "{}" } });
    expect(screen.queryByText(/不是合法 JSON|必须是 JSON 对象|都必须是字符串|不能设置 Authorization/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存连接" })).toBeEnabled();
  });

  it("JSON 纯函数覆盖语法、形状、字符串值和禁头", () => {
    expect(validateJsonRecord("{", "endpoint_templates").ok).toBe(false);
    expect(validateJsonRecord("[]", "endpoint_templates")).toEqual({
      ok: false,
      message: "必须是 JSON 对象，不能是数组或单值",
    });
    expect(validateJsonRecord('{"a":1}', "endpoint_templates")).toEqual({
      ok: false,
      message: "每个键和值都必须是字符串",
    });
    expect(validateJsonRecord('{"Authorization":"x"}', "extra_headers").ok).toBe(false);
    expect(validateJsonRecord('{"models":"/models"}', "endpoint_templates")).toEqual({
      ok: true,
      value: { models: "/models" },
    });
    expect(validateJsonRecord("{}", "extra_headers")).toEqual({ ok: true, value: {} });
  });
});

describe("窄桌面布局", () => {
  it("N1 1100–1279px 工具栏搜索独占一行", () => {
    expect(stylesheet).toContain("@media (max-width: 1279px)");
    const start = stylesheet.indexOf("@media (max-width: 1279px)");
    const body = stylesheet.slice(start, start + 280);
    expect(body).toContain(".provider-toolbar { grid-template-columns: 1fr;");
    expect(body).toContain(".provider-toolbar-search { width: 100%; min-width: 0; }");
  });

  it("N2 900px 创建和密钥表单单列且按钮不小于 32px", () => {
    const start = stylesheet.indexOf("@media (max-width: 900px)");
    const body = stylesheet.slice(start, start + 700);
    expect(body).toContain(".provider-create-form");
    expect(body).toContain(".provider-key-form");
    expect(body).toContain("grid-template-columns: 1fr");
    expect(body).toContain("min-height: 36px");
  });

  it("N3 卡片计数在窄屏仍可见", () => {
    expect(stylesheet).toContain(".provider-card-counts { display: flex;");
    const start = stylesheet.indexOf("@media (max-width: 760px)");
    const body = stylesheet.slice(start, stylesheet.indexOf("@media (max-width: 1439px)"));
    expect(body).not.toContain(".provider-card-counts { display: none; }");
  });

  it("N4 长 URL 与长模型 ID 使用省略", () => {
    expect(stylesheet).toContain(".provider-connection > header span { overflow: hidden;");
    expect(stylesheet).toContain("text-overflow: ellipsis");
    expect(stylesheet).toContain(".provider-models article span, .provider-models article small { overflow: hidden;");
  });
});
