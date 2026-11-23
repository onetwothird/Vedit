// Thumbnails are one sprite image; a frame is found by time and shown with a CSS background offset.

import type { CSSProperties } from "react";
import type { FilmstripInfo } from "../engine/types";

export interface FrameAt {
  index: number;
  col: number;
  row: number;
}

type Layout = Pick<FilmstripInfo, "every" | "count" | "cols">;

/** The frame covering source time `t` (seconds), clamped to the first and last frame. */
export function frameAt(film: Layout, t: number): FrameAt {
  const raw = Math.floor(t / film.every);
  const index = Math.max(0, Math.min(Number.isFinite(raw) ? raw : 0, film.count - 1));
  return { index, col: index % film.cols, row: Math.floor(index / film.cols) };
}

/** Display width at which a frame fits whole inside boxW x boxH, keeping its aspect (portrait or landscape). */
export function fitWidth(film: Pick<FilmstripInfo, "width" | "height"> | null, boxW: number, boxH: number): number {
  const aspect = film && film.width > 0 && film.height > 0 ? film.width / film.height : 16 / 9;
  return Math.max(1, Math.floor(Math.min(boxW, boxH * aspect)));
}

/** Style for a box `displayWidth` wide that shows the frame at time `t`. `image` is a CSS url(...) value. */
export function spriteStyle(film: FilmstripInfo, t: number, displayWidth: number, image: string): CSSProperties {
  const { col, row } = frameAt(film, t);
  const k = displayWidth / film.width;
  const w = film.width * k, h = film.height * k;
  return {
    width: w,
    height: h,
    backgroundImage: image,
    backgroundSize: `${film.cols * w}px ${film.rows * h}px`,
    backgroundPosition: `${-col * w}px ${-row * h}px`,
    backgroundRepeat: "no-repeat",
  };
}

/** Display width at which a frame covers the whole box boxW x boxH (the overflow is cropped by the caller). */
export function coverWidth(film: Pick<FilmstripInfo, "width" | "height"> | null, boxW: number, boxH: number): number {
  const aspect = film && film.width > 0 && film.height > 0 ? film.width / film.height : 16 / 9;
  return Math.max(1, Math.ceil(Math.max(boxW, boxH * aspect)));
}
