// The website's way to bring a file in: the engine hands out a presigned URL, the browser PUTs the bytes straight to
// storage (an XMLHttpRequest, because fetch cannot report upload progress), and the engine is told when it is done.

import { engine } from "../engine/client";
import { EngineError, type Asset, type AssetKind, type Usage } from "../engine/types";
import { t } from "../i18n/t";

const MB = 1024 ** 2;
const GB = 1024 ** 3;

/** What the engine accepts per kind of file (the same numbers as its own check). */
export const KIND_LIMITS: Record<AssetKind, number> = { video: 3 * GB, image: 20 * MB, audio: 200 * MB, font: 10 * MB, lut: 10 * MB };
export const MAX_VIDEO_SECONDS = 60 * 60;

const KIND_TYPES: Record<string, string> = {
  mp4: "video/mp4", mov: "video/quicktime", m4v: "video/x-m4v", webm: "video/webm", mkv: "video/x-matroska",
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", gif: "image/gif",
  mp3: "audio/mpeg", wav: "audio/wav", m4a: "audio/mp4", aac: "audio/aac", ogg: "audio/ogg", flac: "audio/flac",
  ttf: "font/ttf", otf: "font/otf", cube: "text/plain",
};

/** Of these extensions, the ones the engine accepts: what a file dialog may offer (an .opus or .avi would only be
    refused after the pick). */
export const acceptedExtensions = (extensions: string[]): string[] => extensions.filter((e) => kindOfName(`x.${e}`) !== null);

const extOf = (name: string): string => name.split(".").pop()?.toLowerCase() ?? "";

/** The kind of asset a file name stands for, or null when Vedit takes no such file. */
export function kindOfName(name: string): AssetKind | null {
  const type = KIND_TYPES[extOf(name)];
  if (!type) return null;
  return type.startsWith("video/") ? "video" : type.startsWith("image/") ? "image" : type.startsWith("audio/") ? "audio" : type.startsWith("font/") ? "font" : "lut";
}

/** The Content-Type the PUT must carry, which is also what the engine signed: the browser's own when it names the
    right kind of file, else one from the extension (a .mkv often has none). */
function contentTypeOf(file: File, kind: AssetKind): string {
  const own = file.type;
  if ((kind === "video" || kind === "image" || kind === "audio") && own.startsWith(`${kind}/`)) return own;
  return KIND_TYPES[extOf(file.name)] ?? "application/octet-stream";
}

export type UploadErrorCode = "aborted" | "too_large" | "failed";

export class UploadError extends Error {
  constructor(public code: UploadErrorCode, message: string) {
    super(message);
    this.name = "UploadError";
  }
}

const sizeLabel = (bytes: number): string => (bytes >= GB ? `${bytes / GB} GB` : `${bytes / MB} MB`);

/** "A video can be up to 3 GB", for a file that is too big for its kind. */
export function tooLargeMessage(kind: AssetKind): string {
  return t("upload.toolarge", { what: t(`upload.kind.${kind}`), limit: sizeLabel(KIND_LIMITS[kind]) });
}

const aborted = (): UploadError => new UploadError("aborted", t("upload.cancelled"));

function put(url: string, file: File, contentType: string, onProgress: (pct: number) => void, signal?: AbortSignal): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const stop = () => xhr.abort();
    const done = () => signal?.removeEventListener("abort", stop);
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", contentType); // Content-Length is the file's own size; nothing else is signed
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && e.total > 0) onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      done();
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new UploadError("failed", t("upload.failed")));
    };
    xhr.onerror = () => {
      done();
      reject(new UploadError("failed", t("upload.failed")));
    };
    xhr.onabort = () => {
      done();
      reject(aborted());
    };
    signal?.addEventListener("abort", stop, { once: true });
    xhr.send(file);
  });
}

/** Uploads `file` to storage and returns the key that names it. Progress is 0 to 100. Aborting the signal stops the
    transfer and rejects with code "aborted"; nothing is created in the library until commitAsset or a project is made. */
export async function uploadFile(
  file: File, kind: AssetKind, onProgress: (pct: number) => void, signal?: AbortSignal,
): Promise<{ upload_key: string }> {
  if (signal?.aborted) throw aborted();
  if (file.size > KIND_LIMITS[kind]) throw new UploadError("too_large", tooLargeMessage(kind));
  const type = contentTypeOf(file, kind);
  let ticket;
  try {
    ticket = await engine.createUpload({ kind, name: file.name, size: file.size, content_type: type });
  } catch (e) {
    if (signal?.aborted) throw aborted();
    throw e; // the engine's own words (storage full, wrong file type, sign in)
  }
  if (signal?.aborted) throw aborted();
  await put(ticket.url, file, type, onProgress, signal);
  return { upload_key: ticket.upload_key };
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** Adds an uploaded file to the library. A commit of the same key that is still running answers 409: wait and ask again. */
export async function commitAsset(uploadKey: string): Promise<Asset> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await engine.commitAsset(uploadKey);
    } catch (e) {
      if (e instanceof EngineError && e.status === 409 && attempt < 4) {
        await sleep(500 * (attempt + 1));
        continue;
      }
      throw e;
    }
  }
}

/** Upload then commit: a file for the library (b-roll, music, a font). Resolves to its `asset:<id>` reference. */
export async function uploadAsset(
  file: File, kind: AssetKind, onProgress: (pct: number) => void, signal?: AbortSignal,
): Promise<{ ref: string; asset: Asset }> {
  const { upload_key } = await uploadFile(file, kind, onProgress, signal);
  const asset = await commitAsset(upload_key);
  return { ref: `asset:${asset.asset_id}`, asset };
}

/** The length of a video file in seconds, read from its metadata by a hidden <video>. Rejects when the browser cannot
    read it (an AVI or MTS, say): the caller then lets the engine judge the file. */
export function videoDuration(file: File): Promise<number> {
  return new Promise<number>((resolve, reject) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (run: () => void) => {
      clearTimeout(timer);
      video.onloadedmetadata = null;
      video.onerror = null;
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(url);
      run();
    };
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const seconds = video.duration; // read before the element is reset: load() clears it
      finish(() => (Number.isFinite(seconds) ? resolve(seconds) : reject(new Error("unknown length"))));
    };
    video.onerror = () => finish(() => reject(new Error("unreadable video")));
    timer = setTimeout(() => finish(() => reject(new Error("timed out"))), 15_000);
    video.src = url;
  });
}

/** What stops a video from being imported: over 60 minutes, over 3 GB, or longer than the minutes left this month.
    Minutes are counted the engine's way: the video rounds up, what is left rounds down. Null when it can go ahead.
    `duration` is null when the browser could not read it; the engine then checks after the upload. */
export function importProblem(video: { duration: number | null; size: number }, usage: Usage): string | null {
  if (video.duration !== null && video.duration > MAX_VIDEO_SECONDS) return t("import.web.toolong");
  if (video.size > KIND_LIMITS.video) return t("import.web.toobig");
  if (video.duration === null) return null;
  const minutes = Math.ceil(video.duration / 60);
  const left = Math.max(0, Math.floor((usage.seconds_limit - usage.seconds_used) / 60));
  return minutes > left ? t("import.web.overlimit", { m: minutes, r: left }) : null;
}

/** What stops a batch before anything is uploaded: a video over 60 minutes or 3 GB (named), or all of them together
    longer than the minutes left (the engine's words, with Import's rounding). Unknown lengths are left to the engine. */
export function batchProblem(videos: { name: string; duration: number | null; size: number }[], usage: Usage): string | null {
  for (const v of videos) {
    if (v.duration !== null && v.duration > MAX_VIDEO_SECONDS) return `${v.name}: ${t("import.web.toolong")}`;
    if (v.size > KIND_LIMITS.video) return `${v.name}: ${t("import.web.toobig")}`;
  }
  const total = videos.reduce((s, v) => s + (v.duration ?? 0), 0);
  if (!total) return null;
  const minutes = Math.ceil(total / 60);
  const left = Math.max(0, Math.floor((usage.seconds_limit - usage.seconds_used) / 60));
  return minutes > left ? t("batch.web.overlimit", { m: minutes, r: left }) : null;
}

/** Runs `task` over every item with at most `size` running at once, starting them in order. Resolves when all have
    settled; a task's own failure is its business (wrap it), so one rejected task does not stop the rest. */
export async function runPool<T>(items: T[], size: number, task: (item: T) => Promise<void>): Promise<void> {
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++];
      await task(item).catch(() => undefined);
    }
  };
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, worker));
}
