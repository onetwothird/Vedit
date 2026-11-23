import { useCallback, useEffect, useId, useImperativeHandle, useMemo, useRef, useState, type ReactNode, type Ref } from "react";
import type { Aspect, ClipView, Overlay } from "../engine/types";
import { t } from "../i18n/t";
import { getSafeZone, setSafeZone } from "../lib/prefs";
import CaptionOverlay from "./CaptionOverlay";
import OverlayLayer from "./OverlayLayer";
import {
  advance, captionClock, clipFraming, cropStyle, exportOnly, locate, outputLength, outputStart, safeZoneBands, speedOf,
  viewRanges, type Range,
} from "./playback";

export interface PlayerHandle {
  play(): void;
  pause(): void;
  /** Jump to `t` seconds of the edited clip. */
  seekOutput(t: number): void;
  /** Move by `delta` seconds of the edited clip (negative goes back). */
  skip(delta: number): void;
  /** Play if paused, else pause (what Space does). */
  toggle(): void;
}

export interface PlayerProps {
  view: ClipView;
  /** What the video element loads (see previewSrc). */
  src: string;
  aspect: Aspect;
  /** The largest box the frame may fill. */
  fit?: { width: number; height: number };
  onTime?: (outT: number) => void;
  /** Drawn behind the video until it loads, and instead of it when it cannot be shown. */
  poster?: (frame: { width: number; height: number }) => ReactNode;
  ref?: Ref<PlayerHandle>;
}

const SKIP_SECONDS = 5;

function clock(s: number): string {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

/** Keys typed into a field, or aimed at a control that uses them itself, are not ours. */
function ignoreKey(target: EventTarget | null, key: string): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable) return true;
  if (el.closest?.("[contenteditable]:not([contenteditable=false])")) return true;
  if (el.closest?.("[role=tab],[role=slider],[role=radio],[role=listbox],[role=menu]")) return true;
  return key === " " && (el.tagName === "BUTTON" || el.tagName === "A"); // Space already presses a focused button
}

const Icon = ({ d, fill }: { d: string; fill?: boolean }) => (
  <svg viewBox="0 0 24 24" fill={fill ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true">
    <path d={d} />
  </svg>
);

export default function Player({ view, src, aspect, fit = { width: 360, height: 420 }, onTime, poster, ref }: PlayerProps) {
  const video = useRef<HTMLVideoElement>(null);
  const ranges = useMemo(() => viewRanges(view), [view]);
  // overlays are saved in real source time; the mock plays a sample shifted by sample_offset
  const ovRanges = useMemo(() => {
    const off = view.sample_offset ?? 0;
    return off ? ranges.map((r): Range => (r.length > 2 ? [r[0] + off, r[1] + off, r[2]!] : [r[0] + off, r[1] + off])) : ranges;
  }, [ranges, view.sample_offset]);
  const rangesRef = useRef(ranges);
  rangesRef.current = ranges;
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;
  const total = outputLength(ranges);

  const outRef = useRef(0);
  const atEnd = useRef(false); // arrived at the end of the last range and already stopped there
  const idxRef = useRef(0); // the range being played; output time alone is ambiguous at a range boundary
  const [out, setOut] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(!src);
  const [safe, setSafe] = useState(getSafeZone);

  const publish = useCallback((o: number) => {
    const moved = Math.abs(o - outRef.current) >= 0.01;
    outRef.current = o;
    if (moved) setOut(o);
    onTimeRef.current?.(o);
  }, []);

  const seekOutput = useCallback((t: number) => {
    const v = video.current, R = rangesRef.current;
    if (!v || !R.length) return;
    const to = Math.min(Math.max(t, 0), outputLength(R));
    const { idx, srcT } = locate(R, to);
    idxRef.current = idx;
    atEnd.current = false;
    v.playbackRate = speedOf(R[idx]);
    v.currentTime = srcT;
    outRef.current = to;
    setOut(to);
    onTimeRef.current?.(to);
  }, []);

  const play = useCallback(() => {
    const v = video.current;
    if (!v || !rangesRef.current.length) return;
    if (outRef.current >= outputLength(rangesRef.current) - 0.02) seekOutput(0);
    v.play()?.catch(() => undefined); // refused when the file will not play; the error handler shows why
  }, [seekOutput]);
  const pause = useCallback(() => video.current?.pause(), []);
  const toggle = useCallback(() => (video.current?.paused ?? true ? play() : pause()), [play, pause]);
  const skip = useCallback((d: number) => seekOutput(outRef.current + d), [seekOutput]);
  useImperativeHandle(ref, () => ({ play, pause, seekOutput, skip, toggle }), [play, pause, seekOutput, skip, toggle]);

  // a new file starts over
  useEffect(() => {
    setReady(false);
    setFailed(!src);
    setSize(null);
  }, [src]);

  // the edit changed under a paused or playing preview: stay at the same output time on the new ranges
  useEffect(() => {
    if (ready) seekOutput(outRef.current);
  }, [ranges, ready, seekOutput]);

  // follow every presented frame: leave a range at its end for the start of the next one, stop after the last
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const step = (srcT: number): boolean => {
      const R = rangesRef.current;
      if (v.seeking || !R.length) return false;
      if (idxRef.current >= R.length) idxRef.current = R.length - 1;
      const a = advance(R, idxRef.current, srcT);
      if (a.kind === "stay") publish(a.out);
      else if (a.kind === "jump") {
        idxRef.current = a.idx;
        atEnd.current = false;
        v.playbackRate = speedOf(R[a.idx]);
        v.currentTime = a.srcT;
        publish(outputStart(R, a.idx));
        return true;
      } else {
        if (!atEnd.current) { // once per arrival: re-seeking on every presented frame would be an idle seek loop
          atEnd.current = true;
          v.pause();
          idxRef.current = R.length - 1;
          v.currentTime = R[R.length - 1][1];
        }
        publish(a.out);
      }
      return false;
    };
    const onEnded = () => {
      if (step(Infinity)) v.play()?.catch(() => undefined);
    };
    v.addEventListener("ended", onEnded);
    let stop = () => undefined as void;
    if ("requestVideoFrameCallback" in v) {
      let h = 0;
      const loop = (_now: number, meta: { mediaTime: number }) => {
        step(meta.mediaTime);
        h = v.requestVideoFrameCallback(loop);
      };
      h = v.requestVideoFrameCallback(loop);
      stop = () => v.cancelVideoFrameCallback(h);
    } else {
      let h = 0;
      const el: HTMLVideoElement = v;
      const loop = () => {
        if (!el.paused) step(el.currentTime);
        h = requestAnimationFrame(loop);
      };
      h = requestAnimationFrame(loop);
      stop = () => cancelAnimationFrame(h);
    }
    return () => {
      v.removeEventListener("ended", onEnded);
      stop();
    };
  }, [publish, src]);

  // Space plays and pauses, the arrows skip
  useEffect(() => {
    if (failed) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || ignoreKey(e.target, e.key)) return;
      if (e.key === " ") toggle();
      else if (e.key === "ArrowLeft") skip(-SKIP_SECONDS);
      else if (e.key === "ArrowRight") skip(SKIP_SECONDS);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [failed, toggle, skip]);

  const A = Number(aspect.split(":")[0]) / Number(aspect.split(":")[1]);
  const fw = Math.max(1, Math.floor(Math.min(fit.width, fit.height * A)));
  const fh = Math.max(1, Math.round(fw / A));
  const idx = locate(ranges, out).idx;
  const { cropX, zoom } = clipFraming(view.edl.clips[idx]);
  const bands = safeZoneBands(aspect);
  const only = exportOnly(view.edl);
  const look = previewColor(view.edl.color);
  const filterId = `pv-color-${useId().replace(/:/g, "")}`;
  const shown = ready && !failed;

  return (
    <div className="pv" role="group" aria-label={t("player.label")}>
      <div className="pv-frame" style={{ width: fw, height: fh }}>
        {!shown && poster && <div className="pv-poster">{poster({ width: fw, height: fh })}</div>}
        {!failed && (
          <video
            ref={video}
            src={src}
            preload="auto"
            playsInline
            style={{
              ...(size ? cropStyle(aspect, size.w, size.h, cropX, zoom) : { position: "absolute", inset: 0 }),
              opacity: shown ? 1 : 0,
              filter: look.filter ? `url(#${filterId})` : undefined,
            }}
            onLoadedMetadata={(e) => {
              setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight });
              seekOutput(outRef.current);
            }}
            onLoadedData={() => setReady(true)}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onError={() => setFailed(true)}
          />
        )}
        {look.filter && (
          <svg className="pv-filter" aria-hidden="true">
            <filter id={filterId} colorInterpolationFilters="sRGB">
              <feComponentTransfer>
                {(["R", "G", "B"] as const).map((ch) => {
                  const F = `feFunc${ch}` as const;
                  return <F key={ch} type="linear" slope={look.contrast} intercept={0.5 - 0.5 * look.contrast + look.brightness} />;
                })}
              </feComponentTransfer>
              <feColorMatrix type="saturate" values={String(look.saturation)} />
            </filter>
          </svg>
        )}
        {shown && look.vignette && <div className="pv-vignette" aria-hidden="true" />}
        {shown && (
          <OverlayLayer overlays={(view.edl.overlays as Overlay[] | undefined) ?? []} ranges={ovRanges} out={out} frameWidth={fw} frameHeight={fh} playing={playing} />
        )}
        {shown && <CaptionOverlay track={view.captions} outT={captionClock(view.captions, out)} frameHeight={fh} />}
        {shown && safe && (
          <div className="pv-safe" aria-hidden="true">
            <i style={{ height: `${bands.top * 100}%` }} />
            <i style={{ height: `${bands.bottom * 100}%`, marginTop: "auto" }} />
          </div>
        )}
      </div>

      {failed && <p className="pv-error" role="status">{t("player.error")}</p>}

      <div className="pv-transport">
        <span className="pv-tc mono">{clock(out)} / {clock(total)}</span>
        <div className="pv-ctrls">
          <button type="button" className="pv-btn" aria-label={t("player.back")} title={t("player.back")} disabled={failed} onClick={() => skip(-SKIP_SECONDS)}>
            <Icon d="M11 17 6 12l5-5M18 17l-5-5 5-5" />
          </button>
          <button type="button" className="pv-btn pv-play" aria-label={playing ? t("player.pause") : t("player.play")} title={`${playing ? t("player.pause") : t("player.play")} (Space)`} disabled={failed} onClick={toggle}>
            {playing ? <Icon d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill /> : <Icon d="M7 4.5v15l13-7.5z" fill />}
          </button>
          <button type="button" className="pv-btn" aria-label={t("player.forward")} title={t("player.forward")} disabled={failed} onClick={() => skip(SKIP_SECONDS)}>
            <Icon d="m13 17 5-5-5-5M6 17l5-5-5-5" />
          </button>
        </div>
        <button
          type="button"
          className="pv-safe-toggle"
          aria-pressed={safe}
          disabled={failed}
          onClick={() => {
            setSafe(!safe);
            setSafeZone(!safe);
          }}
        >
          {t("player.safezone")}
        </button>
      </div>

      {(only.music || only.color || only.bumpers) && (
        <p className="pv-note">
          {(only.music || only.color) && t("player.note.look")}
          {(only.music || only.color) && only.bumpers && " "}
          {only.bumpers && t("player.note.bumpers")}
        </p>
      )}
    </div>
  );
}

/** The export's colour settings (ffmpeg eq: contrast around mid grey, brightness as an offset, then saturation) as the
    numbers the preview's SVG filter takes. */
export function previewColor(color: unknown): { filter: boolean; brightness: number; contrast: number; saturation: number; vignette: boolean } {
  const c = (color && typeof color === "object" ? color : {}) as Record<string, unknown>;
  const n = (k: string, d: number) => (typeof c[k] === "number" ? (c[k] as number) : d);
  const brightness = n("brightness", 0), contrast = n("contrast", 1), saturation = n("saturation", 1);
  return { filter: brightness !== 0 || contrast !== 1 || saturation !== 1, brightness, contrast, saturation, vignette: c.vignette === true };
}
