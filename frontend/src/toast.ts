// Short-lived notices (drop-incident results, fake call placed). Any module can call toast().

import { useSyncExternalStore } from "react";

export type ToastTone = "ok" | "error" | "info";

export interface Toast {
  id: number;
  text: string;
  tone: ToastTone;
}

const SHOW_MS = 4500;

let toasts: Toast[] = [];
let next = 1;
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((fn) => fn());

/** Show a notice for a few seconds. */
export function toast(text: string, tone: ToastTone = "info"): void {
  const id = next++;
  toasts = [...toasts, { id, text, tone }].slice(-4);
  notify();
  window.setTimeout(() => {
    toasts = toasts.filter((t) => t.id !== id);
    notify();
  }, SHOW_MS);
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** The notices on screen now, oldest first. */
export function useToasts(): Toast[] {
  return useSyncExternalStore(subscribe, () => toasts);
}
