// Desktop sync with the website (CapCut-style): every local project is pushed to the cloud while signed in, newer
// cloud revisions are pulled in place, and both-sides edits keep the push as "<title> (copy)" so nothing is lost.
// The cloud's projects that are not on this computer are listed (never downloaded on their own): openCloudProject
// brings one in. Runs only in the desktop app with VITE_CLOUD_URL set; otherwise it does nothing at all.
//
// Change detection: the local engine reports each project's bundle hash; the hash last synced is kept in the
// project's settings (cloud_hash, never part of the bundle). A project is pushed 5 s after its last edit and on each
// 60 s cycle when the two differ. Every error is quiet: busy (409) and network errors are retried later.

import { useSyncExternalStore } from "react";
import { getSyncSession, signedInUserId } from "../auth/accessToken";
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
  /** This build can sync (the desktop app with a cloud to sync with); false on the website and in plain builds. */
  available: boolean;
  /** Sync is running ("Sync projects with your account" is on). */
  running: boolean;
  /** Every local project's sync row as of the last cycle; empty until then, and while signed out. */
  states: SyncState[];
  /** The ids the cloud listed in the last cycle, with the ones this computer created since. */
  cloudIds: string[];
  /** Local project ids (and cloud ids being opened) with a push, pull or video transfer in flight. */
  busy: string[];
}

const IDLE = { cloudOnly: [], states: [], cloudIds: [], busy: [] };
let snapshot: SyncSnapshot = { ...IDLE, toasts: [], available: false, running: false };
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

const inFlight = new Map<string, number>();

/** Marks `id` busy while `task` runs (shown as "Syncing…"). */
async function during<T>(id: string, task: () => Promise<T>): Promise<T> {
  inFlight.set(id, (inFlight.get(id) ?? 0) + 1);
  update({ busy: [...inFlight.keys()] });
  try {
    return await task();
  } finally {
    const n = (inFlight.get(id) ?? 1) - 1;
    if (n > 0) inFlight.set(id, n);
    else inFlight.delete(id);
    update({ busy: [...inFlight.keys()] });
  }
}

// ---- the sync loop ---------------------------------------------------------------------------------------------

/** The signed-in account, read once (token and user id together) at the start of a unit of work. */
export interface SyncIdentity {
  token: string;
  uid: string;
}

/** The account changed while a unit of work ran: it stops before writing anything, and the next cycle starts over. */
class AccountSwitched extends Error {
  constructor() {
    super("You signed in with another account. Try again.");
    this.name = "AccountSwitched";
  }
}

/** One unit of work (a cycle, a debounced push, opening a cloud project, the web-video switch): every cloud request
    goes out with the token read at its start, and every owner recorded is the account that token belongs to. */
interface Unit {
  uid: string;
  cloud: CloudApi;
  /** Throws AccountSwitched unless the same account is still signed in. Called before every write and before a
      404 is acted on. */
  check(): void;
}

interface Running {
  local: LocalSyncApi;
  begin(): Promise<Unit | null>;
  enqueue<T>(task: () => Promise<T>): Promise<T>;
  push(s: SyncState, unit: Unit): Promise<string>;
  jobPollMs: number;
}

let active: Running | null = null;

/** Whether this account may sync the project: unlinked, linked by this account, or linked before the owner was
    recorded (adopted by this account on its next push or pull). A link of another account is left alone: the
    cloud answers 404 for another user's project, which must never look like "removed from the web". */
const ownedBy = (s: SyncState, uid: string): boolean => !s.cloud_id || !s.cloud_owner || s.cloud_owner === uid;

const isClean = (s: SyncState): boolean => s.hash !== null && s.hash === s.cloud_hash;
const isDirty = (s: SyncState): boolean => s.hash !== null && (s.cloud_id === null || s.hash !== s.cloud_hash);

/** An engine that could not be reached (or broke): the cycle stops and is retried with backoff. */
const transient = (e: unknown): boolean =>
  !(e instanceof AccountSwitched) && (!(e instanceof EngineError) || e.status === 0 || e.status >= 500);

function withoutLink(b: ProjectBundle & { video_url?: string }): ProjectBundle {
  const { video_url: _drop, ...bundle } = b;
  return bundle;
}

/** Starts syncing; returns the stop function.
    - `cloudFor(token)`: the cloud API acting as that token's account; null (VITE_CLOUD_URL unset) = nothing runs.
    - `session()`: the signed-in account's token and user id, read together; null = signed out, no request at all.
    - `user()`: who is signed in right now (synchronous), to notice an account switch inside a unit of work. */
export function startSync(
  local: LocalSyncApi,
  cloudFor: ((token: string) => CloudApi) | null,
  session: () => Promise<SyncIdentity | null>,
  user: () => string | null,
  opts: SyncOptions = {},
): () => void {
  if (!cloudFor) return () => undefined;
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

  /** A unit of work as the account signed in now, or null when nobody is (or an account switch is under way). */
  async function begin(): Promise<Unit | null> {
    const id = await session();
    if (!id || user() !== id.uid) return null;
    return {
      uid: id.uid,
      cloud: cloudFor!(id.token),
      check() {
        if (user() !== id.uid) throw new AccountSwitched();
      },
    };
  }

  /** Pushes one project; returns the cloud id it is linked to afterwards. Errors are the caller's. */
  function push(s: SyncState, unit: Unit): Promise<string> {
    return during(s.id, () => pushNow(s, unit));
  }

  async function pushNow(s: SyncState, unit: Unit): Promise<string> {
    const { uid, cloud } = unit;
    const bundle = await local.projectBundle(s.id);
    const hash = s.hash ?? undefined;
    const pushNew = async (): Promise<string> => {
      unit.check();
      const r = await cloud.pushNew(bundle);
      await local.patchSyncSettings(s.id, { cloud_id: r.id, cloud_rev: r.rev, cloud_hash: hash, cloud_owner: uid });
      return r.id;
    };
    if (!s.cloud_id) return pushNew();
    let r: PushResult;
    unit.check();
    try {
      r = await cloud.pushSynced(s.cloud_id, s.cloud_rev, bundle);
    } catch (e) {
      // only a link this account made is known to be gone (a 404 is also what another account's project gets)
      if (!(e instanceof EngineError && e.status === 404) || s.cloud_owner !== uid) throw e;
      unit.check();
      const id = await pushNew(); // deleted on the website: this computer still has it, so it goes up again
      toast(`${bundle.title} was removed from the web, so it was uploaded again`);
      return id;
    }
    if (r.conflict) {
      // the cloud moved on: the push became a copy, and this project follows it. Linked at revision 0, so the copy
      // (titled "<title> (copy)") is pulled in place now, or by the next cycle if that fails: the local title then
      // matches the copy's and a later push never renames it back
      await local.patchSyncSettings(s.id, { cloud_id: r.id, cloud_rev: 0, cloud_hash: hash, cloud_owner: uid });
      toast(`Saved a copy: ${bundle.title} (copy)`);
      try {
        const copy = withoutLink(await cloud.getSynced(r.id));
        unit.check();
        await local.importBundle({ bundle: copy, expect_hash: hash, cloud_owner: uid });
      } catch {
        // retried by the next cycle (cloud rev 1 > 0)
      }
      return r.id;
    }
    await local.patchSyncSettings(s.id, { cloud_rev: r.rev, cloud_hash: hash, ...(s.cloud_owner !== uid ? { cloud_owner: uid } : {}) });
    return r.id;
  }

  /** Runs one project's step; a busy or refused one is left for the next cycle, an unreachable engine (or an
      account switch) stops the whole unit. */
  async function quietly<T>(step: () => Promise<T>): Promise<T | undefined> {
    try {
      return await step();
    } catch (e) {
      if (transient(e) || e instanceof AccountSwitched) throw e;
      return undefined; // 409 busy, 400/404/413: stays as it is, tried again next cycle
    }
  }

  async function cycle(): Promise<void> {
    const unit = await begin();
    if (!unit) {
      if (snapshot.cloudOnly.length || snapshot.states.length || snapshot.cloudIds.length) {
        update({ cloudOnly: [], states: [], cloudIds: [] });
      }
      return;
    }
    const { uid, cloud } = unit;
    const [list, all] = await Promise.all([cloud.listSynced(), local.syncState()]);
    const states = all.filter((s) => ownedBy(s, uid)); // another account's links: no push, no pull, no toast
    const linked = new Set(states.flatMap((s) => (s.cloud_id ? [s.cloud_id] : [])));
    const handled = new Set<string>();
    for (const c of list) {
      const s = states.find((x) => x.cloud_id === c.id);
      if (!s || c.rev <= s.cloud_rev) continue;
      handled.add(s.id);
      if (isClean(s)) {
        await quietly(() => during(s.id, async () => {
          const bundle = withoutLink(await cloud.getSynced(c.id));
          unit.check();
          // in place (the local video stays, nothing is downloaded), and only if nothing was edited meanwhile:
          // otherwise 409, and the next cycle sees the edit and pushes it (a conflict copy)
          await local.importBundle({ bundle, expect_hash: s.hash ?? undefined, cloud_owner: uid });
        }));
      } else {
        const now = await quietly(() => push(s, unit)); // the cloud answers with a copy: both versions are kept
        if (now !== undefined && now !== c.id) {
          linked.delete(c.id);
          linked.add(now);
        }
      }
    }
    for (const s of states) {
      if (handled.has(s.id) || !isDirty(s)) continue;
      const now = await quietly(() => push(s, unit));
      if (now !== undefined) {
        if (s.cloud_id && now !== s.cloud_id) linked.delete(s.cloud_id);
        linked.add(now);
      }
    }
    unit.check();
    const now = await local.syncState(); // what the pushes and pulls above changed, for the labels
    if (!stopped) {
      update({
        cloudOnly: list.filter((c) => !linked.has(c.id)),
        states: now,
        cloudIds: [...new Set([...list.map((c) => c.id), ...linked])],
      });
    }
  }

  async function pushOne(pid: string): Promise<void> {
    const unit = await begin();
    if (!unit) return;
    const s = (await local.syncState()).find((x) => x.id === pid);
    if (!s || !ownedBy(s, unit.uid) || !isDirty(s)) return;
    const id = await quietly(() => push(s, unit));
    if (stopped) return;
    const states = await local.syncState();
    update(id && !snapshot.cloudIds.includes(id) ? { states, cloudIds: [...snapshot.cloudIds, id] } : { states });
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
        if (e instanceof AccountSwitched) {
          schedule(0); // start over as the account signed in now
        } else if (transient(e)) {
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

  const me: Running = { local, begin, enqueue, push, jobPollMs: opts.jobPollMs ?? JOB_POLL_MS };
  active = me;
  update({ ...IDLE, toasts: [], running: true });
  schedule(0);

  return () => {
    stopped = true;
    if (active === me) update({ ...IDLE, running: false });
    if (timer) clearTimeout(timer);
    timer = null;
    debounces.forEach((t) => clearTimeout(t));
    debounces.clear();
    offEdits?.();
    offSignIns?.();
    if (active === me) active = null;
  };
}

/** The running sync and a unit of work as the account signed in now. */
async function started(): Promise<{ a: Running; unit: Unit }> {
  const a = active;
  const unit = a ? await a.begin() : null;
  if (!a || !unit) throw new Error("Sign in to sync with the website");
  return { a, unit };
}

/** Brings a cloud-only project to this computer: its video (when it is in the cloud) is downloaded once into
    Videos/Vedit/Cloud by the local engine, as a job of kind "sync" whose result names the new project. */
export async function openCloudProject(id: string): Promise<ImportBundleResult> {
  const { a, unit } = await started();
  return during(id, () => a.enqueue(async () => {
    const b = await unit.cloud.getSynced(id);
    const bundle = withoutLink(b);
    unit.check();
    const r = await a.local.importBundle(b.video_url
      ? { bundle, video_url: b.video_url, cloud_owner: unit.uid }
      : { bundle, cloud_owner: unit.uid });
    if (r.project) update({ cloudOnly: snapshot.cloudOnly.filter((c) => c.id !== id) });
    return r;
  }));
}

/** Waits for a sync job of the local engine (a video download or upload) to end; throws when it did not finish.
    `onProgress` hears its percentage on every poll. */
export async function waitForSyncJob(job: Job, onProgress?: (pct: number) => void): Promise<Job> {
  const a = active;
  if (!a) throw new Error("Sign in to sync with the website");
  return finished(a.local, job, a.jobPollMs, onProgress, "The download did not finish");
}

/** A cloud project's video finished downloading: it is linked here now, so it leaves the cloud-only list. */
export async function cloudProjectOpened(id: string): Promise<void> {
  const a = active;
  update({ cloudOnly: snapshot.cloudOnly.filter((c) => c.id !== id) });
  if (a) update({ states: await a.local.syncState() });
}

async function stateOf(local: LocalSyncApi, pid: string): Promise<SyncState> {
  const s = (await local.syncState()).find((x) => x.id === pid);
  if (!s) throw new Error("Project not found");
  return s;
}

async function finished(local: LocalSyncApi, job: Job, pollMs: number, onProgress?: (pct: number) => void,
                        failed = "The upload did not finish"): Promise<Job> {
  let j = job;
  onProgress?.(j.pct);
  while (j.state === "queued" || j.state === "running") {
    await new Promise((r) => setTimeout(r, pollMs));
    j = await local.getJob(j.id);
    onProgress?.(j.pct);
  }
  if (j.state !== "done") throw new Error(j.error || failed);
  return j;
}

/** After the cloud changed web_video (one revision up): record it here too, so neither side looks newer. */
async function settle(a: Running, unit: Unit, pid: string, rev: number, on: boolean): Promise<void> {
  const now = await stateOf(a.local, pid);
  const clean = isClean(now);
  const patch: SyncSettingsPatch = { web_video: on };
  if (rev === now.cloud_rev + 1) patch.cloud_rev = rev; // nothing else changed in the cloud meanwhile
  unit.check();
  const row = await a.local.patchSyncSettings(pid, patch);
  if (clean && patch.cloud_rev !== undefined && row.hash) await a.local.patchSyncSettings(pid, { cloud_hash: row.hash });
}

/** "Available on the web". On: the local engine uploads the video through a presigned URL and the cloud project
    takes it (a refusal, such as over 60 minutes or out of storage, is thrown with the server's message). Off: the
    cloud copy of the video is deleted. The video on this computer is never touched. */
export async function setWebVideo(pid: string, on: boolean, onProgress?: (pct: number) => void): Promise<void> {
  const { a, unit } = await started();
  try {
    await during(pid, () => switchWebVideo(a, unit, pid, on, onProgress));
  } finally {
    if (active === a) update({ states: await a.local.syncState().catch(() => snapshot.states) });
  }
}

async function switchWebVideo(a: Running, unit: Unit, pid: string, on: boolean, onProgress?: (pct: number) => void): Promise<void> {
  const s = await a.enqueue(async () => {
    let s = await stateOf(a.local, pid);
    if (!ownedBy(s, unit.uid)) throw new Error("This project is synced with another account. Sign in with that account to change it.");
    if (!s.cloud_id) { // not in the cloud yet: push it first so there is a project to attach to
      await a.push(s, unit);
      s = await stateOf(a.local, pid);
    }
    return s;
  });
  const cloudId = s.cloud_id!;
  if (on) {
    if (!s.has_source) throw new Error("This project's video is not on this computer");
    unit.check();
    const ticket = await unit.cloud.createUpload({ kind: "video", name: s.source_name, size: s.size, content_type: s.content_type });
    await finished(a.local, await a.local.publishVideo(pid, ticket.url), a.jobPollMs, onProgress);
    // the upload can outlast the access token: attach with a fresh one, of the same account only
    const later = await a.begin();
    if (!later || later.uid !== unit.uid) throw new AccountSwitched();
    await a.enqueue(async () => {
      later.check();
      const summary = await later.cloud.attachVideo(cloudId, ticket.upload_key);
      await settle(a, later, pid, summary.rev, true);
    });
  } else {
    await a.enqueue(async () => {
      unit.check();
      const summary = await unit.cloud.deleteVideo(cloudId);
      await settle(a, unit, pid, summary.rev, false);
    });
  }
}

// ---- "Sync projects with your account" --------------------------------------------------------------------------

const ENABLED = "vedit.sync.enabled";
let starter: (() => () => void) | null = null;
let stopRunning: (() => void) | null = null;

/** The setting, kept on this computer: on unless it was turned off. */
export function syncEnabled(): boolean {
  try {
    return localStorage.getItem(ENABLED) !== "false";
  } catch {
    return true;
  }
}

/** Turns sync on (it starts at once) or off (it stops: no more requests). */
export function setSyncEnabled(on: boolean): void {
  try {
    localStorage.setItem(ENABLED, on ? "true" : "false");
  } catch {
    /* storage blocked: the choice lasts until the app closes */
  }
  if (!starter) return;
  if (on && !stopRunning) stopRunning = starter();
  else if (!on && stopRunning) {
    stopRunning();
    stopRunning = null;
  }
}

/** Makes sync available, started by `start`, and starts it unless the setting is off. Returns the stop function. */
export function bootSyncWith(start: () => () => void): () => void {
  starter = start;
  update({ available: true });
  if (syncEnabled() && !stopRunning) stopRunning = start();
  return () => {
    stopRunning?.();
    stopRunning = null;
    starter = null;
    update({ available: false });
  };
}

/** Tells sync when someone signs in (or switches account), from the auth store. */
export function authSignIns(fn: () => void): () => void {
  let off = (): void => undefined;
  let gone = false;
  void import("../auth/auth").then(({ useAuth }) => {
    if (gone) return;
    let account = useAuth.getState().account?.id ?? null;
    if (account) fn(); // already signed in when the store loaded
    off = useAuth.subscribe((st) => {
      const next = st.account?.id ?? null;
      if (next === account) return;
      account = next;
      if (next) fn(); // signed in, or switched account
    });
  });
  return () => {
    gone = true;
    off();
  };
}

/** The app's sync: on in the desktop app when VITE_CLOUD_URL is set, as the signed-in account. */
export function bootSync(): () => void {
  if (!isTauri() || isWeb() || !localSync) return () => undefined;
  const cloudFor = createCloudApi();
  if (!cloudFor) return () => undefined;
  const local = localSync;
  return bootSyncWith(() => startSync(local, cloudFor, getSyncSession, signedInUserId, {
    edits: onProjectEdited,
    signIns: authSignIns,
  }));
}
