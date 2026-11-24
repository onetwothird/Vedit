import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import ProgressStages from "../components/ProgressStages";
import Screen from "../components/Screen";
import { engine, isTauri } from "../engine/client";
import { useJob } from "../engine/events";
import { EngineError, type Language, type Platform, type ProjectType } from "../engine/types";
import { t } from "../i18n/t";
import {
  VIDEO_ACCEPT, analyzeBody, baseName, isVideoName, listenNativeDrop, pickVideoNative, toPicked, type PickedFile,
} from "../lib/native";
import { getLanguage, getPlatform } from "../lib/prefs";
import { PLATFORMS_FOR, isWhole, newImport, parseType, platformFor, projectHome } from "../lib/projectKind";
import { hasModelStep } from "../lib/modelSeen";
import { secondsLeft, stageRows } from "../lib/progress";
import "./import.css";

interface Meta {
  duration: number;
  width: number;
  height: number;
}

/** 00:42:07 for a duration in seconds; dashes before a video is known. */
function timecode(sec: number | null): string {
  if (sec === null || !Number.isFinite(sec)) return "--:--:--";
  const s = Math.floor(sec);
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60].map((n) => String(n).padStart(2, "0")).join(":");
}

const reducedMotion = (): boolean =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** A URL the viewer can play for a picked file: the desktop app reads the file itself, a browser has the File. */
async function previewFor(path: string, file?: File): Promise<string | null> {
  if (file) return URL.createObjectURL(file);
  if (!isTauri()) return null;
  try {
    const { convertFileSrc } = await import("@tauri-apps/api/core");
    return convertFileSrc(path);
  } catch {
    return null;
  }
}

const LANGUAGES: Language[] = ["en", "tl", "taglish"];

function Choice<T extends string>(props: {
  legend: string;
  name: string;
  keys: T[];
  labelPrefix: string;
  value: T;
  onChange(v: T): void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="field" disabled={props.disabled}>
      <legend>{props.legend}</legend>
      <div className="seg">
        {props.keys.map((k) => (
          <label key={k} className="seg-opt">
            <input
              type="radio"
              name={props.name}
              value={k}
              checked={props.value === k}
              onChange={() => props.onChange(k)}
            />
            <span>{t(`${props.labelPrefix}.${k}`)}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

function timeLeftText(sec: number | null): string {
  if (sec === null) return t("import.estimating");
  if (sec < 10) return t("import.timeleft.few");
  if (sec < 60) return t("import.timeleft.sec", { n: sec });
  return t("import.timeleft.min", { n: Math.ceil(sec / 60) });
}

export default function Import() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const [kind, setKind] = useState<ProjectType>(() => parseType(params.get("type")));
  const [file, setFile] = useState<PickedFile | null>(null);
  const [language, setLanguage] = useState<Language>(getLanguage);
  const [platform, setPlatform] = useState<Platform>(() => platformFor(parseType(params.get("type")), getPlatform()));
  const whole = isWhole(kind);
  const [pid, setPid] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [missing, setMissing] = useState(false);
  const [loading, setLoading] = useState(!!id && id !== "new");
  const [now, setNow] = useState(() => Date.now());
  const startedAt = useRef(Date.now());
  const ownedId = useRef<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const pickedFile = useRef<File | undefined>(undefined);

  const job = useJob(jobId);
  const running = job?.state === "queued" || job?.state === "running";
  const locked = useRef(false); // read by drop handlers, which outlive a render
  locked.current = running || busy;
  const failed = job?.state === "failed";
  const cancelled = job?.state === "cancelled";

  // an existing project: show its file and settings, and pick up an analysis that is running or has stopped
  useEffect(() => {
    if (!id || id === "new" || id === ownedId.current) return;
    let live = true;
    setLoading(true);
    setMissing(false);
    setJobId(undefined);
    setError(null);
    engine.getProject(id).then(
      (p) => {
        if (!live) return;
        setPid(p.id);
        setKind(p.type);
        setFile(toPicked(p.source));
        setLanguage(p.language);
        setPlatform(p.platform);
        const last = p.last_job;
        if (last && last.kind === "analyze" && last.state !== "done") {
          startedAt.current = Date.now();
          setJobId(last.id);
          engine.getJob(last.id).catch(() => undefined);
        }
        setLoading(false);
      },
      () => {
        if (!live) return;
        setMissing(true);
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
  }, [id]);

  useEffect(() => {
    if (job?.state === "done") navigate(projectHome({ id: job.project_id, type: kind }), { state: { analyze: job.result } });
  }, [job?.state, job?.project_id, job?.result, kind, navigate]);

  // the viewer plays the chosen video, so you can see it is the right one before starting
  useEffect(() => {
    setMeta(null);
    setPreviewFailed(false);
    setPlayhead(0);
    if (!file) {
      setPreview(null);
      return;
    }
    let live = true;
    let made: string | null = null;
    void previewFor(file.path, pickedFile.current).then((url) => {
      made = url;
      if (live) setPreview(url);
      else if (url?.startsWith("blob:")) URL.revokeObjectURL(url);
    });
    return () => {
      live = false;
      if (made?.startsWith("blob:")) URL.revokeObjectURL(made);
    };
  }, [file]);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  function accept(path: string, picked?: File) {
    if (locked.current) return; // a running analysis is never replaced by a dropped file
    if (!isVideoName(baseName(path))) {
      setError(t("import.unsupported"));
      return;
    }
    setError(null);
    pickedFile.current = picked;
    setFile(toPicked(path));
    if (pid) {
      // a different video is a different project
      ownedId.current = null;
      setPid(null);
      setJobId(undefined);
      navigate(newImport(kind), { replace: true });
    }
  }

  useEffect(
    () => listenNativeDrop({ onOver: setOver, onDrop: (paths) => paths[0] && accept(paths[0]) }),
    // accept reads pid through its closure, so re-subscribe when pid changes
    [pid],
  );

  async function choose() {
    if (isTauri()) {
      try {
        const path = await pickVideoNative();
        if (path) accept(path);
      } catch {
        setError(t("import.failed.generic"));
      }
    } else {
      input.current?.click();
    }
  }

  function onInput(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = ""; // the same file can be chosen again
    if (f) accept(f.name, f);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files[0];
    if (f) accept(f.name, f);
  }

  async function start() {
    if (!file || busy) return;
    setBusy(true);
    setError(null);
    try {
      let project = pid;
      if (!project) {
        const p = await engine.createProject({ source: file.path, language, platform: platformFor(kind, platform), type: kind });
        project = p.id;
        ownedId.current = p.id;
        setPid(p.id);
        navigate(`/p/${p.id}/import`, { replace: true });
      }
      const started = await engine.analyze(project, await analyzeBody());
      startedAt.current = Date.now();
      setNow(Date.now());
      setJobId(started.id);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : t("import.failed.generic"));
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    if (!jobId) return;
    try {
      await engine.cancelJob(jobId);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : t("import.failed.generic"));
    }
  }

  const bar = { fileName: file?.name, step: 1 as const, whole };

  if (missing) {
    return (
      <Screen bar={bar} narrow>
        <h1 className="title">{t("import.title")}</h1>
        <p className="note-text" role="alert">{t("import.missing")}</p>
        <Link className="secondary inline" to="/">{t("import.home")}</Link>
      </Screen>
    );
  }

  if (loading) {
    return (
      <Screen bar={bar} narrow>
        <h1 className="title">{t("import.title")}</h1>
      </Screen>
    );
  }

  if (job && running) {
    const pct = Math.round(job.pct);
    const rows = stageRows(job.stage, job.pct, hasModelStep(job.id) || job.stage === "model", false, whole);
    const current = rows.find((r) => r.status === "current");
    return (
      <Screen bar={bar}>
        <div className="imp">
          <h1 className="title imp-title">{t("import.analyzing")}</h1>
          <div className="imp-grid">
            <div className="imp-monitor working" style={{ "--p": job.pct / 100 } as React.CSSProperties}>
              <div className={`viewer has-file working${preview && !previewFailed ? " playing" : ""}`}>
                {preview && !previewFailed ? (
                  <video className="vf-video" src={preview} muted loop playsInline autoPlay={!reducedMotion()} aria-hidden="true"
                    onError={() => setPreviewFailed(true)} />
                ) : (
                  <span className="vf-still" aria-hidden="true"><FilmIcon /></span>
                )}
                <span className="vf-scan" aria-hidden="true" />
                <div className="vf-work">
                  <span className="vf-work-pct">{pct}%</span>
                  {current && <span className="vf-work-stage" aria-hidden="true" data-stage={t(`import.stage.${current.key}`)} />}
                </div>
                <div className="vf-bar">
                  <span className="vf-tc mono">{timeLeftText(secondsLeft(now - startedAt.current, job.pct))}</span>
                  <span className="vf-file">
                    {file && <span className="file-name mono" title={file.path}>{file.name}</span>}
                  </span>
                </div>
              </div>
              <DropWave heard={job.pct / 100} />
              <div className="meter imp-meter" role="progressbar" aria-label={t("import.progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
                <i style={{ width: `${pct}%` }} />
              </div>
            </div>
            <aside className="imp-slate" aria-label={t("import.progress")}>
              <ProgressStages stage={job.stage} pct={job.pct} hasModel={hasModelStep(job.id) || job.stage === "model"} whole={whole} />
              <p className="imp-start-note imp-wait">{t("import.working.note")}</p>
              {error && <p className="error" role="alert">{error}</p>}
              <button type="button" className="secondary" onClick={cancel}>{t("import.cancel")}</button>
            </aside>
          </div>
        </div>
      </Screen>
    );
  }

  if (job && failed) {
    return (
      <Screen bar={bar} narrow>
        <h1 className="title">{t("import.failed.title")}</h1>
        <section className="run failed">
          <p className="error" role="alert">{job.error || t("import.failed.generic")}</p>
          {error && <p className="error" role="alert">{error}</p>}
          <div className="row">
            <button type="button" className="primary" onClick={start} disabled={busy}>
              {busy ? t("import.starting") : t("import.retry")}
            </button>
          </div>
        </section>
      </Screen>
    );
  }

  const portrait = !!meta && meta.height > meta.width;
  // what Vedit will cut from a landscape video: the vertical frame (a vlog keeps its own shape)
  const guide = !!file && !portrait && kind !== "vlog";
  const steps = whole ? (["listen", "cut", "edit"] as const) : (["listen", "find", "pick"] as const);

  return (
    <Screen bar={bar}>
      <div className="imp">
        <h1 className="title imp-title">{t(kind === "clip" ? "import.title" : `import.title.${kind}`)}</h1>
        <div className="imp-grid">
          <div className="imp-monitor" style={{ "--t": meta?.duration ? Math.min(1, playhead / meta.duration) : 0 } as React.CSSProperties}>
            <div
              className={`viewer${over ? " over" : ""}${file ? " has-file" : ""}${preview && !previewFailed ? " playing" : ""}`}
              role="group"
              aria-label={t("import.drop.label")}
              onDragOver={(e) => {
                e.preventDefault();
                setOver(true);
              }}
              onDragLeave={() => setOver(false)}
              onDrop={onDrop}
            >
              <span className="vf-marks" aria-hidden="true"><i /><i /><i /><i /></span>
              {file && preview && !previewFailed && (
                <video
                  key={preview}
                  className="vf-video"
                  src={preview}
                  muted
                  loop
                  playsInline
                  autoPlay={!reducedMotion()}
                  preload="metadata"
                  aria-hidden="true"
                  onLoadedMetadata={(e) => {
                    const v = e.currentTarget;
                    setMeta({ duration: v.duration, width: v.videoWidth, height: v.videoHeight });
                  }}
                  onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
                  onError={() => setPreviewFailed(true)}
                />
              )}
              {guide && (
                <span className="vf-guide" aria-hidden="true">
                  <span className="vf-guide-label">{t("import.guide", { platform: t(`import.platform.${platform}`) })}</span>
                </span>
              )}
              {file ? (
                <>
                  {(!preview || previewFailed) && (
                    <span className="vf-still" aria-hidden="true"><FilmIcon /></span>
                  )}
                  <button type="button" className="vf-swap" onClick={choose}>{t("import.choose.another")}</button>
                </>
              ) : (
                <div className="vf-empty">
                  <span className="vf-empty-title">{t("import.drop.title")}</span>
                  <span className="vf-empty-hint">{t("import.drop.formats")}</span>
                  <button type="button" className="vf-choose" onClick={choose}>{t("import.choose")}</button>
                </div>
              )}
              <div className="vf-bar">
                <span className="vf-tc mono">
                  {meta ? `${timecode(playhead)} / ${timecode(meta.duration)}` : timecode(null)}
                </span>
                {file ? (
                  <span className="vf-file">
                    <span className="file-name mono" title={file.path}>{file.name}</span>
                    {meta && meta.width > 0 && <span className="vf-res mono">{meta.width}×{meta.height}</span>}
                  </span>
                ) : (
                  <span className="vf-file vf-file-empty">{t("import.viewer.empty")}</span>
                )}
              </div>
              <input ref={input} type="file" accept={VIDEO_ACCEPT} hidden onChange={onInput} />
            </div>
            <DropWave />
          </div>

          <aside className="imp-slate" aria-label={t("import.settings")}>
            <div className="imp-fields">
              <Choice<Language>
                legend={t("import.language")}
                name="language"
                keys={LANGUAGES}
                labelPrefix="import.language"
                value={language}
                onChange={setLanguage}
                disabled={!!pid}
              />
              {kind !== "vlog" && (
                <Choice<Platform>
                  legend={t("import.platform")}
                  name="platform"
                  keys={PLATFORMS_FOR[kind]}
                  labelPrefix="import.platform"
                  value={platform}
                  onChange={setPlatform}
                  disabled={!!pid}
                />
              )}
            </div>

            <div className="imp-next">
              <h2 className="imp-next-h" id="next-h">{t("import.next")}</h2>
              <ol className="imp-steps" aria-labelledby="next-h">
                {steps.map((k) => <li key={k}>{t(`import.next.${k}`)}</li>)}
              </ol>
            </div>

            {cancelled && (
              <p className="notice" role="status">
                <strong>{t("import.cancelled")}</strong>
                <span>{t("import.cancelled.hint")}</span>
              </p>
            )}
            {error && <p className="error" role="alert">{error}</p>}

            <button type="button" className="primary imp-start" onClick={start} disabled={!file || busy}>
              {busy ? t("import.starting") : t("import.start")}
            </button>
            <p className="imp-start-note">{t(file ? "import.ready" : "import.start.needs")}</p>
          </aside>
        </div>
      </div>
    </Screen>
  );
}

/** A short waveform along the foot of the drop zone; the playhead runs across it while a file is dragged over. */
const WAVE = Array.from({ length: 96 }, (_, i) => {
  const r = Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1;
  return Math.max(0.12, Math.min(1, (0.55 + 0.45 * Math.sin(i / 6)) * (0.35 + 0.65 * r)));
});

/** `heard` (0..1): how much of the video has been listened to; those bars fill in. */
function DropWave({ heard }: { heard?: number }) {
  const upTo = heard === undefined ? -1 : Math.round(heard * WAVE.length);
  return (
    <span className="drop-wave" aria-hidden="true">
      <svg viewBox={`0 0 ${WAVE.length * 6} 40`} preserveAspectRatio="none">
        {WAVE.map((v, i) => {
          const h = 4 + v * 32;
          return <rect key={i} className={i < upTo ? "heard" : undefined} x={i * 6 + 1} y={(40 - h) / 2} width={3.4} height={h} rx={1.2} />;
        })}
      </svg>
      <span className="drop-head"><i /></span>
    </span>
  );
}

const ICON = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;


function FilmIcon() {
  return (
    <svg {...ICON}>
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <path d="m10 9.5 4.5 2.5-4.5 2.5z" />
    </svg>
  );
}
