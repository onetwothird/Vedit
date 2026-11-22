import { describe, expect, it } from "vitest";
import type { FilmstripInfo } from "../engine/types";
import { fitWidth, frameAt, spriteStyle } from "./filmstrip";

const film: FilmstripInfo = { every: 2, height: 96, width: 54, count: 120, cols: 50, rows: 3, image: "x.jpg" };

describe("frameAt", () => {
  it("picks the frame that covers the time", () => {
    expect(frameAt(film, 0)).toEqual({ index: 0, col: 0, row: 0 });
    expect(frameAt(film, 3.9)).toEqual({ index: 1, col: 1, row: 0 });
    expect(frameAt(film, 4)).toEqual({ index: 2, col: 2, row: 0 });
  });

  it("wraps to the next row after cols frames", () => {
    expect(frameAt(film, 100)).toEqual({ index: 50, col: 0, row: 1 });
    expect(frameAt(film, 105)).toEqual({ index: 52, col: 2, row: 1 });
  });

  it("clamps to the last frame and to the first for a negative time", () => {
    expect(frameAt(film, 9999)).toEqual({ index: 119, col: 19, row: 2 });
    expect(frameAt(film, -5)).toEqual({ index: 0, col: 0, row: 0 });
  });
});

describe("spriteStyle", () => {
  it("offsets the sprite and sizes the box at natural scale", () => {
    const s = spriteStyle(film, 105, 54, "url(x.jpg)");
    expect(s.width).toBe(54);
    expect(s.height).toBe(96);
    expect(s.backgroundImage).toBe("url(x.jpg)");
    expect(s.backgroundSize).toBe("2700px 288px");
    expect(s.backgroundPosition).toBe("-108px -96px");
  });

  it("scales everything with the display width", () => {
    const s = spriteStyle(film, 0, 108, "url(x.jpg)");
    expect(s.height).toBe(192);
    expect(s.backgroundSize).toBe("5400px 576px");
    expect(s.backgroundPosition).toBe("0px 0px");
  });
});

describe("fitWidth", () => {
  const landscape = { width: 171, height: 96 }; // 16:9
  const portrait = { width: 54, height: 96 }; // 9:16

  it("fills the width of a landscape frame when the height allows", () => {
    expect(fitWidth(landscape, 560, 420)).toBe(560);
  });

  it("limits a portrait frame by the box height, so it is whole and not taller than the box", () => {
    const w = fitWidth(portrait, 560, 420);
    expect(w).toBe(236); // 420 * 54/96
    expect(w * (portrait.height / portrait.width)).toBeLessThanOrEqual(420);
  });

  it("limits a landscape frame by a narrow box and keeps the aspect", () => {
    const w = fitWidth(landscape, 200, 420);
    expect(w).toBe(200);
    expect(fitWidth(portrait, 100, 420)).toBe(100);
  });

  it("assumes 16:9 with no filmstrip", () => {
    expect(fitWidth(null, 560, 200)).toBe(355);
  });

  it("scales the sprite offsets with the fitted width for a portrait frame", () => {
    const film: FilmstripInfo = { every: 2, height: 96, width: 54, count: 120, cols: 50, rows: 3, image: "x.jpg" };
    const w = fitWidth(film, 560, 192); // 108 wide, 192 tall: factor 2
    const s = spriteStyle(film, 105, w, "url(x.jpg)");
    expect(s.height).toBe(192);
    expect(s.backgroundSize).toBe("5400px 576px");
    expect(s.backgroundPosition).toBe("-216px -192px");
  });
});
