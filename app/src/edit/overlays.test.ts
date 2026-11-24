import { describe, expect, it } from "vitest";
import type { Overlay } from "../engine/types";
import { MIN_LAYER, brollAt, duplicateLayer, newBroll, newText, withIds } from "./overlays";

describe("layer helpers", () => {
  it("puts new text at the playhead for 3 s, inside the clip", () => {
    expect(newText("Hi", 4, 2, 10)).toMatchObject({ type: "text", text: "Hi", position: "top", at: { src: "main", t: 4 }, duration: 3 });
    // near the end of the clip it shortens; before the clip it starts at the clip
    expect(newText("Hi", 9, 2, 10)).toMatchObject({ at: { t: 9 }, duration: 1 });
    expect(newText("Hi", 0, 2, 10).at.t).toBe(2);
  });

  it("places accepted b-roll at its moment, held inside the clip", () => {
    expect(brollAt("C:/a/b.jpg", "image", 4.123, 3, 2, 10)).toMatchObject({ type: "image", at: { src: "main", t: 4.12 }, duration: 3, mode: "full" });
    expect(brollAt("C:/a/b.mp4", "video", 9, 3, 2, 10)).toMatchObject({ at: { t: 9 }, duration: 1 });
    expect(brollAt("C:/a/b.jpg", "image", 1, 3, 2, 10).at.t).toBe(2);
    expect(brollAt("C:/a/b.jpg", "image", 9.9, 3, 2, 10)).toMatchObject({ at: { t: 9.5 }, duration: 0.5 });
  });

  it("knows a video from a photo", () => {
    expect(newBroll("C:/a/shot.MP4", 1, 0, 9)).toMatchObject({ type: "video", mode: "full", path: "C:/a/shot.MP4" });
    expect(newBroll("C:/a/shot.png", 1, 0, 9).type).toBe("image");
  });

  it("copies a layer to start where it ends, with a new id", () => {
    const o = newText("Hi", 2, 0, 10);
    const c = duplicateLayer(o, 0, 10);
    expect(c.id).not.toBe(o.id);
    expect(c).toMatchObject({ text: "Hi", at: { t: 5 }, duration: 3 });
    // at the clip's end it still fits
    const end = duplicateLayer({ ...o, at: { src: "main", t: 8 } }, 0, 10);
    expect(end.at.t + end.duration).toBeLessThanOrEqual(10);
    expect(end.duration).toBeGreaterThanOrEqual(MIN_LAYER);
  });

  it("gives hand-written layers an id and keeps existing ones", () => {
    const a = { type: "text", at: { src: "main", t: 1 }, duration: 1, text: "x", position: "top" } as Overlay;
    expect(withIds([a, { ...a, id: "keep" }]).map((o) => o.id)).toEqual(["o-0", "keep"]);
  });
});
