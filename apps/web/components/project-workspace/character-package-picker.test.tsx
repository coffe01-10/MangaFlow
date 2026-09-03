import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type CharacterModelPackage, type CharacterPackageSummary } from "@/lib/api";

import { CharacterPackagePicker } from "./character-package-picker";

const listApi = vi.spyOn(api, "characterPackages");
const detailApi = vi.spyOn(api, "characterPackage");

function summaryFixture(overrides: Partial<CharacterPackageSummary> = {}): CharacterPackageSummary {
  return {
    id: "pkg-1",
    character_id: "character-1",
    project_id: "project-1",
    status: "ACTIVE",
    published_version_id: "version-1",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 1,
    character: { id: "character-1", primary_name: "林澈", aliases: [], alias_conflict: false },
    published_version_number: 1,
    published_completeness: { score: 85, missing: [] },
    ...overrides,
  };
}

function detailFixture(overrides: Partial<CharacterModelPackage> = {}): CharacterModelPackage {
  return {
    id: "pkg-1",
    character_id: "character-1",
    project_id: "project-1",
    identity_spec: {},
    visual_spec: {},
    negative_constraints: [],
    published_version_id: "version-2",
    status: "ACTIVE",
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 1,
    versions: [
      {
        id: "version-2",
        package_id: "pkg-1",
        version_number: 2,
        status: "READY",
        spec_snapshot: {},
        derived_from_version_id: "version-1",
        published_at: "2026-09-01T02:00:00Z",
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
        version: 1,
        references: [],
        outfits: [],
        completeness: { score: 90, missing: [] },
      },
      {
        id: "version-1",
        package_id: "pkg-1",
        version_number: 1,
        status: "DRAFT",
        spec_snapshot: {},
        derived_from_version_id: null,
        published_at: null,
        created_at: "2026-09-01T00:00:00Z",
        updated_at: "2026-09-01T00:00:00Z",
        version: 1,
        references: [],
        outfits: [],
        completeness: { score: 20, missing: [] },
      },
    ],
    completeness: null,
    ...overrides,
  };
}

function renderPicker({ value = null, onChange = () => undefined } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(
    <CharacterPackagePicker
      projectId="project-1"
      characterId="character-1"
      characterName="林澈"
      value={value}
      onChange={onChange}
    />,
    { wrapper },
  );
}

describe("CharacterPackagePicker", () => {
  beforeEach(() => {
    listApi.mockReset().mockResolvedValue([]);
    detailApi.mockReset();
  });

  it("TEST-PKG-06 没有模型包的角色保持 legacy 路径提示", async () => {
    renderPicker();
    expect(await screen.findByText("未启用角色模型包（沿用人物参考图路径）")).toBeInTheDocument();
    expect(detailApi).not.toHaveBeenCalled();
  });

  it("TEST-PKG-06 有发布版本时列出默认继承与可选版本，显式选择发送 package_version_id", async () => {
    listApi.mockResolvedValue([summaryFixture()]);
    detailApi.mockResolvedValue(detailFixture());
    const onChange = vi.fn();
    renderPicker({ onChange });
    const select = await screen.findByLabelText("林澈的角色模型包版本");
    expect(select).toBeInTheDocument();
    const options = Array.from(select.querySelectorAll("option")).map((option) => option.value);
    expect(options[0]).toBe("");
    // 草稿版本不进入显式选择列表（草稿不能用于生成，契约 §8.1）。
    expect(options).not.toContain("version-1");
    expect(options).toContain("version-2");

    fireEvent.change(select, { target: { value: "version-2" } });
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith("version-2");
    });
    fireEvent.change(select, { target: { value: "" } });
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(null);
    });
  });

  it("TEST-PKG-06 归档包仍显示版本选择器，可显式选择已归档版本（契约 §8.1）", async () => {
    listApi.mockResolvedValue([summaryFixture({
      status: "ARCHIVED",
      published_version_id: null,
      published_version_number: null,
      published_completeness: null,
    })]);
    detailApi.mockResolvedValue(detailFixture({
      status: "ARCHIVED",
      published_version_id: null,
      versions: [{
        ...detailFixture().versions[0],
        id: "version-1",
        version_number: 1,
        status: "ARCHIVED",
        derived_from_version_id: null,
        published_at: "2026-09-01T01:00:00Z",
      }],
    }));
    const onChange = vi.fn();
    renderPicker({ onChange });
    const select = await screen.findByLabelText("林澈的角色模型包版本");
    // 归档版本仍进入显式选择列表；默认项说明退回 legacy 路径而非默认继承。
    expect(within(select).getByRole("option", { name: /V1 · 已归档/ })).toBeInTheDocument();
    expect(within(select).getByRole("option", { name: "不指定版本（沿用人物参考图路径）" })).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "version-1" } });
    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith("version-1");
    });
  });
});
