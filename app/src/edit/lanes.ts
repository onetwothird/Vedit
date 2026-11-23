// Pure layout helpers for the Edit screen's timeline: what each lane draws, in source seconds of the clip's own words.
// A word owns the time from its start to the next word's start (the last one to its end), so kept runs and cut gaps
// tile the clip without holes.

import type { CaptionTrack, TranscriptWord } from "../engine/types";
import { captionClock, toSourceTime, type Range } from "../preview/playback";

export interface LaneSeg {
  kind: "kept" | "gap";
  w0: number;
  w1: number;
  start: number;
  end: number;
}

/** Source time a word owns inside the clip's word range w0..w1. */
export function wordSlot(words: readonly TranscriptWord[], i: number, w1: number): [number, number] {
  const end = i < w1 && words[i + 1] ? words[i + 1].start : words[i].end;
  return [words[i].start, Math.max(words[i].start, end)];
}

/** Kept runs and cut gaps of words w0..w1, in order. */
export function laneSegments(words: readonly TranscriptWord[], w0: number, w1: number, kept: ReadonlySet<number>): LaneSeg[] {
  const out: LaneSeg[] = [];
  for (let i = w0; i <= w1 && i < words.length; i++) {
    const kind = kept.has(i) ? "kept" : "gap";
    const [start, end] = wordSlot(words, i, w1);
    const last = out[out.length - 1];
    if (last && last.kind === kind) {
      last.w1 = i;
      last.end = end;
    } else {
      out.push({ kind, w0: i, w1: i, start, end });
    }
  }
  return out;
}

/** The word in w0..w1 whose start (a start edge) or end (an end edge) is nearest to source time `t`. */
export function snapToWord(words: readonly TranscriptWord[], w0: number, w1: number, t: number, edge: "start" | "end"): number {
  let best = w0;
  let dist = Infinity;
  for (let i = w0; i <= w1 && i < words.length; i++) {
    const d = Math.abs((edge === "start" ? words[i].start : words[i].end) - t);
    if (d < dist) {
      dist = d;
      best = i;
    }
  }
  return best;
}

/** Ruler ticks from 0 to `span` seconds, on a 1/2/5/10/15/30/60 s step that keeps them at least 56 px apart. */
export function rulerTicks(span: number, pxPerSec: number, minPx = 56): number[] {
  const steps = [1, 2, 5, 10, 15, 30, 60, 120, 300];
  const step = steps.find((s) => s * pxPerSec >= minPx) ?? steps[steps.length - 1];
  const out: number[] = [];
  for (let t = 0; t <= span + 1e-9; t += step) out.push(Math.round(t * 1000) / 1000);
  return out;
}

/**
 * A level per bar (0..1) for the video lane's waveform. The engine keeps its measured loudness to itself (no route
 * serves energy.main.json), so this draws speech from the transcript: a bar is as loud as the share of it covered by
 * words, with a steady per-word wobble so it reads as audio. Fillers sit lower.
 */
export function speechEnergy(words: readonly TranscriptWord[], t0: number, t1: number, bars: number): number[] {
  const n = Math.max(0, Math.floor(bars));
  const w = n ? (t1 - t0) / n : 0;
  const out: number[] = [];
  let j = 0;
  for (let k = 0; k < n; k++) {
    const a = t0 + k * w, b = a + w;
    while (j < words.length && words[j].end <= a) j++;
    let covered = 0;
    let level = 0;
    for (let i = j; i < words.length && words[i].start < b; i++) {
      const o = Math.min(b, words[i].end) - Math.max(a, words[i].start);
      if (o <= 0) continue;
      covered += o;
      const wobble = 0.65 + 0.35 * Math.abs(Math.sin((i + 1) * 1.7 + k * 0.9));
      level = Math.max(level, (words[i].filler ? 0.55 : 1) * wobble);
    }
    const share = w > 0 ? Math.min(1, covered / w) : 0;
    out.push(Math.round((0.08 + 0.92 * share * level) * 1000) / 1000);
  }
  return out;
}

/** Caption events (output time, after the intro's lead-in) placed at source time through the clip's kept ranges. */
export function captionSpans(track: Pick<CaptionTrack, "events" | "lead_in">, ranges: Range[]): { start: number; end: number; text: string }[] {
  const lead = captionClock(track, 0);
  return track.events.map((e) => {
    const start = toSourceTime(ranges, e.start - lead);
    // an end exactly on a range boundary belongs to the range it closes, not to the start of the next one
    const eps = Math.min(1e-3, (e.end - e.start) / 2);
    const end = Math.max(start, toSourceTime(ranges, e.end - eps - lead) + eps);
    return { start: round(start), end: round(end), text: e.words.map((w) => w.text).join(" ") };
  });
}

const round = (n: number): number => Math.round(n * 1e4) / 1e4;

/** A piece of the kept video: a kept run, further divided where the person split it. */
export interface Piece { w0: number; w1: number; start: number; end: number }

/** The kept runs of `segs`, each cut into pieces at every split word (a split word starts a new piece). */
export function lanePieces(words: readonly TranscriptWord[], segs: readonly LaneSeg[], splits: ReadonlySet<number>, w1: number): Piece[] {
  const out: Piece[] = [];
  for (const s of segs) {
    if (s.kind !== "kept") continue;
    let a = s.w0;
    for (let i = s.w0 + 1; i <= s.w1; i++) {
      if (!splits.has(i)) continue;
      out.push({ w0: a, w1: i - 1, start: words[a].start, end: wordSlot(words, i - 1, w1)[1] });
      a = i;
    }
    out.push({ w0: a, w1: s.w1, start: words[a].start, end: s.end });
  }
  return out;
}

/** The word a split at `t` would start, or null when `t` is not inside a piece (a split at a piece's first word
    changes nothing). */
export function splitWordAt(words: readonly TranscriptWord[], pieces: readonly Piece[], t: number): number | null {
  const p = pieces.find((q) => t >= q.start && t < q.end);
  if (!p) return null;
  const w = snapToWord(words, p.w0, p.w1, t, "start");
  return w > p.w0 ? w : null;
}
