// The token inside media URLs, as something a component can watch. Kept apart from client.ts: the client sets it, the
// useMediaUrl hook reads it, and neither needs the other (or the engine) to be loaded.

let token: string | null = null;
let refresher: (() => Promise<void>) | null = null;
const listeners = new Set<() => void>();

/** The client calls this whenever it reaches the engine; watchers hear only when the token or address changed. */
export function setMediaToken(next: string, changed: boolean): void {
  token = next;
  if (changed) listeners.forEach((fn) => fn());
}

/** How to get a fresh token: the client registers it (cloud build only). */
export function setMediaRefresher(fn: (() => Promise<void>) | null): void {
  refresher = fn;
}

export const subscribeMedia = (fn: () => void): (() => void) => {
  listeners.add(fn);
  return () => void listeners.delete(fn);
};

export const mediaToken = (): string | null => token;

/** Asks the engine client for a fresh token. Never throws; does nothing where tokens do not expire. */
export async function refreshMediaToken(): Promise<void> {
  try {
    await refresher?.();
  } catch {
    // signed out or offline: the element keeps showing its error
  }
}
