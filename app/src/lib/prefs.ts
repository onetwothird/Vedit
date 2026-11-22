// Defaults that the Settings screen writes to localStorage; Import only reads them.

import type { Language, Platform } from "../engine/types";

const LANGUAGES: Language[] = ["en", "tl", "taglish"];
const PLATFORMS: Platform[] = ["reels", "shorts", "tiktok"];
export type AiProvider = "anthropic" | "openai";

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function getLanguage(): Language {
  const v = read("vedit.language");
  return LANGUAGES.find((l) => l === v) ?? "en";
}

export function getPlatform(): Platform {
  const v = read("vedit.platform");
  return PLATFORMS.find((p) => p === v) ?? "reels";
}

export function getAiProvider(): AiProvider | null {
  const v = read("vedit.ai.provider");
  return v === "anthropic" || v === "openai" ? v : null;
}
