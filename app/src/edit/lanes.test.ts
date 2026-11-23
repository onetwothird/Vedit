import { describe, expect, it } from "vitest";
import { lanePieces, laneSegments, splitWordAt } from "./lanes";

describe("lanePieces and splitWordAt", () => {
  const W = Array.from({ length: 6 }, (_, i) => ({ text: `w${i}`, start: i, end: i + 0.8 }));
  const all = new Set([0, 1, 2, 3, 4, 5]);
  it("cuts kept runs at split words", () => {
    const segs = laneSegments(W, 0, 5, all);
    expect(lanePieces(W, segs, new Set([3]), 5).map((p) => [p.w0, p.w1])).toEqual([[0, 2], [3, 5]]);
    expect(lanePieces(W, laneSegments(W, 0, 5, new Set([0, 1, 4, 5])), new Set([1]), 5).map((p) => [p.w0, p.w1])).toEqual([[0, 0], [1, 1], [4, 5]]);
  });
  it("finds the word a split starts, never a piece's first word", () => {
    const pieces = lanePieces(W, laneSegments(W, 0, 5, all), new Set(), 5);
    expect(splitWordAt(W, pieces, 2.9)).toBe(3);
    expect(splitWordAt(W, pieces, 0.1)).toBe(null);
    expect(splitWordAt(W, pieces, 9)).toBe(null);
  });
});
