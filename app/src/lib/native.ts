// Everything that differs between the desktop shell and a plain browser: the file dialog, drag and drop, the AI key.

import { isTauri } from "../engine/client";
import type { AnalyzeBody } from "../engine/types";
import { getAiProvider } from "./prefs";

export const VIDEO_EXTENSIONS = ["mp4", "mov", "m4v", "mkv", "avi", "webm", "mts", "3gp"];
export const VIDEO_ACCEPT = VIDEO_EXTENSIONS.map((e) => `.${e}`).join(",");

export interface PickedFile {
  /** Full path in the desktop app; just the file name in a browser (the mock engine accepts that). */
  path: string;
  name: string;
}

export const baseName = (path: string): string => path.split(/[\\/]/).pop() ?? path;

export function isVideoName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && VIDEO_EXTENSIONS.includes(name.slice(dot + 1).toLowerCase());
}

export const toPicked = (path: string): PickedFile => ({ path, name: baseName(path) });

/** The desktop file dialog. Resolves null when cancelled. Only call inside Tauri. */
export async function pickVideoNative(): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog");
  const res = await open({ multiple: false, filters: [{ name: "Video", extensions: VIDEO_EXTENSIONS }] });
  return typeof res === "string" ? res : null;
}

export const AUDIO_EXTENSIONS = ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"];
export const AUDIO_ACCEPT = ["audio/*", ...AUDIO_EXTENSIONS.map((e) => `.${e}`)].join(",");

/** The desktop file dialog for a music file. Resolves null when cancelled. Only call inside Tauri. */
export async function pickAudioNative(): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog");
  const res = await open({ multiple: false, filters: [{ name: "Audio", extensions: AUDIO_EXTENSIONS }] });
  return typeof res === "string" ? res : null;
}

export interface DropHandlers {
  onOver(over: boolean): void;
  onDrop(paths: string[]): void;
}

/** Drag-and-drop paths from the desktop shell. Returns an unsubscribe. No-op outside Tauri. */
export function listenNativeDrop(h: DropHandlers): () => void {
  if (!isTauri()) return () => undefined;
  let off: (() => void) | null = null;
  let stopped = false;
  void import("@tauri-apps/api/webview").then(async ({ getCurrentWebview }) => {
    const un = await getCurrentWebview().onDragDropEvent((e) => {
      const p = e.payload;
      if (p.type === "enter" || p.type === "over") h.onOver(true);
      else if (p.type === "leave") h.onOver(false);
      else if (p.type === "drop") {
        h.onOver(false);
        h.onDrop(p.paths);
      }
    });
    if (stopped) un();
    else off = un;
  });
  return () => {
    stopped = true;
    off?.();
  };
}

/** The analyze body with the user's AI key, only inside the desktop app and only when one is saved. Never logged. */
export async function analyzeBody(): Promise<AnalyzeBody> {
  const provider = getAiProvider();
  if (!provider || !isTauri()) return {};
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const key = await invoke<string | null>("get_ai_key", { provider });
    return key ? { ai: { provider, api_key: key } } : {};
  } catch {
    return {};
  }
}
