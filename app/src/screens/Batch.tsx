import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Screen from "../components/Screen";
import UploadList, { type UploadRow } from "../components/UploadList";
import { engine, isTauri } from "../engine/client";
import { isWeb } from "../engine/flavor";
import { useJob } from "../engine/events";
import {
  EngineError, type Aspect, type Batch as BatchDoc, type BatchItem, type ExportLink, type ExportResult, type Language,
  type Platform, type ProjectType,
} from "../engine/types";
import { t } from "../i18n/t";
import {
  VIDEO_ACCEPT, VIDEO_EXTENSIONS, acceptFor, analyzeBody, baseName, isVideoName, listenNativeDrop, pickManyNative,
  showInFolder,
} from "../lib/native";
import { UploadError, batchProblem, kindOfName, runPool, uploadFile, videoDuration } from "../lib/upload";
import { overlayUrl, rememberPicked } from "../lib/overlayMedia";
import { getLanguage, getPlatform, getSettings } from "../lib/prefs";
import { PLATFORMS_FOR, defaultFormat, platformFor, projectHome } from "../lib/projectKind";
import "./import.css";
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
const UPLOADS_AT_ONCE = 3; // the website sends a batch's videos three at a time
/** A download link lasts 24 hours; one this old is replaced before it is followed. */
const LINK_STALE_MS = 23 * 3600 * 1000;

const message = (e: unknown, fallback: string): string => (e instanceof EngineError && e.status !== 0 ? e.message : fallback);
const pad = (n: number) => String(n).padStart(2, "0");

/** A video's first second as its thumbnail; a film icon when this session can't load the file. */
function VideoThumb({ path }: { path: string }) {
  const [broken, setBroken] = useState(false);
  const url = overlayUrl(path);
  useEffect(() => setBroken(false), [url]);
  return (
    <span className="bin-thumb" aria-hidden="true">
      {url && !broken ? <video src={`${url}#t=1`} muted preload="metadata" onError={() => setBroken(true)} /> : <FilmIcon />}
    </span>
  );
}

function FilmIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="m10 9.5 4.5 2.5-4.5 2.5z" />
    </svg>
  );
}

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
  const web = isWeb();
  // the website: the chosen files by name, and the uploads (three at a time) that run before the batch is made
  const picked = useRef(new Map<string, File>());
  const [phase, setPhase] = useState<"idle" | "checking" | "uploading">("idle");
  const [rows, setRows] = useState<UploadRow[]>([]);
  const controllers = useRef(new Map<string, AbortController>());
  const dropped = useRef(new Set<string>());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const live = controllers.current;
    return () => {
      alive.current = false; // leaving the screen stops every upload, and no batch is made
      live.forEach((c) => c.abort());
    };
  }, []);
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
    const ok = paths.filter((p) => (web ? kindOfName(baseName(p)) === "video" : isVideoName(baseName(p))));
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

  /** A file chosen in the browser: remembered for the preview (and, on the website, for the upload). */
  function keep(f: File): string {
    const name = rememberPicked(f);
    picked.current.set(name, f);
    return name;
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (web && busy) return; // the list is fixed while its videos upload
    if (!desktop) add([...e.dataTransfer.files].map(keep));
  }

  function toggle(f: Aspect, on: boolean) {
    setFormats((cur) => {
      const next = new Set(cur);
      if (on) next.add(f);
      else next.delete(f);
      return next;
    });
  }

  /** The website: check every video against the limits, then upload them three at a time, then make the batch. */
  async function startWeb() {
    if (!files.length || busy) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    dropped.current = new Set();
    try {
      setPhase("checking");
      const chosen = files.map((name) => ({ name, file: picked.current.get(name)! })).filter((c) => c.file);
      const lengths = await Promise.all(chosen.map((c) => videoDuration(c.file).catch(() => null)));
      const usage = await engine.usage();
      if (!alive.current) return;
      const problem = batchProblem(chosen.map((c, i) => ({ name: c.name, duration: lengths[i], size: c.file.size })), usage);
      if (problem) {
        setError(problem);
        return;
      }
      setPhase("uploading");
      setRows(chosen.map((c) => ({ id: c.name, name: c.name, pct: null })));
      const setPct = (id: string, pct: number) => setRows((cur) => cur.map((r) => (r.id === id ? { ...r, pct } : r)));
      const keys = new Map<string, string>();
      let failure: string | null = null;
      await runPool(chosen, UPLOADS_AT_ONCE, async (c) => {
        if (!alive.current || failure || dropped.current.has(c.name)) return;
        const ctl = new AbortController();
        controllers.current.set(c.name, ctl);
        try {
          setPct(c.name, 0);
          const { upload_key } = await uploadFile(c.file, "video", (pct) => setPct(c.name, pct), ctl.signal);
          keys.set(c.name, upload_key);
        } catch (e) {
          if (!(e instanceof UploadError && e.code === "aborted")) failure ??= `${c.name}: ${e instanceof Error ? e.message : t("upload.failed")}`;
        } finally {
          controllers.current.delete(c.name);
        }
      });
      if (!alive.current) return;
      if (failure) {
        setError(failure);
        return;
      }
      const upload_keys = chosen.filter((c) => keys.has(c.name)).map((c) => keys.get(c.name)!);
      if (!upload_keys.length) {
        setError(t("batch.web.nothing"));
        return;
      }
      const b = await engine.createBatch({
        ...(await analyzeBody()),
        upload_keys, type: kind, language, platform: platformFor(kind, platform), export: autoExport,
        formats: FORMATS.map((f) => f.value).filter((f) => formats.has(f)), quality: "normal", srt: true, thumbnail: false,
      });
      if (alive.current) navigate(`/batch/${b.id}`, { replace: true });
    } catch (e) {
      if (alive.current) setError(e instanceof UploadError ? e.message : message(e, t("batch.failed")));
    } finally {
      if (alive.current) {
        setBusy(false);
        setPhase("idle");
        setRows([]);
      }
    }
  }

  /** Stops one upload; the video is left out of the batch and the next one takes its place. */
  function cancelUpload(name: string) {
    dropped.current.add(name);
    controllers.current.get(name)?.abort();
    setRows((cur) => cur.filter((r) => r.id !== name));
    setFiles((cur) => cur.filter((f) => f !== name));
    setNotice(t("batch.web.skipped", { name }));
  }

  async function start() {
    if (web) return startWeb();
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
  const full = files.length >= MAX_VIDEOS;
  return (
    <Screen bar={NO_STEPS}>
      <div className="imp st">
        <h1 className="title imp-title">{t("batch.title")}</h1>
        <div className="imp-grid">
          <div className="imp-monitor">
            <div
              className={`viewer bin${over ? " over" : ""}${files.length ? " has-file" : ""}`}
              role="group"
              aria-label={t("batch.add.label")}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={onDrop}
            >
              <span className="vf-marks" aria-hidden="true"><i /><i /><i /><i /></span>
              {files.length ? (
                <ol className="bin-grid" aria-labelledby="videos-h">
                  {files.map((f, i) => (
                    <li key={f} className="bin-tile">
                      <VideoThumb path={f} />
                      <span className="bin-num" aria-hidden="true">{pad(i + 1)}</span>
                      <button type="button" className="bin-remove" aria-label={t("batch.remove", { name: baseName(f) })} disabled={busy}
                        onClick={() => setFiles((cur) => cur.filter((x) => x !== f))}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M7 7l10 10M17 7 7 17" /></svg>
                      </button>
                      <span className="bin-name mono" title={f}>{baseName(f)}</span>
                    </li>
                  ))}
                  {!full && !busy && (
                    <li className="bin-tile">
                      <button type="button" className="bin-add" onClick={() => void choose()}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
                        <span>{t("batch.add")}</span>
                      </button>
                    </li>
                  )}
                </ol>
              ) : (
                <div className="vf-empty">
                  <span className="vf-empty-title">{t("batch.drop.title")}</span>
                  <span className="vf-empty-hint">{t(web ? "batch.add.empty.web" : "batch.add.empty")}</span>
                  <button type="button" className="vf-choose" onClick={() => void choose()}>{t("batch.add")}</button>
                </div>
              )}
              <div className="vf-bar">
                <span className="vf-tc mono">{pad(files.length)} / {MAX_VIDEOS}</span>
                <span className="vf-file">
                  <span id="videos-h" className={files.length ? undefined : "vf-file-empty"}>
                    {files.length ? t(files.length === 1 ? "batch.count.one" : "batch.count.other", { n: files.length }) : t("batch.viewer.empty")}
                  </span>
                </span>
              </div>
              <input ref={input} type="file" accept={web ? acceptFor(VIDEO_EXTENSIONS) : VIDEO_ACCEPT} multiple hidden onChange={(e) => {
                const chosen = [...(e.target.files ?? [])].map(keep);
                e.target.value = "";
                add(chosen);
              }} />
            </div>
            {phase === "uploading" && (
              <p className="set-note" aria-live="polite">
                {t("batch.web.uploading", { done: Math.max(0, files.length - rows.filter((r) => (r.pct ?? 0) < 100).length), n: files.length })}
              </p>
            )}
            <UploadList rows={rows} onCancel={cancelUpload} label={t("batch.videos")} />
            {notice && <p className="set-note" role="status">{notice}</p>}
          </div>

          <aside className="imp-slate" aria-label={t("batch.what")}>
            <div className="imp-fields">
              <Seg legend={t("batch.type")} name="type" keys={TYPES} prefix="batch.type" value={kind} onChange={changeKind} />
              <Seg<Language> legend={t("import.language")} name="language" keys={LANGUAGES} prefix="import.language" value={language} onChange={setLanguage} />
              {kind !== "vlog" && (
                <Seg<Platform> legend={t("import.platform")} name="platform" keys={PLATFORMS_FOR[kind]} prefix="import.platform" value={platform} onChange={setPlatform} />
              )}
            </div>

            <fieldset className="field st-auto">
              <legend>{t("batch.export")}</legend>
              <label className="st-switch">
                <input type="checkbox" checked={autoExport} onChange={(e) => setAutoExport(e.target.checked)} />
                <span>{t(kind === "clip" ? "batch.export.auto.clips" : "batch.export.auto.video")}</span>
              </label>
              {autoExport && (
                <fieldset className="field batch-formats">
                  <legend className="sr-only">{t("export.formats")}</legend>
                  {FORMATS.map((f) => (
                    <label key={f.value} className="fmt-chip">
                      <input type="checkbox" checked={formats.has(f.value)} onChange={(e) => toggle(f.value, e.target.checked)} />
                      <span>{t(f.label)}</span>
                    </label>
                  ))}
                </fieldset>
              )}
              <p className="set-note">{t(autoExport ? (web ? "batch.export.where.web" : "batch.export.where") : "batch.export.later")}</p>
            </fieldset>

            {error && <p className="error" role="alert">{error}</p>}
            <button type="button" className="primary imp-start" disabled={!files.length || noFormat || busy} onClick={() => void start()}>
              {phase === "checking" ? t("batch.web.checking") : busy ? t("batch.starting") : t(files.length === 1 ? "batch.start.one" : "batch.start.other", { n: files.length })}
            </button>
            <p className={`imp-start-note${noFormat ? " warn" : ""}`}>
              {t(noFormat ? "batch.export.needs" : files.length ? "batch.start.note" : "batch.start.needs")}
            </p>
          </aside>
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
          if (it.analyze) engine.getJob(it.analyze).catch(() => undefined);
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
  const done = settled.size === n;
  return (
    <Screen bar={NO_STEPS}>
      <div className="imp st">
        <h1 className="title imp-title">{t("batch.progress.title")}</h1>
        <div className="imp-grid">
          <div className="imp-monitor">
            <ol className="q-list batch-rows bq">
              {batch.items.map((it, i) => (
                <BatchRow key={it.analyze ?? `item-${i}`} index={i} item={it} exporting={batch.export} onSettled={(fin) => setSettled((cur) => {
                  if (fin === cur.has(i)) return cur;
                  const next = new Set(cur);
                  if (fin) next.add(i);
                  else next.delete(i);
                  return next;
                })} />
              ))}
            </ol>
          </div>
          <aside className="imp-slate">
            <div className={`bq-tally${done ? " done" : ""}`} aria-hidden="true">
              <span className="bq-num">{pad(settled.size)}<span className="bq-of"> / {pad(n)}</span></span>
              <span className="bq-label">{t("batch.tally")}</span>
              <span className="bq-bar"><i style={{ width: `${n ? (settled.size / n) * 100 : 0}%` }} /></span>
            </div>
            <p className="note-text" role="status">
              {done ? t("batch.progress.done") : t("batch.progress.note", { done: settled.size, n })}
            </p>
            {isWeb() && done && batch.export && <ZipAll bid={batch.id} />}
            <div className="bq-links">
              <Link className="secondary" to="/batch">{t("batch.again")}</Link>
              <Link className="secondary" to="/">{t("story.home")}</Link>
            </div>
          </aside>
        </div>
      </div>
    </Screen>
  );
}

function BatchRow({ index, item, exporting, onSettled }: { index: number; item: BatchItem; exporting: boolean; onSettled(done: boolean): void }) {
  const analyze = useJob(item.analyze ?? undefined);
  const web = isWeb();
  const [fresh, setFresh] = useState<ExportLink[] | null>(null);
  const [linkError, setLinkError] = useState(false);
  const issued = useRef(0); // when the shown links were signed; 0 until fresh ones are fetched
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

  const files = exporting && state === "done" && !web ? ((exp?.result as ExportResult | null)?.renders ?? []).map((r) => r.path) : [];
  const links = exporting && state === "done" && web
    ? ((exp?.result as ExportResult | null)?.files ?? []).filter((f): f is ExportLink => typeof f === "object").map((l) => fresh?.find((f) => f.name === l.name) ?? l)
    : [];

  // the website: links stored in a job result were signed when the job finished, maybe days ago. Once the row is done,
  // ask for fresh ones and count their age from then; until they arrive the stored ones count as stale (issued 0).
  const pid = item.project?.id;
  const finished = web && exporting && state === "done" && !!pid;
  useEffect(() => {
    if (!finished || !pid) return;
    let live = true;
    engine.projectExports(pid).then((again) => {
      if (!live) return;
      issued.current = Date.now();
      setFresh(again);
    }, () => undefined);
    return () => {
      live = false;
    };
  }, [finished, pid]);

  async function follow(e: React.MouseEvent<HTMLAnchorElement>, link: ExportLink) {
    setLinkError(false);
    if (Date.now() - issued.current < LINK_STALE_MS || !item.project) return; // still good: the browser follows it
    e.preventDefault();
    try {
      const again = await engine.projectExports(item.project.id);
      const next = again.find((l) => l.name === link.name);
      if (!next) throw new Error("gone");
      issued.current = Date.now();
      setFresh(again);
      const a = document.createElement("a"); // follow the new link for the person
      a.href = next.url;
      a.download = next.name;
      a.click();
    } catch {
      setLinkError(true);
    }
  }
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
      <span className="bq-thumb">
        <VideoThumb path={item.source} />
        <span className="bin-num" aria-hidden="true">{pad(index + 1)}</span>
      </span>
      <div className="bq-body">
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
      {links.length > 0 && (
        <ul className="q-files">
          {links.map((link) => (
            <li key={link.name} className="q-file">
              <span className="q-name mono" title={link.name}>{link.name}</span>
              <a className="secondary inline" href={link.url} download={link.name} aria-label={t("export.download.label", { name: link.name })} onClick={(e) => void follow(e, link)}>
                {t("export.download")}
              </a>
            </li>
          ))}
        </ul>
      )}
      {linkError && <p className="error" role="alert">{t("export.download.error")}</p>}
      {showError && <p className="error" role="alert">{t("export.row.show.error")}</p>}
      {state === "failed" && error && <p className="error" role="alert">{error}</p>}
      {item.project && settled && (
        <Link className="secondary batch-open" to={state === "failed" ? `/p/${item.project.id}/import` : projectHome(item.project)}>{t(state === "failed" ? "batch.row.openfix" : "batch.row.open")}</Link>
      )}
      </div>
    </li>
  );
}

/** The website: every exported file of the batch in one zip, made on request; its link works for 24 hours. */
function ZipAll({ bid }: { bid: string }) {
  const [jobId, setJobId] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const job = useJob(jobId);
  const issued = useRef(0);
  const link = job?.state === "done" ? ((job.result as { files?: ExportLink[] } | null)?.files ?? [])[0] : undefined;
  const making = starting || job?.state === "queued" || job?.state === "running";
  useEffect(() => {
    if (link) issued.current = Date.now();
  }, [link]);

  async function make() {
    setError(null);
    setStarting(true);
    try {
      setJobId((await engine.zipBatch(bid)).id);
    } catch (e) {
      setError(message(e, t("batch.zip.failed")));
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="bq-zip">
      {link ? (
        <a className="primary inline" href={link.url} download={link.name} onClick={(e) => {
          if (Date.now() - issued.current < LINK_STALE_MS) return;
          e.preventDefault(); // the zip is gone after a day: make it again
          void make();
        }}>{t("batch.zip.ready")}</a>
      ) : (
        <button type="button" className="secondary" disabled={making} onClick={() => void make()}>
          {making ? t("batch.zip.making") : t("batch.zip")}
        </button>
      )}
      <p className="set-note" aria-live="polite">{link ? t("batch.zip.note") : ""}</p>
      {job?.state === "failed" && <p className="error" role="alert">{job.error || t("batch.zip.failed")}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
