// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ProjectClip } from "../engine/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import ClipList from "./ClipList";

const clip = (id: string, start: number, score: number, kept = true): ProjectClip => ({
  highlight: { id, start, end: start + 32, w0: 0, w1: 1, score, title: `Clip ${id}`, features: {} },
  kept,
  edl_file: `clip-${id}.edl.json`,
});
// time order a, b, c; score order b, c, a
const CLIPS = [clip("a", 0, 50), clip("b", 40, 90), clip("c", 80, 70, false)];

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

function render(over: Partial<React.ComponentProps<typeof ClipList>> = {}) {
  const props = {
    clips: CLIPS, currentId: undefined, sort: "best" as const, film: null,
    onSort: vi.fn(), onSelect: vi.fn(), onToggleKept: vi.fn(), ...over,
  };
  act(() => root.render(<ClipList {...props} />));
  return props;
}
const titles = () => [...host.querySelectorAll(".clip-title")].map((e) => e.textContent);
const press = (el: Element, key: string) =>
  act(() => void el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })));

describe("ClipList", () => {
  it("lists best first by score, or in order by start time", () => {
    render({ sort: "best" });
    expect(titles()).toEqual(["Clip b", "Clip c", "Clip a"]);
    render({ sort: "order" });
    expect(titles()).toEqual(["Clip a", "Clip b", "Clip c"]);
  });

  it("calls onSort from the sort control", () => {
    const p = render({ sort: "best" });
    const order = [...host.querySelectorAll<HTMLInputElement>("input[name=clip-sort]")].find((i) => i.value === "order")!;
    act(() => order.click());
    expect(p.onSort).toHaveBeenCalledWith("order");
  });

  it("shows the length and the score of each clip", () => {
    render();
    expect(host.textContent).toContain("0:32");
    expect(host.textContent).toContain("90");
  });

  it("calls onToggleKept with the clip id and reflects kept", () => {
    const p = render();
    const boxes = [...host.querySelectorAll<HTMLInputElement>("input[type=checkbox]")];
    expect(boxes.map((b) => b.checked)).toEqual([true, false, true]); // b, c, a
    act(() => boxes[1].click());
    expect(p.onToggleKept).toHaveBeenCalledWith("c");
  });

  it("selects on click and marks the current clip", () => {
    const p = render({ currentId: "c" });
    expect(host.querySelector("[aria-current=true] .clip-title")!.textContent).toBe("Clip c");
    act(() => host.querySelectorAll<HTMLButtonElement>(".clip-main")[2].click());
    expect(p.onSelect).toHaveBeenCalledWith("a");
  });

  it("moves the selection with the arrow keys inside the sorted list", () => {
    const p = render({ currentId: "b", sort: "best" });
    press(host.querySelector("[aria-current=true]")!, "ArrowDown");
    expect(p.onSelect).toHaveBeenLastCalledWith("c");
    const q = render({ currentId: "c", sort: "best" });
    press(host.querySelector("[aria-current=true]")!, "ArrowUp");
    expect(q.onSelect).toHaveBeenLastCalledWith("b");
  });

  it("does not move past either end, and starts at the first clip with nothing selected", () => {
    const p = render({ currentId: "a", sort: "best" }); // a is last in best order
    press(host.querySelector("[aria-current=true]")!, "ArrowDown");
    expect(p.onSelect).not.toHaveBeenCalled();
    const top = render({ currentId: "b", sort: "best" });
    press(host.querySelector("[aria-current=true]")!, "ArrowUp");
    expect(top.onSelect).not.toHaveBeenCalled();
    const q = render({ currentId: undefined, sort: "order" });
    press(host.querySelectorAll(".clip-main")[0], "ArrowDown");
    expect(q.onSelect).toHaveBeenCalledWith("a");
  });

  it("follows the order sort for arrow keys", () => {
    const p = render({ currentId: "a", sort: "order" });
    press(host.querySelector("[aria-current=true]")!, "ArrowDown");
    expect(p.onSelect).toHaveBeenCalledWith("b");
  });
});
