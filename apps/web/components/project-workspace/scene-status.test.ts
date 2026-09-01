import { describe, expect, it } from "vitest";

import { countPersistedSceneBindings, pickStructured, pickVariantOverrides } from "./scene-structured";
import { sceneAssetStatusMeta } from "./scene-status";

describe("scene asset status copy", () => {
  it("does not treat unknown or null status as ready", () => {
    expect(sceneAssetStatusMeta(null).ready).toBe(false);
    expect(sceneAssetStatusMeta(undefined).ready).toBe(false);
    expect(sceneAssetStatusMeta("IN_USE").ready).toBe(false);
    expect(sceneAssetStatusMeta("IN_USE").label).toBe("状态未知");
    expect(sceneAssetStatusMeta("UPLOADED").ready).toBe(false);
    expect(sceneAssetStatusMeta("CANONICAL").ready).toBe(true);
  });
});

describe("scene structured whitelist", () => {
  it("drops unknown top-level keys", () => {
    const picked = pickStructured({
      place: "校园",
      alias: "不该存在",
      interior: true,
    } as never);
    expect(picked.place).toBe("校园");
    expect(picked.interior).toBe(true);
    expect(picked).not.toHaveProperty("alias");
  });

  it("keeps only allowed variant override keys", () => {
    const picked = pickVariantOverrides({
      time_of_day: "dusk",
      weather: "rain",
      place: "校园",
      interior: true,
    });
    expect(picked).toEqual({ time_of_day: "dusk", weather: "rain" });
  });
});

describe("scene binding count", () => {
  it("returns null instead of inventing a number when a chapter script fails", async () => {
    const count = await countPersistedSceneBindings(
      "project-1",
      "asset-1",
      async () => [{ id: "chapter-1" }, { id: "chapter-2" }],
      async (chapterId) => {
        if (chapterId === "chapter-2") throw new Error("网络失败");
        return { scenes: [{ scene_asset_id: "asset-1" }] };
      },
      () => false,
    );
    expect(count).toBeNull();
  });

  it("counts persisted bindings across chapters", async () => {
    const count = await countPersistedSceneBindings(
      "project-1",
      "asset-1",
      async () => [{ id: "chapter-1" }, { id: "chapter-2" }],
      async (chapterId) => ({
        scenes: chapterId === "chapter-1"
          ? [{ scene_asset_id: "asset-1" }, { scene_asset_id: "other" }]
          : [{ scene_asset_id: "asset-1" }],
      }),
      () => false,
    );
    expect(count).toBe(2);
  });
});
