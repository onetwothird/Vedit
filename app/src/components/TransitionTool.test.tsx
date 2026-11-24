import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { withInverse } from "../edit/inverse";
import { applyOp, patchSection } from "../edit/timeline";
import type { Edl } from "../engine/types";
import Inspector from "./Inspector";

const EDL = { sources: { main: "a.mp4" }, clips: [{ words: [0, 3] }, { words: [6, 9] }] } as unknown as Edl;

describe("transition patch (the local port of edl_ops)", () => {
  it("sets the type and length, and a null removes them", () => {
    let e = patchSection(EDL, "transition", { type: "crossfade", duration: 0.6 });
    expect(e.transition).toEqual({ type: "crossfade", duration: 0.6 });
    expect(EDL.transition).toBeUndefined();
    e = patchSection(e, "transition", { type: "whip" });
    expect(e.transition).toEqual({ type: "whip", duration: 0.6 });
    e = patchSection(e, "transition", { type: null, duration: null });
    expect(e.transition).toBeUndefined();
  });

  it("refuses what the engine refuses", () => {
    expect(() => patchSection(EDL, "transition", { type: "dissolve" })).toThrow(/type/);
    expect(() => patchSection(EDL, "transition", { type: "whip", duration: 3 })).toThrow(/duration/);
    expect(() => patchSection(EDL, "transition", { style: "fast" })).toThrow(/unknown transition keys/);
  });

  it("undoes back to a hard cut", () => {
    const op = { op: "patch" as const, section: "transition" as const, patch: { type: "whip" } };
    const { inverse } = withInverse(EDL, op);
    const after = applyOp(EDL, op);
    expect(inverse.reduce(applyOp, after).transition).toBeUndefined();
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

const radio = (label: string) => [...host.querySelectorAll("label")].find((l) => l.textContent === label)!.querySelector("input")!;

function show(edl: Edl, onPatch = vi.fn()) {
  const layers = { overlays: [], onOverlays: vi.fn(), layer: null, onLayer: vi.fn(), playhead: 0, t0: 0, t1: 5, onSeek: vi.fn() };
  act(() => root.render(
    <Inspector edl={edl} tab="transition" onTab={vi.fn()} transcript={null} onPatch={onPatch} layers={layers} aspect="9:16" />,
  ));
  return onPatch;
}

describe("Transition tool", () => {
  it("starts on a hard cut, with no length to pick", () => {
    show(EDL);
    expect(radio("Cut").checked).toBe(true);
    expect(host.textContent).not.toContain("0.4 s");
  });

  it("sends the type the engine takes through the patch op", () => {
    const onPatch = show(EDL);
    act(() => radio("Whip").click());
    expect(onPatch).toHaveBeenCalledWith("transition", { type: "whip" });
  });

  it("shows the length once a transition is on, and going back to Cut clears the type", () => {
    const onPatch = show({ ...EDL, transition: { type: "crossfade" } } as Edl);
    expect(radio("Crossfade").checked).toBe(true);
    expect(radio("0.4 s").checked).toBe(true); // the engine's default length
    act(() => radio("0.8 s").click());
    expect(onPatch).toHaveBeenCalledWith("transition", { duration: 0.8 });
    act(() => radio("Cut").click());
    expect(onPatch).toHaveBeenCalledWith("transition", { type: null });
  });
});
