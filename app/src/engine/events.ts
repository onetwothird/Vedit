// Live job state: one shared WebSocket (or the mock's emitter) feeding useJobs() / useJob().

import { useCallback, useSyncExternalStore } from "react";
import { engine, fetchEngineInfo, isMock, onJobResult } from "./client";
import { isCloud } from "./flavor";
import { mockEngine } from "./mock";
import type { EngineInfo, Job } from "./types";

export const BACKOFF_MS = [1000, 2000, 4000, 8000, 10000];
/** The web build's reconnect schedule: 1 s, 2 s, 4 s ... capped at 15 s. */
export const CLOUD_BACKOFF_MS = [1000, 2000, 4000, 8000, 15000];
const TERMINAL = ["done", "failed", "cancelled"];

/** The /events websocket address for an engine: wss from a cloud origin, else the local engine's port. */
export function eventsUrl(info: EngineInfo): string {
  const root = info.base ? info.base.replace(/\/+$/, "").replace(/^http/i, "ws") : `ws://127.0.0.1:${info.port}`;
  return `${root}/events?token=${encodeURIComponent(info.token)}`;
}

export interface SocketLike {
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  close(): void;
}

export interface JobStoreOptions {
  /** `fresh` is true on a reconnect: ask the host for the engine's current port and token again. */
  getInfo: (fresh: boolean) => Promise<EngineInfo>;
  createSocket: (url: string) => SocketLike;
  /** Re-reads a job the socket may have missed while it was down. */
  resync?: (id: string) => Promise<Job>;
  /** Reconnect delays in ms; the last one repeats. Default BACKOFF_MS. */
  backoff?: number[];
  /** Cloud: after a reconnect, re-read every active job (GET /jobs), since events may have been missed while down. */
  resyncAll?: () => Promise<Job[]>;
  /** Live source instead of a socket (the mock engine). Returns its unsubscribe. */
  subscribeSource?: (onJob: (job: Job) => void) => () => void;
}

export type JobSource = "ws" | "http";

export interface JobStore {
  subscribe(listener: () => void): () => void;
  getJobs(): ReadonlyMap<string, Job>;
  getJob(id: string | undefined): Job | undefined;
  /** `ws` (default): a socket event, authoritative. `http`: a route answer, which may predate finalization. */
  upsert(job: Job, source?: JobSource): void;
}

const rank = (j: Job): number => (TERMINAL.includes(j.state) ? 2 : j.state === "running" ? 1 : 0);

const same = (a: Job, b: Job): boolean =>
  a.state === b.state && a.stage === b.stage && a.pct === b.pct && a.message === b.message && a.error === b.error
  && JSON.stringify(a.result) === JSON.stringify(b.result);

export function createJobStore(opts: JobStoreOptions): JobStore {
  let jobs: ReadonlyMap<string, Job> = new Map();
  const listeners = new Set<() => void>();
  let socket: SocketLike | null = null;
  let stopSource: (() => void) | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;
  let opened = false; // a socket has opened before, so the next open is a reconnect
  let generation = 0; // bumps on every stop, so a late async step of an old connection does nothing

  function upsert(job: Job, source: JobSource = "ws"): void {
    const old = jobs.get(job.id);
    let next = job;
    if (old) {
      const a = rank(old), b = rank(job);
      if (b < a) return; // a stale snapshot never moves a job backwards
      if (a === 2) {
        // terminal: the socket is authoritative; an HTTP answer may predate finalization, so it never replaces
        if (source === "http") return;
      } else if (a === 1 && b === 1) {
        if (job.pct < old.pct) return; // keep the newer stage/message and the higher pct
        next = { ...job, pct: Math.max(job.pct, old.pct) };
      }
      if (same(old, next)) return;
    }
    const map = new Map(jobs);
    map.set(next.id, next);
    jobs = map;
    listeners.forEach((l) => l());
  }

  function onMessage(ev: { data: unknown }): void {
    if (typeof ev.data !== "string") return;
    let msg: unknown;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    const m = msg as { type?: string; job?: Job };
    if (m && m.type === "job" && m.job && typeof m.job.id === "string") upsert(m.job);
  }

  function resyncOpen(): void {
    const resync = opts.resync;
    if (!resync) return;
    for (const old of jobs.values()) {
      if (TERMINAL.includes(old.state)) continue;
      resync(old.id).then(
        (fresh) => {
          if (jobs.get(old.id) === old) upsert(fresh, "http"); // skip if a socket event already moved it on
        },
        (err: unknown) => {
          // the engine restarted and forgot the job: it can never finish
          if ((err as { status?: number } | null)?.status === 404 && jobs.get(old.id) === old) {
            upsert({ ...old, state: "failed", error: "interrupted" });
          }
        },
      );
    }
  }

  async function resyncAll(gen: number): Promise<void> {
    if (!opts.resyncAll) return;
    try {
      const list = await opts.resyncAll();
      if (gen === generation) list.forEach((j) => upsert(j, "http"));
    } catch {
      // the socket's own snapshots still arrive; the next reconnect tries again
    }
  }

  function scheduleReconnect(gen: number): void {
    if (gen !== generation || timer) return;
    const schedule = opts.backoff ?? BACKOFF_MS;
    const delay = schedule[Math.min(attempt, schedule.length - 1)];
    attempt++;
    timer = setTimeout(() => {
      timer = null;
      void connect(gen, true);
    }, delay);
  }

  async function connect(gen: number, fresh: boolean): Promise<void> {
    let info: EngineInfo;
    try {
      info = await opts.getInfo(fresh);
    } catch {
      scheduleReconnect(gen);
      return;
    }
    if (gen !== generation) return;
    let ws: SocketLike;
    try {
      ws = opts.createSocket(eventsUrl(info));
    } catch {
      scheduleReconnect(gen);
      return;
    }
    socket = ws;
    ws.onopen = () => {
      if (gen !== generation || socket !== ws) return;
      attempt = 0; // backoff restarts after a successful open
      const reconnect = opened;
      opened = true;
      resyncOpen();
      if (reconnect) void resyncAll(gen);
    };
    ws.onmessage = (ev) => {
      if (socket === ws) onMessage(ev);
    };
    ws.onerror = () => undefined; // a close always follows
    ws.onclose = () => {
      if (socket !== ws) return;
      socket = null;
      scheduleReconnect(gen);
    };
  }

  function start(): void {
    const gen = ++generation;
    if (opts.subscribeSource) {
      stopSource = opts.subscribeSource(upsert);
      return;
    }
    attempt = 0;
    opened = false;
    void connect(gen, false);
  }

  function stop(): void {
    generation++;
    if (timer) clearTimeout(timer);
    timer = null;
    stopSource?.();
    stopSource = null;
    const ws = socket;
    socket = null;
    if (ws) {
      ws.onclose = ws.onmessage = ws.onopen = ws.onerror = null;
      try {
        ws.close();
      } catch {
        // already closed
      }
    }
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) start();
      return () => {
        if (!listeners.delete(listener)) return;
        if (listeners.size === 0) stop();
      };
    },
    getJobs: () => jobs,
    getJob: (id) => (id === undefined ? undefined : jobs.get(id)),
    upsert,
  };
}

// ---- the app-wide store and hooks -----------------------------------------------------------------------------

export const jobStore: JobStore = createJobStore({
  getInfo: () => fetchEngineInfo(),
  createSocket: (url) => new WebSocket(url) as unknown as SocketLike,
  resync: (id) => engine.getJob(id),
  ...(isCloud() ? { backoff: CLOUD_BACKOFF_MS, resyncAll: () => engine.activeJobs() } : {}),
  subscribeSource: isMock() ? (onJob) => mockEngine.onJob(onJob) : undefined,
});

onJobResult((job) => jobStore.upsert(job, "http"));

/** Every job the engine has told us about. Re-renders when any job changes. */
export function useJobs(): ReadonlyMap<string, Job> {
  return useSyncExternalStore(jobStore.subscribe, jobStore.getJobs);
}

/** One job. Re-renders only when that job changes. */
export function useJob(id: string | undefined): Job | undefined {
  const get = useCallback(() => jobStore.getJob(id), [id]);
  return useSyncExternalStore(jobStore.subscribe, get);
}
