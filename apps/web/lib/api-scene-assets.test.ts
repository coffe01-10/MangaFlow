import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  isConflictError,
  sceneAssetQueryString,
} from "./api";

describe("scene asset API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes list filters without inventing extra query keys", () => {
    expect(sceneAssetQueryString({
      status: "CANONICAL",
      include_deleted: true,
      place: "校园",
      interior: false,
      limit: 200,
    })).toBe("?status=CANONICAL&include_deleted=true&place=%E6%A0%A1%E5%9B%AD&interior=false&limit=200");
    expect(sceneAssetQueryString({})).toBe("");
  });

  it("turns 409 into a conflict error that callers can refresh from", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: "场景资产已被更新，请刷新后重试" }),
    }));
    await expect(api.updateSceneAsset("p1", "a1", { version: 1, name: "教室" })).rejects.toMatchObject({
      name: "ApiError",
      status: 409,
      message: "场景资产已被更新，请刷新后重试",
    });
  });

  it("turns 422 bind failures into unprocessable errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: "场景资产已归档，请先恢复" }),
    }));
    const error = await api.bindSceneAsset("scene-1", { scene_asset_id: "archived" }).catch((reason) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(isConflictError(error)).toBe(false);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).message).toBe("场景资产已归档，请先恢复");
  });

  it("calls bind-asset with null ids for unbind", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ id: "scene-1", scene_asset_id: null, scene_asset_variant_id: null }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await api.bindSceneAsset("scene-1", { scene_asset_id: null, scene_asset_variant_id: null });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/scenes/scene-1/bind-asset"),
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ scene_asset_id: null, scene_asset_variant_id: null }),
      }),
    );
  });
});
