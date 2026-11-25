// What an uploaded file (`asset:<id>`) is: the name it was uploaded with and its kind. Files uploaded in this session
// are known at once; for the rest (after a reload) the library is read once with GET /assets. A raw id is never shown.

import { useEffect, useReducer } from "react";
import { engine } from "../engine/client";
import type { AssetKind } from "../engine/types";
import { t } from "../i18n/t";

const info = new Map<string, { name: string; kind: AssetKind }>();
const listeners = new Set<() => void>();
let loading: Promise<void> | null = null;

export const isAssetRef = (path: string): boolean => path.startsWith("asset:");

const notify = () => listeners.forEach((fn) => fn());

/** Remember a file just uploaded, so its name shows without asking the engine. */
export function rememberAssetInfo(ref: string, name: string, kind: AssetKind): void {
  info.set(ref, { name, kind });
  notify();
}

export const assetKind = (ref: string): AssetKind | null => info.get(ref)?.kind ?? null;

/** The name a file was uploaded with; a plain "Uploaded file" until the library has been read (or if it was deleted). */
export const assetName = (ref: string): string => info.get(ref)?.name ?? t("asset.unnamed");

/** Reads the library once (again only after a failure). Never rejects. */
export function loadAssetNames(): Promise<void> {
  if (!loading) {
    loading = engine.listAssets().then(
      (list) => {
        for (const a of list) info.set(`asset:${a.asset_id}`, { name: a.name, kind: a.kind });
        notify();
      },
      () => {
        loading = null; // offline or signed out: the next caller asks again
      },
    );
  }
  return loading;
}

/** Re-renders when names arrive; reads the library when one of `refs` is not known yet. */
export function useAssetNames(refs: (string | null | undefined)[]): void {
  const [, bump] = useReducer((n: number) => n + 1, 0);
  const missing = refs.some((r) => !!r && isAssetRef(r) && !info.has(r));
  useEffect(() => {
    listeners.add(bump);
    return () => void listeners.delete(bump);
  }, []);
  useEffect(() => {
    if (missing) void loadAssetNames();
  }, [missing]);
}

/** Tests only: forget everything. */
export function resetAssetInfo(): void {
  info.clear();
  loading = null;
}
