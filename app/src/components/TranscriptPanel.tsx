import { memo, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from "react";
import type { TranscriptWord } from "../engine/types";
import { t } from "../i18n/t";
import { laneSegments } from "../edit/lanes";
import { bareWord, spelledAs } from "../edit/spelling";

export interface TranscriptPanelProps {
  words: readonly TranscriptWord[];
  /** The clip's word range in the transcript. */
  w0: number;
  w1: number;
  kept: ReadonlySet<number>;
  /** Caption spelling fixes (edl.captions.replace). */
  replace: Record<string, string>;
  /** The word under the playhead. */
  now: number | null;
  /** The panel is on screen: Delete, F2 and Esc act on its selection. */
  active: boolean;
  onCut(w0: number, w1: number): void;
  onRestore(w0: number, w1: number): void;
  onFix(wrong: string, right: string): void;
  onPlayFrom(w: number): void;
}

type Sel = { anchor: number; focus: number };

function typing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return !!el?.tagName && (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable);
}

const clock = (s: number): string => {
  const m = Math.floor(s / 60);
  return `${String(m).padStart(2, "0")}:${String(Math.floor(s - m * 60)).padStart(2, "0")}`;
};

/** The clip's words: click to cut, restore or fix one, shift-click for a range, and the count of what is cut. */
function TranscriptPanel({ words, w0, w1, kept, replace, now, active, onCut, onRestore, onFix, onPlayFrom }: TranscriptPanelProps) {
  const [sel, setSel] = useState<Sel | null>(null);
  const [menu, setMenu] = useState<"menu" | "fix" | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);
  const [focusW, setFocusW] = useState(w0);
  const scroll = useRef<HTMLDivElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const fixInput = useRef<HTMLInputElement>(null);
  const els = useRef(new Map<number, HTMLButtonElement>());

  // another clip starts with nothing selected
  useEffect(() => {
    setSel(null);
    setMenu(null);
    setFocusW(w0);
  }, [w0, w1]);

  const lo = sel ? Math.min(sel.anchor, sel.focus) : -1;
  const hi = sel ? Math.max(sel.anchor, sel.focus) : -1;
  const count = sel ? hi - lo + 1 : 0;
  const allCut = sel ? Array.from({ length: count }, (_, k) => lo + k).every((i) => !kept.has(i)) : false;

  // the popover sits under the last word picked, inside the scrolling transcript
  useLayoutEffect(() => {
    if (!menu || !sel) return setPos(null);
    const box = scroll.current, el = els.current.get(sel.focus);
    if (!box || !el) return;
    const r = el.getBoundingClientRect(), b = box.getBoundingClientRect();
    const popW = pop.current?.offsetWidth ?? 260;
    setPos({
      left: Math.max(8, Math.min(r.left - b.left - 20, box.clientWidth - popW - 8)),
      top: r.bottom - b.top + box.scrollTop + 6,
    });
  }, [menu, sel]);

  // focus the spelling field once the popover is placed (a hidden element cannot take focus)
  useEffect(() => {
    if (menu === "fix" && pos) fixInput.current?.focus();
  }, [menu, pos]);

  function close(refocus = true) {
    setMenu(null);
    if (refocus && sel) els.current.get(sel.focus)?.focus();
  }

  function cutOrRestore() {
    if (!sel) return;
    if (allCut) onRestore(lo, hi);
    else onCut(lo, hi);
    close();
  }

  // Delete cuts the selection, F2 fixes the word, Esc closes the popover and then drops the selection
  useEffect(() => {
    if (!active) return;
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      if ((e.key === "Delete" || e.key === "Backspace") && sel) {
        if (!allCut) onCut(lo, hi);
        setMenu(null);
      } else if (e.key === "F2" && sel) {
        setSel({ anchor: sel.focus, focus: sel.focus });
        setMenu("fix");
      } else if (e.key === "Escape" && (menu || sel)) {
        if (menu) close();
        else setSel(null);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // a click anywhere else closes the popover
  useEffect(() => {
    if (!menu) return;
    const onDown = (e: PointerEvent) => {
      const n = e.target as Node;
      if (pop.current?.contains(n) || [...els.current.values()].some((el) => el.contains(n))) return;
      setMenu(null);
    };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [menu]);

  function onWordClick(i: number, e: MouseEvent<HTMLButtonElement>) {
    setFocusW(i);
    setSel(e.shiftKey && sel ? { anchor: sel.anchor, focus: i } : { anchor: i, focus: i });
    setMenu("menu");
  }

  function onWordsKey(e: KeyboardEvent<HTMLDivElement>) {
    const d = e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0;
    if (!d || !(e.target as HTMLElement).classList.contains("w")) return;
    e.preventDefault(); // the player's own arrow keys skip; here they move between words
    const next = Math.min(w1, Math.max(w0, focusW + d));
    setFocusW(next);
    els.current.get(next)?.focus();
    if (e.shiftKey) setSel({ anchor: sel?.anchor ?? focusW, focus: next });
    setMenu(null);
  }

  // footer: what the cuts in this clip remove
  const segs = laneSegments(words, w0, w1, kept);
  const gaps = segs.filter((s) => s.kind === "gap");
  const cutN = gaps.reduce((n, s) => n + s.w1 - s.w0 + 1, 0);
  const saved = gaps.reduce((n, s) => n + s.end - s.start, 0);

  const speakers = new Map<string, number>();
  const items: React.ReactNode[] = [];
  let lastSpeaker: string | null | undefined;
  for (let i = w0; i <= w1 && i < words.length; i++) {
    const w = words[i];
    const spk = w.speaker ?? null;
    if (i === w0 || spk !== lastSpeaker) {
      if (spk && !speakers.has(spk)) speakers.set(spk, speakers.size + 1);
      items.push(
        <span key={`s${i}`} className="spk">
          {t("edit.tx.speaker", { n: spk ? speakers.get(spk)! : 1 })} · <span className="mono">{clock(w.start - words[w0].start)}</span>
        </span>,
      );
      lastSpeaker = spk;
    }
    const cut = !kept.has(i);
    const cls = ["w", cut && "cut", i === now && "now", i >= lo && i <= hi && "sel"].filter(Boolean).join(" ");
    items.push(
      <button
        key={i}
        type="button"
        className={cls}
        data-w={i}
        tabIndex={i === focusW ? 0 : -1}
        aria-pressed={i >= lo && i <= hi}
        ref={(el) => {
          if (el) els.current.set(i, el);
          else els.current.delete(i);
        }}
        onClick={(e) => onWordClick(i, e)}
      >
        {spelledAs(w.text, replace)}
        {cut && <span className="sr-only"> ({t("edit.tx.cut")})</span>}
      </button>,
      " ",
    );
  }

  const focusWord = sel ? words[sel.focus] : undefined;
  const label = allCut
    ? count === 1 ? t("edit.tx.restore1") : t("edit.tx.restoreN", { n: count })
    : count === 1 ? t("edit.tx.cut1") : t("edit.tx.cutN", { n: count });

  return (
    <div className="ed-tx">
      <div className="ed-tx-scroll" ref={scroll}>
        <p className="hint">{t("edit.tx.hint")}</p>
        <div className="tx" role="group" aria-label={t("edit.tx.words")} onKeyDown={onWordsKey}>
          {items}
        </div>
        {menu && sel && focusWord && (
          <div
            className="ed-pop"
            ref={pop}
            role="group"
            aria-label={t("edit.tx.actions")}
            style={pos ? { left: pos.left, top: pos.top } : { visibility: "hidden" }}
          >
            {menu === "menu" ? (
              <>
                <button type="button" className="secondary" onClick={cutOrRestore}>
                  {label} <kbd className="mono">Del</kbd>
                </button>
                {count === 1 && (
                  <button type="button" className="secondary" onClick={() => setMenu("fix")}>
                    {t("edit.tx.fix")} <kbd className="mono">F2</kbd>
                  </button>
                )}
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    onPlayFrom(lo);
                    close(false);
                  }}
                >
                  {t("edit.tx.play")}
                </button>
              </>
            ) : (
              <label className="ed-fix">
                <input
                  className="ed-input"
                  aria-label={t("edit.tx.fix.label")}
                  defaultValue={bareWord(spelledAs(focusWord.text, replace))}
                  ref={fixInput}
                  spellCheck
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      const right = e.currentTarget.value.trim();
                      if (right) onFix(bareWord(focusWord.text), right);
                      close();
                    } else if (e.key === "Escape") {
                      e.preventDefault();
                      e.stopPropagation();
                      close();
                    }
                  }}
                />
                <span className="ed-fix-hint">{t("edit.tx.fix.hint")}</span>
              </label>
            )}
          </div>
        )}
      </div>
      <div className="ed-foot">
        <span className="ed-cutinfo">
          {t(`edit.tx.foot.${cutN === 1 ? "one" : "other"}`, { n: cutN, s: saved.toFixed(1) })}
        </span>
        <button type="button" className="secondary" disabled={cutN === 0} onClick={() => onRestore(w0, w1)}>
          {t("edit.tx.restoreAll")}
        </button>
      </div>
    </div>
  );
}

export default memo(TranscriptPanel);
