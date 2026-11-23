import { describe, expect, it } from "vitest";
import type { Overlay } from "../engine/types";
import { activeOverlays, overlayStart } from "./OverlayLayer";

const text = (id: string, t: number, duration = 2): Overlay => ({ id, type: "text", at: { src: "main", t }, duration, text: id, position: "top" });
const img = (id: string, t: number, duration = 2): Overlay => ({ id, type: "image", at: { src: "main", t }, duration, path: `${id}.png`, mode: "full" });

// kept source 10..14 then 20..25: output 0..4 then 4..9
const RANGES: [number, number][] = [[10, 14], [20, 25]];

describe("overlayStart", () => {
  it("maps source time to output time across a cut", () => {
    expect(overlayStart(RANGES, text("a", 11))).toBe(1);
    expect(overlayStart(RANGES, text("a", 21))).toBe(5);
  });
  it("is null when the overlay's moment was cut", () => {
    expect(overlayStart(RANGES, text("a", 16))).toBeNull();
  });
});

describe("activeOverlays", () => {
  it("shows what is on at the output time, with how long it has been showing", () => {
    const on = activeOverlays([text("a", 11), text("b", 21)], RANGES, 1.5);
    expect(on.map((x) => x.o.id)).toEqual(["a"]);
    expect(on[0].since).toBeCloseTo(0.5);
  });
  it("ends an overlay after its length", () => {
    expect(activeOverlays([text("a", 11)], RANGES, 3)).toEqual([]);
  });
  it("stacks b-roll under text, like the export", () => {
    const on = activeOverlays([text("t", 10), img("i", 10)], RANGES, 0.5);
    expect(on.map((x) => x.o.id)).toEqual(["i", "t"]);
  });
  it("skips an overlay whose moment was cut", () => {
    expect(activeOverlays([text("a", 16)], RANGES, 0)).toEqual([]);
  });
});
