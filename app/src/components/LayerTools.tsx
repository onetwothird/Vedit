import { useEffect, useId, useRef, useState } from "react";
import { isTauri } from "../engine/client";
import { isWeb } from "../engine/flavor";
import type { BrollSuggestions, Overlay } from "../engine/types";
import { duplicateLayer, newBroll, newText, withIds } from "../edit/overlays";
import { t } from "../i18n/t";
import { baseName, pickFileNative } from "../lib/native";
import { BROLL_IMAGE, BROLL_VIDEO, rememberPicked } from "../lib/overlayMedia";
import { formatClock } from "../lib/time";
import { BrollSuggest } from "./BrollSuggest";

/* The Text and B-roll tools: add a layer at the playhead, see every layer on the clip, and change the selected one.
   The timeline shows the same layers; selecting one there opens it here. */

export interface LayerToolProps {
  overlays: readonly Overlay[];
  onOverlays(items: Overlay[]): void;
  layer: string | null;
  onLayer(id: string | null): void;
  /** Source seconds: where a new layer goes, and the clip it stays inside. */
  playhead: number;
  t0: number;
  t1: number;
  onSeek(srcT: number): void;
  /** Suggests b-roll for this clip from a folder of photos and videos (the website: from `assets`, the files the user
      uploaded); without it the B-roll tool offers none. */
  suggestBroll?(folder: string, assets?: string[]): Promise<BrollSuggestions>;
}

const TEXT_POS = ["top", "center", "bottom"] as const;
const SIZES = [[0.8, "s"], [1, "m"], [1.4, "l"]] as const;
const PIP_POS = ["top-left", "top-right", "bottom-left", "bottom-right"] as const;

const Icon = ({ d }: { d: string }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d={d} />
  </svg>
);
const PLUS = "M12 5v14M5 12h14";

function Seg<T extends string | number>({ label, value, options, onChange }: {
  label: string; value: T; options: readonly (readonly [T, string])[]; onChange(v: T): void;
}) {
  const id = useId();
  return (
    <div className="ed-row">
      <span className="ed-label" id={id}>{label}</span>
      <div className="seg" role="radiogroup" aria-labelledby={id}>
        {options.map(([v, l]) => (
          <label key={String(v)} className="seg-opt">
            <input type="radio" name={id} checked={value === v} onChange={() => onChange(v)} />
            <span>{l}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function useLayers({ overlays, onOverlays, layer, onLayer, t0, t1 }: LayerToolProps, kind: "text" | "broll") {
  const items = withIds(overlays);
  const mine = items.filter((o) => (kind === "text" ? o.type === "text" : o.type !== "text")).sort((a, b) => a.at.t - b.at.t);
  const selected = mine.find((o) => o.id === layer) ?? null;
  return {
    mine,
    selected,
    add(o: Overlay) {
      onOverlays([...items, o]);
      onLayer(o.id!);
    },
    change(next: Overlay) {
      onOverlays(items.map((o) => (o.id === next.id ? next : o)));
    },
    duplicate(o: Overlay) {
      const copy = duplicateLayer(o, t0, t1);
      onOverlays([...items, copy]);
      onLayer(copy.id!);
    },
    remove(o: Overlay) {
      onOverlays(items.filter((x) => x.id !== o.id));
      onLayer(null);
    },
  };
}

/** The clip's layers of one kind, in time order; picking one selects it and moves the playhead there. */
function LayerList({ items, selected, t0, name, onPick }: {
  items: Overlay[]; selected: Overlay | null; t0: number; name(o: Overlay): string; onPick(o: Overlay): void;
}) {
  if (!items.length) return null;
  return (
    <ul className="ly-list" aria-label={t("edit.ly.list")}>
      {items.map((o) => (
        <li key={o.id}>
          <button type="button" className={`ly-item ${o.type}`} aria-pressed={o.id === selected?.id} onClick={() => onPick(o)}>
            <span className="ly-name">{name(o)}</span>
            <span className="ly-when mono">{t("edit.ly.when", { at: formatClock(Math.max(0, o.at.t - t0)), len: o.duration.toFixed(1) })}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function LayerActions({ o, onDuplicate, onDelete }: { o: Overlay; onDuplicate(): void; onDelete(): void }) {
  return (
    <div className="ly-actions">
      <span className="hint">{t("edit.ly.len", { n: o.duration.toFixed(1) })}</span>
      <span className="row">
        <button type="button" className="secondary" onClick={onDuplicate}>{t("edit.ly.duplicate")}</button>
        <button type="button" className="secondary" onClick={onDelete}>{t("edit.ly.delete")}</button>
      </span>
    </div>
  );
}

export function TextTool(props: LayerToolProps) {
  const { mine, selected, add, change, duplicate, remove } = useLayers(props, "text");
  const { playhead, t0, t1, onLayer, onSeek } = props;
  return (
    <>
      <button type="button" className="ly-add" onClick={() => add(newText(t("edit.tl.text.default"), playhead, t0, t1))}>
        <Icon d={PLUS} />
        {t("edit.ly.text.add")}
      </button>
      {!mine.length && <p className="hint">{t("edit.ly.text.empty")}</p>}
      <LayerList items={mine} selected={selected} t0={t0} name={(o) => (o.type === "text" ? o.text : "")} onPick={(o) => { onLayer(o.id!); onSeek(o.at.t); }} />
      {selected?.type === "text" && (
        <div className="ly-edit" role="group" aria-label={t("edit.ly.selected")}>
          <TextField o={selected} onChange={change} />
          <Seg label={t("edit.tl.text.pos")} value={selected.position} options={TEXT_POS.map((p) => [p, t(`edit.tl.text.pos.${p}`)] as const)} onChange={(v) => change({ ...selected, position: v })} />
          <Seg
            label={t("edit.tl.text.size")} value={(selected.size ?? 1) as 0.8 | 1 | 1.4}
            options={SIZES.map(([v, k]) => [v, t(`edit.tl.text.size.${k}`)] as const)} onChange={(v) => change({ ...selected, size: v })}
          />
          <LayerActions o={selected} onDuplicate={() => duplicate(selected)} onDelete={() => remove(selected)} />
        </div>
      )}
    </>
  );
}

/** Saved when you leave the field or press Enter, so typing is one undo step. A new layer opens with its
    placeholder selected, ready to type over. */
function TextField({ o, onChange }: { o: Extract<Overlay, { type: "text" }>; onChange(o: Overlay): void }) {
  const id = useId();
  const [text, setText] = useState(o.text);
  const field = useRef<HTMLTextAreaElement>(null);
  useEffect(() => setText(o.text), [o.text]);
  useEffect(() => {
    if (o.text === t("edit.tl.text.default")) field.current?.select();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [o.id]);
  const commit = () => {
    const v = text.trim().slice(0, 200);
    if (v && v !== o.text) onChange({ ...o, text: v });
    else setText(o.text);
  };
  return (
    <div className="ly-field">
      <label className="ed-label" htmlFor={id}>{t("edit.tl.text.field")}</label>
      <textarea
        id={id}
        ref={field}
        rows={2}
        value={text}
        maxLength={200}
        onChange={(e) => setText(e.currentTarget.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(); e.currentTarget.blur(); }
          if (e.key === "Escape") { setText(o.text); e.currentTarget.blur(); }
        }}
      />
    </div>
  );
}

export function BrollTool(props: LayerToolProps) {
  const { mine, selected, add, change, duplicate, remove } = useLayers(props, "broll");
  const { playhead, t0, t1, onLayer, onSeek, onOverlays, overlays, suggestBroll } = props;
  const file = useRef<HTMLInputElement>(null);
  const addPath = (path: string) => add(newBroll(path, playhead, t0, t1));
  const web = isWeb();
  const [sending, setSending] = useState<number | null>(null); // the website: how far the upload has got
  const [sendError, setSendError] = useState<string | null>(null);
  async function pick() {
    if (web) {
      if (sending !== null) return;
      setSendError(null);
      try {
        const ref = await pickFileNative(t("edit.ly.broll.add"), [...BROLL_IMAGE, ...BROLL_VIDEO], (p) => setSending(p.pct));
        if (ref) addPath(ref);
      } catch (e) {
        setSendError(e instanceof Error ? e.message : t("upload.failed"));
      } finally {
        setSending(null);
      }
      return;
    }
    if (!isTauri()) return file.current?.click();
    const path = await pickFileNative(t("edit.ly.broll.add"), [...BROLL_IMAGE, ...BROLL_VIDEO]).catch(() => null);
    if (path) addPath(path);
  }
  return (
    <>
      <button type="button" className="ly-add" onClick={pick} disabled={sending !== null}>
        <Icon d={PLUS} />
        {sending !== null ? t("edit.ly.broll.uploading", { pct: sending }) : t("edit.ly.broll.add")}
      </button>
      {sendError && <p className="error" role="alert">{sendError}</p>}
      {!web && (
        <input
          ref={file}
          type="file"
          accept="image/*,video/*"
          className="sr-only"
          tabIndex={-1}
          aria-hidden="true"
          onChange={(e) => {
            const f = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (f) addPath(rememberPicked(f));
          }}
        />
      )}
      {!mine.length && <p className="hint">{t("edit.ly.broll.empty")}</p>}
      <LayerList items={mine} selected={selected} t0={t0} name={(o) => (o.type === "text" ? "" : baseName(o.path))} onPick={(o) => { onLayer(o.id!); onSeek(o.at.t); }} />
      {selected && selected.type !== "text" && (
        <div className="ly-edit" role="group" aria-label={t("edit.ly.selected")}>
          <Seg
            label={t("edit.tl.broll.mode")} value={selected.mode}
            options={[["full", t("edit.tl.broll.full")], ["pip", t("edit.tl.broll.pip")]] as const}
            onChange={(v) => change({ ...selected, mode: v, ...(v === "pip" ? { position: selected.position ?? "top-right" } : {}) })}
          />
          {selected.mode === "pip" && (
            <Seg
              label={t("edit.tl.broll.corner")} value={(selected.position ?? "top-right") as (typeof PIP_POS)[number]}
              options={PIP_POS.map((p) => [p, t(`edit.tl.broll.corner.${p}`)] as const)} onChange={(v) => change({ ...selected, position: v })}
            />
          )}
          <LayerActions o={selected} onDuplicate={() => duplicate(selected)} onDelete={() => remove(selected)} />
        </div>
      )}
      {suggestBroll && (
        <BrollSuggest suggest={suggestBroll} t0={t0} t1={t1} onAdd={(items) => onOverlays([...withIds(overlays), ...items])} />
      )}
    </>
  );
}
