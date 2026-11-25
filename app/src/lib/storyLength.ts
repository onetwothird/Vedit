// How long a story's video will be, counted the way the engine checks and charges it (scripts/studio_stories.py
// estimate_seconds): narration at 150 words a minute, a clip with its own sound or no narration its own length, a
// silent photo 3 seconds. The website caps a story at 3 minutes (cloud_outputs.STORY_TOO_LONG).

import type { StoryScene } from "../engine/types";

export const STORY_MAX_SECONDS = 180;
const WORDS_PER_SECOND = 2.5;
const SILENT_SECONDS = 3;

const wordsOf = (text: string): number =>
  text.split(/\r?\n/).filter((l) => l.trim()).reduce((n, l) => n + l.trim().split(/\s+/).length, 0);

/** The estimate in seconds, and how many clips it could not count because their length is not known here. */
export function estimateStorySeconds(
  scenes: Pick<StoryScene, "media" | "kind" | "text" | "keep_audio">[],
  durations: ReadonlyMap<string, number>,
): { seconds: number; unknown: number } {
  let seconds = 0;
  let unknown = 0;
  for (const sc of scenes) {
    const words = wordsOf(sc.text || "");
    if (sc.kind === "video" && (sc.keep_audio || !words)) {
      const d = durations.get(sc.media);
      if (d === undefined) unknown++;
      else seconds += d;
    } else if (words) seconds += words / WORDS_PER_SECOND;
    else seconds += SILENT_SECONDS;
  }
  return { seconds: Math.round(seconds * 100) / 100, unknown };
}

/** 2:05 for 125 seconds. */
export const clock = (sec: number): string => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
