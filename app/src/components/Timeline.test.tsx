// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { CaptionTrack, TranscriptWord } from "../engine/types";
import { captionSpans, laneSegments, rulerTicks, snapToWord, speechEnergy } from "../edit/lanes";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import Timeline, { FALLBACK_WIDTH, type TimelineProps } from "./Timeline";

// six words, one per second: word i is spoken from i to i + 0.9
const WORDS: TranscriptWord[] = Array.from({ length: 6 }, (_, i) => ({ text: `w${i}`, start: i, end: i + 0.9 }));
const ALL = new Set([0, 1, 2, 3, 4, 5]);

describe("lanes", () => {
  it("splits the clip's words into kept runs and cut gaps that tile the source time", () => {
    expect(laneSegments(WORDS, 0, 5, new Set([0, 1, 3, 4, 5]))).toEqual([
      { kind: "kept", w0: 0, w1: 1, start: 0, end: 2 },
      { kind: "gap", w0: 2, w1: 2, start: 2, end: 3 },
      { kind: "kept", w0: 3, w1: 5, start: 3, end: 5.9 },
    ]);
  });

  it("only looks at the clip's own words", () => {
    expect(laneSegments(WORDS, 2, 3, ALL)).toEqual([{ kind: "kept", w0: 2, w1: 3, start: 2, end: 3.9 }]);
  });

  it("snaps a start edge to the nearest word start and an end edge to the nearest word end, inside the clip", () => {
    expect(snapToWord(WORDS, 0, 5, 2.4, "start")).toBe(2);
    expect(snapToWord(WORDS, 0, 5, 2.6, "start")).toBe(3);
    expect(snapToWord(WORDS, 0, 5, 2.6, "end")).toBe(2); // word 2 ends at 2.9
    expect(snapToWord(WORDS, 0, 5, -10, "start")).toBe(0);
    expect(snapToWord(WORDS, 1, 4, 99, "end")).toBe(4);
  });

  it("puts ruler ticks far enough apart to read", () => {
    expect(rulerTicks(60, 10)).toEqual([0, 10, 20, 30, 40, 50, 60]); // 10 px a second: a tick every 10 s
    expect(rulerTicks(4, 100)).toEqual([0, 1, 2, 3, 4]);
  });

  it("gives speech a higher level than silence", () => {
    const words: TranscriptWord[] = [{ text: "a", start: 0, end: 1 }];
    const bars = speechEnergy(words, 0, 2, 4);
    expect(bars).toHaveLength(4);
    expect(Math.min(bars[0], bars[1])).toBeGreaterThan(Math.max(bars[2], bars[3]));
    for (const b of bars) {
      expect(b).toBeGreaterThanOrEqual(0);
      expect(b).toBeLessThanOrEqual(1);
    }
  });

  it("places each caption event at its source time, across a cut", () => {
    const track = {
      lead_in: 0,
      events: [
        { start: 0, end: 1, words: [{ text: "a" }, { text: "b" }] },
        { start: 2.5, end: 3.5, words: [{ text: "c" }] },
      ],
    } as unknown as CaptionTrack;
    // kept source 0..2 then 5..8: output 2.5 is source 5.5
    expect(captionSpans(track, [[0, 2], [5, 8]])).toEqual([
      { start: 0, end: 1, text: "a b" },
      { start: 5.5, end: 6.5, text: "c" },
    ]);
  });
});

let host: HTMLElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function render(over: Partial<TimelineProps> = {}) {
  const props: TimelineProps = {
    words: WORDS, w0: 0, w1: 5, kept: new Set([0, 1, 3, 4, 5]),
    captions: [{ start: 0, end: 1.9, text: "W0 W1" }, { start: 3, end: 5, text: "W3 W4" }],
    music: null, playhead: 0, onSeek: vi.fn(), onTrim: vi.fn(), onCut: vi.fn(), ...over,
  };
  act(() => root.render(<Timeline {...props} />));
  return props;
}
const $ = <T extends Element = HTMLElement>(sel: string) => host.querySelector<T>(sel)!;
const pointer = (el: Element, type: string, clientX: number) =>
  act(() => void el.dispatchEvent(new MouseEvent(type, { clientX, clientY: 5, bubbles: true, cancelable: true, button: 0 })));
const press = (el: Element, key: string) =>
  act(() => void el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })));
// with no layout in jsdom the lane is FALLBACK_WIDTH wide over 5.9 s at zoom 1
const pxPerSec = FALLBACK_WIDTH / 5.9;

describe("Timeline", () => {
  it("draws a caption block per caption event, the kept video with a waveform, and a gap per cut", () => {
    render();
    expect([...host.querySelectorAll(".tl-cap")].map((e) => e.textContent)).toEqual(["W0 W1", "W3 W4"]);
    expect(host.querySelectorAll(".tl-seg.kept")).toHaveLength(2);
    expect(host.querySelectorAll(".tl-gap")).toHaveLength(1);
    expect(host.querySelectorAll(".tl-seg.kept svg rect").length).toBeGreaterThan(0);
  });

  it("shows the music file in the music lane, or that there is none", () => {
    render();
    expect($(".tl-music").textContent).toContain("No music");
    render({ music: "C:/music/lofi.mp3" });
    expect($(".tl-music").textContent).toContain("lofi.mp3");
  });

  it("seeks where the lanes are clicked, and while the playhead is dragged", () => {
    const p = render();
    const lanes = $(".tl-content");
    pointer(lanes, "pointerdown", 2.5 * pxPerSec);
    expect(p.onSeek).toHaveBeenLastCalledWith(expect.closeTo(2.5, 2));
    pointer(lanes, "pointermove", 4 * pxPerSec);
    expect(p.onSeek).toHaveBeenLastCalledWith(expect.closeTo(4, 2));
    pointer(lanes, "pointerup", 4 * pxPerSec);
    pointer(lanes, "pointermove", 1 * pxPerSec);
    expect(p.onSeek).toHaveBeenCalledTimes(2);
  });

  it("puts the playhead at its source time", () => {
    render({ playhead: 3 });
    expect(parseFloat($(".tl-playhead").style.left)).toBeCloseTo(3 * pxPerSec, 2);
  });

  it("in Trim mode, dragging the start edge trims to the nearest word", () => {
    const p = render();
    expect(host.querySelector(".tl-handle")).toBeNull();
    act(() => $<HTMLButtonElement>("button[aria-label=Trim]").click());
    const start = $("[data-edge=start]");
    pointer(start, "pointerdown", 0);
    pointer(start, "pointermove", 1.3 * pxPerSec);
    pointer(start, "pointerup", 1.3 * pxPerSec);
    expect(p.onTrim).toHaveBeenCalledWith(1, 5);
  });

  it("in Trim mode, dragging the end edge snaps to the nearest word end", () => {
    const p = render();
    act(() => $<HTMLButtonElement>("button[aria-label=Trim]").click());
    const end = $("[data-edge=end]");
    pointer(end, "pointerdown", 5.9 * pxPerSec);
    pointer(end, "pointerup", 3.7 * pxPerSec);
    expect(p.onTrim).toHaveBeenCalledWith(0, 3); // word 3 ends at 3.9
  });

  it("moves a trim edge by one word with the arrow keys", () => {
    const p = render();
    act(() => $<HTMLButtonElement>("button[aria-label=Trim]").click());
    press($("[data-edge=start]"), "ArrowRight");
    expect(p.onTrim).toHaveBeenLastCalledWith(1, 5);
    press($("[data-edge=end]"), "ArrowLeft");
    expect(p.onTrim).toHaveBeenLastCalledWith(0, 4);
  });

  it("widens the lanes with the zoom slider", () => {
    render();
    const content = $(".tl-content");
    expect(content.style.width).toBe(`${FALLBACK_WIDTH}px`);
    const zoom = $<HTMLInputElement>("input[type=range]");
    act(() => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      set.call(zoom, "3");
      zoom.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(content.style.width).toBe(`${FALLBACK_WIDTH * 3}px`);
  });
});

describe("split and delete", () => {
  it("splits the piece under the playhead, selects the new piece, and deletes it as a cut", () => {
    const p = render({ playhead: 4.1, kept: new Set([0, 1, 2, 3, 4, 5]) });
    const splitBtn = [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.getAttribute("aria-label") === "Split")!;
    expect(splitBtn.disabled).toBe(false);
    act(() => splitBtn.click());
    expect(host.querySelectorAll(".tl-piece").length).toBe(2);
    const del = [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.getAttribute("aria-label") === "Delete piece")!;
    expect(del.disabled).toBe(false);
    act(() => del.click());
    expect(p.onCut).toHaveBeenCalledWith(4, 5);
  });
  it("cannot split at the start of a piece", () => {
    render({ playhead: 0 });
    const splitBtn = [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.getAttribute("aria-label") === "Split")!;
    expect(splitBtn.disabled).toBe(true);
  });
});

describe("text and b-roll layers", () => {
  const OV = [
    { id: "t1", type: "text" as const, at: { src: "main", t: 1 }, duration: 2, text: "Hello", position: "top" as const },
    { id: "b1", type: "image" as const, at: { src: "main", t: 3 }, duration: 1, path: "C:/clips/shot.png", mode: "full" as const },
  ];
  const btn = (label: string) => [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.getAttribute("aria-label") === label || b.title === label || b.textContent === label)!;

  it("draws each layer in its own lane", () => {
    render({ overlays: OV, onOverlays: vi.fn() });
    expect($(".tl-ov-text .tl-ovb").textContent).toBe("Hello");
    expect($(".tl-ov-broll .tl-ovb").textContent).toBe("shot.png");
  });

  it("shows the layer the tools picked and hands a click back to them", () => {
    const onLayer = vi.fn();
    render({ overlays: OV, onOverlays: vi.fn(), layer: "b1", onLayer });
    expect($(".tl-ov-broll .tl-ovb").getAttribute("aria-pressed")).toBe("true");
    expect($(".tl-ov-text .tl-ovb").getAttribute("aria-pressed")).toBe("false");
    pointer($(".tl-ov-text .tl-ovb"), "pointerdown", 1.5 * pxPerSec);
    pointer($(".tl-ov-text .tl-ovb"), "pointerup", 1.5 * pxPerSec);
    expect(onLayer).toHaveBeenCalledWith("t1");
  });

  it("drags a layer later as one edit", () => {
    const onOverlays = vi.fn();
    render({ overlays: OV, onOverlays });
    const block = $(".tl-ov-text .tl-ovb");
    pointer(block, "pointerdown", 1 * pxPerSec);
    pointer(block, "pointermove", 2 * pxPerSec);
    expect(onOverlays).not.toHaveBeenCalled();
    pointer(block, "pointerup", 2 * pxPerSec);
    expect(onOverlays).toHaveBeenCalledTimes(1);
    expect(onOverlays.mock.calls[0][0][0].at.t).toBeCloseTo(2, 1);
    expect(onOverlays.mock.calls[0][0][0].duration).toBe(2);
  });

  it("drags the end edge to make a layer longer", () => {
    const onOverlays = vi.fn();
    render({ overlays: OV, onOverlays });
    const end = $(".tl-ov-text .tl-ovb-edge.end");
    pointer(end, "pointerdown", 3 * pxPerSec);
    pointer(end, "pointerup", 4 * pxPerSec);
    expect(onOverlays.mock.calls[0][0][0]).toMatchObject({ at: { t: 1 } });
    expect(onOverlays.mock.calls[0][0][0].duration).toBeCloseTo(3, 1);
  });

  it("nudges a layer with the arrow keys", () => {
    const onOverlays = vi.fn();
    render({ overlays: OV, onOverlays });
    press($(".tl-ov-text .tl-ovb"), "ArrowRight");
    expect(onOverlays.mock.calls[0][0][0].at.t).toBeCloseTo(1.1, 5);
  });

  it("deletes the selected layer, not the video under it", () => {
    const onOverlays = vi.fn();
    const p = render({ overlays: OV, onOverlays });
    pointer($(".tl-ov-broll .tl-ovb"), "pointerdown", 3 * pxPerSec);
    pointer($(".tl-ov-broll .tl-ovb"), "pointerup", 3 * pxPerSec);
    act(() => btn("Delete layer").click());
    expect(onOverlays).toHaveBeenLastCalledWith([OV[0]]);
    expect(p.onCut).not.toHaveBeenCalled();
  });
});

describe("toolbar", () => {
  const OV = [{ id: "t1", type: "text" as const, at: { src: "main", t: 1 }, duration: 2, text: "Hello", position: "top" as const }];
  const btn = (label: string) => [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.getAttribute("aria-label") === label)!;

  it("undoes and redoes, and is off when there is nothing to undo or redo", () => {
    const onUndo = vi.fn();
    const onRedo = vi.fn();
    render({ onUndo, onRedo, canUndo: true, canRedo: false });
    expect(btn("Redo").disabled).toBe(true);
    act(() => btn("Undo").click());
    expect(onUndo).toHaveBeenCalledTimes(1);
  });

  it("duplicates the selected layer right after it", () => {
    const onOverlays = vi.fn();
    render({ overlays: OV, onOverlays });
    expect(btn("Duplicate layer").disabled).toBe(true);
    pointer($(".tl-ov-text .tl-ovb"), "pointerdown", 1 * pxPerSec);
    pointer($(".tl-ov-text .tl-ovb"), "pointerup", 1 * pxPerSec);
    act(() => btn("Duplicate layer").click());
    const items = onOverlays.mock.calls[0][0];
    expect(items).toHaveLength(2);
    expect(items[1]).toMatchObject({ type: "text", text: "Hello", at: { t: 3 }, duration: 2 });
    expect(items[1].id).not.toBe("t1");
  });

  it("zooms in, out, and back to the whole clip", () => {
    render();
    const content = $(".tl-content");
    expect(btn("Zoom out").disabled).toBe(true);
    act(() => btn("Zoom in").click());
    act(() => btn("Zoom in").click());
    expect(content.style.width).toBe(`${FALLBACK_WIDTH * 3}px`);
    act(() => btn("Zoom out").click());
    expect(content.style.width).toBe(`${FALLBACK_WIDTH * 2}px`);
    act(() => btn("Fit to clip").click());
    expect(content.style.width).toBe(`${FALLBACK_WIDTH}px`);
  });
});
