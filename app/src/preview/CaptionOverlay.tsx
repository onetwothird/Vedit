import type { CSSProperties } from "react";
import type { CaptionTrack } from "../engine/types";
import { activeCaption } from "./playback";

function rgba(hex: string, alpha: number): string {
  const h = hex.replace("#", "");
  const n = h.length === 6 ? parseInt(h, 16) : 0;
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/**
 * The caption group on screen at track time `outT` (the caller has already added the intro's lead-in), drawn the way the
 * export burns it in: the active word in the highlight colour, scaled by active_scale; sizes are fractions of the
 * frame height so it matches at any preview size.
 */
export default function CaptionOverlay({ track, outT, frameHeight }: { track: CaptionTrack; outT: number; frameHeight: number }) {
  const hit = activeCaption(track, outT);
  if (!hit) return null;
  const { event, activeWord } = hit;
  const H = frameHeight;
  const box = track.style === "box";
  const clean = track.style === "clean";
  const outline = Math.round(track.outline_frac * H * 100) / 100;
  const shadow = Math.round(track.shadow * (H / 1080) * 100) / 100;

  const wrap: CSSProperties = {
    position: "absolute", left: 0, right: 0, bottom: `${track.baseline_frac * H}px`, padding: "0 6%",
    textAlign: "center", pointerEvents: "none", lineHeight: 1.1, fontWeight: 700,
    fontFamily: `${track.font}, Arial, sans-serif`, fontSize: `${track.size_frac * H}px`,
    textTransform: track.uppercase ? "uppercase" : "none", color: track.color,
    ...(box
      ? {}
      : {
          paintOrder: "stroke fill", WebkitTextStrokeWidth: `${outline}px`, WebkitTextStrokeColor: track.outline_color,
          textShadow: shadow > 0 ? `${shadow}px ${shadow}px 0 rgba(0, 0, 0, 0.5)` : undefined,
        }),
  };
  const boxStyle: CSSProperties | undefined = box
    ? { background: rgba(track.back_color ?? "#000000", track.back_alpha ?? 0.44), padding: `0 ${Math.round(track.outline_frac * H)}px` }
    : undefined;
  const groupColor = clean ? track.speaker_colors[event.words[0]?.speaker ?? ""] ?? track.color : track.color;

  const words = event.words.map((w, i) => {
    const active = !clean && i === activeWord;
    const style: CSSProperties = { color: active ? track.speaker_colors[w.speaker ?? ""] ?? track.highlight : groupColor };
    if (active) {
      style.display = "inline-block";
      if (track.active_scale !== 100) style.transform = `scale(${track.active_scale / 100})`;
    }
    return (
      <span key={i}>
        {i > 0 && " "}
        <span data-word="" data-active={active ? "true" : undefined} style={style}>{w.text}</span>
      </span>
    );
  });

  return (
    <div data-caption="" style={wrap} aria-hidden="true">
      {box ? <span data-box="" style={boxStyle}>{words}</span> : words}
    </div>
  );
}
