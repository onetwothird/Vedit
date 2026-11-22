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

interface Job {
  ops: Op[];
  /** History once this job has been confirmed. */
  past: HistoryEntry[];
  future: HistoryEntry[];
}

/** The wire form of an op: the store's own `prev` bookkeeping stays local. */
function wire(op: Op): ClipOp {
  if (op.op === "fix_word") return { op: "fix_word", wrong: op.wrong, right: op.right };
  if (op.op === "patch") return { op: "patch", section: op.section, patch: op.patch };
  return op;
}

const message = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export function createClipStore(eng: ClipEngine): UseBoundStore<StoreApi<ClipState>> {
  let generation = 0; // bumped by load(): replies for an older clip are ignored
  let transcript: Transcript | null = null;
  let queue: Job[] = [];
  let running = false;
  let confirmed: { view: ClipView; past: HistoryEntry[]; future: HistoryEntry[] } | null = null;

  return create<ClipState>()((set, get) => {
    async function pump(): Promise<void> {
      if (running) return;
      running = true;
      const gen = generation;
      try {
        while (queue.length && gen === generation) {
          const job = queue[0];
          const { pid, hid } = get();
          let sent = 0;
          try {
            let reply: ClipView | undefined;
            for (const op of job.ops) {
              reply = await eng.patchClip(pid!, hid!, wire(op));
              sent++;
              if (gen !== generation) return;
            }
            queue.shift();
            confirmed = { view: reply!, past: job.past, future: job.future };
            // while ops are still queued the optimistic view is ahead of this reply, so keep it
            set((s) => ({ view: queue.length ? s.view : reply!, saving: queue.length > 0 }));
          } catch (e) {
            if (gen !== generation) return;
            queue = [];
            let view = confirmed!.view;
            if (sent > 0) view = await eng.clip(pid!, hid!).catch(() => view); // a half-sent undo: show what the server has
            if (gen !== generation) return;
            set({ view, past: confirmed!.past, future: confirmed!.future, saving: false, error: message(e) });
          }
        }
      } finally {
        running = false;
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
      queue.push({ ops, past, future });
      set({ view: next, past, future, saving: true, error: undefined });
      void pump();
    }

    return {
      pid: null, hid: null, view: null, past: [], future: [], saving: false, error: undefined,

      async load(pid, hid) {
        const gen = ++generation;
        queue = [];
        confirmed = null;
        transcript = null;
        set({ pid, hid, view: null, past: [], future: [], saving: false, error: undefined });
        try {
          const [view, tr] = await Promise.all([eng.clip(pid, hid), eng.transcript(pid).catch(() => null)]);
          if (gen !== generation) return;
          transcript = tr;
          confirmed = { view, past: [], future: [] };
          set({ view });
        } catch (e) {
          if (gen === generation) set({ error: message(e) });
        }
      },

      apply(op) {
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
      },

      undo() {
        const { past, future } = get();
        const entry = past[past.length - 1];
        if (!entry) return;
        enqueue(entry.inverse, past.slice(0, -1), [entry, ...future]);
      },

      redo() {
        const { past, future } = get();
        const [entry, ...rest] = future;
        if (!entry) return;
        enqueue([entry.op], [...past, entry], rest);
      },
    };
  });
}

export const useClipStore = createClipStore(defaultEngine);
