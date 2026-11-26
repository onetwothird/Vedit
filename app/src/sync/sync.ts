// Desktop sync with the website (CapCut-style): every local project is pushed to the cloud while signed in, newer
// cloud revisions are pulled in place, and both-sides edits keep the push as "<title> (copy)" so nothing is lost.
// The cloud's projects that are not on this computer are listed (never downloaded on their own): openCloudProject
// brings one in. Runs only in the desktop app with VITE_CLOUD_URL set; otherwise it does nothing at all.
//
// Change detection: the local engine reports each project's bundle hash; the hash last synced is kept in the
// project's settings (cloud_hash, never part of the bundle). A project is pushed 5 s after its last edit and on each
// 60 s cycle when the two differ. Every error is quiet: busy (409) and network errors are retried later.

import { useSyncExternalStore } from "react";
import { getAccessToken } from "../auth/accessToken";
import { createCloudApi, localSync, onProjectEdited } from "../engine/client";
import { isTauri, isWeb } from "../engine/flavor";
import { EngineError } from "../engine/types";
import type { CloudApi, CloudProject, ImportBundleResult, Job, LocalSyncApi, ProjectBundle, PushResult, SyncSettingsPatch, SyncState } from "../engine/types";

export const PUSH_DEBOUNCE_MS = 5_000;
export const PULL_INTERVAL_MS = 60_000;
/** Delays before retrying after a cycle that could not reach an engine; the last one repeats. */
export const SYNC_BACKOFF_MS = [5_000, 15_000, 30_000, 60_000];
const JOB_POLL_MS = 1_000;

export interface SyncOptions {
  /** The app's "a project was edited" signal (onProjectEdited). Returns its unsubscribe. */
  edits?: (onEdit: (pid: string) => void) => () => void;
  /** Fires when someone signs in. Returns its unsubscribe. */
  signIns?: (onSignIn: () => void) => () => void;
  /** Shows a message; default: the sync store's toasts. */
  toast?: (message: string) => void;
  debounceMs?: number;
  intervalMs?: number;
  backoffMs?: number[];
  jobPollMs?: number;
}

// ---- what the UI reads -----------------------------------------------------------------------------------------

export interface SyncToast {
  id: number;
  message: string;
}

export interface SyncSnapshot {
  /** Cloud projects with no linked project on this computer (shown with a cloud badge; open with openCloudProject). */
  cloudOnly: CloudProject[];
  toasts: SyncToast[];
}

let snapshot: SyncSnapshot = { cloudOnly: [], toasts: [] };
const listeners = new Set<() => void>();
let nextToast = 1;

function update(next: Partial<SyncSnapshot>): void {
  snapshot = { ...snapshot, ...next };
  listeners.forEach((l) => l());
}

export const syncStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  },
  getSnapshot: (): SyncSnapshot => snapshot,
  dismissToast(id: number): void {
    update({ toasts: snapshot.toasts.filter((t) => t.id !== id) });
  },
};

export function useSync(): SyncSnapshot {
  return useSyncExternalStore(syncStore.subscribe, syncStore.getSnapshot);
}

const storeToast = (message: string): void => update({ toasts: [...snapshot.toasts, { id: nextToast++, message }] });

// ---- the sync loop ---------------------------------------------------------------------------------------------

interface Running {
  local: LocalSyncApi;
  cloud: CloudApi;
  enqueue<T>(task: () => Promise<T>): Promise<T>;
  push(s: SyncState): Promise<string>;
  jobPollMs: number;
}

let active: Running | null = null;

const isClean = (s: SyncState): boolean => s.hash !== null && s.hash === s.cloud_hash;
const isDirty = (s: SyncState): boolean => s.hash !== null && (s.cloud_id === null || s.hash !== s.cloud_hash);

/** An engine that could not be reached (or broke): the cycle stops and is retried with backoff. */
const transient = (e: unknown): boolean =>
  !(e instanceof EngineError) || e.status === 0 || e.status >= 500;

function withoutLink(b: ProjectBundle & { video_url?: string }): ProjectBundle {
  const { video_url: _drop, ...bundle } = b;
  return bundle;
}

/** Starts syncing; returns the stop function. With no cloud API (VITE_CLOUD_URL unset) nothing ever runs; signed
    out (session() gives null) a cycle makes no request at all. */
export function startSync(
  local: LocalSyncApi,
  cloud: CloudApi | null,
  session: () => Promise<string | null>,
  opts: SyncOptions = {},
): () => void {
  if (!cloud) return () => undefined;
  const debounceMs = opts.debounceMs ?? PUSH_DEBOUNCE_MS;
  const intervalMs = opts.intervalMs ?? PULL_INTERVAL_MS;
  const backoff = opts.backoffMs ?? SYNC_BACKOFF_MS;
  const toast = opts.toast ?? storeToast;
  let stopped = false;
  let chain: Promise<unknown> = Promise.resolve();
  let timer: ReturnType<typeof setTimeout> | null = null;
  let fails = 0;
  const debounces = new Map<string, ReturnType<typeof setTimeout>>();

  /** One sync step at a time: a push never races a pull of the same project. */
  function enqueue<T>(task: () => Promise<T>): Promise<T> {
    const run = chain.then(() => (stopped ? Promise.reject(new Error("sync stopped")) : task()));
    chain = run.catch(() => undefined);
    return run;
  }

  /** Pushes one project; returns the cloud id it is linked to afterwards. Errors are the caller's. */
  async function push(s: SyncState): Promise<string> {
    const bundle = await local.projectBundle(s.id);
    const hash = s.hash ?? undefined;
    const pushNew = async (): Promise<string> => {
      const r = await cloud!.pushNew(bundle);
      await local.patchSyncSettings(s.id, { cloud_id: r.id, cloud_rev: r.rev, cloud_hash: hash });
      return r.id;
    };
    if (!s.cloud_id) return pushNew();
    let r: PushResult;
    try {
      r = await cloud!.pushSynced(s.cloud_id, s.cloud_rev, bundle);
    } catch (e) {
      if (!(e instanceof EngineError && e.status === 404)) throw e;
      const id = await pushNew(); // deleted on the website: this computer still has it, so it goes up again
      toast(`${bundle.title} was removed from the web, so it was uploaded again`);
      return id;
    }
    if (r.conflict) {
      // the cloud moved on: the push became a copy, and this project follows it. Linked at revision 0, so the copy
      // (titled "<title> (copy)") is pulled in place now, or by the next cycle if that fails: the local title then
      // matches the copy's and a later push never renames it back
      await local.patchSyncSettings(s.id, { cloud_id: r.id, cloud_rev: 0, cloud_hash: hash });
      toast(`Saved a copy: ${bundle.title} (copy)`);
      try {
        await local.importBundle({ bundle: withoutLink(await cloud!.getSynced(r.id)), expect_hash: hash });
      } catch {
        // retried by the next cycle (cloud rev 1 > 0)
      }
      return r.id;
    }
    await local.patchSyncSettings(s.id, { cloud_rev: r.rev, cloud_hash: hash });
    return r.id;
  }

  /** Runs one project's step; a busy or refused one is left for the next cycle, an unreachable engine stops it. */
  async function quietly<T>(step: () => Promise<T>): Promise<T | undefined> {
    try {
      return await step();
    } catch (e) {
      if (transient(e)) throw e;
      return undefined; // 409 busy, 400/404/413: stays as it is, tried again next cycle
    }
  }

  async function cycle(): Promise<void> {
    if (!(await session())) {
      if (snapshot.cloudOnly.length) update({ cloudOnly: [] });
      return;
    }
    const [list, states] = await Promise.all([cloud!.listSynced(), local.syncState()]);
    const linked = new Set(states.flatMap((s) => (s.cloud_id ? [s.cloud_id] : [])));
    const handled = new Set<string>();
    for (const c of list) {
      const s = states.find((x) => x.cloud_id === c.id);
      if (!s || c.rev <= s.cloud_rev) continue;
      handled.add(s.id);
      if (isClean(s)) {
        await quietly(async () => {
          const bundle = withoutLink(await cloud!.getSynced(c.id));
          // in place (the local video stays, nothing is downloaded), and only if nothing was edited meanwhile:
          // otherwise 409, and the next cycle sees the edit and pushes it (a conflict copy)
          await local.importBundle({ bundle, expect_hash: s.hash ?? undefined });
        });
      } else {
        const now = await quietly(() => push(s)); // the cloud answers with a copy: both versions are kept
        if (now !== undefined && now !== c.id) {
          linked.delete(c.id);
          linked.add(now);
        }
      }
    }
    for (const s of states) {
      if (handled.has(s.id) || !isDirty(s)) continue;
      const now = await quietly(() => push(s));
      if (now !== undefined) {
        if (s.cloud_id && now !== s.cloud_id) linked.delete(s.cloud_id);
        linked.add(now);
      }
    }
    if (!stopped) update({ cloudOnly: list.filter((c) => !linked.has(c.id)) });
  }

  async function pushOne(pid: string): Promise<void> {
    if (!(await session())) return;
    const s = (await local.syncState()).find((x) => x.id === pid);
    if (s && isDirty(s)) await quietly(() => push(s));
  }

  function schedule(delay: number): void {
    if (stopped) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(tick, delay);
  }

  function tick(): void {
    timer = null;
    enqueue(cycle).then(
      () => {
        fails = 0;
        schedule(intervalMs);
      },
      (e: unknown) => {
        if (stopped) return;
        if (transient(e)) {
          fails++;
          schedule(backoff[Math.min(fails - 1, backoff.length - 1)]);
        } else {
          schedule(intervalMs);
        }
      },
    );
  }

  const offEdits = opts.edits?.((pid) => {
    if (stopped) return;
    const old = debounces.get(pid);
    if (old) clearTimeout(old);
    debounces.set(pid, setTimeout(() => {
      debounces.delete(pid);
      enqueue(() => pushOne(pid)).catch(() => undefined); // a failure is picked up by the next cycle
    }, debounceMs));
  });
  const offSignIns = opts.signIns?.(() => schedule(0));

  const me: Running = { local, cloud, enqueue, push, jobPollMs: opts.jobPollMs ?? JOB_POLL_MS };
  active = me;
  update({ cloudOnly: [], toasts: [] });
  schedule(0);

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    timer = null;
    debounces.forEach((t) => clearTimeout(t));
    debounces.clear();
    offEdits?.();
    offSignIns?.();
    if (active === me) active = null;
  };
}

function running(): Running {
  if (!active) throw new Error("Sign in to sync with the website");
  return active;
}

/** Brings a cloud-only project to this computer: its video (when it is in the cloud) is downloaded once into
    Videos/Vedit/Cloud by the local engine, as a job of kind "sync" whose result names the new project. */
export async function openCloudProject(id: string): Promise<ImportBundleResult> {
  const a = running();
  return a.enqueue(async () => {
    const b = await a.cloud.getSynced(id);
    const bundle = withoutLink(b);
    const r = await a.local.importBundle(b.video_url ? { bundle, video_url: b.video_url } : { bundle });
    if (r.project) update({ cloudOnly: snapshot.cloudOnly.filter((c) => c.id !== id) });
    return r;
  });
}

async function stateOf(local: LocalSyncApi, pid: string): Promise<SyncState> {
  const s = (await local.syncState()).find((x) => x.id === pid);
  if (!s) throw new Error("Project not found");
  return s;
}

async function finished(local: LocalSyncApi, job: Job, pollMs: number): Promise<void> {
  let j = job;
  while (j.state === "queued" || j.state === "running") {
    await new Promise((r) => setTimeout(r, pollMs));
    j = await local.getJob(j.id);
  }
  if (j.state !== "done") throw new Error(j.error || "The upload did not finish");
}

/** After the cloud changed web_video (one revision up): record it here too, so neither side looks newer. */
async function settle(a: Running, pid: string, rev: number, on: boolean): Promise<void> {
  const now = await stateOf(a.local, pid);
  const clean = isClean(now);
  const patch: SyncSettingsPatch = { web_video: on };
  if (rev === now.cloud_rev + 1) patch.cloud_rev = rev; // nothing else changed in the cloud meanwhile
  const row = await a.local.patchSyncSettings(pid, patch);
  if (clean && patch.cloud_rev !== undefined && row.hash) await a.local.patchSyncSettings(pid, { cloud_hash: row.hash });
}

/** "Available on the web". On: the local engine uploads the video through a presigned URL and the cloud project
    takes it (a refusal, such as over 60 minutes or out of storage, is thrown with the server's message). Off: the
    cloud copy of the video is deleted. The video on this computer is never touched. */
export async function setWebVideo(pid: string, on: boolean): Promise<void> {
  const a = running();
  const s = await a.enqueue(async () => {
    let s = await stateOf(a.local, pid);
    if (!s.cloud_id) { // not in the cloud yet: push it first so there is a project to attach to
      await a.push(s);
      s = await stateOf(a.local, pid);
    }
    return s;
  });
  const cloudId = s.cloud_id!;
  if (on) {
    if (!s.has_source) throw new Error("This project's video is not on this computer");
    const ticket = await a.cloud.createUpload({ kind: "video", name: s.source_name, size: s.size, content_type: s.content_type });
    await finished(a.local, await a.local.publishVideo(pid, ticket.url), a.jobPollMs);
    await a.enqueue(async () => {
      const summary = await a.cloud.attachVideo(cloudId, ticket.upload_key);
      await settle(a, pid, summary.rev, true);
    });
  } else {
    await a.enqueue(async () => {
      const summary = await a.cloud.deleteVideo(cloudId);
      await settle(a, pid, summary.rev, false);
    });
  }
}

/** The app's sync: on in the desktop app when VITE_CLOUD_URL is set, with the signed-in user's access token. */
export function bootSync(): () => void {
  if (!isTauri() || isWeb() || !localSync) return () => undefined;
  const cloud = createCloudApi();
  if (!cloud) return () => undefined;
  return startSync(localSync, cloud, getAccessToken, {
    edits: onProjectEdited,
    signIns: (fn) => {
      let off = (): void => undefined;
      let gone = false;
      void import("../auth/auth").then(({ useAuth }) => {
        if (!gone) off = useAuth.subscribe((st, prev) => {
          if (st.account && !prev.account) fn();
        });
      });
      return () => {
        gone = true;
        off();
      };
    },
  });
}
