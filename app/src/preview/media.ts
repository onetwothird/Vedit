import { engineMediaUrl, isMock } from "../engine/client";
import type { Project } from "../engine/types";

/** What the <video> loads: the engine streams the proxy if analysis made one, else the source. The same URL works in
    the installed app and in a browser, which cannot open a path on disk. */
export function previewSrc(p: Pick<Project, "id">): string {
  if (isMock()) return `${import.meta.env.BASE_URL}mock/sample.mp4`; // a tiny generated clip stands in for the file
  return engineMediaUrl(`/projects/${encodeURIComponent(p.id)}/media`) ?? "";
}
