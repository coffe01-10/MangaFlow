"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Landmark, LoaderCircle } from "lucide-react";
import { useMemo, useState } from "react";

import {
  api,
  isUnprocessableError,
  type SceneAsset,
  type ScriptScene,
} from "@/lib/api";

import { interiorLabel } from "./scene-status";

export function ScenePicker({
  projectId,
  scene,
  sceneAssets,
  disabled = false,
}: {
  projectId: string;
  scene: ScriptScene;
  sceneAssets: SceneAsset[];
  disabled?: boolean;
}) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const bind = useMutation({
    mutationFn: (payload: { scene_asset_id: string | null; scene_asset_variant_id: string | null }) =>
      api.bindSceneAsset(scene.id, payload),
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["script"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["scene-assets", projectId] });
    },
    onError: (reason) => {
      setError(isUnprocessableError(reason)
        ? reason.message
        : reason instanceof Error ? reason.message : "绑定场景资产失败");
    },
  });
  const createFromLocation = useMutation({
    mutationFn: async () => {
      const location = scene.location.trim();
      if (!location) throw new Error("地点文本为空，无法创建场景资产");
      const created = await api.createSceneAsset(projectId, {
        name: location.slice(0, 120),
        description: location,
        location_hint: location.slice(0, 200),
      });
      await api.bindSceneAsset(scene.id, {
        scene_asset_id: created.id,
        scene_asset_variant_id: null,
      });
      return created;
    },
    onSuccess: () => {
      setError("");
      queryClient.invalidateQueries({ queryKey: ["script"] });
      queryClient.invalidateQueries({ queryKey: ["pages"] });
      queryClient.invalidateQueries({ queryKey: ["scene-assets", projectId] });
    },
    onError: (reason) => {
      setError(isUnprocessableError(reason)
        ? reason.message
        : reason instanceof Error ? reason.message : "从地点创建场景资产失败");
    },
  });

  const activeAssets = useMemo(
    () => sceneAssets.filter((item) => item.deleted_at == null),
    [sceneAssets],
  );
  const boundAsset = sceneAssets.find((item) => item.id === scene.scene_asset_id) ?? null;
  const boundMissing = Boolean(scene.scene_asset_id) && !boundAsset;
  const boundArchived = Boolean(boundAsset?.deleted_at);
  const variantOptions = (boundAsset?.variants ?? []).filter((item) => item.deleted_at == null);
  const boundVariant = boundAsset?.variants.find((item) => item.id === scene.scene_asset_variant_id) ?? null;

  return (
    <div className="scene-picker" aria-label="场景资产绑定">
      <strong><Landmark size={13} />本场场景资产</strong>
      <p className="scene-picker-hint">地点文本会保留作历史兜底，绑定资产时不会被清空。</p>
      <label>
        <span>所属场景资产</span>
        <select
          aria-label="选择场景资产"
          aria-expanded="false"
          value={scene.scene_asset_id ?? ""}
          disabled={disabled || bind.isPending}
          onChange={(event) => {
            const value = event.target.value;
            bind.mutate({
              scene_asset_id: value || null,
              scene_asset_variant_id: null,
            });
          }}
        >
          <option value="">不绑定场景资产</option>
          {boundArchived && boundAsset ? (
            <option value={boundAsset.id}>{boundAsset.name}（已归档，不可用于新绑定）</option>
          ) : null}
          {activeAssets.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.name} · {interiorLabel(asset.structured.interior)}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>环境变体</span>
        <select
          aria-label="选择环境变体"
          aria-expanded="false"
          value={scene.scene_asset_variant_id ?? ""}
          disabled={disabled || bind.isPending || !scene.scene_asset_id || boundArchived}
          onChange={(event) => {
            bind.mutate({
              scene_asset_id: scene.scene_asset_id,
              scene_asset_variant_id: event.target.value || null,
            });
          }}
        >
          <option value="">使用资产默认变体</option>
          {boundVariant?.deleted_at ? (
            <option value={boundVariant.id}>{boundVariant.name}（已归档）</option>
          ) : null}
          {variantOptions.map((variant) => (
            <option key={variant.id} value={variant.id}>
              {variant.name}{variant.is_canonical ? "（默认）" : ""}
            </option>
          ))}
        </select>
      </label>
      {boundMissing ? <p className="scene-picker-note">绑定的场景资产当前不可用，请改绑或到资产工作区确认。</p> : null}
      {boundArchived ? <p className="scene-picker-note">当前绑定已归档，不能作为新的生成参考；可解绑或先恢复资产。</p> : null}
      {!scene.scene_asset_id && scene.location.trim() ? (
        <button
          type="button"
          className="button outline compact"
          disabled={createFromLocation.isPending || disabled}
          onClick={() => createFromLocation.mutate()}
        >
          {createFromLocation.isPending ? <LoaderCircle className="spin" size={13} /> : null}
          从地点创建场景资产
        </button>
      ) : null}
      {(error || bind.isError || createFromLocation.isError) && (
        <p className="form-error"><CircleAlert size={14} />{error || (bind.error ?? createFromLocation.error)?.message}</p>
      )}
    </div>
  );
}
