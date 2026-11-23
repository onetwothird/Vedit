import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Overlay } from "../engine/types";
import { t } from "../i18n/t";
import { baseName } from "../lib/native";

export const MIN_OVERLAY = 0.3; // seconds
const NUDGE = 0.1; // seconds per arrow key (Shift: 1 s)

type Grab = { id: string; kind: "move" | "start" | "end"; x0: number; at: number; dur: number };

/** Move an overlay to start at `at` (seconds of source time), kept inside t0..t1. */
export function moveOverlay(o: Overlay, at: number, t0: number, t1: number): Overlay {
  const t = Math.min(Math.max(t0, at), Math.max(t0, t1 - MIN_OVERLAY));
  return { ...o, at: { ...o.at, t: Math.round(t * 100) / 100 } };
}

/** Change an overlay's start or end edge, keeping the other edge and at least MIN_OVERLAY of length. */
export function resizeOverlay(o: Overlay, edge: "start" | "end", to: number, t0: number, t1: number): Overlay {
  const a = o.at.t;
  const b = a + o.duration;
  if (edge === "end") {
    const end = Math.min(t1, Math.max(a + MIN_OVERLAY, to));
    return { ...o, duration: Math.round((end - a) * 100) / 100 };
  }
  const start = Math.max(t0, Math.min(b - MIN_OVERLAY, to));
  return { ...o, at: { ...o.at, t: Math.round(start * 100) / 100 }, duration: Math.round((b - start) * 100) / 100 };
}

/** One lane of overlays (text or b-roll) on the timeline: drag a block to move it, drag its edges to change when it
    starts and ends, click to select it. A drag is one edit (one undo step), sent when the pointer lets go. */
export default function OverlayLane({ kind, overlays, x, pps, t0, t1, selected, onSelect, onChange }: {
  kind: "text" | "broll";
  overlays: readonly Overlay[];
  x(s: number): number;
  pps: number;
  t0: number;
  t1: number;
  selected: string | null;
  onSelect(id: string | null): void;
  onChange(next: Overlay): void;
}) {
  const grab = useRef<Grab | null>(null);
  const [draft, setDraft] = useState<Overlay | null>(null);
  const mine = overlays.filter((o) => (kind === "text" ? o.type === "text" : o.type !== "text"));

  function place(o: Overlay, e: PointerEvent<HTMLElement>): Overlay {
    const g = grab.current!;
    const d = (e.clientX - g.x0) / pps;
    const base = { ...o, at: { ...o.at, t: g.at }, duration: g.dur };
    if (g.kind === "move") return moveOverlay(base, g.at + d, t0, t1 - g.dur + MIN_OVERLAY);
    return resizeOverlay(base, g.kind, g.kind === "end" ? g.at + g.dur + d : g.at + d, t0, t1);
  }

  function down(o: Overlay, part: Grab["kind"], e: PointerEvent<HTMLElement>) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault();
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* synthetic events have no pointer id */
    }
    onSelect(o.id!);
    grab.current = { id: o.id!, kind: part, x0: e.clientX, at: o.at.t, dur: o.duration };
  }
  function move(o: Overlay, e: PointerEvent<HTMLElement>) {
    if (grab.current?.id !== o.id) return;
    e.stopPropagation();
    setDraft(place(o, e));
  }
  function up(o: Overlay, e: PointerEvent<HTMLElement>) {
    if (grab.current?.id !== o.id) return;
    e.stopPropagation();
    const next = place(o, e);
    grab.current = null;
    setDraft(null);
    if (next.at.t !== o.at.t || next.duration !== o.duration) onChange(next);
  }
  function key(o: Overlay, e: KeyboardEvent<HTMLElement>) {
    const d = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    if (!d) return;
    e.preventDefault();
    e.stopPropagation();
    const step = (e.shiftKey ? 1 : NUDGE) * d;
    onChange(e.altKey ? resizeOverlay(o, "end", o.at.t + o.duration + step, t0, t1) : moveOverlay(o, o.at.t + step, t0, t1 - o.duration + MIN_OVERLAY));
  }

  return (
    <div className={`tl-lane tl-ov tl-ov-${kind}`} role="group" aria-label={t(`edit.tl.lane.${kind}`)}>
      {mine.map((o) => {
        const shown = draft && draft.id === o.id ? draft : o;
        const left = x(shown.at.t);
        const w = Math.max(8, shown.duration * pps - 2);
        const label = o.type === "text" ? o.text : baseName(o.path);
        return (
          <div
            key={o.id}
            className={`tl-ovb ${o.type}${o.id === selected ? " on" : ""}`}
            style={{ left, width: w }}
            role="button"
            tabIndex={0}
            aria-pressed={o.id === selected}
            aria-label={t("edit.tl.ov.label", { what: label })}
            title={t("edit.tl.ov.hint")}
            onPointerDown={(e) => down(o, "move", e)}
            onPointerMove={(e) => move(o, e)}
            onPointerUp={(e) => up(o, e)}
            onPointerCancel={() => { grab.current = null; setDraft(null); }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(o.id!); }
              else key(o, e);
            }}
          >
            <span className="tl-ovb-name">{label}</span>
            <span className="tl-ovb-edge start" aria-hidden="true" onPointerDown={(e) => down(o, "start", e)} onPointerMove={(e) => move(o, e)} onPointerUp={(e) => up(o, e)} />
            <span className="tl-ovb-edge end" aria-hidden="true" onPointerDown={(e) => down(o, "end", e)} onPointerMove={(e) => move(o, e)} onPointerUp={(e) => up(o, e)} />
          </div>
        );
      })}
    </div>
  );
}
