"use client";

import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex=\"-1\"])";

export function SceneModal({
  title,
  children,
  onClose,
  triggerRef,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  triggerRef?: RefObject<HTMLElement | null>;
  wide?: boolean;
}) {
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const triggerNode = triggerRef?.current ?? null;
    const focusable = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    focusable()[0]?.focus();

    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const nodes = focusable();
      if (!nodes.length) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      (triggerNode ?? previouslyFocused)?.focus?.();
    };
    // Mount-only: parent re-renders pass a new onClose every keystroke.
  }, [triggerRef]);

  return (
    <div className="provider-dialog-backdrop" onClick={() => onCloseRef.current()}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={wide ? "provider-dialog scene-dialog-wide" : "provider-dialog"}
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}

export function SceneConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel = "取消",
  pending = false,
  onConfirm,
  onCancel,
  triggerRef,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel?: string;
  pending?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  triggerRef?: RefObject<HTMLElement | null>;
}) {
  const messageId = useId();
  return (
    <SceneModal title={title} onClose={onCancel} triggerRef={triggerRef}>
      <p id={messageId}>{message}</p>
      <div className="provider-dialog-actions">
        <button type="button" onClick={onCancel}>{cancelLabel}</button>
        <button type="button" className="provider-danger-action" disabled={pending} onClick={onConfirm}>
          {confirmLabel}
        </button>
      </div>
    </SceneModal>
  );
}
