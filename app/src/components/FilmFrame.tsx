import type { FilmstripInfo } from "../engine/types";
import { spriteStyle } from "../lib/filmstrip";

/** The video frame at time `t`, or a neutral tile when there is no filmstrip. Decorative. */
export default function FilmFrame({ film, t, width, className = "" }: {
  film: FilmstripInfo | null;
  t: number;
  width: number;
  className?: string;
}) {
  if (!film) {
    return <div className={`film-frame none ${className}`} style={{ width, height: Math.round((width * 9) / 16) }} aria-hidden="true" />;
  }
  // JSON.stringify quotes the URL, so a quote or backslash in a path stays inside the CSS string
  const style = spriteStyle(film, t, width, `url(${JSON.stringify(film.image)})`);
  return <div className={`film-frame ${className}`} style={style} aria-hidden="true" />;
}
