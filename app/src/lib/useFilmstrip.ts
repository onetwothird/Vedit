import { useEffect, useState } from "react";
import { engine, engineMediaUrl, isMock } from "../engine/client";
import type { FilmstripInfo } from "../engine/types";

/** The project's thumbnail sprite with a loadable image URL, or null while loading or when there is none. */
export function useFilmstrip(pid: string | undefined): FilmstripInfo | null {
  const [film, setFilm] = useState<FilmstripInfo | null>(null);
  useEffect(() => {
    setFilm(null);
    if (!pid) return;
    let live = true;
    (async () => {
      try {
        const info = await engine.filmstrip(pid);
        if (!live) return;
        const image = isMock() ? info.image : engineMediaUrl(`/projects/${encodeURIComponent(pid)}/filmstrip.jpg`);
        if (image) setFilm({ ...info, image });
      } catch {
        // no thumbnails: the screen shows a neutral tile, never an error
      }
    })();
    return () => {
      live = false;
    };
  }, [pid]);
  return film;
}
