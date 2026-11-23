import { useEffect, useRef, useState } from "react";
import type React from "react";
import podcast from "./podcast.mp4";
import poster from "./podcast-poster.jpg";
import banca from "./img/banca.jpg";
import jeepney from "./img/jeepney.jpg";
import lagoon from "./img/lagoon.jpg";
import map from "./img/map.jpg";
import postoffice from "./img/postoffice.jpg";
import reel from "./img/reel.jpg";
import vlog from "./img/vlog.jpg";

/* Tile artwork for the Home screen. Each picture shows what that mode does to a video,
   built from the app's own parts: timelines, waveforms, phone frames, caption chips. */

/** Deterministic bar heights (0..1) so the waveform looks the same on every render. */
export function bars(n: number, seed: number): number[] {
  let s = seed;
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    s = (s * 9301 + 49297) % 233280;
    const r = s / 233280;
    const swell = 0.55 + 0.45 * Math.sin(i / 7 + seed);
    out.push(Math.max(0.12, Math.min(1, r * 0.7 * swell + 0.18)));
  }
  return out;
}

/* podcast.mp4: Pexels video 38655900 ("Engaging podcast interview in professional studio", Pexels license,
   free for commercial use), first 6 s, 960x540, no audio. podcast-poster.jpg is its frame at 1 s. */
const VIDEO_ASPECT = 16 / 9;

/** Where to place a cover-fitted video inside a box of aspect `boxAspect` so the point (cx, cy)
    (fractions of the frame) sits as close to the centre as the edges allow, magnified by `zoom`.
    Returns CSS percentages of the box. */
export function cropPlacement(boxAspect: number, cx: number, cy: number, zoom = 1) {
  const cover = boxAspect < VIDEO_ASPECT
    ? { w: (100 * VIDEO_ASPECT) / boxAspect, h: 100 }
    : { w: 100, h: (100 * boxAspect) / VIDEO_ASPECT };
  const w = cover.w * zoom;
  const h = cover.h * zoom;
  const clamp = (v: number, lo: number) => Math.min(0, Math.max(lo, v));
  return { width: w, height: h, left: clamp(50 - cx * w, 100 - w), top: clamp(50 - cy * h, 100 - h) };
}

/** Plays its videos only while on screen and while motion is allowed; otherwise shows the poster. */
function useInViewPlayback(root: React.RefObject<HTMLElement | null>) {
  const [motion, setMotion] = useState(false);
  useEffect(() => {
    const el = root.current;
    if (!el || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const videos = () => Array.from(el.querySelectorAll("video"));
    let visible = false;
    const sync = () => {
      const play = visible && !mq.matches && document.visibilityState === "visible";
      setMotion(!mq.matches);
      for (const v of videos()) {
        if (play) v.play().catch(() => undefined);
        else v.pause();
      }
    };
    const io = typeof IntersectionObserver === "function"
      ? new IntersectionObserver(([e]) => { visible = e.isIntersecting; sync(); })
      : null;
    if (io) io.observe(el); else { visible = true; sync(); }
    mq.addEventListener?.("change", sync);
    document.addEventListener("visibilitychange", sync);
    return () => {
      io?.disconnect();
      mq.removeEventListener?.("change", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [root]);
  return motion;
}

type Output = {
  shape: "9:16" | "1:1" | "16:9";
  aspect: number;
  focus: [number, number, number];
  start: number;
  time: string;
  label: string;
};

/* Three different things vedit makes from the same episode: each its own moment, shape and caption style. */
const OUTPUTS: Output[] = [
  { shape: "9:16", aspect: 9 / 16, focus: [0.75, 0.42, 1.35], start: 0, time: "06:12", label: "Reels, Shorts, TikTok" },
  { shape: "1:1", aspect: 1, focus: [0.24, 0.42, 1.45], start: 2.2, time: "21:40", label: "Feed post" },
  { shape: "16:9", aspect: 16 / 9, focus: [0.5, 0.45, 1.3], start: 4, time: "38:05", label: "YouTube" },
];

function Cut({ o, children }: { o: Output; children: React.ReactNode }) {
  const p = cropPlacement(o.aspect, ...o.focus);
  const style = { width: `${p.width}%`, height: `${p.height}%`, left: `${p.left}%`, top: `${p.top}%` };
  return (
    <figure className={`cut cut-${o.shape.replace(":", "x")}`} title={o.label}>
      <div className="cut-frame">
        <video
          src={podcast}
          poster={poster}
          muted
          loop
          playsInline
          preload="metadata"
          style={style}
          onLoadedMetadata={(e) => { e.currentTarget.currentTime = o.start; }}
        />
        {children}
        <span className="cut-time mono">{o.time}</span>
      </div>
    </figure>
  );
}

/** Hero: an hour-long podcast (frame + timeline) and three clips vedit cut from it, each reframed
    for its platform with its own caption style. Matching timecodes tie each clip to its moment. */
export function ClipArt() {
  const root = useRef<HTMLDivElement>(null);
  const motion = useInViewPlayback(root);
  const N = 72;
  const W = bars(N, 3);
  const spans: [number, number][] = [[6, 13], [30, 36], [53, 61]];
  return (
    <div ref={root} className={motion ? "hero-art moving" : "hero-art"} aria-hidden="true">
      <div className="src">
        <div className="src-frame">
          <video src={podcast} poster={poster} muted loop playsInline preload="metadata" />
          <span className="src-dur mono">1:12:40</span>
        </div>
        <svg className="wave" viewBox={`0 0 ${N * 5} 34`} preserveAspectRatio="none">
          {W.map((v, i) => {
            const on = spans.findIndex(([a, b]) => i >= a && i <= b);
            const h = 4 + v * 26;
            return <rect key={i} className={on >= 0 ? `art-bar on d${on}` : "art-bar"} x={i * 5} y={30 - h} width={3.4} height={h} rx={1} />;
          })}
        </svg>
        <div className="wave-times mono">
          {spans.map(([a], i) => (
            <span key={a} style={{ left: `${(a / N) * 100}%` }}>{OUTPUTS[i].time}</span>
          ))}
        </div>
      </div>

      <div className="cuts">
        <Cut o={OUTPUTS[0]}>
          <p className="cap-pop">
            <span>NOBODY</span> <span>TELLS</span> <span>YOU</span>
          </p>
        </Cut>
        <div className="cuts-col">
          <Cut o={OUTPUTS[1]}>
            <p className="cap-box">The ₱500 mistake</p>
          </Cut>
          <Cut o={OUTPUTS[2]}>
            <p className="cap-sub">
              <span className="spk-a">I almost quit after a year.</span>
              <span className="spk-b">What kept you going?</span>
            </p>
          </Cut>
        </div>
      </div>
    </div>
  );
}

/* Coming-soon tiles: Pexels photos (Pexels license), 560px wide. Each tile is composed for its own job. */

/** Vlog: the take with its transcript; filler words struck out and the length it comes down to. */
export function VlogArt() {
  const words: [string, boolean][] = [["So", false], ["um,", true], ["today", false], ["uh,", true], ["we fix it", false]];
  return (
    <div className="sa sa-vlog" aria-hidden="true">
      <img src={vlog} alt="" loading="lazy" />
      <span className="sa-chip mono">3:12 → 2:41</span>
      <p className="sa-transcript">
        {words.map(([w, cut], i) => (
          <span key={i} className={cut ? "cut" : undefined}>{w}</span>
        ))}
      </p>
    </div>
  );
}

/** Quick Reel: one phone video, fast cut, pop captions. */
export function ReelArt() {
  return (
    <div className="sa sa-reel" aria-hidden="true">
      <div className="sa-phone">
        <img src={reel} alt="" loading="lazy" />
        <p className="sa-pop"><span>WAIT</span> <span>FOR</span> <span>IT</span></p>
      </div>
      <ol className="sa-shots">
        {[["0.8s", "50% 20%"], ["1.2s", "50% 45%"], ["0.6s", "30% 70%"]].map(([len, pos]) => (
          <li key={len}>
            <img src={reel} alt="" loading="lazy" style={{ objectPosition: pos }} />
            <span className="mono">{len}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Photos + script: three photos and a short script become one Reel with a title card. */
export function PhotosArt() {
  return (
    <div className="sa sa-photos" aria-hidden="true">
      <div className="sa-stack">
        <img src={postoffice} alt="" loading="lazy" />
        <img src={jeepney} alt="" loading="lazy" />
        <img src={lagoon} alt="" loading="lazy" />
      </div>
      <ol className="sa-script">
        <li>Day 1, Manila</li>
        <li>Day 2, El Nido</li>
      </ol>
      <div className="sa-phone">
        <img src={lagoon} alt="" loading="lazy" />
        <p className="sa-title">3 days in the Philippines</p>
      </div>
    </div>
  );
}

/** Batch: one saved template applied to a week of inputs. */
export function BatchArt() {
  const days: [string, string, string][] = [
    ["Mon", map, "7,641 islands"],
    ["Tue", jeepney, "Since 1945"],
    ["Wed", lagoon, "El Nido"],
    ["Thu", banca, "The banca"],
  ];
  return (
    <div className="sa sa-batch" aria-hidden="true">
      {days.map(([d, src, fact]) => (
        <figure key={d}>
          <div className="sa-mini">
            <img src={src} alt="" loading="lazy" />
            <span className="sa-tag">Fact</span>
            <span className="sa-fact">{fact}</span>
          </div>
          <figcaption className="mono">{d}</figcaption>
        </figure>
      ))}
    </div>
  );
}
