// Local port of scripts/edl_ops.py (cut/restore/trim/fix_word/patch, kept_ranges) and the helpers it leans on in
// scripts/edl.py (word_range, clip_span) and scripts/common.py (parse_time). The edit screen applies ops here first so
// the timeline answers instantly; the server stays the authority. tests/fixtures/edl_ops_cases.json (written by the
// Python tests) pins this port to the Python output.

import type { ClipView, Edl, EdlClip, EditSection, Transcript } from "../engine/types";

export type Op =
  | { op: "cut" | "restore"; w0: number; w1: number }
  | { op: "trim"; w_first: number; w_last: number }
  | { op: "fix_word"; wrong: string; right: string; prev?: string }
  | { op: "patch"; section: EditSection; patch: Record<string, unknown>; prev?: Record<string, unknown> };

/** edl_ops.OUTPUT_ASPECTS: the shapes an "output" patch may set. */
export const OUTPUT_ASPECTS = ["9:16", "4:5", "1:1", "16:9"] as const;

export const DEFAULT_PAD: [number, number] = [0.1, 0.18];

const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

/** Python's round(): halves go to the even neighbour (Math.round sends them up). */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

/** common.parse_time: 12.5, "12.5", "1:02.5", "01:02:03.25". */
export function parseTime(v: unknown): number {
  if (typeof v === "number") return v;
  let total = 0;
  for (const p of String(v).trim().split(":")) total = total * 60 + Number(p);
  return total;
}

// ---- kept ranges ---------------------------------------------------------------------------------------------

export function wordRange(
  W: Transcript["words"] | undefined, a: number, b: number, pad: [number, number], alias = "main",
): [number, number] {
  if (!W || !W.length) throw new Error(`clip uses 'words' but no transcript for '${alias}'`);
  if (!(a >= 0 && a <= b && b < W.length)) throw new Error(`word range [${a},${b}] out of bounds for '${alias}' (0..${W.length - 1})`);
  let start = W[a].start - pad[0];
  if (a > 0) start = Math.max(start, (W[a - 1].end + W[a].start) / 2);
  let end = W[b].end + pad[1];
  if (b + 1 < W.length) end = Math.min(end, (W[b].end + W[b + 1].start) / 2);
  return [Math.max(0, start), end];
}

/** edl.clip_span: [output duration, source end] once the clip length is snapped to whole output frames. */
export function clipSpan(tin: number, tout: number, speed: number, fps: number): [number, number] {
  if (tout <= tin) throw new Error(`out (${tout}) <= in (${tin})`);
  const frames = Math.max(1, pyRound(((tout - tin) / speed) * fps));
  const dur = frames / fps;
  return [dur, tin + dur * speed];
}

export function keptRanges(edl: Edl, transcript: Transcript): [number, number][] {
  const fps = Number((edl.output as { fps?: number } | undefined)?.fps ?? 30);
  const out: [number, number][] = [];
  for (const c of edl.clips ?? []) {
    let tin: number;
    let tout: number;
    if (c.words) {
      const pad = (c.pad as [number, number] | undefined) ?? (edl.pad as [number, number] | undefined) ?? DEFAULT_PAD;
      [tin, tout] = wordRange(transcript.words, Math.trunc(c.words[0]), Math.trunc(c.words[1]), [pad[0], pad[1]], c.src ?? "main");
    } else {
      tin = parseTime(c.in ?? 0);
      tout = c.out !== undefined ? parseTime(c.out) : Number(transcript.duration ?? tin);
    }
    out.push([tin, clipSpan(tin, tout, Number(c.speed ?? 1), fps)[1]]);
  }
  return out;
}

// ---- word ops ------------------------------------------------------------------------------------------------

function checkRange(w0: number, w1: number): void {
  if (w0 < 0 || w1 < 0 || w0 > w1) throw new Error(`invalid word range [${w0},${w1}]`);
}

export function cutWords(edl: Edl, w0: number, w1: number): Edl {
  checkRange(w0, w1);
  const e = copy(edl);
  const out: EdlClip[] = [];
  for (const c of e.clips) {
    if (!c.words) { out.push(c); continue; }
    const [a, b] = c.words;
    if (b < w0 || a > w1) { out.push(c); continue; }
    if (a < w0) out.push({ ...c, words: [a, w0 - 1] });
    if (b > w1) out.push({ ...c, words: [w1 + 1, b] });
  }
  e.clips = out;
  return e;
}

export function restoreWords(edl: Edl, w0: number, w1: number): Edl {
  checkRange(w0, w1);
  const e = copy(edl);
  const clips = e.clips;
  const words = clips.map((c, i) => [i, c] as const).filter(([, c]) => c.words);
  const src = words.length ? words[0][1].src ?? "main" : "main";
  const same = words.filter(([, c]) => (c.src ?? "main") === src);
  const touching = same.filter(([, c]) => c.words![1] + 1 >= w0 && c.words![0] - 1 <= w1);
  let node: EdlClip;
  let drop = new Set<number>();
  let pos: number;
  if (touching.length) {
    const lo = Math.min(w0, ...touching.map(([, c]) => c.words![0]));
    const hi = Math.max(w1, ...touching.map(([, c]) => c.words![1]));
    node = { ...touching[0][1], words: [lo, hi] };
    drop = new Set(touching.map(([i]) => i));
    pos = touching[0][0];
  } else {
    node = { src, words: [w0, w1] };
    const after = same.filter(([, c]) => c.words![0] > w1).map(([i]) => i);
    pos = after.length ? after[0] : same.length ? same[same.length - 1][0] + 1 : clips.length;
  }
  e.clips = [
    ...clips.slice(0, pos).filter((_, i) => !drop.has(i)),
    node,
    ...clips.slice(pos).filter((_, i) => !drop.has(i + pos)),
  ];
  return e;
}

export function trimWords(edl: Edl, wFirst: number, wLast: number): Edl {
  checkRange(wFirst, wLast);
  const e = copy(edl);
  e.clips = e.clips
    .filter((c) => !c.words || (c.words[1] >= wFirst && c.words[0] <= wLast))
    .map((c) => (c.words ? { ...c, words: [Math.max(c.words[0], wFirst), Math.min(c.words[1], wLast)] as [number, number] } : c));
  return e;
}

// ---- caption fixes and section patches -----------------------------------------------------------------------

export function fixWord(edl: Edl, wrong: string, right: string): Edl {
  const e = copy(edl);
  const captions = (e.captions ??= {}) as { replace?: Record<string, string> };
  const rep = (captions.replace ??= {});
  if (right === wrong) delete rep[wrong];
  else rep[wrong] = right;
  return e;
}

export function patchSection(edl: Edl, section: EditSection, patch: Record<string, unknown>): Edl {
  const e = copy(edl);
  if (section === "captions" || section === "audio" || section === "color") {
    const sec = (e[section] ??= {}) as Record<string, unknown>;
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) delete sec[k];
      else sec[k] = copy(v);
    }
  } else if (section === "framing") {
    const bad = Object.keys(patch).filter((k) => !["crop_x", "zoom", "fit"].includes(k));
    if (bad.length) throw new Error(`unknown framing keys: ${bad.sort().join(", ")}`);
    if ("fit" in patch) {
      if (patch.fit === null) delete e.fit;
      else e.fit = patch.fit;
    }
    for (const c of e.clips) {
      for (const [k, v] of Object.entries(patch)) {
        if (k === "fit") continue;
        if (v === null) delete c[k];
        else c[k] = v;
      }
    }
  } else if (section === "output") {
    const bad = Object.keys(patch).filter((k) => k !== "aspect");
    if (bad.length) throw new Error(`unknown output keys: ${bad.sort().join(", ")}`);
    const out = (e.output ??= {}) as Record<string, unknown>;
    if ("aspect" in patch) {
      const a = patch.aspect;
      if (a === null) delete out.aspect;
      else if (typeof a === "string" && (OUTPUT_ASPECTS as readonly string[]).includes(a)) out.aspect = a;
      else throw new Error(`aspect must be one of ${OUTPUT_ASPECTS.join(", ")}`);
    }
  } else {
    throw new Error(`unknown section '${String(section)}'`);
  }
  return e;
}

export function applyOp(edl: Edl, op: Op): Edl {
  switch (op.op) {
    case "cut": return cutWords(edl, op.w0, op.w1);
    case "restore": return restoreWords(edl, op.w0, op.w1);
    case "trim": return trimWords(edl, op.w_first, op.w_last);
    case "fix_word": return fixWord(edl, op.wrong, op.right);
    case "patch": return patchSection(edl, op.section, op.patch);
  }
}

/** The view after `ops`: the edl through the local port, kept_ranges recomputed when a transcript is at hand. */
export function applyLocal(view: ClipView, ops: Op[], transcript: Transcript | null): ClipView {
  const edl = ops.reduce((e, o) => applyOp(e, o), view.edl);
  return { ...view, edl, kept_ranges: transcript ? keptRanges(edl, transcript) : view.kept_ranges };
}
