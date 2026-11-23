import { useEffect } from "react";
import { useClipStore } from "./edlStore";

interface UndoActions {
  undo: () => void;
  redo: () => void;
}

// inputs with no text of their own to undo: Ctrl+Z there undoes the edit they just made
const CHOICE_INPUTS = new Set(["radio", "checkbox", "range", "color", "button", "submit", "reset", "file"]);

function typingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el || !el.tagName) return false;
  return (el.tagName === "INPUT" && !CHOICE_INPUTS.has((el as HTMLInputElement).type)) || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable
    || el.closest?.("[contenteditable]:not([contenteditable=false])") != null;
}

/** Ctrl/Cmd+Z undoes; Ctrl/Cmd+Shift+Z and Ctrl/Cmd+Y redo. Keys typed into a text field are left alone;
 * a radio, checkbox, slider or colour control has no text to undo, so there they undo the edit. */
export function useUndoKeys(actions: UndoActions = useClipStore.getState()): void {
  const { undo, redo } = actions;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || typingTarget(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) undo();
      else if ((k === "z" && e.shiftKey) || (k === "y" && !e.shiftKey)) redo();
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);
}
