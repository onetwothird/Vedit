import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const css = readFileSync("src/theme/tokens.css", "utf8");

function block(selector: RegExp): Record<string, string> {
  const m = css.match(selector);
  if (!m) throw new Error("block not found");
  const out: Record<string, string> = {};
  for (const d of m[1].matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{3,8})\s*;/g)) out[d[1]] = d[2];
  return out;
}

function rgb(hex: string): [number, number, number] {
  let h = hex.slice(1);
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function ratio(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

const dark = block(/:root\s*\{([^}]*)\}/);
const light = { ...dark, ...block(/html\.light\s*\{([^}]*)\}/) };

describe.each([
  ["dark", dark],
  ["light", light],
])("%s theme text contrast (WCAG AA)", (_name, tokens) => {
  for (const fg of ["ink", "body", "muted"]) {
    for (const bg of ["page", "panel", "bg"]) {
      it(`--${fg} on --${bg} is at least 4.5:1`, () => {
        expect(ratio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});
