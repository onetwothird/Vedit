import type { ProjectClip } from "../engine/types";

export type ClipSort = "best" | "order";

/** "best" is the highest score first; "order" is where the clip starts in the video. Never changes the input. */
export function sortClips(clips: readonly ProjectClip[], sort: ClipSort): ProjectClip[] {
  const by = sort === "best"
    ? (a: ProjectClip, b: ProjectClip) => b.highlight.score - a.highlight.score || a.highlight.start - b.highlight.start
    : (a: ProjectClip, b: ProjectClip) => a.highlight.start - b.highlight.start;
  return [...clips].sort(by);
}

export const keptCount = (clips: readonly ProjectClip[]): number => clips.filter((c) => c.kept).length;
