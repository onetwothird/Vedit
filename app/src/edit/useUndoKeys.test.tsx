// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUndoKeys } from "./useUndoKeys";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const store = { undo: vi.fn(), redo: vi.fn() };

function Probe() {
  useUndoKeys(store);
  return <input data-testid="field" />;
}

let host: HTMLElement;
let root: Root;
beforeEach(async () => {
  store.undo.mockClear();
  store.redo.mockClear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root.render(<Probe />));
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

const press = (init: KeyboardEventInit, target: EventTarget = document.body) =>
  target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }));

describe("useUndoKeys", () => {
  it("binds Ctrl+Z to undo and Ctrl+Shift+Z / Ctrl+Y to redo", () => {
    press({ key: "z", ctrlKey: true });
    press({ key: "Z", ctrlKey: true, shiftKey: true });
    press({ key: "y", ctrlKey: true });
    expect(store.undo).toHaveBeenCalledTimes(1);
    expect(store.redo).toHaveBeenCalledTimes(2);
  });

  it("also answers to Cmd on a Mac", () => {
    press({ key: "z", metaKey: true });
    expect(store.undo).toHaveBeenCalledTimes(1);
  });

  it("ignores plain keys and other combos", () => {
    press({ key: "z" });
    press({ key: "x", ctrlKey: true });
    press({ key: "z", ctrlKey: true, altKey: true });
    expect(store.undo).not.toHaveBeenCalled();
  });

  it("leaves the keys to inputs, textareas and contenteditable", () => {
    const field = host.querySelector("input")!;
    press({ key: "z", ctrlKey: true }, field);
    const area = document.createElement("textarea");
    const rich = document.createElement("div");
    rich.setAttribute("contenteditable", "true");
    host.append(area, rich);
    press({ key: "z", ctrlKey: true }, area);
    press({ key: "z", ctrlKey: true }, rich);
    expect(store.undo).not.toHaveBeenCalled();
  });

  it("still undoes from a radio, checkbox, slider or colour control, which have no text to undo", () => {
    for (const type of ["radio", "checkbox", "range", "color"]) {
      const el = document.createElement("input");
      el.type = type;
      host.append(el);
      press({ key: "z", ctrlKey: true }, el);
    }
    expect(store.undo).toHaveBeenCalledTimes(4);
  });

  it("stops listening on unmount", async () => {
    await act(async () => root.render(<div />));
    press({ key: "z", ctrlKey: true });
    expect(store.undo).not.toHaveBeenCalled();
  });
});
