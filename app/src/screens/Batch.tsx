import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Screen from "../components/Screen";
import { engine, isTauri } from "../engine/client";
import { useJob } from "../engine/events";
import {
  EngineError, type Aspect, type Batch as BatchDoc, type BatchItem, type ExportResult, type Language, type Platform,
  type ProjectType,
} from "../engine/types";
import { t } from "../i18n/t";
import {
  VIDEO_ACCEPT, VIDEO_EXTENSIONS, analyzeBody, baseName, isVideoName, listenNativeDrop, pickManyNative, showInFolder,
} from "../lib/native";
import { getLanguage, getPlatform, getSettings } from "../lib/prefs";
import { PLATFORMS_FOR, defaultFormat, platformFor, projectHome } from "../lib/projectKind";
import "./story.css";

const NO_STEPS = { steps: false };

const TYPES: ProjectType[] = ["clip", "vlog", "reel"];
const LANGUAGES: Language[] = ["en", "tl", "taglish"];
const FORMATS: { value: Aspect; label: string }[] = [
  { value: "9:16", label: "export.format.916" },
  { value: "1:1", label: "export.format.11" },
  { value: "16:9", label: "export.format.169" },
];
const POLL_MS = 1500;
const MAX_VIDEOS = 50;

const message = (e: unknown, fallback: string): string => (e instanceof EngineError && e.status !== 0 ? e.message : fallback);

function Seg<T extends string>({ legend, name, keys, prefix, value, onChange }: {
  legend: string; name: string; keys: T[]; prefix: string; value: T; onChange(v: T): void;
}) {
  return (
    <fieldset className="field">
      <legend>{legend}</legend>
      <div className="seg">
        {keys.map((k) => (
          <label key={k} className="seg-opt">
            <input type="radio" name={name} value={k} checked={value === k} onChange={() => onChange(k)} />
            <span>{t(`${prefix}.${k}`)}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export default function Batch() {
  const { bid } = useParams();
  return bid ? <BatchProgress bid={bid} /> : <BatchForm />;
}

function BatchForm() {
  const navigate = useNavigate();
  const desktop = isTauri();
  const [files, setFiles] = useState<string[]>([]);
  const [kind, setKind] = useState<ProjectType>("clip");
  const [language, setLanguage] = useState<Language>(getLanguage);
  const [platform, setPlatform] = useState<Platform>(() => platformFor("clip", getPlatform()));
  const [autoExport, setAutoExport] = useState(true);
  const [formats, setFormats] = useState<ReadonlySet<Aspect>>(new Set<Aspect>(["9:16"]));
  const [over, setOver] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function add(paths: string[]) {
    const ok = paths.filter((p) => isVideoName(baseName(p)));
    const skipped = paths.length - ok.length;
    setNotice(skipped ? t(skipped === 1 ? "batch.add.skipped.one" : "batch.add.skipped.other", { n: skipped }) : null);
    setFiles((cur) => [...cur, ...ok.filter((p) => !cur.includes(p))].slice(0, MAX_VIDEOS));
  }
  const addRef = useRef(add);
  addRef.current = add;
  useEffect(() => listenNativeDrop({ onOver: setOver, onDrop: (paths) => addRef.current(paths) }), []);

  function changeKind(k: ProjectType) {
    setKind(k);
    setPlatform((p) => platformFor(k, p));
    setFormats(new Set([defaultFormat(k)]));
  }

  async function choose() {
    if (!desktop) {
      input.current?.click();
      return;
    }
    try {
      add(await pickManyNative(t("batch.add.dialog"), VIDEO_EXTENSIONS));
    } catch {
      setError(t("batch.failed"));
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (!desktop) add([...e.dataTransfer.files].map((f) => f.name));
  }

  function toggle(f: Aspect, on: boolean) {
    setFormats((cur) => {
      const next = new Set(cur);
      if (on) next.add(f);
      else next.delete(f);
      return next;
    });
  }

  async function start() {
    if (!files.length || busy) return;
    setBusy(true);
    setError(null);
    try {
      const settings = getSettings();
      const b = await engine.createBatch({
        ...(await analyzeBody()),
        sources: files, type: kind, language, platform: platformFor(kind, platform), export: autoExport,
        formats: FORMATS.map((f) => f.value).filter((f) => formats.has(f)), quality: "normal", srt: true, thumbnail: false,
        ...(settings.outputMode === "folder" ? { out_dir: settings.outputFolder } : {}),
      });
      navigate(`/batch/${b.id}`, { replace: true });
    } catch (e) {
      setError(message(e, t("batch.failed")));
    } finally {
      setBusy(false);
    }
  }

  const noFormat = autoExport && formats.size === 0;
  return (
    <Screen bar={NO_STEPS} narrow>
      <h1 className="title">{t("batch.title")}</h1>
      <div className="story">
        <section aria-labelledby="videos-h">
          <h2 id="videos-h">{t("batch.videos")}</h2>
          {files.length > 0 && (
            <ol className="batch-files">
              {files.map((f) => (
                <li key={f}>
                  <span className="sc-name mono" title={f}>{baseName(f)}</span>
                  <button type="button" className="icon-btn" aria-label={t("batch.remove", { name: baseName(f) })}
                    onClick={() => setFiles((cur) => cur.filter((x) => x !== f))}>
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" /></svg>
                  </button>
                </li>
              ))}
            </ol>
          )}
          <div
            className={`sc-drop${over ? " over" : ""}`}
            role="group"
            aria-label={t("batch.add.label")}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <span>{files.length ? t(files.length === 1 ? "batch.count.one" : "batch.count.other", { n: files.length }) : t("batch.add.empty")}</span>
            <button type="button" className="secondary" disabled={files.length >= MAX_VIDEOS} onClick={() => void choose()}>{t("batch.add")}</button>
            <input ref={input} type="file" accept={VIDEO_ACCEPT} multiple hidden onChange={(e) => {
              const picked = [...(e.target.files ?? [])].map((f) => f.name);
              e.target.value = "";
              add(picked);
            }} />
          </div>
          {notice && <p className="set-note" role="status">{notice}</p>}
        </section>

        <section aria-labelledby="make-h">
          <h2 id="make-h">{t("batch.what")}</h2>
          <div className="fields">
            <Seg legend={t("batch.type")} name="type" keys={TYPES} prefix="batch.type" value={kind} onChange={changeKind} />
            <Seg<Language> legend={t("import.language")} name="language" keys={LANGUAGES} prefix="import.language" value={language} onChange={setLanguage} />
            {kind !== "vlog" && (
              <Seg<Platform> legend={t("import.platform")} name="platform" keys={PLATFORMS_FOR[kind]} prefix="import.platform" value={platform} onChange={setPlatform} />
            )}
          </div>
        </section>

        <section aria-labelledby="export-h">
          <h2 id="export-h">{t("batch.export")}</h2>
          <label className="sc-own">
            <input type="checkbox" checked={autoExport} onChange={(e) => setAutoExport(e.target.checked)} />
            <span>{t(kind === "clip" ? "batch.export.auto.clips" : "batch.export.auto.video")}</span>
          </label>
          {autoExport && (
            <fieldset className="field batch-formats">
              <legend>{t("export.formats")}</legend>
              {FORMATS.map((f) => (
                <label key={f.value} className="sc-own">
                  <input type="checkbox" checked={formats.has(f.value)} onChange={(e) => toggle(f.value, e.target.checked)} />
                  <span>{t(f.label)}</span>
                </label>
              ))}
            </fieldset>
          )}
          <p className="set-note">{t(autoExport ? "batch.export.where" : "batch.export.later")}</p>
        </section>

        <div className="story-make">
          {error && <p className="error" role="alert">{error}</p>}
          <div className="row">
            <button type="button" className="primary" disabled={!files.length || noFormat || busy} onClick={() => void start()}>
              {busy ? t("batch.starting") : t(files.length === 1 ? "batch.start.one" : "batch.start.other", { n: files.length })}
            </button>
            {noFormat && <span className="set-note">{t("batch.export.needs")}</span>}
          </div>
        </div>
      </div>
    </Screen>
  );
}

function BatchProgress({ bid }: { bid: string }) {
  const [batch, setBatch] = useState<BatchDoc | null>(null);
  const [missing, setMissing] = useState(false);
  const [settled, setSettled] = useState<ReadonlySet<number>>(new Set());

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () => engine.getBatch(bid).then(
      (b) => {
        if (!live) return;
        setBatch(b);
        b.items.forEach((it) => {
          engine.getJob(it.analyze).catch(() => undefined);
          if (it.export) engine.getJob(it.export).catch(() => undefined);
        });
        timer = setTimeout(load, POLL_MS);
      },
      () => {
        if (live && !batch) setMissing(true);
        else if (live) timer = setTimeout(load, POLL_MS);
      },
    );
    void load();
    return () => {
      live = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bid]);

  if (missing) {
    return (
      <Screen bar={NO_STEPS} narrow>
        <h1 className="title">{t("batch.title")}</h1>
        <p className="note-text" role="alert">{t("batch.missing")}</p>
        <Link className="secondary inline" to="/">{t("story.home")}</Link>
      </Screen>
    );
  }
  if (!batch) {
    return (
      <Screen bar={NO_STEPS} narrow>
        <h1 className="title">{t("batch.title")}</h1>
        <p className="note-text">{t("story.loading")}</p>
      </Screen>
    );
  }
  const n = batch.items.length;
  return (
    <Screen bar={NO_STEPS} narrow>
      <h1 className="title">{t("batch.progress.title")}</h1>
      <p className="note-text" role="status">
        {settled.size === n ? t("batch.progress.done") : t("batch.progress.note", { done: settled.size, n })}
      </p>
      <ol className="q-list batch-rows">
        {batch.items.map((it, i) => (
          <BatchRow key={it.analyze} item={it} exporting={batch.export} onSettled={(done) => setSettled((cur) => {
            if (done === cur.has(i)) return cur;
            const next = new Set(cur);
            if (done) next.add(i);
            else next.delete(i);
            return next;
          })} />
        ))}
      </ol>
      <div className="row batch-foot">
        <Link className="secondary" to="/batch">{t("batch.again")}</Link>
        <Link className="secondary" to="/">{t("story.home")}</Link>
      </div>
    </Screen>
  );
}

function BatchRow({ item, exporting, onSettled }: { item: BatchItem; exporting: boolean; onSettled(done: boolean): void }) {
  const analyze = useJob(item.analyze);
  const exp = useJob(item.export ?? undefined);
  const [showError, setShowError] = useState(false);
  const desktop = isTauri();
  const name = baseName(item.source);

  let state: "waiting" | "analyzing" | "exporting" | "done" | "failed";
  let pct = 0;
  let error: string | null = null;
  const aState = analyze?.state ?? item.project?.last_job?.state ?? "queued";
  if (!item.project) {
    state = "failed";
    error = item.error;
  } else if (aState === "queued") state = "waiting";
  else if (aState === "running") {
    state = "analyzing";
    pct = analyze?.pct ?? 0;
  } else if (aState === "failed" || aState === "cancelled") {
    state = "failed";
    error = analyze?.error ?? (aState === "cancelled" ? t("batch.row.cancelled") : null);
  } else if (!exporting) state = "done";
  else if (item.error) {
    state = "failed";
    error = item.error;
  } else if (!item.export || !exp || exp.state === "queued") state = item.export ? "waiting" : "exporting";
  else if (exp.state === "running") {
    state = "exporting";
    pct = exp.pct;
  } else if (exp.state === "done") state = "done";
  else {
    state = "failed";
    error = exp.error ?? t(exp.state === "cancelled" ? "batch.row.cancelled" : "export.row.failed");
  }
  const settled = state === "done" || state === "failed";
  useEffect(() => onSettled(settled), [settled, onSettled]);

  const files = exporting && state === "done" ? ((exp?.result as ExportResult | null)?.renders ?? []).map((r) => r.path) : [];
  async function show(path: string) {
    setShowError(false);
    try {
      await showInFolder(path);
    } catch {
      setShowError(true);
    }
  }

  return (
    <li className={`q-row ${state === "analyzing" || state === "exporting" ? "running" : state}`}>
      <div className="q-main">
        <span className="q-title" title={item.source}>{name}</span>
        <span className="q-state">
          {state === "analyzing" || state === "exporting" ? t(`batch.row.${state}`, { pct: Math.round(pct) }) : t(`batch.row.${state}`)}
        </span>
      </div>
      {(state === "analyzing" || state === "exporting") && (
        <div className="meter" role="progressbar" aria-label={t("batch.row.progress", { name })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
          <i style={{ width: `${Math.round(pct)}%` }} />
        </div>
      )}
      {files.length > 0 && (
        <ul className="q-files">
          {files.map((path) => (
            <li key={path} className="q-file">
              <span className="q-name mono" title={path}>{baseName(path)}</span>
              <button type="button" className="secondary" disabled={!desktop} onClick={() => void show(path)}>{t("export.row.show")}</button>
            </li>
          ))}
        </ul>
      )}
      {showError && <p className="error" role="alert">{t("export.row.show.error")}</p>}
      {state === "failed" && error && <p className="error" role="alert">{error}</p>}
      {item.project && settled && (
        <Link className="secondary batch-open" to={state === "failed" ? `/p/${item.project.id}/import` : projectHome(item.project)}>{t(state === "failed" ? "batch.row.openfix" : "batch.row.open")}</Link>
      )}
    </li>
  );
}
