import { describe, expect, it } from "vitest";
import { defaultOutputFolder, joinPath } from "./outputFolder";

const BS = "\\";
const win = (...parts: string[]) => parts.join(BS);

describe("joinPath", () => {
  it("uses the folder's own separator and never doubles it", () => {
    expect(joinPath("C:/Videos", "a.mp4")).toBe("C:/Videos/a.mp4");
    expect(joinPath(win("C:", "Videos"), "a.mp4")).toBe(win("C:", "Videos", "a.mp4"));
    expect(joinPath(win("D:", ""), "a.mp4")).toBe(win("D:", "a.mp4"));
    expect(joinPath("C:/Videos/", "a.mp4")).toBe("C:/Videos/a.mp4");
  });
  it("keeps unicode folders intact", () => {
    const dir = win("D:", "Mga Bidyo", "Ñandú 日本語");
    expect(joinPath(dir, "01 x.mp4")).toBe(`${dir}${BS}01 x.mp4`);
  });
});

describe("defaultOutputFolder", () => {
  it("is '<name> - Vedit' beside the video", () => {
    expect(defaultOutputFolder("C:/Videos/podcast-ep12.mp4")).toBe("C:/Videos/podcast-ep12 - Vedit");
    expect(defaultOutputFolder(win("C:", "Videos", "my.talk.mov"))).toBe(win("C:", "Videos", "my.talk - Vedit"));
  });
  it("handles a drive root and unicode names", () => {
    expect(defaultOutputFolder(win("D:", "clip.mp4"))).toBe(win("D:", "clip - Vedit"));
    expect(defaultOutputFolder(win("D:", "Mga Bidyo", "Ñandú 日本語.mp4"))).toBe(win("D:", "Mga Bidyo", "Ñandú 日本語 - Vedit"));
  });
  it("copes with a bare file name", () => {
    expect(defaultOutputFolder("talk.mp4")).toBe("talk - Vedit");
  });
});
