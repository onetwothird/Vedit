import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Overlay } from "../engine/types";
import { BrollTool, TextTool, type LayerToolProps } from "./LayerTools";

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

const OV: Overlay[] = [
  { id: "t1", type: "text", at: { src: "main", t: 1 }, duration: 2, text: "Hello", position: "top" },
  { id: "b1", type: "image", at: { src: "main", t: 3 }, duration: 1, path: "C:/clips/shot.png", mode: "full" },
];
function props(over: Partial<LayerToolProps> = {}): LayerToolProps {
  return { overlays: OV, onOverlays: vi.fn(), layer: null, onLayer: vi.fn(), playhead: 2, t0: 0, t1: 6, onSeek: vi.fn(), ...over };
}
const btn = (text: string) => [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.includes(text))!;
const radio = (label: string) => [...host.querySelectorAll("label")].find((l) => l.textContent === label)!.querySelector("input")!;

describe("Text tool", () => {
  it("adds text at the playhead and selects it", () => {
    const p = props();
    act(() => root.render(<TextTool {...p} />));
    act(() => btn("Add text at the playhead").click());
    const items = (p.onOverlays as ReturnType<typeof vi.fn>).mock.calls[0][0] as Overlay[];
    expect(items).toHaveLength(3);
    expect(items[2]).toMatchObject({ type: "text", text: "Your text", at: { t: 2 } });
    expect(p.onLayer).toHaveBeenCalledWith(items[2].id);
  });

  it("lists only text layers, and picking one selects it and seeks there", () => {
    const p = props();
    act(() => root.render(<TextTool {...p} />));
    const items = host.querySelectorAll(".ly-item");
    expect(items).toHaveLength(1);
    act(() => (items[0] as HTMLButtonElement).click());
    expect(p.onLayer).toHaveBeenCalledWith("t1");
    expect(p.onSeek).toHaveBeenCalledWith(1);
  });

  it("edits the selected text: words on Enter, position, duplicate and delete", () => {
    const p = props({ layer: "t1" });
    act(() => root.render(<TextTool {...p} />));
    const on = p.onOverlays as ReturnType<typeof vi.fn>;
    const field = host.querySelector("textarea")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(field, "Wait for it");
      field.dispatchEvent(new Event("input", { bubbles: true }));
    });
    act(() => void field.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
    expect(on.mock.calls.at(-1)![0][0]).toMatchObject({ id: "t1", text: "Wait for it" });
    act(() => radio("Middle").click());
    expect(on.mock.calls.at(-1)![0][0]).toMatchObject({ id: "t1", position: "center" });
    act(() => btn("Duplicate").click());
    const dup = on.mock.calls.at(-1)![0] as Overlay[];
    expect(dup).toHaveLength(3);
    expect(dup[2]).toMatchObject({ type: "text", at: { t: 3 } });
    act(() => btn("Delete").click());
    expect((on.mock.calls.at(-1)![0] as Overlay[]).map((o) => o.id)).toEqual(["b1"]);
    expect(p.onLayer).toHaveBeenLastCalledWith(null);
  });
});

describe("B-roll tool", () => {
  it("switches the selected b-roll into a corner", () => {
    const p = props({ layer: "b1" });
    act(() => root.render(<BrollTool {...p} />));
    expect(host.querySelectorAll(".ly-item")).toHaveLength(1);
    act(() => radio("In a corner").click());
    expect((p.onOverlays as ReturnType<typeof vi.fn>).mock.calls[0][0][1]).toMatchObject({ id: "b1", mode: "pip", position: "top-right" });
  });
});
