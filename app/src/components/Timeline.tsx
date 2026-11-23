import { memo, useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Overlay, TranscriptWord } from "../engine/types";
import { isTauri } from "../engine/client";
import { BROLL_IMAGE, BROLL_VIDEO, brollType, rememberPicked } from "../lib/overlayMedia";
import { pickFileNative } from "../lib/native";
import OverlayLane, { MIN_OVERLAY } from "./OverlayLane";
import { t } from "../i18n/t";
import { lanePieces, laneSegments, rulerTicks, snapToWord, speechEnergy, splitWordAt, wordSlot, type Piece } from "../edit/lanes";
import { baseName } from "../lib/native";

export interface TimelineProps {
  words: readonly TranscriptWord[];
  /** The clip's word range in the transcript. */
  w0: number;
  w1: number;
  kept: ReadonlySet<number>;
  /** Caption events at source time (see captionSpans). */
  captions: { start: number; end: number; text: string }[];
  music: string | null;
  /** Source seconds. */
  playhead: number;
  onSeek(srcT: number): void;
  /** Keep words first..last of the clip (snapped to words). */
  onTrim(first: number, last: number): void;
  /** Cut words w0..w1 (a deleted piece). */
  onCut(w0: number, w1: number): void;
  /** Text and b-roll on the clip; onOverlays saves the whole list (one undo step). */
  overlays?: readonly Overlay[];
  onOverlays?(items: Overlay[]): void;
  /** Undo and redo the last edit (Ctrl+Z, Ctrl+Shift+Z). */
  onUndo?(): void;
  onRedo?(): void;
  canUndo?: boolean;
  canRedo?: boolean;
}

const newId = () => `o${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
const OVERLAY_LEN = 3; // seconds a new text or b-roll lasts
const ZOOM_MAX = 10;

/** Lane width when the layout cannot be measured (jsdom, first paint). */
export const FALLBACK_WIDTH = 600;
const BAR_PX = 4; // one waveform bar per 4 px
const SEEK_STEP = 1; // seconds the playhead moves per arrow key
const MIN_PPS = 40; // a narrow window scrolls the lanes rather than squeezing captions below this many px a second

type Tool = "select" | "trim";
type Drag = { kind: "seek" } | { kind: "start" | "end"; w: number };

const clock = (s: number): string => {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s - m * 60)).padStart(2, "0")}`;
};

const ToolIcon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

/** The clip on a source-time ruler: captions, the video with its cuts, music, and a draggable playhead. */
function Timeline({ words, w0, w1, kept, captions, music, playhead, onSeek, onTrim, onCut, overlays = [], onOverlays, onUndo, onRedo, canUndo = false, canRedo = false }: TimelineProps) {
  const [tool, setTool] = useState<Tool>("select");
  const [zoom, setZoom] = useState(1);
  const [laneW, setLaneW] = useState(FALLBACK_WIDTH);
  const [draft, setDraft] = useState<Drag | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  // Split points (the word each new piece starts at) and the selected piece. They only shape the timeline: a split
  // alone changes nothing in the video, so they are not saved; deleting a piece is a cut, saved and undoable.
  const [splits, setSplits] = useState<ReadonlySet<number>>(() => new Set());
  const [picked, setPicked] = useState<number | null>(null); // the selected piece's first word
  const [ovSel, setOvSel] = useState<string | null>(null); // the selected text or b-roll
  const brollInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setSplits(new Set());
    setPicked(null);
    setOvSel(null);
  }, [w0, w1]);

  useEffect(() => {
    const el = scroller.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => el.clientWidth > 0 && setLaneW(el.clientWidth);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const t0 = words[w0]?.start ?? 0;
  const t1 = words[w1] ? wordSlot(words, w1, w1)[1] : t0 + 1;
  const span = Math.max(0.1, t1 - t0);
  const width = Math.max(laneW, span * MIN_PPS) * zoom;
  const pps = width / span;
  const x = (s: number) => (s - t0) * pps;
  const segs = useMemo(() => laneSegments(words, w0, w1, kept), [words, w0, w1, kept]);
  const keptSegs = segs.filter((s) => s.kind === "kept");
  const pieces = useMemo(() => lanePieces(words, segs, splits, w1), [words, segs, splits, w1]);
  const selected: Piece | undefined = pieces.find((p) => p.w0 === picked);
  const first = keptSegs[0]?.w0 ?? w0;
  const last = keptSegs[keptSegs.length - 1]?.w1 ?? w1;

  const timeAt = (clientX: number): number => {
    const left = content.current?.getBoundingClientRect().left ?? 0;
    return Math.min(t1, Math.max(t0, t0 + (clientX - left) / pps));
  };
  const capture = (e: PointerEvent<HTMLElement>) => {
    try {
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* no pointer id (synthetic events) */
    }
  };

  function edgeWord(kind: "start" | "end", clientX: number): number {
    const w = snapToWord(words, w0, w1, timeAt(clientX), kind);
    return kind === "start" ? Math.min(w, last) : Math.max(w, first);
  }

  function onLanesDown(e: PointerEvent<HTMLDivElement>) {
    if (e.button !== 0) return;
    capture(e);
    drag.current = { kind: "seek" };
    onSeek(timeAt(e.clientX));
  }
  function onLanesMove(e: PointerEvent<HTMLDivElement>) {
    if (drag.current?.kind === "seek") onSeek(timeAt(e.clientX));
  }
  function onLanesUp() {
    if (drag.current?.kind === "seek") drag.current = null;
  }

  function onEdgeDown(kind: "start" | "end", e: PointerEvent<HTMLElement>) {
    e.stopPropagation();
    if (e.button !== 0) return;
    capture(e);
    drag.current = { kind, w: kind === "start" ? first : last };
    setDraft(drag.current);
  }
  function onEdgeMove(kind: "start" | "end", e: PointerEvent<HTMLElement>) {
    e.stopPropagation();
    if (drag.current?.kind !== kind) return;
    drag.current = { kind, w: edgeWord(kind, e.clientX) };
    setDraft(drag.current);
  }
  function onEdgeUp(kind: "start" | "end", e: PointerEvent<HTMLElement>) {
    e.stopPropagation();
    if (drag.current?.kind !== kind) return;
    const w = edgeWord(kind, e.clientX);
    drag.current = null;
    setDraft(null);
    if (kind === "start" && w !== first) onTrim(w, last);
    if (kind === "end" && w !== last) onTrim(first, w);
  }
  function onEdgeKey(kind: "start" | "end", e: KeyboardEvent<HTMLElement>) {
    const d = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    if (!d) return;
    e.preventDefault();
    if (kind === "start") {
      const w = Math.min(last, Math.max(w0, first + d));
      if (w !== first) onTrim(w, last);
    } else {
      const w = Math.min(w1, Math.max(first, last + d));
      if (w !== last) onTrim(first, w);
    }
  }
  function onPlayheadKey(e: KeyboardEvent<HTMLElement>) {
    const d = e.key === "ArrowLeft" ? -SEEK_STEP : e.key === "ArrowRight" ? SEEK_STEP : 0;
    if (!d) return;
    e.preventDefault();
    onSeek(Math.min(t1, Math.max(t0, playhead + d)));
  }

  const splitAt = splitWordAt(words, pieces, playhead);
  function split() {
    if (splitAt === null) return;
    setSplits((s) => new Set([...s, splitAt]));
    setPicked(splitAt);
  }
  // overlays without an id (written by hand or by an older build) get one, so they can be selected and dragged
  const items: Overlay[] = useMemo(() => overlays.map((o, i) => (o.id ? o : { ...o, id: `o-${i}` })), [overlays]);
  const ovSelected = items.find((o) => o.id === ovSel);
  const saveOverlays = (next: Overlay[]) => onOverlays?.(next);
  const changeOverlay = (next: Overlay) => saveOverlays(items.map((o) => (o.id === next.id ? next : o)));
  function selectOverlay(id: string | null) {
    setOvSel(id);
    if (id) setPicked(null);
  }
  const startAt = Math.min(Math.max(t0, playhead), Math.max(t0, t1 - 0.5));
  const lenAt = (at: number) => Math.max(0.5, Math.min(OVERLAY_LEN, t1 - at));
  function addText() {
    const o: Overlay = { id: newId(), type: "text", at: { src: "main", t: startAt }, duration: lenAt(startAt), text: t("edit.tl.text.default"), position: "top" };
    saveOverlays([...items, o]);
    selectOverlay(o.id!);
  }
  function addBroll(path: string) {
    const o: Overlay = { id: newId(), type: brollType(path), at: { src: "main", t: startAt }, duration: lenAt(startAt), path, mode: "full" };
    saveOverlays([...items, o]);
    selectOverlay(o.id!);
  }
  async function pickBroll() {
    if (!isTauri()) return brollInput.current?.click();
    const path = await pickFileNative(t("edit.tl.broll"), [...BROLL_IMAGE, ...BROLL_VIDEO]).catch(() => null);
    if (path) addBroll(path);
  }

  function duplicate() {
    if (!ovSelected) return;
    const at = Math.min(ovSelected.at.t + ovSelected.duration, Math.max(t0, t1 - MIN_OVERLAY));
    const o: Overlay = { ...ovSelected, id: newId(), at: { ...ovSelected.at, t: Math.round(at * 100) / 100 }, duration: Math.max(MIN_OVERLAY, Math.min(ovSelected.duration, t1 - at)) };
    saveOverlays([...items, o]);
    selectOverlay(o.id!);
  }
  const zoomBy = (d: number) => setZoom((z) => Math.min(ZOOM_MAX, Math.max(1, z + d)));

  function remove() {
    if (ovSelected) {
      setOvSel(null);
      saveOverlays(items.filter((o) => o.id !== ovSelected.id));
      return;
    }
    if (!selected) return;
    setPicked(null);
    onCut(selected.w0, selected.w1);
  }
  // S splits at the playhead; Delete or Backspace removes the selected piece. Not while typing.
  const keys = useRef({ split, remove, duplicate, zoomBy });
  keys.current = { split, remove, duplicate, zoomBy };
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && !typing && e.key.toLowerCase() === "d") {
        e.preventDefault();
        keys.current.duplicate();
        return;
      }
      if (!typing && !e.ctrlKey && !e.metaKey && (e.key === "=" || e.key === "+" || e.key === "-")) {
        e.preventDefault();
        keys.current.zoomBy(e.key === "-" ? -1 : 1);
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || (el && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable))) return;
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        keys.current.split();
      } else if (e.key === "Delete" || e.key === "Backspace") {
        // the transcript and other panels use these keys for their own selection
        if (el && el !== document.body && !el.closest?.(".ed-tl")) return;
        e.preventDefault();
        keys.current.remove();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const edgeAt = (kind: "start" | "end"): number => {
    const w = draft?.kind === kind ? draft.w : kind === "start" ? first : last;
    return kind === "start" ? words[w]?.start ?? t0 : wordSlot(words, w, w1)[1];
  };
  const ph = Math.min(t1, Math.max(t0, playhead));

  return (
    <section className="ed-tl" aria-label={t("edit.tl.label")}>
      <div className="tl-bar">
        <div className="tl-tools">
          <button type="button" className="icon" aria-label={t("edit.tl.undo")} title={t("edit.tl.undo.title")} disabled={!canUndo || !onUndo} onClick={onUndo}>
            <ToolIcon d="M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
          </button>
          <button type="button" className="icon" aria-label={t("edit.tl.redo")} title={t("edit.tl.redo.title")} disabled={!canRedo || !onRedo} onClick={onRedo}>
            <ToolIcon d="m15 14 5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13" />
          </button>
          <span className="divider" aria-hidden="true" />
          <button type="button" className="icon" aria-label={t("edit.tl.select")} title={t("edit.tl.select.title")} aria-pressed={tool === "select"} onClick={() => setTool("select")}>
            <ToolIcon d="M5 3l14 8-6 2-2 6z" />
          </button>
          <button type="button" className="icon" aria-label={t("edit.tl.split")} title={t("edit.tl.split.title")} disabled={splitAt === null} onClick={split}>
            <ToolIcon d="M8.6 7.6 20 18M8.6 16.4 20 6M9 6a3 3 0 1 1-6 0 3 3 0 0 1 6 0zM9 18a3 3 0 1 1-6 0 3 3 0 0 1 6 0z" />
          </button>
          <button type="button" className="icon" aria-label={t("edit.tl.trim")} title={t("edit.tl.trim.title")} aria-pressed={tool === "trim"} onClick={() => setTool(tool === "trim" ? "select" : "trim")}>
            <ToolIcon d="M7 4v16M17 4v16M7 12h10" />
          </button>
          <button type="button" className="icon" aria-label={t("edit.tl.duplicate")} title={t("edit.tl.duplicate.title")} disabled={!ovSelected} onClick={duplicate}>
            <ToolIcon d="M9 9h11v11H9zM5 15V4h11" />
          </button>
          <button type="button" className="icon" aria-label={t(ovSelected ? "edit.tl.delete.layer" : "edit.tl.delete")} title={t(ovSelected ? "edit.tl.delete.layer.title" : "edit.tl.delete.title")} disabled={!selected && !ovSelected} onClick={remove}>
            <ToolIcon d="M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v5M14 11v5" />
          </button>
          <span className="divider" aria-hidden="true" />
          <button type="button" className="tl-add text" title={t("edit.tl.text.title")} disabled={!onOverlays} onClick={addText}>
            <ToolIcon d="M5 7V5h14v2M12 5v14M9 19h6" />
            {t("edit.tl.text")}
          </button>
          <button type="button" className="tl-add broll" title={t("edit.tl.broll.title")} disabled={!onOverlays} onClick={pickBroll}>
            <ToolIcon d="M4 5h16v14H4zM4 16l5-5 4 4 2-2 5 5M15 9.5h.01" />
            {t("edit.tl.broll")}
          </button>
          <input
            ref={brollInput}
            type="file"
            accept="image/*,video/*"
            hidden
            onChange={(e) => {
              const f = e.currentTarget.files?.[0];
              e.currentTarget.value = "";
              if (f) addBroll(rememberPicked(f));
            }}
          />
        </div>
        <div className="tl-zoom">
          <button type="button" className="icon" aria-label={t("edit.tl.zoom.out")} title={t("edit.tl.zoom.out.title")} disabled={zoom <= 1} onClick={() => zoomBy(-1)}>
            <ToolIcon d="M5 12h14" />
          </button>
          <input type="range" aria-label={t("edit.tl.zoom")} min={1} max={ZOOM_MAX} step={1} value={zoom} onChange={(e) => setZoom(Number(e.currentTarget.value))} />
          <button type="button" className="icon" aria-label={t("edit.tl.zoom.in")} title={t("edit.tl.zoom.in.title")} disabled={zoom >= ZOOM_MAX} onClick={() => zoomBy(1)}>
            <ToolIcon d="M12 5v14M5 12h14" />
          </button>
          <button type="button" className="icon" aria-label={t("edit.tl.zoom.fit")} title={t("edit.tl.zoom.fit.title")} disabled={zoom === 1} onClick={() => setZoom(1)}>
            <ToolIcon d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
          </button>
        </div>
      </div>

      {ovSelected && <OverlayProps o={ovSelected} onChange={changeOverlay} onDelete={remove} onDone={() => setOvSel(null)} />}

      <div className="tl-body">
        <div className="tl-names" aria-hidden="true">
          <span />
          <span>{t("edit.tl.lane.text")}</span>
          <span>{t("edit.tl.lane.broll")}</span>
          <span>{t("edit.tl.captions")}</span>
          <span className="video">{t("edit.tl.video")}</span>
          <span>{t("edit.tl.music")}</span>
        </div>
        <div className="tl-scroll" ref={scroller}>
          <div
            className={`tl-content${tool === "trim" ? " trim" : ""}`}
            ref={content}
            style={{ width }}
            onPointerDown={onLanesDown}
            onPointerMove={onLanesMove}
            onPointerUp={onLanesUp}
            onPointerCancel={onLanesUp}
          >
            <div className="tl-ruler mono" aria-hidden="true">
              {rulerTicks(span, pps).map((s) => (
                <span key={s} style={{ left: s * pps }}>{clock(s)}</span>
              ))}
            </div>

            {(["text", "broll"] as const).map((kind) => (
              <OverlayLane
                key={kind}
                kind={kind}
                overlays={items}
                x={x}
                pps={pps}
                t0={t0}
                t1={t1}
                selected={ovSel}
                onSelect={selectOverlay}
                onChange={changeOverlay}
              />
            ))}

            <div className="tl-lane tl-caps" role="group" aria-label={t("edit.tl.captions")}>
              {captions.map((c, i) => (
                <div key={i} className="tl-cap" style={{ left: x(c.start), width: Math.max(2, x(c.end) - x(c.start) - 2) }} title={c.text}>
                  {c.text}
                </div>
              ))}
            </div>

            <div className="tl-lane tl-video" role="group" aria-label={t("edit.tl.video")}>
              {segs.map((s) => {
                const left = x(s.start);
                const w = Math.max(2, x(s.end) - left - 2);
                if (s.kind === "gap") {
                  return <div key={`g${s.w0}`} className="tl-gap" style={{ left, width: w }} title={t("edit.tl.cut")} data-w0={s.w0} />;
                }
                const n = Math.max(1, Math.round(w / BAR_PX));
                const bars = speechEnergy(words, s.start, s.end, n);
                return (
                  <div key={`k${s.w0}`} className="tl-seg kept" style={{ left, width: w }} data-w0={s.w0} data-w1={s.w1}>
                    <svg viewBox={`0 0 ${n * BAR_PX} 32`} preserveAspectRatio="none" aria-hidden="true">
                      {bars.map((b, i) => {
                        const h = 2 + b * 28;
                        return <rect key={i} x={i * BAR_PX + 1} y={16 - h / 2} width={2} height={h} rx={1} />;
                      })}
                    </svg>
                  </div>
                );
              })}
              {tool === "select" && pieces.map((p) => (
                <button
                  key={`p${p.w0}`}
                  type="button"
                  className={`tl-piece${p.w0 === picked ? " on" : ""}${splits.has(p.w0) ? " split" : ""}`}
                  style={{ left: x(p.start), width: Math.max(2, x(p.end) - x(p.start) - 2) }}
                  aria-label={t("edit.tl.piece", { a: words[p.w0]?.text ?? "", b: words[p.w1]?.text ?? "" })}
                  aria-pressed={p.w0 === picked}
                  onClick={() => {
                    setPicked(p.w0 === picked ? null : p.w0);
                    setOvSel(null);
                  }}
                />
              ))}
              {tool === "trim" && keptSegs.length > 0 && (["start", "end"] as const).map((kind) => (
                <span
                  key={kind}
                  className="tl-handle"
                  data-edge={kind}
                  role="slider"
                  tabIndex={0}
                  aria-label={t(`edit.tl.trim.${kind}`)}
                  aria-valuemin={w0 + 1}
                  aria-valuemax={w1 + 1}
                  aria-valuenow={(kind === "start" ? first : last) + 1}
                  aria-valuetext={words[kind === "start" ? first : last]?.text}
                  style={{ left: x(edgeAt(kind)) - (kind === "start" ? 0 : 2) }}
                  onPointerDown={(e) => onEdgeDown(kind, e)}
                  onPointerMove={(e) => onEdgeMove(kind, e)}
                  onPointerUp={(e) => onEdgeUp(kind, e)}
                  onKeyDown={(e) => onEdgeKey(kind, e)}
                />
              ))}
            </div>

            <div className="tl-lane tl-music" role="group" aria-label={t("edit.tl.music")}>
              {music ? (
                <div className="tl-mus" style={{ left: 0, width: Math.max(2, width - 2) }}>
                  {t("edit.tl.music.on", { name: baseName(music) })}
                </div>
              ) : (
                <span className="tl-none">{t("edit.tl.music.none")}</span>
              )}
            </div>

            <div
              className="tl-playhead"
              style={{ left: x(ph) }}
              role="slider"
              tabIndex={0}
              aria-label={t("edit.tl.playhead")}
              aria-valuemin={0}
              aria-valuemax={Math.round(span * 10) / 10}
              aria-valuenow={Math.round((ph - t0) * 10) / 10}
              aria-valuetext={clock(ph - t0)}
              onKeyDown={onPlayheadKey}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

const TEXT_POS = ["top", "center", "bottom"] as const;
const SIZES = [[0.8, "s"], [1, "m"], [1.4, "l"]] as const;
const PIP_POS = ["top-left", "top-right", "bottom-left", "bottom-right"] as const;

/** The selected text or b-roll's settings, in a row over the lanes. Text is saved when you leave the field or press
    Enter, so typing is one undo step. */
function OverlayProps({ o, onChange, onDelete, onDone }: { o: Overlay; onChange(o: Overlay): void; onDelete(): void; onDone(): void }) {
  const [text, setText] = useState(o.type === "text" ? o.text : "");
  const field = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (o.type === "text") setText(o.text);
  }, [o]);
  useEffect(() => {
    if (o.type === "text") field.current?.select();
    // a newly added text: ready to type over the placeholder
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [o.id]);
  const commit = () => {
    if (o.type === "text" && text.trim() && text !== o.text) onChange({ ...o, text: text.slice(0, 200) });
  };
  const seg = <T extends string | number>(name: string, label: string, opts: readonly (readonly [T, string])[], value: T, set: (v: T) => void) => (
    <div className="seg" role="radiogroup" aria-label={label}>
      {opts.map(([v, l]) => (
        <label key={String(v)} className="seg-opt">
          <input type="radio" name={`${name}-${o.id}`} checked={value === v} onChange={() => set(v)} />
          <span>{l}</span>
        </label>
      ))}
    </div>
  );
  return (
    <div className="tl-props" role="group" aria-label={t("edit.tl.props")}>
      {o.type === "text" ? (
        <>
          <input
            ref={field}
            className="tl-props-text"
            value={text}
            maxLength={200}
            aria-label={t("edit.tl.text.field")}
            onChange={(e) => setText(e.currentTarget.value)}
            onBlur={commit}
            onKeyDown={(e) => {
              if (e.key === "Enter") { commit(); e.currentTarget.blur(); }
              if (e.key === "Escape") { setText(o.text); e.currentTarget.blur(); }
            }}
          />
          {seg("pos", t("edit.tl.text.pos"), TEXT_POS.map((p) => [p, t(`edit.tl.text.pos.${p}`)] as const), o.position, (v) => onChange({ ...o, position: v }))}
          {seg("size", t("edit.tl.text.size"), SIZES.map(([v, k]) => [v, t(`edit.tl.text.size.${k}`)] as const), (o.size ?? 1) as 0.8 | 1 | 1.4, (v) => onChange({ ...o, size: v }))}
        </>
      ) : (
        <>
          <span className="tl-props-name mono" title={o.path}>{baseName(o.path)}</span>
          {seg("mode", t("edit.tl.broll.mode"), [["full", t("edit.tl.broll.full")], ["pip", t("edit.tl.broll.pip")]] as const, o.mode, (v) => onChange({ ...o, mode: v, ...(v === "pip" ? { position: o.position ?? "top-right" } : {}) }))}
          {o.mode === "pip" && seg("corner", t("edit.tl.broll.corner"), PIP_POS.map((p) => [p, t(`edit.tl.broll.corner.${p}`)] as const), (o.position ?? "top-right") as (typeof PIP_POS)[number], (v) => onChange({ ...o, position: v }))}
        </>
      )}
      <span className="tl-props-len mono">{t("edit.tl.ov.len", { n: o.duration.toFixed(1) })}</span>
      <span className="tl-props-end">
        <button type="button" className="secondary" onClick={onDelete}>{t("edit.tl.ov.delete")}</button>
        <button type="button" className="secondary" onClick={onDone}>{t("edit.tl.ov.done")}</button>
      </span>
    </div>
  );
}

export default memo(Timeline);
