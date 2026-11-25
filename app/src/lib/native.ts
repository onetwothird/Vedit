// Everything that differs between the desktop shell and a plain browser: the file dialog, drag and drop, the AI key.

import { isMock, isTauri } from "../engine/client";
import { isWeb } from "../engine/flavor";
import type { AnalyzeBody } from "../engine/types";
import { t } from "../i18n/t";
import { rememberAsset } from "./overlayMedia";
import { getAiProvider, type AiProvider } from "./prefs";
import { UploadError, kindOfName, uploadAsset } from "./upload";

export const VIDEO_EXTENSIONS = ["mp4", "mov", "m4v", "mkv", "avi", "webm", "mts", "3gp"];
export const VIDEO_ACCEPT = VIDEO_EXTENSIONS.map((e) => `.${e}`).join(",");

export interface PickedFile {
  /** Full path in the desktop app; just the file name in a browser (the mock engine accepts that). */
  path: string;
  name: string;
}

const assetNames = new Map<string, string>(); // `asset:<id>` -> the name the file was uploaded with

/** The name to show for a path: the last part of it, or for an uploaded file (`asset:<id>`) the name it came with. */
export const baseName = (path: string): string =>
  path.startsWith("asset:") ? assetNames.get(path) ?? path : path.split(/[\\/]/).pop() ?? path;

export function isVideoName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  return dot >= 0 && VIDEO_EXTENSIONS.includes(name.slice(dot + 1).toLowerCase());
}

export const toPicked = (path: string): PickedFile => ({ path, name: baseName(path) });

// ---- the website: the browser's file dialog, and the files go to the library ---------------------------------------

/** How far an upload of several files has got: `done` finished, of `n`, and the one in flight at `pct`. */
export type PickProgress = (p: { done: number; n: number; pct: number }) => void;

/** The browser's file dialog (opened from inside a click handler). Resolves [] when cancelled. */
export function pickFilesWeb(accept: string, multiple: boolean): Promise<File[]> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = accept;
    input.multiple = multiple;
    input.hidden = true;
    document.body.appendChild(input);
    const finish = (files: File[]) => {
      input.remove();
      resolve(files);
    };
    input.addEventListener("change", () => finish([...(input.files ?? [])]));
    input.addEventListener("cancel", () => finish([]));
    input.click();
  });
}

/** Opens the file dialog on the website, uploads what was chosen into the library, and returns an `asset:<id>` for each.
    Rejects with the reason (too big, not a kind of file Vedit takes, storage full) so the caller can say it. */
async function pickAssetsWeb(extensions: string[], multiple: boolean, onProgress?: PickProgress): Promise<string[]> {
  const files = await pickFilesWeb(extensions.map((e) => `.${e}`).join(","), multiple);
  const refs: string[] = [];
  for (const [i, file] of files.entries()) {
    const kind = kindOfName(file.name);
    if (!kind) throw new UploadError("failed", t("upload.unsupported", { name: file.name }));
    const { ref } = await uploadAsset(file, kind, (pct) => onProgress?.({ done: i, n: files.length, pct }));
    assetNames.set(ref, file.name);
    rememberAsset(ref, file); // the preview plays the local copy
    refs.push(ref);
  }
  return refs;
}

/** The desktop file dialog. Resolves null when cancelled. Only call inside Tauri. */
export async function pickVideoNative(): Promise<string | null> {
  const { open } = await import("@tauri-apps/plugin-dialog");
  const res = await open({ multiple: false, filters: [{ name: "Video", extensions: VIDEO_EXTENSIONS }] });
  return typeof res === "string" ? res : null;
}

export const AUDIO_EXTENSIONS = ["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"];
export const AUDIO_ACCEPT = ["audio/*", ...AUDIO_EXTENSIONS.map((e) => `.${e}`)].join(",");

/** The desktop file dialog for a music file. Resolves null when cancelled. Only call inside Tauri. */
export async function pickAudioNative(onProgress?: PickProgress): Promise<string | null> {
  if (isWeb()) return (await pickAssetsWeb(AUDIO_EXTENSIONS, false, onProgress))[0] ?? null;
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

/** The user's AI provider and key, when both are set in Settings (the desktop app's Credential Manager, or the mock
    build's memory). Never logged. */
export async function aiOptions(): Promise<AnalyzeBody["ai"] | undefined> {
  const provider = getAiProvider();
  if (!provider) return undefined;
  const key = await loadAiKey(provider).catch(() => null);
  return key ? { provider, api_key: key } : undefined;
}

export const IMAGE_ACCEPT = "image/*";
export const BRAND_VIDEO_ACCEPT = "video/*";

/** A native file dialog restricted to some extensions. Resolves null when cancelled. Only call inside Tauri. */
export async function pickFileNative(name: string, extensions: string[], onProgress?: PickProgress): Promise<string | null> {
  if (isWeb()) return (await pickAssetsWeb(extensions, false, onProgress))[0] ?? null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const res = await open({ multiple: false, filters: [{ name, extensions }] });
  return typeof res === "string" ? res : null;
}

/** A native folder dialog. Resolves null when cancelled. Only call inside Tauri. */
export async function pickFolderNative(): Promise<string | null> {
  if (isWeb()) return null; // there are no folders on the website; screens hide their folder buttons
  const { open } = await import("@tauri-apps/plugin-dialog");
  const res = await open({ directory: true, multiple: false });
  return typeof res === "string" ? res : null;
}

// ---- the AI key: Credential Manager in the desktop app; in the mock build, memory only (never storage) ----------

const memoryKeys = new Map<string, string>();
if (isMock() && typeof window !== "undefined") (window as unknown as { __veditMockKeys: Map<string, string> }).__veditMockKeys = memoryKeys;

async function keyCommand<T>(cmd: string, args: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

export async function saveAiKey(provider: AiProvider, key: string): Promise<void> {
  if (isTauri()) await keyCommand("set_ai_key", { provider, key });
  else memoryKeys.set(provider, key);
}

export async function loadAiKey(provider: AiProvider): Promise<string | null> {
  if (isTauri()) return (await keyCommand<string | null>("get_ai_key", { provider })) || null;
  return memoryKeys.get(provider) ?? null;
}

export async function removeAiKey(provider: AiProvider): Promise<void> {
  if (isTauri()) await keyCommand("delete_ai_key", { provider });
  else memoryKeys.delete(provider);
}

/** Opens Explorer with the file selected. Desktop app only; throws if the file is gone. */
export async function showInFolder(path: string): Promise<void> {
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke("show_in_folder", { path });
}

export const PHOTO_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "bmp"];
/** What a story scene can be: a photo or a clip. */
export const STORY_ACCEPT = [...PHOTO_EXTENSIONS, ...VIDEO_EXTENSIONS].map((e) => `.${e}`).join(",");

export function isStoryMediaName(name: string): boolean {
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
  return PHOTO_EXTENSIONS.includes(ext) || VIDEO_EXTENSIONS.includes(ext);
}

/** The desktop file dialog with several files at once. Resolves [] when cancelled. Only call inside Tauri. */
export async function pickManyNative(name: string, extensions: string[], onProgress?: PickProgress): Promise<string[]> {
  if (isWeb()) return pickAssetsWeb(extensions, true, onProgress);
  const { open } = await import("@tauri-apps/plugin-dialog");
  const res = await open({ multiple: true, filters: [{ name, extensions }] });
  return Array.isArray(res) ? res : typeof res === "string" ? [res] : [];
}
