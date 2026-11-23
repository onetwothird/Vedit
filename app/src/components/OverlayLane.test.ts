import { describe, expect, it } from "vitest";
import type { Overlay } from "../engine/types";
import { MIN_OVERLAY, moveOverlay, resizeOverlay } from "./OverlayLane";

const TEXT: Overlay = { id: "a", type: "text", at: { src: "main", t: 2 }, duration: 3, text: "Hi", position: "top" };

describe("moveOverlay", () => {
  it("moves the start and keeps the length", () => {
    const o = moveOverlay(TEXT, 4.123, 0, 10);
    expect(o.at.t).toBe(4.12);
    expect(o.duration).toBe(3);
  });
  it("stays inside the clip", () => {
    expect(moveOverlay(TEXT, -5, 1, 10).at.t).toBe(1);
    expect(moveOverlay(TEXT, 99, 1, 10).at.t).toBe(10 - MIN_OVERLAY);
  });
});

describe("resizeOverlay", () => {
  it("drags the end edge and keeps the start", () => {
    expect(resizeOverlay(TEXT, "end", 6.5, 0, 10)).toMatchObject({ at: { t: 2 }, duration: 4.5 });
  });
  it("drags the start edge and keeps the end", () => {
    expect(resizeOverlay(TEXT, "start", 1, 0, 10)).toMatchObject({ at: { t: 1 }, duration: 4 });
  });
  it("never gets shorter than the minimum, or past the clip", () => {
    expect(resizeOverlay(TEXT, "end", 0, 0, 10).duration).toBe(MIN_OVERLAY);
    expect(resizeOverlay(TEXT, "start", 99, 0, 10)).toMatchObject({ at: { t: 5 - MIN_OVERLAY }, duration: MIN_OVERLAY });
    expect(resizeOverlay(TEXT, "end", 99, 0, 6).duration).toBe(4);
    expect(resizeOverlay(TEXT, "start", -3, 0.5, 10).at.t).toBe(0.5);
  });
});
