import { useEffect, useState } from "react";
import { engine, isTauri } from "../engine/client";
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
        if (isTauri()) {
          const { convertFileSrc } = await import("@tauri-apps/api/core");
          if (!live) return;
          setFilm({ ...info, image: convertFileSrc(info.image) });
        } else {
          setFilm(info);
        }
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
