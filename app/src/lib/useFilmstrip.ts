import { useEffect, useMemo, useState } from "react";
import { engine, engineMediaUrl, isMock } from "../engine/client";
import { isCloud } from "../engine/flavor";
import type { FilmstripInfo } from "../engine/types";
import { useMediaUrl } from "./useMediaUrl";

/** The project's thumbnail sprite with a loadable image URL, or null while loading or when there is none. */
export function useFilmstrip(pid: string | undefined): FilmstripInfo | null {
  const [info, setInfo] = useState<FilmstripInfo | null>(null);
  useEffect(() => {
    setInfo(null);
    if (!pid) return;
    let live = true;
    (async () => {
      try {
        const got = await engine.filmstrip(pid);
        if (live) setInfo(got);
      } catch {
        // no thumbnails: the screen shows a neutral tile, never an error
      }
    })();
    return () => {
      live = false;
    };
  }, [pid]);

  // the image URL carries the engine's token, which the website renews; see useMediaUrl
  const { url, onError } = useMediaUrl(() =>
    info && pid && !isMock() ? engineMediaUrl(`/projects/${encodeURIComponent(pid)}/filmstrip.jpg`) : null,
  );
  // a sprite is a CSS background, which reports no error: load it once here to find out
  useEffect(() => {
    if (!isCloud() || !url) return;
    const probe = new Image();
    probe.onerror = onError;
    probe.src = url;
    return () => {
      probe.onerror = null;
    };
  }, [url, onError]);

  return useMemo(() => {
    if (!info) return null;
    if (isMock()) return info;
    return url ? { ...info, image: url } : null;
  }, [info, url]);
}
