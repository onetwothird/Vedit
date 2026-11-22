// Clip edit store: optimistic local edits over the engine's EDL, with exact undo/redo.
//
// Ops are applied to the local view at once (timeline.ts) and sent to the engine one at a time; the rest wait in a queue.
// Each server reply reconciles `view`. A failed op rolls the view and history back to the last server-confirmed state,
// drops the failed entry and everything queued behind it, and sets `error`. undo/redo ride the same queue.

import { create, type StoreApi, type UseBoundStore } from "zustand";
import { engine as defaultEngine } from "../engine/client";
import type { ClipOp, ClipView, Engine, Transcript } from "../engine/types";
import { withInverse, type HistoryEntry } from "./inverse";
import { applyLocal, type Op } from "./timeline";

export type { HistoryEntry } from "./inverse";
export type { Op } from "./timeline";

export interface ClipState {
  pid: string | null;
  hid: string | null;
  view: ClipView | null;
  /** Applied edits, oldest first; each carries the ops that undo it. */
  past: HistoryEntry[];
  future: HistoryEntry[];
  saving: boolean;
  error?: string;
  load: (pid: string, hid: string) => Promise<void>;
  apply: (op: Op) => void;
  undo: () => void;
  redo: () => void;
}

type ClipEngine = Pick<Engine, "clip" | "patchClip" | "transcript">;

type Job =
  | { kind: "ops"; gen: number; ops: Op[]; past: HistoryEntry[]; future: HistoryEntry[] } // past/future: history once confirmed
  | { kind: "load"; gen: number; pid: string; hid: string };

/** The wire form of an op: the store's own `prev` bookkeeping stays local. */
function wire(op: Op): ClipOp {
  if (op.op === "fix_word") return { op: "fix_word", wrong: op.wrong, right: op.right };
  if (op.op === "patch") return { op: "patch", section: op.section, patch: op.patch };
  return op;
}

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function createClipStore(eng: ClipEngine): UseBoundStore<StoreApi<ClipState>> {
  let generation = 0; // bumped when a different clip is loaded: work for the old clip is dropped
  let transcript: Transcript | null = null;
  let queue: Job[] = []; // everything that talks to the engine (ops, loads) runs here, one at a time
  let running = false;
  let rollingBack = false;
  let deferred: (() => void)[] = []; // edits made during a rollback wait for it
  let confirmed: { view: ClipView; past: HistoryEntry[]; future: HistoryEntry[] } | null = null;

  return create<ClipState>()((set, get) => {
    const hasOps = () => queue.some((j) => j.kind === "ops");
    const sync = () => set({ saving: hasOps() || rollingBack });

    async function runLoad(job: Extract<Job, { kind: "load" }>): Promise<void> {
      try {
        const [view, tr] = await Promise.all([eng.clip(job.pid, job.hid), eng.transcript(job.pid).catch(() => null)]);
        if (job.gen !== generation) return;
        transcript = tr;
        confirmed = { view, past: confirmed?.past ?? [], future: confirmed?.future ?? [] };
        set({ view });
      } catch (e) {
        if (job.gen === generation) set({ error: message(e) });
      }
    }

    async function runOps(job: Extract<Job, { kind: "ops" }>): Promise<void> {
      const { pid, hid } = get();
      let sent = 0;
      try {
        let reply: ClipView | undefined;
        for (const op of job.ops) {
          reply = await eng.patchClip(pid!, hid!, wire(op));
          sent++;
        }
        if (job.gen !== generation) return;
        queue.shift();
        confirmed = { view: reply!, past: job.past, future: job.future };
        // while ops are still queued the optimistic view is ahead of this reply, so keep it
        set((s) => ({ view: hasOps() ? s.view : reply! }));
      } catch (e) {
        if (job.gen !== generation) return;
        rollingBack = true;
        queue = []; // the failed op and everything queued behind it
        set({ saving: true });
        let { view, past, future } = confirmed!;
        if (sent > 0) {
          // a half-sent multi-op undo: show what the server really has, and start the history over
          view = await eng.clip(pid!, hid!).catch(() => view);
          past = [];
          future = [];
        }
        if (job.gen !== generation) return; // another clip was loaded meanwhile
        confirmed = { view, past, future };
        rollingBack = false;
        set({ view, past, future, saving: false, error: message(e) });
        const waiting = deferred;
        deferred = [];
        waiting.forEach((fn) => fn());
      }
    }

    async function pump(): Promise<void> {
      if (running) return;
      running = true;
      try {
        while (queue.length) {
          const job = queue[0];
          if (job.gen !== generation) { queue.shift(); continue; }
          if (job.kind === "load") {
            await runLoad(job);
            if (queue[0] === job) queue.shift();
          } else {
            await runOps(job);
          }
        }
      } finally {
        running = false;
        if (!rollingBack) sync();
      }
    }

    function enqueue(ops: Op[], past: HistoryEntry[], future: HistoryEntry[]): void {
      const { view } = get();
      if (!view) return;
      let next: ClipView;
      try {
        next = applyLocal(view, ops, transcript);
      } catch (e) {
        set({ error: message(e) });
        return;
      }
      queue.push({ kind: "ops", gen: generation, ops, past, future });
      set({ view: next, past, future, saving: true, error: undefined });
      void pump();
    }

    const whenSettled = (fn: () => void): void => {
      if (rollingBack) deferred.push(fn);
      else fn();
    };

    return {
      pid: null, hid: null, view: null, past: [], future: [], saving: false, error: undefined,

      load(pid, hid) {
        const same = get().pid === pid && get().hid === hid;
        const job: Job = { kind: "load", gen: same ? generation : ++generation, pid, hid };
        if (same) {
          queue.push(job); // a refresh behind this clip's pending edits
        } else {
          queue = [job]; // the old clip's pending ops are dropped
          deferred = [];
          rollingBack = false;
          transcript = null;
          confirmed = null;
          set({ pid, hid, view: null, past: [], future: [], saving: false, error: undefined });
        }
        const done = new Promise<void>((resolve) => {
          const poll = () => (queue.includes(job) ? setTimeout(poll, 0) : resolve());
          poll();
        });
        void pump();
        return done;
      },

      apply(op) {
        whenSettled(() => {
          const { view, past } = get();
          if (!view) return;
          let entry: HistoryEntry;
          try {
            entry = withInverse(view.edl, op);
            applyLocal(view, [op], null); // reject a bad op before it touches history
          } catch (e) {
            set({ error: message(e) });
            return;
          }
          enqueue([entry.op], [...past, entry], []);
        });
      },

      undo() {
        whenSettled(() => {
          const { past, future } = get();
          const entry = past[past.length - 1];
          if (entry) enqueue(entry.inverse, past.slice(0, -1), [entry, ...future]);
        });
      },

      redo() {
        whenSettled(() => {
          const { past, future } = get();
          const [entry, ...rest] = future;
          if (entry) enqueue([entry.op], [...past, entry], rest);
        });
      },
    };
  });
}

export const useClipStore = createClipStore(defaultEngine);
