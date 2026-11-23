import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router-dom";
import Screen from "../components/Screen";
import { engine, isTauri } from "../engine/client";
import { useJob, useJobs } from "../engine/events";
import type { Aspect, ExportBody, ExportResult, Project, ProjectClip } from "../engine/types";
import { t } from "../i18n/t";
import { sortClips } from "../lib/clips";
import { baseName, pickFolderNative, showInFolder } from "../lib/native";
import { defaultOutputFolder } from "../lib/outputFolder";
import { getSettings } from "../lib/prefs";

type Quality = "normal" | "high";

const ACTIVE = ["queued", "running"];

const FORMATS: { value: Aspect; label: string }[] = [
  { value: "9:16", label: "export.format.916" },
  { value: "1:1", label: "export.format.11" },
  { value: "16:9", label: "export.format.169" },
];

const plural = (key: string, n: number, vars: Record<string, string | number> = {}) =>
  t(`${key}.${n === 1 ? "one" : "other"}`, { n, ...vars });

/** One export job per clip: its own progress, its own failure, its own retry. */
interface Row {
  hid: string;
  title: string;
  number: number; // the "NN" of its file name, kept on a retry
  jobId?: string;
  startError?: boolean;
}

function Check({ checked, onChange, children }: { checked: boolean; onChange(v: boolean): void; children: ReactNode }) {
  return (
    <label className="opt">
      <span className="keep">
        <input type="checkbox" className="sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="box" aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12.5l4.5 4.5L19 7.5" />
          </svg>
        </span>
      </span>
      <span className="opt-text">{children}</span>
    </label>
  );
}

function QueueRow({ row, desktop, busy, onRetry }: { row: Row; desktop: boolean; busy: boolean; onRetry(row: Row): void }) {
  const job = useJob(row.jobId);
  const nameId = useId();
  const [showError, setShowError] = useState(false);
  const state = row.startError ? "failed" : job?.state ?? "queued";
  const pct = Math.round(job?.pct ?? 0);
  const result = job?.result as ExportResult | null | undefined;
  const files = (result?.renders ?? []).map((r) => r.path);
  const warnedThumb = !!result?.warnings?.some((w) => w.kind === "thumbnail");

  async function show(path: string) {
    setShowError(false);
    try {
      await showInFolder(path);
    } catch {
      setShowError(true);
    }
  }

  return (
    <li className={`q-row ${state}`}>
      <div className="q-main">
        <span className="q-title">{row.title}</span>
        {state === "queued" && <span className="q-state">{t("export.row.waiting")}</span>}
        {state === "running" && <span className="q-state mono">{t("export.row.pct", { pct })}</span>}
        {state === "cancelled" && <span className="q-state">{t("export.row.cancelled")}</span>}
      </div>
      {state === "running" && (
        <div className="meter" role="progressbar" aria-label={t("export.row.progress", { title: row.title })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <i style={{ width: `${pct}%` }} />
        </div>
      )}
      {state === "done" && (
        <ul className="q-files">
          {files.map((path, i) => (
            <li key={path} className="q-file">
              <span className="q-name mono" id={`${nameId}-${i}`} title={path}>{baseName(path)}</span>
              <button type="button" className="secondary" disabled={!desktop} aria-describedby={`${nameId}-${i}`} onClick={() => void show(path)}>
                {t("export.row.show")}
              </button>
            </li>
          ))}
        </ul>
      )}
      {state === "done" && warnedThumb && <p className="set-note">{t("export.row.thumbwarn")}</p>}
      {state === "done" && showError && <p className="error" role="alert">{t("export.row.show.error")}</p>}
      {(state === "failed" || state === "cancelled") && (
        <div className="q-fail">
          {state === "failed" && <p className="error" role="alert">{(!row.startError && job?.error) || t(row.startError ? "export.error.start" : "export.row.failed")}</p>}
          <button type="button" className="secondary" disabled={busy} onClick={() => onRetry(row)}>{t("export.row.retry")}</button>
        </div>
      )}
    </li>
  );
}

export default function Export() {
  const { id } = useParams();
  const desktop = isTauri();
  const [project, setProject] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const [off, setOff] = useState<ReadonlySet<string>>(new Set()); // kept clips switched off for this export
  const [formats, setFormats] = useState<ReadonlySet<Aspect>>(new Set<Aspect>(["9:16"]));
  const [quality, setQuality] = useState<Quality>("normal");
  const [srt, setSrt] = useState(true);
  const [thumb, setThumb] = useState(true);
  const [override, setOverride] = useState<string | null>(null); // "Change folder": this export only
  const [rows, setRows] = useState<Row[] | null>(null);
  const [starting, setStarting] = useState(false);
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set()); // rows whose retry is being submitted
  const jobs = useJobs();
  const live = useRef(true);
  const cancelled = useRef(false);
  const retrying = useRef(new Set<string>());

  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    setProject(null);
    setMissing(false);
    engine.getProject(id).then(
      (p) => {
        if (!alive) return;
        setProject(p);
        const last = p.last_job;
        // coming back to a running export: pull its state in so it is shown (and Export waits for it)
        if (last && last.kind === "export" && ACTIVE.includes(last.state)) engine.getJob(last.id).catch(() => undefined);
      },
      () => alive && setMissing(true),
    );
    return () => {
      alive = false;
    };
  }, [id]);

  // exports of this project that this visit did not start: Export waits for them (or they can be cancelled)
  const own = new Set((rows ?? []).map((r) => r.jobId));
  const oldJobs = [...jobs.values()].filter((j) => j.project_id === id && j.kind === "export" && ACTIVE.includes(j.state) && !own.has(j.id));
  const blocked = oldJobs.length > 0;

  const kept: ProjectClip[] = sortClips(project?.clips.filter((c) => c.kept) ?? [], "order");
  const chosen = kept.filter((c) => !off.has(c.highlight.id));
  const settings = getSettings();
  // an explicit folder is sent to the engine; "next to the video" is the engine's own default
  const sendFolder = override ?? (settings.outputMode === "folder" ? settings.outputFolder : "");
  const shownFolder = sendFolder || (project ? defaultOutputFolder(project.source) : "");
  const bar = { fileName: project ? baseName(project.source) : undefined, step: 4 as const };

  function bodyFor(row: Row): ExportBody {
    return {
      clips: [row.hid],
      formats: FORMATS.map((f) => f.value).filter((f) => formats.has(f)),
      quality,
      srt,
      thumbnail: thumb,
      first_number: row.number,
      ...(sendFolder ? { out_dir: sendFolder } : {}),
    };
  }

  async function launch(row: Row): Promise<Row> {
    if (!id) return row;
    try {
      const job = await engine.exportClips(id, bodyFor(row));
      return { ...row, jobId: job.id, startError: false };
    } catch {
      return { ...row, jobId: undefined, startError: true };
    }
  }

  async function start() {
    if (!id || starting || blocked || !chosen.length || !formats.size) return;
    setStarting(true);
    cancelled.current = false;
    setRows([]);
    // the engine runs the jobs one after another, so every clip is queued at once
    for (const [i, c] of chosen.entries()) {
      if (!live.current || cancelled.current) break;
      const row = await launch({ hid: c.highlight.id, title: c.highlight.title, number: i + 1 });
      if (row.jobId && (cancelled.current || !live.current)) void engine.cancelJob(row.jobId).catch(() => undefined);
      if (!live.current) return;
      setRows((list) => (list ? [...list, row] : [row]));
    }
    if (live.current) setStarting(false);
  }

  async function retry(row: Row) {
    if (retrying.current.has(row.hid)) return; // a double click must not start a second job for this clip
    retrying.current.add(row.hid);
    setBusy(new Set(retrying.current));
    try {
      const next = await launch(row);
      if (live.current) setRows((list) => list && list.map((r) => (r.hid === row.hid ? next : r)));
    } finally {
      retrying.current.delete(row.hid);
      if (live.current) setBusy(new Set(retrying.current));
    }
  }

  /** Stops the clips still waiting or rendering; the ones already finished stay. */
  async function cancel() {
    cancelled.current = true;
    const ids = (rows ?? []).map((r) => r.jobId).concat(oldJobs.map((j) => j.id));
    await Promise.all(ids.filter((j): j is string => !!j && ACTIVE.includes(jobs.get(j)?.state ?? "")).map((j) => engine.cancelJob(j).catch(() => undefined)));
  }

  async function changeFolder() {
    try {
      const path = await pickFolderNative();
      if (path) setOverride(path);
    } catch {
      // the dialog failed to open: the folder stays as it was
    }
  }

  if (missing) {
    return (
      <Screen bar={{ step: 4 }} narrow>
        <h1 className="title">{t("export.title")}</h1>
        <p className="note-text" role="alert">{t("export.missing")}</p>
        <Link className="secondary inline" to="/">{t("export.home")}</Link>
      </Screen>
    );
  }
  if (!project) {
    return (
      <Screen bar={{ step: 4 }} narrow>
        <h1 className="title">{t("export.title")}</h1>
      </Screen>
    );
  }
  if (!kept.length && !rows) {
    return (
      <Screen bar={bar} narrow>
        <h1 className="title">{t("export.title")}</h1>
        <p className="note-text" role="status">{t("export.none")}</p>
        <Link className="secondary inline" to={`/p/${project.id}/pick`}>{t("export.none.action")}</Link>
      </Screen>
    );
  }

  if (rows) {
    const states = rows.map((r) => (r.startError ? "failed" : jobs.get(r.jobId ?? "")?.state ?? "queued"));
    const finished = states.filter((s) => s === "done").length;
    const bad = states.filter((s) => s === "failed" || s === "cancelled").length;
    const over = !starting && finished + bad === rows.length;
    const summary = !over
      ? t("export.summary.running", { done: finished + bad, n: rows.length })
      : bad
        ? t("export.summary.failed", { ok: finished, bad })
        : plural("export.summary.done", finished);
    return (
      <Screen bar={bar} narrow>
        <h1 className="title">{t("export.title")}</h1>
        <div className="settings">
          <section className="set-sec" aria-label={t("export.queue")}>
            <h2 aria-live="polite">{summary}</h2>
            <p className="set-note wrap-path" title={shownFolder}>{shownFolder}</p>
            <ul className="q-list">
              {rows.map((row) => <QueueRow key={row.hid} row={row} desktop={desktop} busy={busy.has(row.hid)} onRetry={retry} />)}
            </ul>
            {!desktop && <p className="set-note">{t("export.show.browser")}</p>}
          </section>
          <div className="row wrap">
            {over ? (
              <button type="button" className="secondary" onClick={() => setRows(null)}>{t("export.again")}</button>
            ) : (
              <button type="button" className="secondary" onClick={() => void cancel()}>{t("export.cancel")}</button>
            )}
          </div>
        </div>
      </Screen>
    );
  }

  const count = chosen.length;
  const ready = count > 0 && formats.size > 0 && !starting && !blocked;
  return (
    <Screen bar={bar} narrow>
      <h1 className="title">{t("export.title")}</h1>
      <div className="settings">
        <section className="set-sec" aria-labelledby="ex-clips">
          <h2 id="ex-clips">{t("export.clips")}</h2>
          <p className="set-note">{t("export.clips.count", { k: count, n: kept.length })}</p>
          <div role="group" aria-labelledby="ex-clips">
          <ul className="opt-list">
            {kept.map((c) => (
              <li key={c.highlight.id}>
                <Check
                  checked={!off.has(c.highlight.id)}
                  onChange={(on) => setOff((s) => { const n = new Set(s); if (on) n.delete(c.highlight.id); else n.add(c.highlight.id); return n; })}
                >
                  {c.highlight.title}
                </Check>
              </li>
            ))}
          </ul>
          </div>
        </section>

        <section className="set-sec" aria-label={t("export.options")}>
          <div className="set-grid">
            <fieldset className="field">
              <legend>{t("export.formats")}</legend>
              {FORMATS.map((f) => (
                <Check
                  key={f.value}
                  checked={formats.has(f.value)}
                  onChange={(on) => setFormats((s) => { const n = new Set(s); if (on) n.add(f.value); else n.delete(f.value); return n; })}
                >
                  {t(f.label)}
                </Check>
              ))}
            </fieldset>
            <fieldset className="field">
              <legend>{t("export.quality")}</legend>
              <div className="seg">
                {(["normal", "high"] as Quality[]).map((q) => (
                  <label key={q} className="seg-opt">
                    <input type="radio" name="quality" value={q} checked={quality === q} onChange={() => setQuality(q)} />
                    <span>{t(`export.quality.${q}`)}</span>
                  </label>
                ))}
              </div>
              {quality === "high" && <p className="set-note">{t("export.quality.hint")}</p>}
            </fieldset>
            <fieldset className="field">
              <legend>{t("export.also")}</legend>
              <Check checked={srt} onChange={setSrt}>{t("export.also.srt")}</Check>
              <Check checked={thumb} onChange={setThumb}>{t("export.also.thumb")}</Check>
            </fieldset>
          </div>
        </section>

        <section className="set-sec" aria-labelledby="ex-folder">
          <h2 id="ex-folder">{t("export.folder")}</h2>
          <p className="set-note wrap-path" title={shownFolder} data-testid="export-folder">{shownFolder}</p>
          <div className="row wrap">
            <button type="button" className="secondary" disabled={!desktop} onClick={() => void changeFolder()}>{t("export.folder.change")}</button>
            {override && <button type="button" className="secondary" onClick={() => setOverride(null)}>{t("export.folder.reset")}</button>}
          </div>
          {!desktop && <p className="set-note">{t("export.folder.browser")}</p>}
        </section>

        {blocked && (
          <div className="set-sec" role="status">
            <p className="set-note">{t("export.busy")}</p>
            <div className="row wrap"><button type="button" className="secondary" onClick={() => void cancel()}>{t("export.cancel")}</button></div>
          </div>
        )}

        <div className="row wrap">
          <button type="button" className="primary" disabled={!ready} aria-busy={starting} onClick={() => void start()}>
            {plural("export.go", count)}
          </button>
        </div>
      </div>
    </Screen>
  );
}
