"use client";

import { useSyncExternalStore } from "react";

// 水合安全的 localStorage 外部存储（与 use-assets-workspace.ts 顶部的风格
// 色彩模式同一模式）：useSyncExternalStore 在水合渲染期间采用服务端快照
// （fallback），客户端快照只在水合完成后同步生效——渲染期直接读
// localStorage 会造成水合不匹配，effect 里同步 setState 又会级联重渲染。
// 所有写入必须走 writeLocalStorage，让跨组件/跨实例的订阅者保持一致。
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emitChange() {
  listeners.forEach((listener) => listener());
}

/**
 * Hydration-safe localStorage read as a hook: the hydration render sees
 * `fallback`; the real stored value applies immediately after hydration.
 * Returns the raw string — derive typed values at the call site.
 */
export function useLocalStorageValue(key: string, fallback: string): string {
  return useSyncExternalStore(
    subscribe,
    () => window.localStorage.getItem(key) ?? fallback,
    () => fallback,
  );
}

/** Write a key and notify every useLocalStorageValue subscriber. */
export function writeLocalStorage(key: string, value: string): void {
  window.localStorage.setItem(key, value);
  emitChange();
}
