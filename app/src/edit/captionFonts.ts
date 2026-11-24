/**
 * The caption fonts the editor offers. All but Arial ship in fonts/ (export, through libass) and app/src/fonts (preview,
 * through @font-face in theme/tokens.css), so the preview and the export draw the same letters on any computer.
 * `single` fonts come in one weight: scripts/captions.py SINGLE_WEIGHT draws them as designed, never fake-bolded.
 */
export interface CaptionFont {
  family: string;
  single: boolean;
  /** A shorter name for the tile when the family name is long. */
  short?: string;
}

export const CAPTION_FONTS: CaptionFont[] = [
  { family: "Montserrat ExtraBold", single: false, short: "Montserrat" },
  { family: "Poppins", single: false },
  { family: "Arial", single: false },
  { family: "Anton", single: true },
  { family: "Bebas Neue", single: true },
  { family: "Archivo Black", single: true, short: "Archivo" },
  { family: "Bangers", single: true },
  { family: "Permanent Marker", single: true, short: "Marker" },
  { family: "Pacifico", single: true },
  { family: "DM Serif Display", single: true, short: "DM Serif" },
];

/** scripts/captions.py DEFAULT_FONT: heavy, rounded and readable at phone size, the usual Reels caption look. */
export const DEFAULT_CAPTION_FONT = "Montserrat ExtraBold";

/** A CSS font-family list for a caption font name (quoted, with fallbacks). */
export function cssFont(family: string): string {
  const safe = family.replace(/["\\;{}]/g, "").trim() || DEFAULT_CAPTION_FONT;
  return `"${safe}", Arial, sans-serif`;
}

/** The caption look keys "Reset look" clears, back to the style's defaults. */
export const LOOK_KEYS = ["font", "scale", "color", "outline_color", "outline_scale", "shadow", "uppercase", "active_scale", "bold"] as const;
