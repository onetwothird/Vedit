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

/** The preview's safe-zone guide: off until the person turns it on. */
export function getSafeZone(): boolean {
  return read("vedit.safezone") === "1";
}

export function setSafeZone(on: boolean): void {
  try {
    localStorage.setItem("vedit.safezone", on ? "1" : "0");
  } catch {
    /* private mode: the toggle still works for this session */
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // storage unavailable: the choice lasts until the app closes
  }
}

export const setLanguage = (v: Language): void => write("vedit.language", v);
export const setPlatform = (v: Platform): void => write("vedit.platform", v);
export const setAiProvider = (v: AiProvider): void => write("vedit.ai.provider", v);

/** The rest of Settings, in localStorage["vedit.settings"]. The AI key is never here (Credential Manager). */
export interface Settings {
  outputMode: "next" | "folder";
  outputFolder: string;
}

const DEFAULT_SETTINGS: Settings = { outputMode: "next", outputFolder: "" };

export function getSettings(): Settings {
  try {
    const raw = JSON.parse(read("vedit.settings") ?? "{}") as Partial<Settings>;
    const folder = typeof raw.outputFolder === "string" ? raw.outputFolder : "";
    return { outputMode: raw.outputMode === "folder" && folder ? "folder" : "next", outputFolder: folder };
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(s: Settings): void {
  write("vedit.settings", JSON.stringify(s));
}
