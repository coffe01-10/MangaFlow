import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ProviderProfile } from "@/lib/api";

import { ProviderManagement } from "./provider-management";

const providersApi = vi.spyOn(api, "providers");
const modelsApi = vi.spyOn(api, "models");
const createProvider = vi.spyOn(api, "createProvider");
const testConnection = vi.spyOn(api, "testProviderConnection");

function providerFixture(): ProviderProfile {
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
    connections: [{
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
    }],
  };
}

describe("ProviderManagement 错误展示", () => {
  beforeEach(() => {
    providersApi.mockReset().mockResolvedValue([providerFixture()]);
    modelsApi.mockReset().mockResolvedValue([]);
    createProvider.mockReset();
    testConnection.mockReset();
  });

  it("连接健康错误对用户可见，且不回显密钥明文", async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <ProviderManagement />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("密钥无效")).toBeInTheDocument();
    });
    expect(screen.getByText(/sk-\*\*\*\*1234/)).toBeInTheDocument();
    expect(screen.queryByText(/sk-live-secret/)).not.toBeInTheDocument();
  });

  it("创建供应商失败时展示 form-error，成功后刷新 providers", async () => {
    createProvider.mockRejectedValueOnce(new Error("供应商名称已存在"));
    render(
      <QueryClientProvider client={new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })}>
        <ProviderManagement />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByText("添加自定义供应商")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("添加自定义供应商"));
    fireEvent.change(screen.getByPlaceholderText("供应商名称"), { target: { value: "Custom" } });
    fireEvent.change(screen.getByPlaceholderText("https://api.example.com/v1"), {
      target: { value: "https://api.example.com/v1" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(createProvider).toHaveBeenCalled();
      expect(screen.getByText("供应商名称已存在")).toBeInTheDocument();
    });

    createProvider.mockResolvedValueOnce(providerFixture());
    const before = providersApi.mock.calls.length;
    fireEvent.click(screen.getByRole("button", { name: "创建" }));
    await waitFor(() => {
      expect(providersApi.mock.calls.length).toBeGreaterThan(before);
    });
  });

  it("凭据测试失败展示用户可见错误，不出现输入密钥", async () => {
    testConnection.mockRejectedValue(new Error("上游返回 401"));
    render(
      <QueryClientProvider client={new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })}>
        <ProviderManagement />
      </QueryClientProvider>,
    );
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
    expect(screen.getByText("上游返回 401").textContent).not.toContain("sk-live-secret");
  });
});
