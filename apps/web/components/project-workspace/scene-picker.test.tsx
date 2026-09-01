import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type SceneAsset, type ScriptScene } from "@/lib/api";

import { ScenePicker } from "./scene-picker";

vi.mock("next/image", () => ({
  default: ({ alt }: { alt: string }) => <span role="img" aria-label={alt} />,
}));

const bindApi = vi.spyOn(api, "bindSceneAsset");
const createApi = vi.spyOn(api, "createSceneAsset");

function sceneFixture(overrides: Partial<ScriptScene> = {}): ScriptScene {
  return {
    id: "scene-1",
    ordinal: 1,
    location: "学校天台",
    scene_asset_id: null,
    scene_asset_variant_id: null,
    time_label: "",
    weather: "",
    purpose: "",
    emotional_arc: "",
    source_range: {},
    outfit_assignments: {},
    locked_fields: [],
    version: 1,
    beats: [],
    ...overrides,
  };
}

function assetFixture(overrides: Partial<SceneAsset> = {}): SceneAsset {
  return {
    id: "asset-1",
    project_id: "project-1",
    name: "学校天台",
    description: "",
    location_hint: "学校天台",
    structured: { interior: false, place: "校园" },
    status: "CANONICAL",
    deleted_at: null,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    version: 1,
    references: [],
    variants: [{
      id: "variant-1",
      scene_asset_id: "asset-1",
      name: "暴雨黄昏",
      structured_overrides: { weather: "rain" },
      is_canonical: true,
      deleted_at: null,
      version: 1,
      references: [],
    }],
    ...overrides,
  };
}

function renderPicker(scene: ScriptScene, assets: SceneAsset[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return render(<ScenePicker projectId="project-1" scene={scene} sceneAssets={assets} />, { wrapper });
}

describe("ScenePicker", () => {
  beforeEach(() => {
    bindApi.mockReset();
    createApi.mockReset();
  });

  it("TEST-SCENE-05 绑定和解绑都走 bind-asset，且不要求改地点文本", async () => {
    bindApi.mockResolvedValue(sceneFixture({ scene_asset_id: "asset-1" }));
    renderPicker(sceneFixture(), [assetFixture()]);
    fireEvent.change(screen.getByLabelText("选择场景资产"), { target: { value: "asset-1" } });
    await waitFor(() => {
      expect(bindApi).toHaveBeenCalledWith("scene-1", {
        scene_asset_id: "asset-1",
        scene_asset_variant_id: null,
      });
    });
    bindApi.mockResolvedValue(sceneFixture());
    fireEvent.change(screen.getByLabelText("选择场景资产"), { target: { value: "" } });
    await waitFor(() => {
      expect(bindApi).toHaveBeenLastCalledWith("scene-1", {
        scene_asset_id: null,
        scene_asset_variant_id: null,
      });
    });
    expect(screen.getByText(/地点文本会保留作历史兜底/)).toBeInTheDocument();
  });

  it("已归档资产不能出现在新绑定选项里，422 展示明确错误", async () => {
    renderPicker(
      sceneFixture({ scene_asset_id: "archived-1" }),
      [assetFixture({ id: "archived-1", name: "旧天台", deleted_at: "2026-09-01T00:00:00Z" }), assetFixture()],
    );
    const select = screen.getByLabelText("选择场景资产") as HTMLSelectElement;
    expect([...select.options].map((item) => item.value)).toEqual(["", "archived-1", "asset-1"]);
    expect(screen.getByText(/当前绑定已归档/)).toBeInTheDocument();
    bindApi.mockRejectedValue(new ApiError("场景资产已归档，请先恢复", 422));
    fireEvent.change(select, { target: { value: "archived-1" } });
    await waitFor(() => {
      expect(screen.getByText("场景资产已归档，请先恢复")).toBeInTheDocument();
    });
  });
});
