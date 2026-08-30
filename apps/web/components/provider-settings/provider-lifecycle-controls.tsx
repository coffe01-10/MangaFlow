"use client";

import { api, type ProviderProfile } from "@/lib/api";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "./confirm-dialog";
import { mapOptimisticConflict } from "./provider-copy";

export function ProviderLifecycleControls({
  provider,
}: {
  provider: ProviderProfile;
}) {
  const queryClient = useQueryClient();
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState(provider.name);
  const [confirm, setConfirm] = useState<"disable" | "delete" | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const renameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (renameOpen) renameRef.current?.focus();
  }, [renameOpen]);

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ["providers"] });
    queryClient.invalidateQueries({ queryKey: ["models"] });
  }

  function closeConfirm() {
    const trigger = triggerRef.current;
    setConfirm(null);
    trigger?.focus();
  }

  const rename = useMutation({
    mutationFn: () => api.updateProvider(provider.id, {
      version: provider.version,
      name: renameValue.trim(),
    }),
    onSuccess: () => {
      setRenameOpen(false);
      refresh();
    },
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.updateProvider(provider.id, {
      version: provider.version,
      enabled,
    }),
    onSuccess: refresh,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteProvider(provider.id),
    onSuccess: refresh,
  });

  const renameError = rename.error
    ? mapOptimisticConflict(rename.error.message, "provider")
    : "";
  const lifecycleError = toggle.error
    ? mapOptimisticConflict(toggle.error.message, "provider")
    : remove.error?.message ?? "";

  return (
    <div className="provider-lifecycle">
      {renameOpen ? (
        <div className="provider-rename">
          <input
            ref={renameRef}
            aria-label="供应商显示名"
            aria-invalid={Boolean(renameError)}
            aria-describedby={renameError ? `provider-${provider.id}-edit-error` : undefined}
            value={renameValue}
            onChange={(event) => setRenameValue(event.target.value)}
          />
          <button
            type="button"
            disabled={!renameValue.trim() || rename.isPending}
            onClick={() => rename.mutate()}
          >
            保存名称
          </button>
          <button
            type="button"
            onClick={() => {
              setRenameValue(provider.name);
              setRenameOpen(false);
              rename.reset();
            }}
          >
            取消
          </button>
          {renameError && (
            <button
              type="button"
              onClick={() => {
                setRenameValue(provider.name);
                setRenameOpen(false);
                rename.reset();
                refresh();
              }}
            >
              放弃草稿并重新加载
            </button>
          )}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => {
            setRenameValue(provider.name);
            setRenameOpen(true);
          }}
        >
          重命名
        </button>
      )}
      <button
        type="button"
        className="provider-enable-toggle"
        disabled={toggle.isPending}
        onClick={(event) => {
          if (provider.enabled) {
            triggerRef.current = event.currentTarget;
            setConfirm("disable");
            return;
          }
          toggle.mutate(true);
        }}
      >
        {provider.enabled ? "停用" : "启用"}
      </button>
      {!provider.built_in && (
        <button
          type="button"
          className="provider-danger-action"
          disabled={remove.isPending}
          onClick={(event) => {
            triggerRef.current = event.currentTarget;
            setConfirm("delete");
          }}
        >
          删除
        </button>
      )}
      {(renameError || lifecycleError) && (
        <p id={`provider-${provider.id}-edit-error`} className="form-error" role="alert">
          <CircleAlert size={14} />{renameError || lifecycleError}
        </p>
      )}
      {confirm === "disable" && (
        <ConfirmDialog
          title="停用供应商"
          message="停用后此供应商不可用于生成"
          confirmLabel="确认停用"
          onCancel={closeConfirm}
          onConfirm={() => {
            closeConfirm();
            toggle.mutate(false);
          }}
        />
      )}
      {confirm === "delete" && (
        <ConfirmDialog
          title="删除自定义供应商"
          message="删除后连接与模型目录一并移除。进行中的任务会阻止删除。"
          confirmLabel="确认删除"
          danger
          onCancel={closeConfirm}
          onConfirm={() => {
            closeConfirm();
            remove.mutate();
          }}
        />
      )}
    </div>
  );
}
