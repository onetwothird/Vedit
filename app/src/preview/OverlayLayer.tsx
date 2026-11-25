import { useEffect, useRef, type CSSProperties } from "react";
import type { Overlay } from "../engine/types";
import { overlayUrl } from "../lib/overlayMedia";
import { useMediaUrl } from "../lib/useMediaUrl";
import { toOutputTime, type Range } from "./playback";

/** Where an overlay starts in output time, or null when its moment was cut (the export skips it too). */
export function overlayStart(ranges: Range[], o: Overlay): number | null {
  return toOutputTime(ranges, o.at.t);
}

/** The overlays showing at output time `out`, in the order the export stacks them: b-roll first, text on top. */
export function activeOverlays(overlays: readonly Overlay[], ranges: Range[], out: number): { o: Overlay; since: number }[] {
  const on = overlays
    .map((o) => ({ o, at: overlayStart(ranges, o) }))
    .filter((x): x is { o: Overlay; at: number } => x.at !== null && out >= x.at && out < x.at + x.o.duration)
    .map(({ o, at }) => ({ o, since: out - at }));
  return [...on.filter((x) => x.o.type !== "text"), ...on.filter((x) => x.o.type === "text")];
}

/** Text and b-roll drawn over the preview frame, matching what the export draws. */
const FADE = 0.25; // seconds b-roll fades in and out (render.py's default "fade")

/** How visible b-roll is `since` seconds into its `duration`: the export's fade in and out. */
export function brollOpacity(since: number, duration: number, fade = FADE): number {
  if (fade <= 0) return 1;
  return Math.max(0, Math.min(1, since / fade, (duration - since) / fade));
}

export default function OverlayLayer({ overlays, ranges, out, frameWidth, frameHeight, playing }: {
  overlays: readonly Overlay[];
  ranges: Range[];
  out: number;
  frameWidth: number;
  frameHeight: number;
  playing: boolean;
}) {
  const shown = activeOverlays(overlays, ranges, out);
  if (!shown.length) return null;
  return (
    <div className="pv-ov" aria-hidden="true" style={{ "--m": `${Math.round(frameWidth * 0.04)}px` } as CSSProperties}>
      {shown.map(({ o, since }, i) =>
        o.type === "text" ? (
          <p key={o.id ?? i} className={`pv-ov-text ${o.position}`} style={{ fontSize: Math.max(10, frameHeight * 0.05 * (o.size ?? 1)) }}>
            <span>{o.text}</span>
          </p>
        ) : (
          <Broll key={o.id ?? i} o={o} since={since} playing={playing} />
        ),
      )}
    </div>
  );
}

function Broll({ o, since, playing }: { o: Extract<Overlay, { type: "image" | "video" }>; since: number; playing: boolean }) {
  const { url, onError } = useMediaUrl(() => overlayUrl(o.path)); // a fresh link when the website's token changes
  const video = useRef<HTMLVideoElement>(null);
  const cls = `pv-ov-media ${o.mode === "pip" ? `pip ${o.position ?? "top-right"}` : "full"}`;
  useEffect(() => {
    const v = video.current;
    if (!v) return;
    const want = (o.src_in ?? 0) + since;
    if (Math.abs(v.currentTime - want) > 0.3) v.currentTime = want;
    if (playing) v.play().catch(() => undefined);
    else v.pause();
  }, [since, playing, o.src_in]);
  const style = { opacity: brollOpacity(since, o.duration) };
  if (!url) return <div className={`${cls} missing`} style={style}><span>{o.path.split(/[\\/]/).pop()}</span></div>;
  return o.type === "image" ? (
    <img className={cls} src={url} alt="" style={style} onError={onError} />
  ) : (
    <video ref={video} className={cls} src={url} muted playsInline preload="auto" style={style} onError={onError} />
  );
}
