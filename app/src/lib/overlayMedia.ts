import { engineMediaUrl, isMock } from "../engine/client";

/* B-roll files for the preview. The engine streams a path once a clip uses it. A file picked in a browser has no
   path, so it is also kept as an object URL for this session, under the name it was saved with. */

const picked = new Map<string, string>();

/** Remember a file picked in the browser; returns the path to save in the edit. */
export function rememberPicked(file: File): string {
  const path = file.name;
  const old = picked.get(path);
  if (old) URL.revokeObjectURL(old);
  picked.set(path, URL.createObjectURL(file));
  return path;
}

/** What the preview loads for a b-roll path, or null when this session cannot show it. */
export function overlayUrl(path: string): string | null {
  const local = picked.get(path);
  if (local) return local;
  return isMock() ? null : engineMediaUrl("/file", { path });
}

export const BROLL_IMAGE = ["png", "jpg", "jpeg", "webp"];
export const BROLL_VIDEO = ["mp4", "mov", "m4v", "webm", "mkv"];

/** image or video, from the file's extension. */
export function brollType(path: string): "image" | "video" {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return BROLL_IMAGE.includes(ext) ? "image" : "video";
}

/** The object URL of a file picked in the browser this session, if any. */
export const pickedUrl = (path: string): string | null => picked.get(path) ?? null;
