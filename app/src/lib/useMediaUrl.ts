import { useCallback, useRef, useSyncExternalStore } from "react";
import { isCloud } from "../engine/flavor";
import { mediaToken, refreshMediaToken, subscribeMedia } from "../engine/mediaToken";

const RETRY_GAP_MS = 60_000;

/** A media URL that keeps working in the website build, where the token in it expires after an hour.
    `build` makes the URL from the engine's current token (engineMediaUrl). The URL is built again whenever the token
    changes; and when the element fails to load it, `onError` asks for a fresh token (at most once a minute) and the new
    URL replaces the old one. On the desktop the token never changes and `onError` does nothing. */
export function useMediaUrl(build: () => string | null): { url: string | null; onError(): void } {
  useSyncExternalStore(subscribeMedia, mediaToken); // a new token re-renders, and build() reads it
  const url = build();
  const lastRetry = useRef(0);
  const onError = useCallback(() => {
    // one retry per minute: a file that still fails with a fresh token is broken, not expired
    if (!isCloud() || !url || Date.now() - lastRetry.current < RETRY_GAP_MS) return;
    lastRetry.current = Date.now();
    void refreshMediaToken();
  }, [url]);
  return { url, onError };
}
