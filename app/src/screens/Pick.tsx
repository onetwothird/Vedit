import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import ClipList from "../components/ClipList";
import EpisodeTimeline from "../components/EpisodeTimeline";
import FilmFrame from "../components/FilmFrame";
import Screen from "../components/Screen";
import { engine } from "../engine/client";
import { useJob } from "../engine/events";
import type { Aspect, AnalyzeResult, ClipView, Project, Transcript } from "../engine/types";
import { t } from "../i18n/t";
import { isWhole, projectHome } from "../lib/projectKind";
import { keptCount, sortClips, type ClipSort } from "../lib/clips";
import { analyzeBody, baseName } from "../lib/native";
import { formatClock, formatLength } from "../lib/time";
import { whyPicked } from "../lib/why";
import { useFilmstrip } from "../lib/useFilmstrip";
import { coverWidth } from "../lib/filmstrip";
import { previewSrc } from "../preview/media";
import Player from "../preview/Player";

const MORE_CLIPS = 6;
const MAX_CLIPS = 60; // the most the engine accepts
const STAGE_MAX_H = 560; // the phone frame never grows past this, however tall the window
const CONTROLS_H = 56; // the player's controls under the frame

const ASPECTS: Aspect[] = ["9:16", "1:1", "4:5", "16:9"];

const plural = (key: string, n: number, vars: Record<string, string | number> = {}) =>
  t(`${key}.${n === 1 ? "one" : "other"}`, { n, ...vars });

function lastAnalyze(p: Project): AnalyzeResult | undefined {
  const j = p.last_job;
  return j && j.kind === "analyze" && j.state === "done" ? (j.result as AnalyzeResult | null) ?? undefined : undefined;
}

export default function Pick() {
  const { id } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  // a reload loses router state, so the project's last job is the fallback
  const routed = (location.state as { analyze?: AnalyzeResult } | null)?.analyze;

  const [project, setProject] = useState<Project | null>(null);
  const [missing, setMissing] = useState(false);
  const [sort, setSort] = useState<ClipSort>("best");
  const [picked, setPicked] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | undefined>();
  const [starting, setStarting] = useState(false);
  const pending = useRef(0); // setKept calls in flight
  const handled = useRef<string | undefined>(undefined);
  const film = useFilmstrip(id);

  const job = useJob(jobId);
  const running = job?.state === "queued" || job?.state === "running";
  const busy = running || starting;
  const stage = useRef<HTMLDivElement>(null);
  const [fit, setFit] = useState({ width: 320, height: 420 });
  const [transcript, setTranscript] = useState<Transcript | null>(null);

  // the selected clip's edit, for the player; until it arrives (or if it never does) the filmstrip frame stands in
  const [loaded, setLoaded] = useState<{ hid: string; view: ClipView } | null>(null);

  function load(pid: string): Promise<void> {
    return engine.getProject(pid).then((p) => setProject(p));
  }

  useEffect(() => {
    if (!id) return;
    let live = true;
    setProject(null);
    setMissing(false);
    engine.getProject(id).then(
      (p) => {
        if (!live) return;
        if (isWhole(p.type)) {
          navigate(projectHome(p), { replace: true });
          return;
        }
        setProject(p);
        const last = p.last_job;
        if (last && last.kind === "analyze" && (last.state === "queued" || last.state === "running")) {
          setJobId(last.id); // a "find more" run is still going
          engine.getJob(last.id).catch(() => undefined);
        }
      },
      () => live && setMissing(true),
    );
    return () => {
      live = false;
    };
  }, [id]);

  // a finished "find more" run: take the new clips
  useEffect(() => {
    if (!id || !job || job.state !== "done" || handled.current === job.id) return;
    handled.current = job.id;
    const added = (job.result as AnalyzeResult | null)?.new ?? 0;
    load(id).then(
      () => setNote(added > 0 ? plural("pick.more.added", added) : t("pick.more.none")),
      () => setError(t("pick.error.reload")),
    );
  }, [id, job]);

  // the preview frame fits the stage as it resizes
  useEffect(() => {
    const el = stage.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const measure = () =>
      setFit({ width: Math.max(80, el.clientWidth), height: Math.max(160, Math.min(STAGE_MAX_H, el.clientHeight - CONTROLS_H)) });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [project !== null]);

  // the words, for each clip's quote and the video's waveform; Pick still works without them
  const hasClips = (project?.clips.length ?? 0) > 0;
  useEffect(() => {
    if (!id || !hasClips) return;
    let live = true;
    engine.transcript(id).then((tr) => live && setTranscript(tr), () => undefined);
    return () => {
      live = false;
    };
  }, [id, hasClips]);

  const clips = project?.clips ?? [];
  const sorted = sortClips(clips, sort);
  const currentId = sorted.some((c) => c.highlight.id === picked) ? picked : sorted[0]?.highlight.id;
  const current = sorted.find((c) => c.highlight.id === currentId);
  useEffect(() => {
    if (!id || !currentId) return;
    let live = true;
    Promise.resolve()
      .then(() => engine.clip(id, currentId))
      .then((view) => live && setLoaded({ hid: currentId, view }), () => undefined);
    return () => {
      live = false;
    };
  }, [id, currentId]);

  const kept = keptCount(clips);
  const full = clips.length >= MAX_CLIPS;
  const analyze = routed ?? (project ? lastAnalyze(project) : undefined);
  const empty = !!project && (analyze?.reason === "no_speech" || clips.length === 0);

  async function toggleKept(hid: string) {
    if (!id || !project) return;
    const before = clips.find((c) => c.highlight.id === hid);
    if (!before) return;
    const next = !before.kept;
    const set = (v: boolean) =>
      setProject((p) => p && { ...p, clips: p.clips.map((c) => (c.highlight.id === hid ? { ...c, kept: v } : c)) });
    setError(null);
    set(next);
    pending.current++;
    try {
      const fresh = await engine.setKept(id, hid, next);
      pending.current--;
      if (pending.current === 0) setProject(fresh); // with other changes in flight, this answer may be stale
    } catch {
      pending.current--;
      set(before.kept);
      setError(t("pick.error.keep"));
    }
  }

  async function findMore() {
    if (!id || busy) return;
    setStarting(true);
    setError(null);
    setNote(null);
    try {
      const started = await engine.analyze(id, { ...(await analyzeBody()), max_clips: Math.min(clips.length + MORE_CLIPS, MAX_CLIPS) });
      setJobId(started.id);
    } catch {
      setError(t("pick.error.more"));
    } finally {
      setStarting(false);
    }
  }

  const bar = {
    fileName: project ? baseName(project.source) : undefined,
    step: 2 as const,
    editHid: currentId,
  };

  if (missing) {
    return (
      <Screen bar={{ step: 2 }}>
        <h1 className="title">{t("pick.title")}</h1>
        <p className="note-text" role="alert">{t("pick.missing")}</p>
        <Link className="secondary inline" to="/">{t("pick.home")}</Link>
      </Screen>
    );
  }

  if (!project) {
    return (
      <Screen bar={{ step: 2 }}>
        <h1 className="title">{t("pick.title")}</h1>
      </Screen>
    );
  }

  if (empty) {
    return (
      <Screen bar={{ fileName: bar.fileName, step: 2 }}>
        <h1 className="title">{t("pick.title")}</h1>
        <p className="note-text" role="status">{t("pick.empty")}</p>
        <Link className="secondary inline" to="/">{t("pick.empty.action")}</Link>
      </Screen>
    );
  }

  const failedJob = job?.state === "failed";
  const pct = job ? Math.round(job.pct) : 0;

  return (
    <Screen bar={bar} wide>
      <div className="pick">
        <section className="pick-side" aria-label={t("pick.list")}>
          <header className="pick-head">
            <h1 className="pick-count">
              {plural("pick.counts", clips.length, { k: kept })}
            </h1>
          </header>
          <ClipList
            clips={clips}
            currentId={currentId}
            sort={sort}
            film={film}
            onSort={setSort}
            onSelect={setPicked}
            onToggleKept={toggleKept}
          />
          <footer className="pick-foot">
            <button type="button" className="secondary" onClick={findMore} disabled={busy || full} aria-busy={busy}>
              {running ? t("pick.more.running", { pct }) : t("pick.more")}
            </button>
            {error && <p className="error" role="alert">{error}</p>}
            {!error && failedJob && <p className="error" role="alert">{job.error || t("pick.error.more")}</p>}
            {full && <p className="note-text" role="status">{t("pick.more.full", { n: MAX_CLIPS })}</p>}
            {note && !error && <p className="note-text" role="status">{note}</p>}
          </footer>
        </section>

        <section className="pick-preview" aria-label={t("pick.preview")}>
          {current && (
            <div className="stage" ref={stage}>
              {loaded && loaded.hid === current.highlight.id && project ? (
                <Player
                  key={loaded.hid}
                  view={loaded.view}
                  src={previewSrc(project)}
                  aspect={ASPECTS.find((a) => a === loaded.view.edl.output?.aspect) ?? "9:16"}
                  fit={fit}
                  poster={(f) => <FilmFrame film={film} t={current.highlight.start} width={coverWidth(film, f.width, f.height)} />}
                />
              ) : (
                <FilmFrame film={film} t={current.highlight.start} fit={fit} className="preview-frame" />
              )}
            </div>
          )}
        </section>

        {current && (
          <ClipDetails
            clip={current}
            words={transcript?.words}
            onToggleKept={() => toggleKept(current.highlight.id)}
            onEdit={() => id && navigate(`/p/${id}/edit/${current.highlight.id}`)}
          />
        )}

        {transcript && (
          <EpisodeTimeline
            clips={clips}
            words={transcript.words}
            duration={transcript.duration ?? transcript.words[transcript.words.length - 1]?.end ?? 0}
            currentId={currentId}
            onSelect={setPicked}
          />
        )}
      </div>
    </Screen>
  );
}

/** The selected clip: where it is in the video, what's said, why it was picked, and what to do with it. */
function ClipDetails({ clip, words, onToggleKept, onEdit }: {
  clip: Project["clips"][number];
  words?: Transcript["words"];
  onToggleKept(): void;
  onEdit(): void;
}) {
  const h = clip.highlight;
  const said = words?.slice(h.w0, h.w1 + 1).filter((w) => !w.filler).map((w) => w.text).join(" ");
  const why = whyPicked(h.features);
  return (
    <section className="pick-info" aria-labelledby="pick-info-title">
      <h2 className="preview-title" id="pick-info-title">{h.title}</h2>
      <p className="preview-meta">
        <span>{t("pick.range", { a: formatClock(h.start), b: formatClock(h.end) })}</span>
        <span>{t("pick.length", { len: formatLength(h.end - h.start) })}</span>
        <span>{t("pick.score", { n: Math.round(h.score) })}</span>
      </p>
      {said && (
        <div className="pick-said">
          <h3>{t("pick.words")}</h3>
          <blockquote>{said}</blockquote>
        </div>
      )}
      {why.length > 0 && (
        <div className="pick-why">
          <h3>{t("pick.why")}</h3>
          <ul>{why.map((k) => <li key={k}>{t(`pick.why.${k}`)}</li>)}</ul>
        </div>
      )}
      <div className="pick-actions">
        <button type="button" className={clip.kept ? "secondary keep-btn on" : "secondary keep-btn"} aria-pressed={clip.kept} onClick={onToggleKept}>
          <span className="box" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          </span>
          {t("pick.keep")}
        </button>
        <button type="button" className="primary" onClick={onEdit}>{t("pick.edit")}</button>
      </div>
    </section>
  );
}
