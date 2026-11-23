import { describe, expect, it } from "vitest";
import type { Edl } from "../engine/types";
import { patchSection } from "./timeline";

describe("Framing zoom", () => {
  it("marks a zoom you choose as chosen, so the engine never caps it like an automatic punch-in", () => {
    const edl = { sources: { main: "a.mp4" }, clips: [{ words: [0, 3] }, { words: [4, 8], zoom: 1.12, punchin: true }] } as unknown as Edl;
    const next = patchSection(edl, "framing", { zoom: 1.2 });
    expect(next.clips.map((c) => [c.zoom, c.punchin])).toEqual([[1.2, false], [1.2, false]]);
    expect(patchSection(edl, "framing", { crop_x: 0.4 }).clips[1].punchin).toBe(true); // other framing leaves it
  });
});
