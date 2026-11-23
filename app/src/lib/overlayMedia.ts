import { convertFileSrc } from "@tauri-apps/api/core";
import { isTauri } from "../engine/client";

/* B-roll files for the preview. The installed app loads any path through the asset protocol. A browser cannot read a
   path, so a file picked there is kept as an object URL for this session, under the name it was saved with. */

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
  if (isTauri()) return convertFileSrc(path);
  return picked.get(path) ?? null;
}

export const BROLL_IMAGE = ["png", "jpg", "jpeg", "webp"];
export const BROLL_VIDEO = ["mp4", "mov", "m4v", "webm", "mkv"];

/** image or video, from the file's extension. */
export function brollType(path: string): "image" | "video" {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return BROLL_IMAGE.includes(ext) ? "image" : "video";
}
