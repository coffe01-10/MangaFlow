import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ProviderConnection, type ProviderProfile } from "@/lib/api";

import { ProviderCard } from "./provider-card";

const providerModelsApi = vi.spyOn(api, "providerModels");

function makeConnection(overrides: Partial<ProviderConnection> = {}): ProviderConnection {
  return {
    id: "conn-1",
    provider_id: "provider-1",
    name: "default",
    protocol: "OPENAI",
    base_url: "https://api.openai.com/v1",
    enabled: true,
    configured: true,
    credential_source: "CONNECTION_KEY",
    credential_writable: true,
    supports_model_discovery: true,
    supports_balance: false,
    supported_model_types: ["TEXT", "IMAGE"],
    use_responses_api: false,
    endpoint_templates: {},
    extra_headers: {},
    balance_config: {},
    nonsecret_config: {},
    health_state: "HEALTHY",
    last_checked_at: null,
    last_success_at: null,
    latency_ms: null,
    error_code: null,
    message: "ok",
    key_count: 0,
    model_count: 0,
    keys: [],
    version: 1,
    ...overrides,
  };
}

function makeProvider(overrides: Partial<ProviderProfile> = {}): ProviderProfile {
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
    connections: overrides.connections ?? [makeConnection()],
  };
}

function renderCard(provider: ProviderProfile, onDirtyChange: (dirty: boolean) => void) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ProviderCard
        provider={provider}
        forceExpanded={false}
        preferExpanded={false}
        modelType="ALL"
        capability="ALL"
        verifiedOnly={false}
        showHidden={false}
        catalog={[]}
        autoFocusKey={false}
        onKeyFocused={() => undefined}
        onDirtyChange={onDirtyChange}
      />
    </QueryClientProvider>,
  );
}

describe("ProviderCard 脏集合生命周期（#546-5）", () => {
  beforeEach(() => {
    providerModelsApi.mockReset().mockResolvedValue([]);
  });

  it("确认收起清空连接脏集合：onDirtyChange 回落 false，重新展开后收起不再确认", async () => {
    const onDirtyChange = vi.fn();
    renderCard(makeProvider(), onDirtyChange);

    // 半成品密钥输入把连接面板标脏，卡片上抛 true。
    fireEvent.change(await screen.findByLabelText("API Key"), { target: { value: "sk-half-typed" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));

    // 确认丢弃后收起：面板卸载，卡片必须自己清空脏集合——否则脏标记
    // 残留到重新展开，没有任何输入时仍弹确认、beforeunload 一直误报。
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const toggle = document.getElementById("provider-card-toggle-provider-1")!;
    fireEvent.click(toggle);
    await waitFor(() => {
      expect(document.getElementById("provider-card-toggle-provider-1")).toHaveAttribute("aria-expanded", "false");
    });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(false));
    expect(screen.queryByLabelText("API Key")).not.toBeInTheDocument();

    // 重新展开：新面板无任何输入，再次收起不得弹确认。
    fireEvent.click(document.getElementById("provider-card-toggle-provider-1")!);
    await screen.findByLabelText("API Key");
    fireEvent.click(document.getElementById("provider-card-toggle-provider-1")!);
    await waitFor(() => {
      expect(document.getElementById("provider-card-toggle-provider-1")).toHaveAttribute("aria-expanded", "false");
    });
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();
  });

  it("取消收起保留输入：脏集合与半成品输入都不受影响", async () => {
    const onDirtyChange = vi.fn();
    renderCard(makeProvider(), onDirtyChange);

    fireEvent.change(await screen.findByLabelText("API Key"), { target: { value: "sk-half-typed" } });
    await waitFor(() => expect(onDirtyChange).toHaveBeenLastCalledWith(true));

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    fireEvent.click(document.getElementById("provider-card-toggle-provider-1")!);
    expect(document.getElementById("provider-card-toggle-provider-1")).toHaveAttribute("aria-expanded", "true");
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByLabelText("API Key")).toHaveValue("sk-half-typed");
    confirmSpy.mockRestore();
  });
});
