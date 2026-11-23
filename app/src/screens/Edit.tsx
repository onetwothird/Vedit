import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import ClipList from "../components/ClipList";
import FilmFrame from "../components/FilmFrame";
import Inspector, { type InspectorTab } from "../components/Inspector";
import Screen from "../components/Screen";
import Timeline from "../components/Timeline";
import TranscriptPanel from "../components/TranscriptPanel";
import { useClipStore } from "../edit/edlStore";
import { keptWords } from "../edit/inverse";
import { captionSpans, wordSlot } from "../edit/lanes";
import { keptRanges, OUTPUT_ASPECTS } from "../edit/timeline";
import { useUndoKeys } from "../edit/useUndoKeys";
import { engine } from "../engine/client";
import type { Aspect, EditSection, Edl, Overlay, Project, Transcript } from "../engine/types";
import { t } from "../i18n/t";
import { keptCount, sortClips, type ClipSort } from "../lib/clips";
import { coverWidth } from "../lib/filmstrip";
import { baseName } from "../lib/native";
import { formatLength } from "../lib/time";
import { useFilmstrip } from "../lib/useFilmstrip";
import { previewSrc } from "../preview/media";
import { nextKeptTime, outputLength, toOutputTime, toSourceTime, type Range } from "../preview/playback";
import Player, { type PlayerHandle } from "../preview/Player";

const SHAPES: Aspect[] = ["9:16", "1:1", "16:9"];
const SKIP = 5; // J and L, like the player's arrows
const STAGE_PAD = 32; // .ed-player .stage padding, both sides
const TRANSPORT = 84; // the player's transport row, its gap and a note line under the frame

/** Keys typed into a field, or aimed at a control that uses them itself, are not the screen's. */
function ownKeys(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el?.tagName) return false;
  if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.isContentEditable) return true;
  return !!el.closest?.("[role=tab],[role=slider],[role=radio],[role=radiogroup],[role=listbox],[role=menu]");
}

export default function Edit() {
  const { id, hid } = useParams();
  const navigate = useNavigate();
  const [project, setProject] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [sort, setSort] = useState<ClipSort>("best");
  const [keepError, setKeepError] = useState(false);
  const [tab, setTab] = useState<InspectorTab>("transcript");
  const [layer, setLayer] = useState<string | null>(null); // the selected text or b-roll, shared by the timeline and tools
  const [outT, setOutT] = useState(0);
  const [stageBox, setStageBox] = useState({ width: 480, height: 520 });
  const player = useRef<PlayerHandle>(null);
  const stage = useRef<HTMLDivElement>(null);
  const pendingKeep = useRef(0);
  const film = useFilmstrip(id);

  const view = useClipStore((s) => (s.pid === id && s.hid === hid ? s.view : null));
  const saving = useClipStore((s) => s.saving);
  const error = useClipStore((s) => s.error);
  const apply = useClipStore((s) => s.apply);
  const canUndo = useClipStore((s) => s.past.length > 0);
  const canRedo = useClipStore((s) => s.future.length > 0);
  useUndoKeys();

  const [transcriptFailed, setTranscriptFailed] = useState(false);
  const [attempt, setAttempt] = useState(0); // bumped by Try again

  useEffect(() => {
    if (!id) return;
    let live = true;
    setProject(null);
    setMissing(false);
    engine.getProject(id).then((p) => live && setProject(p), () => live && setMissing(true));
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    if (!id) return;
    let live = true;
    setTranscriptFailed(false);
    engine.transcript(id).then(
      (tr) => live && setTranscript(tr),
      () => live && setTranscriptFailed(true),
    );
    return () => {
      live = false;
    };
  }, [id, attempt]);

  useEffect(() => {
    if (!id || !hid) return;
    setOutT(0);
    setLayer(null);
    void useClipStore.getState().load(id, hid);
  }, [id, hid]);

  // the player fits the stage as the window resizes
  useEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () => setStageBox({
      width: Math.max(80, el.clientWidth - STAGE_PAD),
      height: Math.max(120, el.clientHeight - STAGE_PAD - TRANSPORT),
    });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [project !== null, view !== null]);

  const clips = project?.clips ?? [];
  const sorted = sortClips(clips, sort);
  const at = sorted.findIndex((c) => c.highlight.id === hid);
  const clip = at >= 0 ? sorted[at] : undefined;
  const h = clip?.highlight;
  const words = transcript?.words ?? [];
  // where up/down count from: set at once on a switch, so a second key press before the next render moves on from it
  const cur = useRef({ hid, rendered: hid, sorted });
  if (cur.current.rendered !== hid) cur.current = { ...cur.current, hid, rendered: hid };
  cur.current.sorted = sorted;
  const goTo = useCallback((to: string | undefined) => {
    if (!to || !id) return;
    cur.current.hid = to;
    navigate(`/p/${id}/edit/${to}`);
  }, [id, navigate]);
  const step = useCallback((d: number) => {
    const list = cur.current.sorted;
    const i = list.findIndex((c) => c.highlight.id === cur.current.hid);
    if (i >= 0) goTo(list[i + d]?.highlight.id);
  }, [goTo]);

  // where the edit is, in source time: the local port of the engine's kept ranges, against the real transcript
  const edl = view?.edl;
  const kept = useMemo(() => (edl ? keptWords(edl) : new Set<number>()), [edl]);
  const { ranges, rangesError } = useMemo<{ ranges: Range[]; rangesError?: string }>(() => {
    if (!edl || !transcript) return { ranges: [] };
    try {
      return { ranges: keptRanges(edl, transcript) };
    } catch (e) {
      return { ranges: [], rangesError: e instanceof Error ? e.message : String(e) };
    }
  }, [edl, transcript]);
  // a store error with no view is the clip failing to load; with a view it is an edit the engine refused
  const clipFailed = !!error && !view;
  const loadFailed = clipFailed || transcriptFailed;
  const retry = () => {
    if (!id || !hid) return;
    setAttempt((n) => n + 1);
    void useClipStore.getState().load(id, hid);
  };
  const srcT = ranges.length ? toSourceTime(ranges, outT) : h?.start ?? 0;
  const captions = useMemo(() => {
    if (!view) return [];
    const spans = captionSpans(view.captions, ranges);
    return view.captions.uppercase ? spans.map((c) => ({ ...c, text: c.text.toUpperCase() })) : spans;
  }, [view, ranges]);
  let now: number | null = null;
  if (h && words.length) {
    for (let i = h.w0; i <= h.w1 && i < words.length; i++) {
      const [a, b] = wordSlot(words, i, h.w1);
      if (srcT >= a && srcT < b) {
        now = kept.has(i) ? i : null;
        break;
      }
    }
  }

  const seekSource = useCallback((s: number) => {
    if (!ranges.length) return;
    const into = nextKeptTime(ranges, s);
    const out = into === null ? outputLength(ranges) : toOutputTime(ranges, into) ?? 0;
    player.current?.seekOutput(out);
  }, [ranges]);

  const onCut = useCallback((a: number, b: number) => apply({ op: "cut", w0: a, w1: b }), [apply]);
  const onRestore = useCallback((a: number, b: number) => apply({ op: "restore", w0: a, w1: b }), [apply]);
  const onFix = useCallback((wrong: string, right: string) => apply({ op: "fix_word", wrong, right }), [apply]);
  const onOverlays = useCallback((items: Overlay[]) => apply({ op: "patch", section: "overlays", patch: { items } }), [apply]);
  const onPatch = useCallback((section: EditSection, patch: Record<string, unknown>) => apply({ op: "patch", section, patch }), [apply]);
  // selecting a layer on the timeline opens its tool, so its settings are always where you look for them
  const onLayer = useCallback((lid: string | null) => {
    setLayer(lid);
    if (!lid) return;
    const o = ((useClipStore.getState().view?.edl.overlays as Overlay[] | undefined) ?? []).find((x, i) => (x.id ?? `o-${i}`) === lid);
    if (o) setTab(o.type === "text" ? "text" : "broll");
  }, []);
  const onTogglePlay = useCallback(() => player.current?.toggle(), []);
  const onPlayFrom = useCallback((w: number) => {
    if (!words[w]) return;
    seekSource(words[w].start);
    player.current?.play();
  }, [words, seekSource]);

  // a dragged edge: shrinking is a trim, growing back out restores the words it passes
  const onTrim = useCallback((first: number, last: number) => {
    if (!h) return;
    const inClip = [...kept].filter((w) => w >= h.w0 && w <= h.w1);
    if (!inClip.length) return;
    const curFirst = Math.min(...inClip), curLast = Math.max(...inClip);
    if (first < curFirst) apply({ op: "restore", w0: first, w1: curFirst - 1 });
    if (last > curLast) apply({ op: "restore", w0: curLast + 1, w1: last });
    if (first > curFirst || last < curLast) apply({ op: "trim", w_first: Math.max(first, curFirst), w_last: Math.min(last, curLast) });
  }, [apply, h, kept]);

  // J and L skip like the player's arrows; up and down switch clips
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || ownKeys(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "j") player.current?.skip(-SKIP);
      else if (k === "l") player.current?.skip(SKIP);
      else if (e.key === "ArrowDown") step(1);
      else if (e.key === "ArrowUp") step(-1);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  async function toggleKept(which: string) {
    if (!id || !project) return;
    const before = clips.find((c) => c.highlight.id === which);
    if (!before) return;
    const set = (v: boolean) =>
      setProject((p) => p && { ...p, clips: p.clips.map((c) => (c.highlight.id === which ? { ...c, kept: v } : c)) });
    setKeepError(false);
    set(!before.kept);
    pendingKeep.current++;
    try {
      const fresh = await engine.setKept(id, which, !before.kept);
      if (--pendingKeep.current === 0) setProject(fresh);
    } catch {
      pendingKeep.current--;
      set(before.kept);
      setKeepError(true);
    }
  }

  const kc = keptCount(clips);
  const bar = {
    fileName: project ? baseName(project.source) : undefined,
    saved: !!view && !saving,
    step: 3 as const,
    editHid: hid,
    primary: id ? { label: t("edit.export"), count: kc, disabled: kc === 0, onClick: () => navigate(`/p/${id}/export`) } : undefined,
  };

  if (missing || (project && !clip)) {
    return (
      <Screen bar={{ fileName: bar.fileName, step: 3 }}>
        <h1 className="title">{t("edit.title")}</h1>
        <p className="note-text" role="alert">{t("edit.missing")}</p>
        <Link className="secondary inline" to={id ? `/p/${id}/pick` : "/"}>{t("edit.back")}</Link>
      </Screen>
    );
  }

  const outputAspect = edl?.output?.aspect as Aspect | undefined;
  const aspect: Aspect = outputAspect && (OUTPUT_ASPECTS as readonly string[]).includes(outputAspect) ? outputAspect : "9:16";
  const ready = !!(project && h && view && edl);

  return (
    <Screen bar={bar} wide>
      <div className="edit">
        <section className="ed-player" aria-label={t("edit.player")}>
          <div className="pv-top">
            <h1 className="pv-title">
              {h?.title ?? t("edit.loading")}
              {ranges.length > 0 && <span className="mono"> · {formatLength(outputLength(ranges))}</span>}
            </h1>
            <div className="pv-tools">
              <div className="seg" role="radiogroup" aria-label={t("edit.shape")}>
                {SHAPES.map((s) => (
                  <label key={s} className="seg-opt">
                    <input type="radio" name="shape" checked={aspect === s} onChange={() => onPatch("output", { aspect: s })} />
                    <span>{s}</span>
                  </label>
                ))}
              </div>
              <button type="button" className="secondary" disabled={at <= 0} onClick={() => step(-1)}>{t("edit.prev")}</button>
              <button type="button" className="secondary" disabled={at < 0 || at >= sorted.length - 1} onClick={() => step(1)}>{t("edit.next")}</button>
            </div>
          </div>
          <div className="stage" ref={stage}>
            {ready && project && view && h && (
              <Player
                key={hid}
                ref={player}
                view={view}
                src={previewSrc(project)}
                aspect={aspect}
                fit={stageBox}
                onTime={setOutT}
                poster={(f) => <FilmFrame film={film} t={h.start} width={coverWidth(film, f.width, f.height)} />}
              />
            )}
          </div>
          {loadFailed && (
            <div className="ed-load-error" role="alert">
              <p className="error">{t(clipFailed ? "edit.error.load" : "edit.error.transcript")}</p>
              <button type="button" className="secondary" onClick={retry}>{t("edit.retry")}</button>
            </div>
          )}
          {error && view && <p className="error" role="alert">{t("edit.error.save", { msg: error })}</p>}
          {rangesError && <p className="error" role="alert">{t("edit.error.ranges", { msg: rangesError })}</p>}
        </section>

        {ready && edl && h ? (
          <Inspector
            edl={edl}
            tab={tab}
            onTab={setTab}
            onPatch={onPatch}
            aspect={aspect}
            layers={{
              overlays: (edl.overlays as Overlay[] | undefined) ?? [],
              onOverlays,
              layer,
              onLayer,
              playhead: srcT,
              t0: words[h.w0]?.start ?? h.start,
              t1: words[h.w1] ? wordSlot(words, h.w1, h.w1)[1] : h.end,
              onSeek: seekSource,
            }}
            transcript={
              <TranscriptPanel
                words={words}
                w0={h.w0}
                w1={h.w1}
                kept={kept}
                replace={((edl.captions ?? {}) as { replace?: Record<string, string> }).replace ?? {}}
                now={now}
                active={tab === "transcript"}
                onCut={onCut}
                onRestore={onRestore}
                onFix={onFix}
                onPlayFrom={onPlayFrom}
                onTogglePlay={onTogglePlay}
              />
            }
          />
        ) : (
          <aside className="ed-insp" aria-label={t("edit.tabs")} aria-busy="true" />
        )}

        <section className="ed-clips" aria-label={t("edit.clips")}>
          <header className="ed-head">
            <h2>{t("edit.clips")}</h2>
            <span className="meta">{t("edit.clips.kept", { k: kc, n: clips.length })}</span>
          </header>
          <ClipList clips={clips} currentId={hid} sort={sort} film={film} onSort={setSort} onSelect={goTo} onToggleKept={toggleKept} />
          {keepError && <p className="error ed-pad" role="alert">{t("edit.error.keep")}</p>}
          <footer className="ed-foot">
            {id && <Link className="secondary" to={`/p/${id}/pick`}>{t("edit.clips.more")}</Link>}
            <span className="meta">{t("edit.clips.keys")}</span>
          </footer>
        </section>

        {ready && h && words.length > 0 ? (
          <Timeline
            words={words}
            w0={h.w0}
            w1={h.w1}
            kept={kept}
            captions={captions}
            music={musicPath(edl)}
            playhead={srcT}
            onSeek={seekSource}
            onTrim={onTrim}
            onCut={onCut}
            overlays={(edl?.overlays as Overlay[] | undefined) ?? []}
            onOverlays={onOverlays}
            onUndo={() => useClipStore.getState().undo()}
            onRedo={() => useClipStore.getState().redo()}
            canUndo={canUndo}
            canRedo={canRedo}
            layer={layer}
            onLayer={onLayer}
          />
        ) : (
          <section className="ed-tl" aria-label={t("edit.tl.label")} aria-busy="true" />
        )}
      </div>
    </Screen>
  );
}

function musicPath(edl: Edl | undefined): string | null {
  const m = (edl?.audio as { music?: unknown } | undefined)?.music;
  if (typeof m === "string") return m;
  const p = (m as { path?: unknown } | null | undefined)?.path;
  return typeof p === "string" ? p : null;
}
