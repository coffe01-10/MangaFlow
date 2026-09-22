"use client";

import { useEffect, useRef, type ReactNode } from "react";
import styles from "./workflow-studio.module.css";

export function WorkflowDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement as HTMLElement | null;
    dialog?.showModal();
    return () => { dialog?.close(); previous?.focus(); };
  }, []);
  return <dialog ref={ref} className={styles.dialog} aria-label={title} onCancel={onClose}
    onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <header><div><small>MANGAFLOW / STUDIO</small><h2>{title}</h2></div><button aria-label="关闭对话框" onClick={onClose}>Esc ×</button></header>
    {children}
  </dialog>;
}
