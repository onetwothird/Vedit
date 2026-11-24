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
import { secondsLeft } from "../lib/progress";

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

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);

  function accept(path: string) {
    if (locked.current) return; // a running analysis is never replaced by a dropped file
    if (!isVideoName(baseName(path))) {
      setError(t("import.unsupported"));
      return;
    }
    setError(null);
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
    if (f) accept(f.name);
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files[0];
    if (f) accept(f.name);
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
    return (
      <Screen bar={bar} narrow>
        <h1 className="title">{t("import.analyzing")}</h1>
        <section className="run">
          <div className="run-head">
            <span className="mono eta">{timeLeftText(secondsLeft(now - startedAt.current, job.pct))}</span>
            <span className="mono total">{pct}%</span>
          </div>
          <div
            className="meter"
            role="progressbar"
            aria-label={t("import.progress")}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
          >
            <i style={{ width: `${pct}%` }} />
          </div>
          <ProgressStages stage={job.stage} pct={job.pct} hasModel={hasModelStep(job.id) || job.stage === "model"} whole={whole} />
          {error && <p className="error" role="alert">{error}</p>}
          <button type="button" className="secondary" onClick={cancel}>{t("import.cancel")}</button>
        </section>
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

  return (
    <Screen bar={bar} narrow>
      <h1 className="title">{t(kind === "clip" ? "import.title" : `import.title.${kind}`)}</h1>
      <div className="import">
        <div
          className={`drop${over ? " over" : ""}${file ? " has-file" : ""}`}
          role="group"
          aria-label={t("import.drop.label")}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          {file ? (
            <div className="drop-file">
              <span className="drop-icon" aria-hidden="true"><FilmIcon /></span>
              <span className="drop-file-text">
                <span className="file-name mono" title={file.path}>{file.name}</span>
                <span className="drop-hint">{t("import.ready")}</span>
              </span>
              <button type="button" className="secondary" onClick={choose}>{t("import.choose.another")}</button>
            </div>
          ) : (
            <>
              <span className="drop-icon" aria-hidden="true"><DropIcon /></span>
              <span className="drop-title">{t("import.drop.title")}</span>
              <span className="drop-hint">{t("import.drop.formats")}</span>
              <button type="button" className="secondary" onClick={choose}>{t("import.choose")}</button>
            </>
          )}
          <DropWave />
          <input ref={input} type="file" accept={VIDEO_ACCEPT} hidden onChange={onInput} />
        </div>

        <div className="fields import-opts">
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

        <ol className="import-next" aria-label={t("import.next")}>
          {(whole ? (["listen", "cut", "edit"] as const) : (["listen", "find", "pick"] as const)).map((k) => (
            <li key={k}>{t(`import.next.${k}`)}</li>
          ))}
        </ol>

        {cancelled && (
          <p className="notice" role="status">
            <strong>{t("import.cancelled")}</strong>
            <span>{t("import.cancelled.hint")}</span>
          </p>
        )}
        {error && <p className="error" role="alert">{error}</p>}

        <div className="row">
          <button type="button" className="primary" onClick={start} disabled={!file || busy}>
            {busy ? t("import.starting") : t("import.start")}
          </button>
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

function DropWave() {
  return (
    <span className="drop-wave" aria-hidden="true">
      <svg viewBox={`0 0 ${WAVE.length * 6} 40`} preserveAspectRatio="none">
        {WAVE.map((v, i) => {
          const h = 4 + v * 32;
          return <rect key={i} x={i * 6 + 1} y={(40 - h) / 2} width={3.4} height={h} rx={1.2} />;
        })}
      </svg>
      <span className="drop-head"><i /></span>
    </span>
  );
}

const ICON = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round" } as const;

function DropIcon() {
  return (
    <svg {...ICON}>
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <path d="M12 15V9m-3 3 3-3 3 3" />
    </svg>
  );
}

function FilmIcon() {
  return (
    <svg {...ICON}>
      <rect x="3" y="5" width="18" height="14" rx="2.5" />
      <path d="m10 9.5 4.5 2.5-4.5 2.5z" />
    </svg>
  );
}
