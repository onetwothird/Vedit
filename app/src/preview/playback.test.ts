import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import type { CaptionTrack } from "../engine/types";
import CaptionOverlay from "./CaptionOverlay";
import FIXTURE_JSON from "./__fixtures__/captions.json";
import {
  activeCaption, advance, captionClock, cropStyle, locate, nextKeptTime, outputLength, safeZoneBands, toOutputTime,
  toSourceTime, type Range,
} from "./playback";

// caption_events() + the ASS group starts, written by tests/fixtures/export_caption_fixture.py
const FIXTURE = FIXTURE_JSON as unknown as CaptionTrack & { ass_starts: number[] };

/** The overlay as rendered at a frame `frameHeight` tall: the caption block's bottom edge as a fraction of the frame. */
function renderOverlay(track: CaptionTrack, frameHeight: number) {
  const e = track.events[0];
  const html = renderToStaticMarkup(createElement(CaptionOverlay, { track, outT: e.start + 0.001, frameHeight }));
  const doc = new DOMParser().parseFromString(html, "text/html");
  const el = doc.querySelector<HTMLElement>("[data-caption]")!;
  return { el, bottomFrac: parseFloat(el.style.bottom) / frameHeight, fontPx: parseFloat(el.style.fontSize) };
}

const R: Range[] = [[0, 4], [6, 10]];

describe("range helpers", () => {
  it("skips cut ranges", () => {
    expect(nextKeptTime(R, 4.01)).toBe(6);
    expect(toOutputTime(R, 7)).toBeCloseTo(5);
    expect(nextKeptTime(R, 10.01)).toBeNull();
  });

  it("nextKeptTime stays put inside a range and finds the start of the next one from a gap or before the first", () => {
    expect(nextKeptTime(R, 2)).toBe(2);
    expect(nextKeptTime(R, 4)).toBe(6); // ranges are [start, end)
    expect(nextKeptTime(R, -1)).toBe(0);
  });

  it("toOutputTime is null in a cut, and maps through earlier ranges", () => {
    expect(toOutputTime(R, 5)).toBeNull();
    expect(toOutputTime(R, 1)).toBeCloseTo(1);
    expect(toOutputTime(R, 6)).toBeCloseTo(4);
  });

  it("toSourceTime inverts it and clamps to the ends", () => {
    expect(toSourceTime(R, 5)).toBeCloseTo(7);
    expect(toSourceTime(R, 3.99)).toBeCloseTo(3.99);
    expect(toSourceTime(R, -2)).toBe(0);
    expect(toSourceTime(R, 99)).toBe(10);
  });

  it("a range's output length is (end - start) / speed, and speed defaults to 1", () => {
    const s: Range[] = [[0, 4, 2], [6, 10]];
    expect(outputLength(s)).toBeCloseTo(2 + 4);
    expect(toOutputTime(s, 2)).toBeCloseTo(1);
    expect(toOutputTime(s, 7)).toBeCloseTo(3);
    expect(toSourceTime(s, 1)).toBeCloseTo(2);
    expect(toSourceTime(s, 3)).toBeCloseTo(7);
    expect(locate(s, 3)).toEqual({ idx: 1, srcT: expect.closeTo(7) });
  });

  it("locate puts an output time on the right range even when ranges are out of source order", () => {
    const back: Range[] = [[6, 10], [0, 4]];
    expect(locate(back, 5)).toEqual({ idx: 1, srcT: expect.closeTo(1) });
    expect(toOutputTime(back, 1)).toBeCloseTo(5);
  });
});

describe("advance (leaving a range)", () => {
  it("stays inside a range and reports the output time", () => {
    expect(advance(R, 1, 7)).toEqual({ kind: "stay", out: expect.closeTo(5) });
  });
  it("jumps to the start of the next range in play order, not the next in source time", () => {
    const back: Range[] = [[6, 10], [0, 4], [12, 14]];
    expect(advance(back, 0, 10.01)).toEqual({ kind: "jump", idx: 1, srcT: 0 });
    expect(advance(back, 1, 4.02)).toEqual({ kind: "jump", idx: 2, srcT: 12 });
  });
  it("ends after the last range", () => {
    expect(advance(R, 1, 10.01)).toEqual({ kind: "end", out: expect.closeTo(8) });
  });
  it("ignores a stale frame from before a seek into the range", () => {
    expect(advance(R, 1, 3.9)).toEqual({ kind: "stay", out: expect.closeTo(4) });
  });
});

describe("captions", () => {
  it("caption timing matches engine within one frame", () => {
    expect(FIXTURE.events.length).toBeGreaterThan(2);
    for (const e of FIXTURE.events) {
      expect(Math.abs(activeCaption(FIXTURE, e.start + 0.001)!.event.start - e.start)).toBeLessThan(1 / 30);
    }
  });

  it("group starts line up with the ASS dialogue starts (within one frame)", () => {
    expect(FIXTURE.events).toHaveLength(FIXTURE.ass_starts.length);
    FIXTURE.events.forEach((e, i) => expect(Math.abs(e.start - FIXTURE.ass_starts[i])).toBeLessThan(1 / 30));
  });

  it("activeWord follows the hl window of each word", () => {
    for (const e of FIXTURE.events) {
      e.words.forEach((w, k) => {
        if (w.hl_end - w.hl_start < 0.02) return;
        expect(activeCaption(FIXTURE, (w.hl_start + w.hl_end) / 2)).toMatchObject({ activeWord: k });
      });
    }
  });

  it("is null between groups and outside the track", () => {
    expect(activeCaption(FIXTURE, -1)).toBeNull();
    expect(activeCaption(FIXTURE, FIXTURE.events[FIXTURE.events.length - 1].end + 5)).toBeNull();
  });

  it("captionClock adds the intro length: the preview skips the intro, caption times include it", () => {
    expect(captionClock({ lead_in: 2 } as CaptionTrack, 1.5)).toBeCloseTo(3.5);
    expect(captionClock({} as CaptionTrack, 1.5)).toBe(1.5);
    const track = { ...FIXTURE, lead_in: 2, events: FIXTURE.events.map((e) => ({ ...e, start: e.start + 2, end: e.end + 2 })) };
    const first = track.events[0];
    expect(activeCaption(track, captionClock(track, first.start - 2 + 0.001))?.event.start).toBeCloseTo(first.start);
  });
});

describe("caption overlay", () => {
  it("caption position within 2% of frame height", () => {
    const box = renderOverlay(FIXTURE, 1920);
    expect(Math.abs(box.bottomFrac - FIXTURE.baseline_frac)).toBeLessThan(0.02);
  });

  it("font size is size_frac of the frame height and scales with it", () => {
    expect(renderOverlay(FIXTURE, 1920).fontPx).toBeCloseTo(FIXTURE.size_frac * 1920, 0);
    expect(renderOverlay(FIXTURE, 480).fontPx).toBeCloseTo(FIXTURE.size_frac * 480, 0);
  });

  it("outlines with paint-order stroke at outline_frac of the height, and highlights only the active word", () => {
    const { el } = renderOverlay(FIXTURE, 1000);
    expect(el.getAttribute("style")).toContain("paint-order:stroke");
    expect(el.getAttribute("style")).toMatch(/-webkit-text-stroke-width:\s*3\.1px/);
    const words = [...el.querySelectorAll<HTMLElement>("[data-word]")];
    expect(words.length).toBe(FIXTURE.events[0].words.length);
    expect(words[0].dataset.active).toBe("true");
    expect(words[1].dataset.active).toBeUndefined();
    expect(words[0].style.color).not.toBe(words[1].style.color);
    expect(words[0].style.transform).toBe(`scale(${FIXTURE.active_scale / 100})`);
    expect(words[1].style.transform).toBe("");
  });

  it("uses the speaker's colour for the active word, and a box with back_alpha in box style", () => {
    const e = FIXTURE.events[0];
    const spk: CaptionTrack = {
      ...FIXTURE, speaker_colors: { maria: "#FF6FB5" },
      events: [{ ...e, words: e.words.map((w) => ({ ...w, speaker: "maria" })) }],
    };
    const a = renderOverlay(spk, 1000).el.querySelector<HTMLElement>("[data-active=true]")!;
    expect(a.style.color).toBe("rgb(255, 111, 181)");
    const box: CaptionTrack = { ...FIXTURE, style: "box", back_color: "#000000", back_alpha: 0.4392 };
    const b = renderOverlay(box, 1000).el.querySelector<HTMLElement>("[data-box]")!;
    expect(b.getAttribute("style")).toContain("rgba(0, 0, 0, 0.4392)");
  });

  it("karaoke shows the whole group and fills each word with the highlight while it is spoken", () => {
    const e = FIXTURE.events[0];
    const track: CaptionTrack = { ...FIXTURE, style: "karaoke", events: [e] };
    const [w0, w1] = e.words;
    const at = (outT: number) => {
      const html = renderToStaticMarkup(createElement(CaptionOverlay, { track, outT, frameHeight: 1000 }));
      return [...new DOMParser().parseFromString(html, "text/html").querySelectorAll<HTMLElement>("[data-word]")];
    };
    const mid = at((w0.start + w0.end) / 2);
    expect(mid.map((w) => w.textContent)).toEqual(e.words.map((w) => w.text));
    expect(Number(mid[0].dataset.fill)).toBeGreaterThan(40);
    expect(Number(mid[0].dataset.fill)).toBeLessThan(60);
    expect(mid[0].getAttribute("style")).toContain("#FFD400 50%"); // FIXTURE highlight, half way
    expect(mid[1].dataset.fill).toBe("0");
    expect(mid[0].style.transform).toBe(""); // no pop
    expect(at(w1.start + 0.001)[0].dataset.fill).toBe("100");
  });

  it("emoji draws the word's emoji after it, outside the highlight", () => {
    const e = FIXTURE.events[0];
    const words = e.words.map((w, i) => (i === 0 ? { ...w, emoji: "👋" } : w));
    const el = renderOverlay({ ...FIXTURE, style: "emoji", events: [{ ...e, words }] }, 1000).el;
    const emoji = el.querySelectorAll<HTMLElement>("[data-emoji]");
    expect(emoji).toHaveLength(1);
    expect(emoji[0].textContent).toBe(" 👋");
    expect(emoji[0].previousElementSibling!.textContent).toBe(e.words[0].text);
    expect(el.querySelector("[data-active=true]")!.textContent).toBe(e.words[0].text); // still pop
    expect(renderOverlay(FIXTURE, 1000).el.querySelector("[data-emoji]")).toBeNull();
  });

  it("draws nothing when no group is on screen", () => {
    const html = renderToStaticMarkup(createElement(CaptionOverlay, { track: FIXTURE, outT: -1, frameHeight: 500 }));
    expect(html).toBe("");
  });
});

describe("framing", () => {
  it("crop centers subject", () => {
    // 1920x1080 into 9:16: the video is 316% of the frame wide, so x = 0.25 sits at the frame centre when left = 50% - 0.25 * 316%
    expect(cropStyle("9:16", 1920, 1080, 0.25, 1)).toMatchObject({ width: "316.0494%", height: "100%", left: "-29.0123%", top: "0%" });
  });
  it("clamps so the frame never shows past the video edge", () => {
    expect(cropStyle("9:16", 1920, 1080, 0.02, 1)).toMatchObject({ left: "0%" });
    expect(cropStyle("9:16", 1920, 1080, 1, 1)).toMatchObject({ left: "-216.0494%" });
  });
  it("zoom enlarges the video about the crop point", () => {
    const z = cropStyle("9:16", 1920, 1080, 0.5, 2);
    expect(z).toMatchObject({ width: "632.0988%", height: "200%", top: "-50%" });
  });
  it("a 16:9 source in a 16:9 frame fills it", () => {
    expect(cropStyle("16:9", 1920, 1080, 0.5, 1)).toMatchObject({ width: "100%", height: "100%", left: "0%", top: "0%" });
  });
  it("a portrait source in a landscape frame fills the width and centres vertically", () => {
    expect(cropStyle("16:9", 1080, 1920, 0.5, 1)).toMatchObject({ width: "100%", left: "0%" });
  });
});

describe("safe zone", () => {
  it("shades 12% top and 20% bottom for 9:16, 8% each for the other shapes", () => {
    expect(safeZoneBands("9:16")).toEqual({ top: 0.12, bottom: 0.2 });
    expect(safeZoneBands("1:1")).toEqual({ top: 0.08, bottom: 0.08 });
    expect(safeZoneBands("16:9")).toEqual({ top: 0.08, bottom: 0.08 });
  });
});
