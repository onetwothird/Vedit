// Pure helpers for the live preview: mapping between the source video's time and the edited clip's output time,
// finding the active caption, and framing the video inside the chosen shape. No DOM, so all of it is unit-tested.

import type { CSSProperties } from "react";
import type { Aspect, CaptionEvent, CaptionTrack, ClipView, Edl, EdlClip } from "../engine/types";

/** A kept range in source seconds: [start, end) and an optional playback speed (default 1). */
export type Range = [start: number, end: number] | [start: number, end: number, speed: number];

/** A frame is about 1/30 s: leave a range this early so the first frame of the cut never shows. */
const LEAVE_EARLY = 1 / 60;

export const speedOf = (r: Range): number => (r[2] && r[2] > 0 ? r[2] : 1);
/** Seconds a range takes on the output timeline. */
export const rangeLength = (r: Range): number => Math.max(0, r[1] - r[0]) / speedOf(r);
export const outputLength = (ranges: Range[]): number => ranges.reduce((n, r) => n + rangeLength(r), 0);

/** Output time at which range `idx` starts. */
export function outputStart(ranges: Range[], idx: number): number {
  let n = 0;
  for (let i = 0; i < idx && i < ranges.length; i++) n += rangeLength(ranges[i]);
  return n;
}

/** Output time of source time `srcT` while playing range `idx`. */
export function outputAt(ranges: Range[], idx: number, srcT: number): number {
  const r = ranges[idx];
  const into = Math.min(Math.max(srcT, r[0]), r[1]) - r[0];
  return outputStart(ranges, idx) + into / speedOf(r);
}

/** Output time of source time `srcT`, or null when it falls in a cut. A time in two ranges maps through the first. */
export function toOutputTime(ranges: Range[], srcT: number): number | null {
  const i = ranges.findIndex((r) => srcT >= r[0] && srcT < r[1]);
  return i < 0 ? null : outputAt(ranges, i, srcT);
}

/** Which range output time `outT` falls in, and the source time there. Clamps to the first and last frame. */
export function locate(ranges: Range[], outT: number): { idx: number; srcT: number } {
  if (!ranges.length) return { idx: 0, srcT: 0 };
  let start = 0;
  for (let i = 0; i < ranges.length; i++) {
    const len = rangeLength(ranges[i]);
    if (outT < start + len) return { idx: i, srcT: ranges[i][0] + Math.max(0, outT - start) * speedOf(ranges[i]) };
    start += len;
  }
  const last = ranges.length - 1;
  return { idx: last, srcT: ranges[last][1] };
}

export const toSourceTime = (ranges: Range[], outT: number): number => locate(ranges, outT).srcT;

/** The source time to play from at or after `srcT`: itself inside a kept range, else the start of the next one. */
export function nextKeptTime(ranges: Range[], srcT: number): number | null {
  if (ranges.some((r) => srcT >= r[0] && srcT < r[1])) return srcT;
  let next: number | null = null;
  for (const r of ranges) if (r[0] > srcT && (next === null || r[0] < next)) next = r[0];
  return next;
}

export type Step =
  | { kind: "stay"; out: number }
  | { kind: "jump"; idx: number; srcT: number }
  | { kind: "end"; out: number };

/**
 * One video frame at source time `srcT` while playing range `idx`: keep going, jump to the start of the next range in
 * play order (which need not be later in the source), or end after the last one. A frame from before the range starts is
 * a stale frame from just before a seek; it stays at the range's first moment.
 */
export function advance(ranges: Range[], idx: number, srcT: number): Step {
  if (srcT >= ranges[idx][1] - LEAVE_EARLY) {
    if (idx + 1 < ranges.length) return { kind: "jump", idx: idx + 1, srcT: ranges[idx + 1][0] };
    return { kind: "end", out: outputLength(ranges) };
  }
  return { kind: "stay", out: outputAt(ranges, idx, srcT) };
}

/** Ranges of a clip view: kept_ranges[i] belongs to the i-th clip of the edl, which carries its speed. */
export function viewRanges(view: ClipView): Range[] {
  return view.kept_ranges.map(([s, e], i): Range => {
    const speed = Number(view.edl.clips[i]?.speed);
    return speed > 0 && speed !== 1 ? [s, e, speed] : [s, e];
  });
}

// ---- captions ---------------------------------------------------------------------------------------------------

/** The caption group on screen at track time `outT`, and which of its words is being spoken (-1 between words). */
export function activeCaption(track: CaptionTrack, outT: number): { event: CaptionEvent; activeWord: number } | null {
  const event = track.events.find((e) => outT >= e.start && outT < e.end);
  if (!event) return null;
  return { event, activeWord: event.words.findIndex((w) => outT >= w.hl_start && outT < w.hl_end) };
}

/**
 * Caption times are on the exported timeline, which starts with the intro. The preview skips bumpers, so its clock is
 * `lead_in` seconds behind the captions'.
 */
export const captionClock = (track: Pick<CaptionTrack, "lead_in">, outT: number): number => outT + (track.lead_in ?? 0);

// ---- framing ----------------------------------------------------------------------------------------------------

const ratio = (a: Aspect): number => {
  const [w, h] = a.split(":").map(Number);
  return w / h;
};
const pct = (f: number): string => `${Math.round(f * 1e6) / 1e4}%`;
const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n));

/**
 * Style for the <video> inside a frame of shape `aspect` (the frame is position:relative, overflow:hidden). The video
 * covers the frame (times `zoom`); horizontally it slides so the subject at `cropX` (0..1 of the source width) is
 * centred, never past either edge; vertically it is centred.
 */
export function cropStyle(aspect: Aspect, srcW: number, srcH: number, cropX: number, zoom: number): CSSProperties {
  const A = ratio(aspect);
  const S = srcW > 0 && srcH > 0 ? srcW / srcH : A;
  const z = Math.max(1, zoom || 1);
  const wide = S >= A;
  const w = wide ? (z * S) / A : z; // video size as a fraction of the frame
  const h = wide ? z : (z * A) / S;
  const left = clamp(0.5 - clamp(cropX, 0, 1) * w, 1 - w, 0);
  return { position: "absolute", width: pct(w), height: pct(h), left: pct(left), top: pct((1 - h) / 2), maxWidth: "none" };
}

/** The share of the frame height the platforms' own buttons and text usually cover, at the top and at the bottom. */
export function safeZoneBands(aspect: Aspect): { top: number; bottom: number } {
  return aspect === "9:16" ? { top: 0.12, bottom: 0.2 } : { top: 0.08, bottom: 0.08 };
}

// ---- what the preview leaves out ----------------------------------------------------------------------------------

const rec = (v: unknown): Record<string, unknown> | undefined =>
  v && typeof v === "object" ? (v as Record<string, unknown>) : undefined;

/** Music and colour grading happen in the export; so do the intro and outro. The preview shows none of them. */
export function exportOnly(edl: Edl): { music: boolean; color: boolean; bumpers: boolean } {
  return {
    music: Boolean(rec(edl.audio)?.music),
    color: Boolean(rec(edl.color)?.lut) || edl.clips.some((c) => Boolean(c.lut)),
    bumpers: Boolean(edl.intro || edl.outro),
  };
}

/** Where the subject is (0..1) and the zoom for the clip being played. */
export function clipFraming(clip: EdlClip | undefined): { cropX: number; zoom: number } {
  const cropX = typeof clip?.crop_x === "number" ? clip.crop_x : 0.5;
  const zoom = typeof clip?.zoom === "number" ? clip.zoom : 1;
  return { cropX, zoom };
}
