import { convertFileSrc } from "@tauri-apps/api/core";
import { isMock, isTauri } from "../engine/client";
import type { Project } from "../engine/types";

/** What the <video> loads: the proxy if analysis made one, else the source. */
export function previewSrc(p: Pick<Project, "source" | "settings">): string {
  const path = p.settings.proxy || p.source;
  if (isTauri()) return convertFileSrc(path);
  if (isMock()) return `${import.meta.env.BASE_URL}mock/sample.mp4`; // a tiny generated clip stands in for the file
  return path;
}
