import { Fragment, useEffect, useRef, useState } from "react";
import { t as tr } from "../../i18n/t";
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

/* A local, git-ignored preview of other footage (hero-local.mp4 + hero-local.jpg, 16:9), used only by the dev
   server. Third-party footage stays out of git and out of release builds until it is cleared for use. */
const LOCAL = import.meta.env.DEV
  ? (import.meta.glob("./hero-local.{mp4,jpg}", { eager: true, import: "default" }) as Record<string, string>)
  : {};
const HERO_LOCAL = Boolean(LOCAL["./hero-local.mp4"]);
const heroVideo = LOCAL["./hero-local.mp4"] ?? podcast;
/** podcast.mp4 was saved without audio; the local preview keeps its sound, so only it offers the sound button. */
export const HERO_HAS_SOUND = HERO_LOCAL;
const heroPoster = LOCAL["./hero-local.jpg"] ?? poster;

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

/* Where the people sit in the local preview's wide shot of three hosts: right (cap), left, middle. */
const LOCAL_FOCUS: Record<Output["shape"], [number, number, number]> = {
  "9:16": [0.83, 0.46, 1.3],
  "1:1": [0.2, 0.5, 1.45],
  "16:9": [0.5, 0.42, 1.35],
};

/* What is said in the local preview's 10 s loop (start second, words), transcribed from its audio. */
export const LOCAL_CUES: [number, string][] = [
  [0, "Bakit? Bawal ba?"],
  [0.8, "Hindi, wala akong sinabi."],
  [1.9, "Akala po niya, sabi niya po sa akin kanina parang,"],
  [4.4, "si Jy po, di ba, 1983 po pinanganak?"],
  [7.3, "Sabi niya po."],
  [8.7, "Sir, may narinig ka."],
];
const LOOP_END = 10;

/** The line being said at second `t`. */
export function cueAt(t: number): string {
  let line = LOCAL_CUES[0][1];
  for (const [at, words] of LOCAL_CUES) if (t >= at) line = words;
  return line;
}

/** Pop captions show up to three words at a time, spread evenly over the line's time. */
export function popAt(t: number): string[] {
  const i = LOCAL_CUES.reduce((k, [at], j) => (t >= at ? j : k), 0);
  const [at, line] = LOCAL_CUES[i];
  const end = LOCAL_CUES[i + 1]?.[0] ?? LOOP_END;
  const words = line.split(" ");
  const chunks = Math.ceil(words.length / 3);
  const c = Math.min(chunks - 1, Math.floor(((t - at) / (end - at)) * chunks));
  return words.slice(c * 3, c * 3 + 3);
}

function Cut({ o, children, caption }: { o: Output; children: React.ReactNode; caption?: (t: number) => React.ReactNode }) {
  const [t, setT] = useState(o.start);
  const p = cropPlacement(o.aspect, ...(HERO_LOCAL ? LOCAL_FOCUS[o.shape] : o.focus));
  const style = { width: `${p.width}%`, height: `${p.height}%`, left: `${p.left}%`, top: `${p.top}%` };
  return (
    <figure className={`cut cut-${o.shape.replace(":", "x")}`} title={o.label}>
      <div className="cut-frame">
        <video
          src={heroVideo}
          poster={heroPoster}
          muted
          loop
          playsInline
          preload="metadata"
          style={style}
          onLoadedMetadata={(e) => { e.currentTarget.currentTime = o.start; }}
          onTimeUpdate={caption ? (e) => setT(e.currentTarget.currentTime) : undefined}
        />
        {caption ? caption(t) : children}
        <span className="cut-time mono">{o.time}</span>
      </div>
    </figure>
  );
}

/** Hero: an hour-long podcast (frame + timeline) and three clips vedit cut from it, each reframed
    for its platform with its own caption style. Matching timecodes tie each clip to its moment. */
export function ClipArt() {
  const root = useRef<HTMLDivElement>(null);
  const source = useRef<HTMLVideoElement>(null);
  const motion = useInViewPlayback(root);
  const [sound, setSound] = useState(false);
  function toggleSound() {
    const v = source.current;
    if (!v) return;
    const on = !sound;
    v.muted = !on;
    if (on) {
      v.currentTime = 0; // from the top, so the line starts with its first word
      v.play().catch(() => undefined);
    }
    setSound(on);
  }
  const N = 72;
  const W = bars(N, 3);
  const spans: [number, number][] = [[6, 13], [30, 36], [53, 61]];
  return (
    <div ref={root} className={motion ? "hero-art moving" : "hero-art"}>
      <div className="src">
        <div className="src-frame">
          <video ref={source} src={heroVideo} poster={heroPoster} muted loop playsInline preload="metadata" aria-hidden="true" />
          <span className="src-dur mono" aria-hidden="true">1:12:29</span>
          {HERO_HAS_SOUND && (
            <button
              type="button"
              className="hero-sound"
              aria-pressed={sound}
              aria-label={tr("home.sound")}
              title={tr(sound ? "home.sound.off" : "home.sound.on")}
              onClick={toggleSound}
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M11 5 6 9H3v6h3l5 4z" />
                {sound ? <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" /> : <path d="m16 9 6 6M22 9l-6 6" />}
              </svg>
              <span>{tr(sound ? "home.sound.on.label" : "home.sound.off.label")}</span>
            </button>
          )}
        </div>
        <svg className="wave" aria-hidden="true" viewBox={`0 0 ${N * 5} 34`} preserveAspectRatio="none">
          {W.map((v, i) => {
            const on = spans.findIndex(([a, b]) => i >= a && i <= b);
            const h = 4 + v * 26;
            return <rect key={i} className={on >= 0 ? `art-bar on d${on}` : "art-bar"} x={i * 5} y={30 - h} width={3.4} height={h} rx={1} />;
          })}
        </svg>
        <div className="wave-times mono" aria-hidden="true">
          {spans.map(([a], i) => (
            <span key={a} style={{ left: `${(a / N) * 100}%` }}>{OUTPUTS[i].time}</span>
          ))}
        </div>
      </div>

      <div className="cuts" aria-hidden="true">
        <Cut o={OUTPUTS[0]} caption={HERO_LOCAL ? (t) => (
          <p className="cap-pop synced">
            {popAt(t).map((w, i) => <Fragment key={i}>{i > 0 && " "}<span>{w.toUpperCase()}</span></Fragment>)}
          </p>
        ) : undefined}>
          <p className="cap-pop">
            <span>NOBODY</span> <span>TELLS</span> <span>YOU</span>
          </p>
        </Cut>
        <div className="cuts-col">
          <Cut o={OUTPUTS[1]} caption={HERO_LOCAL ? (t) => <p className="cap-box">{cueAt(t)}</p> : undefined}>
            <p className="cap-box">The ₱500 mistake</p>
          </Cut>
          <Cut o={OUTPUTS[2]} caption={HERO_LOCAL ? (t) => <p className="cap-sub"><span className="spk-a">{cueAt(t)}</span></p> : undefined}>
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
