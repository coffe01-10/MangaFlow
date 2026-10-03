"use client";

import { useEffect } from "react";

/**
 * Cancelable navigation request. The CommandPalette (and any non-anchor
 * navigator) dispatches this before router.push; a dirty page's guard calls
 * preventDefault to veto. dispatchEvent returns false once prevented.
 */
export const REQUEST_NAVIGATION = "mangaflow:request-navigation";

export function requestNavigation(href: string): boolean {
  return window.dispatchEvent(
    new CustomEvent(REQUEST_NAVIGATION, {
      cancelable: true,
      detail: { href },
    }),
  );
}

/**
 * Shared dirty-draft leave guard, mirroring the script-editor discipline:
 * beforeunload covers reload/close, a capture-phase click listener covers
 * in-app <Link> anchors, and the cancelable REQUEST_NAVIGATION event covers
 * non-anchor navigation (CommandPalette router.push, future shortcuts).
 */
export function useUnsavedChangesGuard(dirty: boolean, message: string) {
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      const target = event.target;
      const anchor = target instanceof Element ? target.closest("a[href]") : null;
      if (!anchor) return;
      const href = anchor.getAttribute("href") ?? "";
      if (!href.startsWith("/") || href === window.location.pathname) return;
      if (!window.confirm(message)) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    const externalNavigation = (event: Event) => {
      if (event.defaultPrevented) return;
      if (!window.confirm(message)) event.preventDefault();
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", click, true);
    window.addEventListener(REQUEST_NAVIGATION, externalNavigation);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", click, true);
      window.removeEventListener(REQUEST_NAVIGATION, externalNavigation);
    };
  }, [dirty, message]);
}
