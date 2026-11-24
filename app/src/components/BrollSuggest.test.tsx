import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BrollSuggestions, Overlay } from "../engine/types";
import { BrollSuggest } from "./BrollSuggest";

let host: HTMLElement;
let root: Root;
beforeEach(() => {
  localStorage.clear();
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const RESULT: BrollSuggestions = {
  files: 3, ai: null, suggestions: [
    { path: "C:/pics/beach.jpg", name: "beach.jpg", type: "image", t: 12, duration: 3, word: "beach", source: "name", why: "" },
    { path: "C:/pics/jeep.mp4", name: "jeep.mp4", type: "video", t: 17, duration: 3, word: "jeepney", source: "ai", why: "a ride in town" },
  ],
};

const btn = (text: string) => [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === text);
const byLabel = (label: string) => host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
const field = () => host.querySelector<HTMLInputElement>("input")!;

function type(value: string) {
  const input = field();
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    set.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function submit() {
  await act(async () => {
    host.querySelector("form")!.requestSubmit();
  });
}

function render(suggest = vi.fn(async () => RESULT), onAdd = vi.fn()) {
  act(() => root.render(<BrollSuggest suggest={suggest} onAdd={onAdd} t0={10} t1={18.5} />));
  return { suggest, onAdd };
}

describe("Suggest b-roll", () => {
  it("asks only once a folder is typed, and remembers the folder", async () => {
    const { suggest } = render();
    expect(btn("Suggest b-roll")!.disabled).toBe(true);
    type("  C:/pics ");
    await submit();
    expect(suggest).toHaveBeenCalledWith("C:/pics");
    expect(localStorage.getItem("vedit.broll.folder")).toBe("C:/pics");
    act(() => root.unmount());
    root = createRoot(host);
    render();
    expect(field().value).toBe("C:/pics");
  });

  it("lists suggestions with their moment and words, and adds one inside the clip", async () => {
    const { onAdd } = render();
    type("C:/pics");
    await submit();
    expect(host.querySelectorAll(".bs-item")).toHaveLength(2);
    expect(host.textContent).toContain("At 0:02, “beach”");
    expect(host.textContent).toContain("a ride in town");
    act(() => byLabel("Add jeep.mp4").click());
    const [items] = onAdd.mock.calls[0] as [Overlay[]];
    // 17 s + 3 s would run past the clip's end at 18.5
    expect(items).toEqual([expect.objectContaining({ type: "video", path: "C:/pics/jeep.mp4", at: { src: "main", t: 17 }, duration: 1.5, mode: "full" })]);
    expect(items[0].id).toBeTruthy();
    expect(host.querySelectorAll(".bs-item")).toHaveLength(1);
  });

  it("skips without adding, then says every suggestion is handled", async () => {
    const { onAdd } = render();
    type("C:/pics");
    await submit();
    act(() => byLabel("Skip beach.jpg").click());
    act(() => byLabel("Skip jeep.mp4").click());
    expect(onAdd).not.toHaveBeenCalled();
    expect(host.textContent).toContain("That's every suggestion.");
  });

  it("adds all in one edit", async () => {
    const { onAdd } = render();
    type("C:/pics");
    await submit();
    act(() => btn("Add all")!.click());
    expect(onAdd).toHaveBeenCalledTimes(1);
    expect((onAdd.mock.calls[0][0] as Overlay[]).map((o) => o.type !== "text" && o.path)).toEqual(["C:/pics/beach.jpg", "C:/pics/jeep.mp4"]);
    expect(host.querySelectorAll(".bs-item")).toHaveLength(0);
  });

  it("explains an empty folder, no matches, an AI failure and an engine error", async () => {
    const suggest = vi.fn()
      .mockResolvedValueOnce({ files: 0, ai: null, suggestions: [] })
      .mockResolvedValueOnce({ files: 7, ai: "ai_failed", suggestions: [] })
      .mockRejectedValueOnce(new Error("folder must be an existing absolute folder"));
    render(suggest);
    type("C:/pics");
    await submit();
    expect(host.textContent).toContain("That folder has no photos or videos.");
    await submit();
    expect(host.textContent).toContain("(7 photos and videos)");
    expect(host.textContent).toContain("The AI couldn't be reached");
    await submit();
    expect(host.querySelector("[role=alert]")!.textContent).toContain("folder must be an existing absolute folder");
  });
});
