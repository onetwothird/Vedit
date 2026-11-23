import { useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";

export type RulerSegment = { id: string; label: string };

/** "00:05" for 5 seconds: the ruler's labels are real timecodes, one region every `step` seconds. */
export const timecode = (s: number): string =>
  `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;

/** The editor ruler: named regions along a timeline with the green playhead parked on the current one.
    `links` is a nav of in-page links (Settings), `tabs` a tablist whose tabs control `panel` (sign in).
    The ticks are CSS gradients; the only moving part is the playhead, by transform. */
export default function Ruler({ segments, active, onPick, label, kind = "links", panel, step = 5 }: {
  segments: RulerSegment[];
  active: string;
  onPick(id: string): void;
  label: string;
  kind?: "links" | "tabs";
  panel?: string;
  step?: number;
}) {
  const track = useRef<HTMLDivElement>(null);
  const [x, setX] = useState<number | null>(null);

  // Park the playhead on the active region, and keep that region in view when the ruler scrolls sideways (phones).
  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const place = () => {
      const seg = el.querySelector<HTMLElement>(`[data-seg="${CSS.escape(active)}"]`);
      if (!seg) return;
      setX(seg.offsetLeft);
      const scroller = el.parentElement;
      if (scroller && scroller.scrollWidth > scroller.clientWidth) {
        const at = seg.getBoundingClientRect().left - scroller.getBoundingClientRect().left + scroller.scrollLeft;
        const left = at - 24;
        const right = at + seg.offsetWidth + 24 - scroller.clientWidth;
        const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
        if (scroller.scrollLeft > left || scroller.scrollLeft < right) {
          scroller.scrollTo({ left: scroller.scrollLeft > left ? left : right, behavior: reduce ? "auto" : "smooth" });
        }
      }
    };
    place();
    if (typeof ResizeObserver !== "function") return;
    const ro = new ResizeObserver(place);
    ro.observe(el);
    return () => ro.disconnect();
  }, [active, segments]);

  function onKey(e: KeyboardEvent<HTMLButtonElement>, i: number) {
    const go = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : e.key === "Home" ? 0 : e.key === "End" ? segments.length - 1 : null;
    if (go === null) return;
    e.preventDefault();
    const next = segments[(go + segments.length) % segments.length];
    onPick(next.id);
    track.current?.querySelector<HTMLElement>(`[data-seg="${CSS.escape(next.id)}"]`)?.focus();
  }

  const inner = (s: RulerSegment, i: number) => (
    <>
      <span className="ruler-tc" aria-hidden="true">{timecode(i * step)}</span>
      <span className="ruler-label">{s.label}</span>
    </>
  );

  const head = <span className="ruler-head" aria-hidden="true" style={{ transform: `translateX(${x ?? 0}px)`, opacity: x === null ? 0 : 1 }} />;

  if (kind === "tabs") {
    return (
      <div className="ruler">
        <div ref={track} className="ruler-track" role="tablist" aria-label={label}>
          {segments.map((s, i) => (
            <button
              key={s.id}
              type="button"
              role="tab"
              id={`tab-${s.id}`}
              data-seg={s.id}
              className="ruler-seg"
              aria-selected={active === s.id}
              aria-controls={panel}
              tabIndex={active === s.id ? 0 : -1}
              onClick={() => onPick(s.id)}
              onKeyDown={(e) => onKey(e, i)}
            >
              {inner(s, i)}
            </button>
          ))}
          {head}
        </div>
      </div>
    );
  }

  return (
    <nav className="ruler" aria-label={label}>
      <div ref={track} className="ruler-track">
        {segments.map((s, i) => (
          <a
            key={s.id}
            href={`#${s.id}`}
            data-seg={s.id}
            className="ruler-seg"
            aria-current={active === s.id ? "true" : undefined}
            onClick={(e) => {
              e.preventDefault(); // the app routes on the hash, so the link scrolls instead of navigating
              onPick(s.id);
            }}
          >
            {inner(s, i)}
          </a>
        ))}
        {head}
      </div>
    </nav>
  );
}
