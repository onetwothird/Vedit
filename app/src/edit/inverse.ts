// Exact inverses for undo. cut/restore/trim are inverted from the kept-word sets before and after the op, so the round
// trip restores the kept word set (and so kept_ranges) however much of the range was already cut.

import type { Edl } from "../engine/types";
import { applyOp, type Op } from "./timeline";

export interface HistoryEntry {
  op: Op;
  inverse: Op[];
}

export function keptWords(edl: Edl): Set<number> {
  const s = new Set<number>();
  for (const c of edl.clips) if (c.words) for (let i = c.words[0]; i <= c.words[1]; i++) s.add(i);
  return s;
}

function runs(words: Iterable<number>): [number, number][] {
  const out: [number, number][] = [];
  for (const w of [...words].sort((a, b) => a - b)) {
    const last = out[out.length - 1];
    if (last && w === last[1] + 1) last[1] = w;
    else out.push([w, w]);
  }
  return out;
}

function previous(edl: Edl, op: Extract<Op, { op: "patch" }>): Record<string, unknown> {
  const prev: Record<string, unknown> = {};
  for (const k of Object.keys(op.patch)) {
    let v: unknown;
    if (op.section === "overlays") v = edl.overlays;
    else if (op.section === "framing") v = k === "fit" ? edl.fit : (edl.clips[0] as Record<string, unknown> | undefined)?.[k];
    else v = ((edl[op.section] ?? {}) as Record<string, unknown>)[k];
    prev[k] = v === undefined ? null : v; // null = the key was absent; the server drops it
  }
  return prev;
}

/** The op as stored in history (prev filled in) and the ops that undo it, computed against `edl` (the state before). */
export function withInverse(edl: Edl, op: Op): HistoryEntry {
  switch (op.op) {
    case "fix_word": {
      const old = (edl.captions as { replace?: Record<string, string> } | undefined)?.replace?.[op.wrong];
      const prev = op.prev ?? old;
      return {
        op: { ...op, ...(prev !== undefined ? { prev } : {}) },
        inverse: [{ op: "fix_word", wrong: op.wrong, right: prev ?? op.wrong }],
      };
    }
    case "patch": {
      const prev = op.prev ?? previous(edl, op);
      return { op: { ...op, prev }, inverse: [{ op: "patch", section: op.section, patch: prev }] };
    }
    default: {
      const before = keptWords(edl);
      const after = keptWords(applyOp(edl, op));
      const restore = runs([...before].filter((w) => !after.has(w))).map(([w0, w1]): Op => ({ op: "restore", w0, w1 }));
      const cut = runs([...after].filter((w) => !before.has(w))).map(([w0, w1]): Op => ({ op: "cut", w0, w1 }));
      return { op, inverse: [...restore, ...cut] };
    }
  }
}
