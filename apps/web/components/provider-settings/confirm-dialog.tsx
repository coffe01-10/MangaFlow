"use client";

import { useEffect, useId, useRef } from "react";

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  cancelLabel = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const messageId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  // Latest-callback ref: the parent passes a fresh closure per render (e.g.
  // while provider polling re-renders), and re-subscribing the effect would
  // re-run the focus-restore cleanup mid-dialog (focus churn). The ref is
  // written in a dep-less effect (never during render).
  const onCancelRef = useRef(onCancel);
  useEffect(() => { onCancelRef.current = onCancel; });

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onCancelRef.current();
        return;
      }
      // Tab stays inside the modal (same trap the scene modal implements).
      if (event.key === "Tab" && dialogRef.current) {
        const focusables = Array.from(dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled])"));
        if (!focusables.length) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;
        if (event.shiftKey && (active === first || !dialogRef.current.contains(active))) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && (active === last || !dialogRef.current.contains(active))) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previouslyFocused?.focus();
    };
  }, []);

  return (
    <div className="provider-dialog-backdrop" onClick={onCancel}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        className="provider-dialog"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id={titleId}>{title}</h2>
        <p id={messageId}>{message}</p>
        <div className="provider-dialog-actions">
          <button ref={cancelRef} type="button" onClick={onCancel}>{cancelLabel}</button>
          <button
            type="button"
            className={danger ? "provider-danger-action" : undefined}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
