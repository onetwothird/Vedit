import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { isTauri } from "../engine/client";
import type { Edl, EditSection } from "../engine/types";
import { t } from "../i18n/t";
import { AUDIO_ACCEPT, baseName, pickAudioNative } from "../lib/native";

export type InspectorTab = "transcript" | "captions" | "framing" | "sound";
export const TABS: InspectorTab[] = ["transcript", "captions", "framing", "sound"];

export interface InspectorProps {
  edl: Edl;
  tab: InspectorTab;
  onTab(tab: InspectorTab): void;
  /** The Transcript tab's content. */
  transcript: ReactNode;
  onPatch(section: EditSection, patch: Record<string, unknown>): void;
}

type Rec = Record<string, unknown>;
const rec = (v: unknown): Rec => (v && typeof v === "object" ? (v as Rec) : {});

const LOUDNESS: [lufs: number, key: string][] = [[-14, "social"], [-16, "podcast"], [-23, "quiet"]];
const DEFAULT_VOLUME = 0.18;

interface Option<T> {
  value: T;
  label: string;
  /** The accessible name, when the visible label alone is ambiguous (On / Off). */
  aria?: string;
}

/** A labelled row with one segmented choice. */
function Choice<T extends string | number | boolean>({ label, sub, name, value, options, onChange }: {
  label: string; sub?: string; name: string; value: T | undefined; options: Option<T>[]; onChange(v: T): void;
}) {
  const id = useId();
  return (
    <div className="ed-row">
      <span className="ed-label">
        <span id={id}>{label}</span>
        {sub && <span className="ed-file">{sub}</span>}
      </span>
      <div className="seg" role="radiogroup" aria-labelledby={id}>
        {options.map((o) => (
          <label key={String(o.value)} className="seg-opt">
            <input type="radio" name={`${name}-${id}`} checked={value === o.value} aria-label={o.aria} onChange={() => onChange(o.value)} />
            <span>{o.label}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

const onOff = (label: string): Option<boolean>[] => [
  { value: true, label: t("edit.on"), aria: t("edit.choice", { label, value: t("edit.on") }) },
  { value: false, label: t("edit.off"), aria: t("edit.choice", { label, value: t("edit.off") }) },
];

/** A slider that saves once, when it is let go (dragging would otherwise save every step). */
function Slider({ label, value, min, max, step, format, onCommit }: {
  label: string; value: number; min: number; max: number; step: number; format(v: number): string; onCommit(v: number): void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  const dirty = useRef(false);
  useEffect(() => {
    if (!dirty.current) setDraft(value);
  }, [value]);
  const commit = () => {
    if (!dirty.current) return;
    dirty.current = false;
    if (Math.abs(draft - value) > 1e-9) onCommit(Math.round(draft / step) * step);
  };
  return (
    <div className="ed-row">
      <label className="ed-label" htmlFor={id}>{label}</label>
      <span className="ed-slider">
        <input
          id={id}
          type="range"
          min={min}
          max={max}
          step={step}
          value={draft}
          onChange={(e) => {
            dirty.current = true;
            setDraft(Number(e.currentTarget.value));
          }}
          onPointerUp={commit}
          onKeyUp={commit}
          onBlur={commit}
        />
        <output className="ed-value" htmlFor={id}>{format(draft)}</output>
      </span>
    </div>
  );
}

/** A colour input that saves when the picker closes, not on every colour it passes through. */
function Color({ label, value, onCommit }: { label: string; value: string; onCommit(v: string): void }) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const latest = useRef({ value, onCommit });
  latest.current = { value, onCommit };
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    const onChange = () => {
      const v = el.value.toUpperCase();
      if (v !== latest.current.value.toUpperCase()) latest.current.onCommit(v);
    };
    el.addEventListener("change", onChange);
    return () => el.removeEventListener("change", onChange);
  }, []);
  return (
    <div className="ed-row">
      <label className="ed-label" htmlFor={id}>{label}</label>
      <span className="ed-slider">
        <span className="ed-value">{value.toUpperCase()}</span>
        <input id={id} ref={input} className="ed-color" type="color" defaultValue={value} key={value} />
      </span>
    </div>
  );
}

function Captions({ edl, onPatch }: Pick<InspectorProps, "edl" | "onPatch">) {
  const c = rec(edl.captions);
  const style = (c.style as string | undefined) ?? "pop";
  const clean = style === "clean";
  const patch = (p: Rec) => onPatch("captions", p);
  return (
    <>
      <p className="hint">{t("edit.cap.hint")}</p>
      <Choice
        label={t("edit.cap.style")} name="cap-style" value={style}
        options={["pop", "box", "clean"].map((s) => ({ value: s, label: t(`edit.cap.style.${s}`) }))}
        onChange={(v) => patch({ style: v })}
      />
      <Choice
        label={t("edit.cap.words")} name="cap-words" value={Number(c.max_words ?? (clean ? 10 : 3))}
        options={[1, 2, 3, 4, 5].map((n) => ({ value: n, label: String(n) }))}
        onChange={(v) => patch({ max_words: v })}
      />
      <Color label={t("edit.cap.highlight")} value={String(c.highlight ?? "#FFD400")} onCommit={(v) => patch({ highlight: v })} />
      <Choice
        label={t("edit.cap.position")} name="cap-pos" value={(c.position as string | undefined) ?? (clean ? "bottom" : "lower")}
        options={["lower", "middle", "bottom"].map((p) => ({ value: p, label: t(`edit.cap.position.${p}`) }))}
        onChange={(v) => patch({ position: v })}
      />
      <Choice
        label={t("edit.cap.fillers")} name="cap-fill" value={c.hide_fillers !== false}
        options={onOff(t("edit.cap.fillers"))} onChange={(v) => patch({ hide_fillers: v })}
      />
    </>
  );
}

function Framing({ edl, onPatch }: Pick<InspectorProps, "edl" | "onPatch">) {
  const clip = rec(edl.clips[0]);
  const cx = clip.crop_x;
  const face = cx === "auto" || (typeof cx === "number" && Math.abs(cx - 0.5) > 1e-3);
  const z = clip.zoom;
  const zoom = typeof z === "number" ? z : typeof rec(z).to === "number" ? (rec(z).to as number) : 1;
  const fit = (edl.fit as string | undefined) ?? "crop";
  return (
    <>
      <Choice
        label={t("edit.frame.face")} name="face" value={face} options={onOff(t("edit.frame.face"))}
        onChange={(v) => onPatch("framing", { crop_x: v ? "auto" : 0.5 })}
      />
      <Slider
        label={t("edit.frame.zoom")} value={Math.min(1.3, Math.max(1, zoom))} min={1} max={1.3} step={0.01}
        format={(v) => `${v.toFixed(2)}×`} onCommit={(v) => onPatch("framing", { zoom: Math.round(v * 100) / 100 })}
      />
      <Choice
        label={t("edit.frame.fit")} name="fit" value={fit}
        options={["crop", "blur", "pad"].map((f) => ({ value: f, label: t(`edit.frame.fit.${f}`) }))}
        onChange={(v) => onPatch("framing", { fit: v })}
      />
    </>
  );
}

function Sound({ edl, onPatch }: Pick<InspectorProps, "edl" | "onPatch">) {
  const a = rec(edl.audio);
  const raw = a.music;
  const music = typeof raw === "string" ? { path: raw } : raw ? rec(raw) : null;
  const lufs = Number(a.target_lufs ?? -14);
  const file = useRef<HTMLInputElement>(null);
  const patch = (p: Rec) => onPatch("audio", p);
  const setMusic = (path: string) => patch({ music: { volume: DEFAULT_VOLUME, ...(music ?? {}), path, duck: true } });

  async function pick() {
    if (!isTauri()) return file.current?.click();
    const path = await pickAudioNative().catch(() => null);
    if (path) setMusic(path);
  }

  return (
    <>
      <Choice
        label={t("edit.sound.voice")} name="voice" value={a.voice_polish !== false}
        options={onOff(t("edit.sound.voice"))} onChange={(v) => patch({ voice_polish: v })}
      />
      <Choice
        label={t("edit.sound.noise")} name="noise" value={a.denoise === true}
        options={onOff(t("edit.sound.noise"))} onChange={(v) => patch({ denoise: v })}
      />
      <Choice
        label={t("edit.sound.loudness")} sub={t("edit.sound.loudness.value", { n: String(lufs).replace("-", "−") })} name="lufs" value={lufs}
        options={LOUDNESS.map(([v, k]) => ({ value: v, label: t(`edit.sound.loudness.${k}`) }))}
        onChange={(v) => patch({ target_lufs: v, normalize: true })}
      />
      <div className="ed-row">
        <span className="ed-label">
          {t("edit.sound.music")}
          {music && <span className="ed-file" title={String(music.path)}>{baseName(String(music.path))}</span>}
        </span>
        <span className="row">
          <button type="button" className="secondary" onClick={pick}>
            {music ? t("edit.sound.music.change") : t("edit.sound.music.add")}
          </button>
          {music && (
            <button type="button" className="secondary" onClick={() => patch({ music: null })}>{t("edit.sound.music.remove")}</button>
          )}
        </span>
        <input
          ref={file}
          type="file"
          accept={AUDIO_ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const f = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (f) setMusic(f.name); // a browser only knows the name; the mock engine takes that
          }}
        />
      </div>
      {music && (
        <>
          <Slider
            label={t("edit.sound.music.volume")} value={Number(music.volume ?? DEFAULT_VOLUME)} min={0} max={1} step={0.01}
            format={(v) => `${Math.round(v * 100)}%`} onCommit={(v) => patch({ music: { ...music, volume: Math.round(v * 100) / 100, duck: true } })}
          />
          <p className="hint">{t("edit.sound.music.duck")}</p>
        </>
      )}
    </>
  );
}

/** The right pane: Transcript, Captions, Framing and Sound tabs. Every setting is one `patch` op on the clip. */
export default function Inspector({ edl, tab, onTab, transcript, onPatch }: InspectorProps) {
  const id = useId();
  const tabs = useRef(new Map<InspectorTab, HTMLButtonElement>());

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = TABS[(TABS.indexOf(tab) + d + TABS.length) % TABS.length];
    onTab(next);
    tabs.current.get(next)?.focus();
  }

  return (
    <aside className="ed-insp" aria-label={t("edit.tabs")}>
      <div className="ed-tabs" role="tablist" aria-label={t("edit.tabs")} onKeyDown={onKey}>
        {TABS.map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            id={`${id}-${k}-tab`}
            className="ed-tab"
            aria-selected={tab === k}
            aria-controls={`${id}-${k}`}
            tabIndex={tab === k ? 0 : -1}
            ref={(el) => {
              if (el) tabs.current.set(k, el);
              else tabs.current.delete(k);
            }}
            onClick={() => onTab(k)}
          >
            {t(`edit.tab.${k}`)}
          </button>
        ))}
      </div>
      {TABS.map((k) => (
        <div
          key={k}
          role="tabpanel"
          id={`${id}-${k}`}
          aria-labelledby={`${id}-${k}-tab`}
          className={`ed-panel${k === "transcript" ? " tx-panel" : ""}`}
          hidden={tab !== k}
        >
          {k === "transcript" && transcript}
          {k === "captions" && <Captions edl={edl} onPatch={onPatch} />}
          {k === "framing" && <Framing edl={edl} onPatch={onPatch} />}
          {k === "sound" && <Sound edl={edl} onPatch={onPatch} />}
        </div>
      ))}
    </aside>
  );
}
