import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { isTauri } from "../engine/client";
import type { Aspect, Edl, EditSection } from "../engine/types";
import { OUTPUT_ASPECTS } from "../edit/timeline";
import { BrollTool, TextTool, type LayerToolProps } from "./LayerTools";
import { t } from "../i18n/t";
import { AUDIO_ACCEPT, baseName, pickAudioNative } from "../lib/native";

export type InspectorTab = "transcript" | "captions" | "text" | "broll" | "sound" | "framing" | "color" | "speed";
export const TABS: InspectorTab[] = ["transcript", "captions", "text", "broll", "sound", "framing", "color", "speed"];

/* One icon per tool, drawn on the same 24px grid and stroke as the timeline's tools. */
const TOOL_ICON: Record<InspectorTab, string> = {
  transcript: "M5 6h14M5 10h14M5 14h9M5 18h6",
  captions: "M3 6h18v12H3zM7 11h3M7 14h6M13 11h4M15 14h2",
  text: "M5 7V5h14v2M12 5v14M9 19h6",
  broll: "M4 5h16v14H4zM4 16l5-5 4 4 2-2 5 5M15 9.5h.01",
  sound: "M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2",
  framing: "M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3M9 8h6v8H9z",
  color: "M12 3a9 9 0 1 0 0 18c1.2 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.2 0-1 .8-1.8 1.8-1.8H17a4 4 0 0 0 4-4C21 6.6 17 3 12 3zM7.5 11.5h.01M10 7.5h.01M15 7.5h.01",
  speed: "M12 14l4-5M4.9 19a9 9 0 1 1 14.2 0",
};

export interface InspectorProps {
  edl: Edl;
  tab: InspectorTab;
  onTab(tab: InspectorTab): void;
  /** The Transcript tab's content. */
  transcript: ReactNode;
  onPatch(section: EditSection, patch: Record<string, unknown>): void;
  /** The Text and B-roll tools' layers, playhead and clip bounds. */
  layers: LayerToolProps;
  /** The shape the clip is saved in, also switched above the preview. */
  aspect: Aspect;
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

function Framing({ edl, onPatch, aspect }: Pick<InspectorProps, "edl" | "onPatch" | "aspect">) {
  const clip = rec(edl.clips[0]);
  const cx = clip.crop_x;
  const face = cx === "auto" || (typeof cx === "number" && Math.abs(cx - 0.5) > 1e-3);
  const z = clip.zoom;
  const zoom = typeof z === "number" ? z : typeof rec(z).to === "number" ? (rec(z).to as number) : 1;
  const fit = (edl.fit as string | undefined) ?? "crop";
  return (
    <>
      <Choice
        label={t("edit.shape")} name="shape" value={aspect}
        options={OUTPUT_ASPECTS.map((a) => ({ value: a as Aspect, label: a }))}
        onChange={(v) => onPatch("output", { aspect: v })}
      />
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

const COLOR_DEFAULT = { brightness: 0, contrast: 1, saturation: 1 };
type ColorKey = keyof typeof COLOR_DEFAULT;

/** Brightness, contrast and saturation (ffmpeg's eq filter on export, a CSS filter in the preview), sharpen and vignette. */
function ColorTool({ edl, onPatch }: Pick<InspectorProps, "edl" | "onPatch">) {
  const c = rec(edl.color);
  const num = (k: ColorKey) => (typeof c[k] === "number" ? (c[k] as number) : COLOR_DEFAULT[k]);
  const patch = (p: Rec) => onPatch("color", p);
  // a value back at its default is removed, so a reset clip has no colour settings at all
  const set = (k: ColorKey, v: number) => patch({ [k]: Math.abs(v - COLOR_DEFAULT[k]) < 1e-6 ? null : Math.round(v * 100) / 100 });
  const pct = (v: number, base: number) => `${v - base >= 0 ? "+" : "\u2212"}${Math.round(Math.abs(v - base) * 100)}`;
  const changed = ["brightness", "contrast", "saturation", "sharpen", "vignette"].some((k) => c[k] !== undefined);
  return (
    <>
      <Slider label={t("edit.color.brightness")} value={num("brightness")} min={-0.2} max={0.2} step={0.01} format={(v) => pct(v, 0)} onCommit={(v) => set("brightness", v)} />
      <Slider label={t("edit.color.contrast")} value={num("contrast")} min={0.7} max={1.4} step={0.01} format={(v) => pct(v, 1)} onCommit={(v) => set("contrast", v)} />
      <Slider label={t("edit.color.saturation")} value={num("saturation")} min={0} max={2} step={0.01} format={(v) => pct(v, 1)} onCommit={(v) => set("saturation", v)} />
      <Choice label={t("edit.color.sharpen")} name="sharpen" value={c.sharpen === true} options={onOff(t("edit.color.sharpen"))} onChange={(v) => patch({ sharpen: v || null })} />
      <Choice label={t("edit.color.vignette")} name="vignette" value={c.vignette === true} options={onOff(t("edit.color.vignette"))} onChange={(v) => patch({ vignette: v || null })} />
      <div className="ed-row">
        <span className="hint">{t("edit.color.hint")}</span>
        <button type="button" className="secondary" disabled={!changed} onClick={() => patch({ brightness: null, contrast: null, saturation: null, sharpen: null, vignette: null })}>
          {t("edit.color.reset")}
        </button>
      </div>
    </>
  );
}

const SPEEDS = [1, 1.1, 1.25, 1.5, 2];

/** How fast the clip plays. Speech keeps its pitch; captions and layers follow. */
function SpeedTool({ edl, onPatch }: Pick<InspectorProps, "edl" | "onPatch">) {
  const speed = Number(rec(edl.clips[0]).speed ?? 1);
  return (
    <>
      <Choice
        label={t("edit.speed")} name="speed" value={SPEEDS.includes(speed) ? speed : 1}
        options={SPEEDS.map((v) => ({ value: v, label: `${v}\u00d7` }))}
        onChange={(v) => onPatch("framing", { speed: v === 1 ? null : v })}
      />
      <p className="hint">{t("edit.speed.hint")}</p>
    </>
  );
}

/** The right pane: one rail with every tool (transcript, captions, text, b-roll, sound, framing, colour, speed) and
    the chosen tool's settings. Every setting is one `patch` op on the clip. */
export default function Inspector({ edl, tab, onTab, transcript, onPatch, layers, aspect }: InspectorProps) {
  const id = useId();
  const tabs = useRef(new Map<InspectorTab, HTMLButtonElement>());

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const d = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const next = TABS[(TABS.indexOf(tab) + d + TABS.length) % TABS.length];
    onTab(next);
    tabs.current.get(next)?.focus();
  }

  return (
    <aside className="ed-insp" aria-label={t("edit.tabs")}>
      <div className="ed-rail" role="tablist" aria-label={t("edit.tabs")} aria-orientation="vertical" onKeyDown={onKey}>
        {TABS.map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            id={`${id}-${k}-tab`}
            className="ed-tool"
            aria-selected={tab === k}
            aria-controls={`${id}-${k}`}
            tabIndex={tab === k ? 0 : -1}
            ref={(el) => {
              if (el) tabs.current.set(k, el);
              else tabs.current.delete(k);
            }}
            onClick={() => onTab(k)}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d={TOOL_ICON[k]} />
            </svg>
            <span>{t(`edit.tab.${k}`)}</span>
          </button>
        ))}
      </div>
      <div className="ed-panes">
        {TABS.map((k) => (
          <div
            key={k}
            role="tabpanel"
            id={`${id}-${k}`}
            aria-labelledby={`${id}-${k}-tab`}
            className={`ed-panel${k === "transcript" ? " tx-panel" : ""}`}
            hidden={tab !== k}
          >
            {k !== "transcript" && <h2 className="ed-panel-title">{t(`edit.tab.${k}`)}</h2>}
            {k === "transcript" && transcript}
            {k === "captions" && <Captions edl={edl} onPatch={onPatch} />}
            {k === "text" && <TextTool {...layers} />}
            {k === "broll" && <BrollTool {...layers} />}
            {k === "sound" && <Sound edl={edl} onPatch={onPatch} />}
            {k === "framing" && <Framing edl={edl} onPatch={onPatch} aspect={aspect} />}
            {k === "color" && <ColorTool edl={edl} onPatch={onPatch} />}
            {k === "speed" && <SpeedTool edl={edl} onPatch={onPatch} />}
          </div>
        ))}
      </div>
    </aside>
  );
}
