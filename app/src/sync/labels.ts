// What a recent project's sync badges say: where it lives and whether it is synced. Pure, for the Home list.

import type { ProjectSettings, SyncState } from "../engine/types";

export type Where = "local" | "both" | "cloud";
export type SyncStatus = "syncing" | "synced" | "signedout";

export interface SyncLabel {
  where: Where;
  /** Null when there is nothing to say yet (before the first cycle, or a project that cannot be exported). */
  status: SyncStatus | null;
}

export interface SyncView {
  /** This build can sync (desktop with a cloud); false: no badges at all. */
  available: boolean;
  /** Signed in with sync switched on. */
  active: boolean;
  states: SyncState[];
  cloudIds: string[];
  busy: string[];
}

/** "This computer" (no link), "Both" (linked and listed by the cloud); "Syncing…" while a push, pull or transfer is in
    flight or an edit waits to be pushed, "Synced" when the hash is the one last synced, "Not synced (signed out)"
    when signed out or sync is off. A cloud-only project is labelled by its own list ("Cloud"). */
export function projectSyncLabel(pid: string, settings: ProjectSettings, view: SyncView): SyncLabel | null {
  if (!view.available) return null;
  if (!view.active) return { where: "local", status: "signedout" };
  const row = view.states.find((s) => s.id === pid);
  const cloudId = row ? row.cloud_id : settings.cloud_id ?? null;
  const where: Where = cloudId && view.cloudIds.includes(cloudId) ? "both" : "local";
  if (view.busy.includes(pid)) return { where, status: "syncing" };
  if (!row || row.hash === null) return { where, status: null };
  return { where, status: row.hash === row.cloud_hash ? "synced" : "syncing" };
}

const MB = 1024 ** 2;
const GB = 1024 ** 3;

/** A file size as people read it: "120 MB", "1.3 GB" (no ".0"). */
export function sizeText(bytes: number): string {
  const b = Math.max(0, bytes);
  if (b < GB) return `${b === 0 ? 0 : Math.max(1, Math.round(b / MB))} MB`;
  return `${Math.round((b / GB) * 10) / 10} GB`;
}
