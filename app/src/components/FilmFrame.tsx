import type { FilmstripInfo } from "../engine/types";
import { fitWidth, spriteStyle } from "../lib/filmstrip";

/** The video frame at time `t`, or a neutral tile when there is no filmstrip. Decorative. */
export default function FilmFrame({ film, t, width, fit, className = "" }: {
  film: FilmstripInfo | null;
  t: number;
  /** Display width. Ignored when `fit` is given. */
  width?: number;
  /** Fit the whole frame inside this box without distortion. */
  fit?: { width: number; height: number };
  className?: string;
}) {
  const w = fit ? fitWidth(film, fit.width, fit.height) : (width ?? 64);
  if (!film) {
    return <div className={`film-frame none ${className}`} style={{ width: w, height: Math.round((w * 9) / 16) }} aria-hidden="true" />;
  }
  // JSON.stringify quotes the URL, so a quote or backslash in a path stays inside the CSS string
  const style = spriteStyle(film, t, w, `url(${JSON.stringify(film.image)})`);
  return <div className={`film-frame ${className}`} style={style} aria-hidden="true" />;
}
