// Caption spelling fixes, matched the way scripts/captions.py apply_replacements does: by the word without case or
// punctuation, keeping the word's own trailing punctuation.

const norm = (w: string): string => w.toLowerCase().replace(/[^\p{L}\p{N}_']/gu, "");

/** The word with its trailing punctuation removed: what a fix replaces. */
export const bareWord = (text: string): string => text.replace(/[.,!?;:"”]+$/u, "") || text;

/** How `text` reads in the captions after the `replace` fixes. */
export function spelledAs(text: string, replace: Record<string, string> | undefined): string {
  if (!replace) return text;
  const key = norm(text);
  let out = text;
  for (const [wrong, right] of Object.entries(replace)) {
    if (key === norm(wrong)) out = right + text.replace(/^[\p{L}\p{N}_']+/u, "");
  }
  return out;
}
