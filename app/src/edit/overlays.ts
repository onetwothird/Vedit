import type { Overlay } from "../engine/types";
import { brollType } from "../lib/overlayMedia";

/* Making text and b-roll layers. The timeline and the Text and B-roll tools both use these, so a layer added or
   copied from either place lands the same way. Times are source seconds on the main video; t0..t1 is the clip. */

export const MIN_LAYER = 0.3; // seconds
export const NEW_LAYER = 3; // seconds a new text or b-roll lasts

export const newLayerId = () => `o${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

const r2 = (n: number) => Math.round(n * 100) / 100;

/** Where a new layer starts: the playhead, held inside the clip with room for half a second. */
function startAt(playhead: number, t0: number, t1: number): number {
  return Math.min(Math.max(t0, playhead), Math.max(t0, t1 - 0.5));
}
const lengthAt = (at: number, t1: number) => Math.max(0.5, Math.min(NEW_LAYER, t1 - at));

export function newText(text: string, playhead: number, t0: number, t1: number): Overlay {
  const at = startAt(playhead, t0, t1);
  return { id: newLayerId(), type: "text", at: { src: "main", t: r2(at) }, duration: r2(lengthAt(at, t1)), text, position: "top" };
}

export function newBroll(path: string, playhead: number, t0: number, t1: number): Overlay {
  const at = startAt(playhead, t0, t1);
  return { id: newLayerId(), type: brollType(path), at: { src: "main", t: r2(at) }, duration: r2(lengthAt(at, t1)), path, mode: "full" };
}

/** B-roll at a set moment (a suggestion the user accepted), held inside the clip t0..t1. */
export function brollAt(path: string, type: "image" | "video", at: number, duration: number, t0: number, t1: number): Overlay {
  const start = startAt(at, t0, t1);
  return {
    id: newLayerId(), type, at: { src: "main", t: r2(start) },
    duration: r2(Math.max(MIN_LAYER, Math.min(duration, t1 - start))), path, mode: "full",
  };
}

/** A copy that starts where the layer ends (as far as the clip allows), with the same settings. */
export function duplicateLayer(o: Overlay, t0: number, t1: number): Overlay {
  const at = Math.max(t0, Math.min(o.at.t + o.duration, t1 - MIN_LAYER));
  return { ...o, id: newLayerId(), at: { ...o.at, t: r2(at) }, duration: r2(Math.max(MIN_LAYER, Math.min(o.duration, t1 - at))) };
}

/** Layers without an id (written by hand or by an older build) get one, so they can be selected and dragged. */
export function withIds(overlays: readonly Overlay[]): Overlay[] {
  return overlays.map((o, i) => (o.id ? o : { ...o, id: `o-${i}` }));
}
