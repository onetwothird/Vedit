import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import ClipList from "../components/ClipList";
import FilmFrame from "../components/FilmFrame";
import Screen from "../components/Screen";
import { engine } from "../engine/client";
import { useJob } from "../engine/events";
import type { Aspect, AnalyzeResult, ClipView, Project } from "../engine/types";
import { t } from "../i18n/t";
import { keptCount, sortClips, type ClipSort } from "../lib/clips";
import { analyzeBody, baseName } from "../lib/native";
import { formatLength } from "../lib/time";
import { useFilmstrip } from "../lib/useFilmstrip";
import { coverWidth } from "../lib/filmstrip";
import { previewSrc } from "../preview/media";
import Player from "../preview/Player";

const MORE_CLIPS = 6;
const MAX_CLIPS = 60; // the most the engine accepts
const STAGE_PAD = 48; // .stage padding, both sides
const STAGE_MAX_H = 420;

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
  const [stageW, setStageW] = useState(560);

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
    const measure = () => setStageW(Math.max(80, el.clientWidth - STAGE_PAD));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [project !== null]);

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
    primary: id
      ? { label: t("pick.edit"), disabled: !currentId, onClick: () => currentId && navigate(`/p/${id}/edit/${currentId}`) }
      : undefined,
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
            <>
              <div className="stage" ref={stage}>
                {loaded && loaded.hid === current.highlight.id && project ? (
                  <Player
                    key={loaded.hid}
                    view={loaded.view}
                    src={previewSrc(project)}
                    aspect={ASPECTS.find((a) => a === loaded.view.edl.output?.aspect) ?? "9:16"}
                    fit={{ width: stageW, height: STAGE_MAX_H }}
                    poster={(f) => <FilmFrame film={film} t={current.highlight.start} width={coverWidth(film, f.width, f.height)} />}
                  />
                ) : (
                  <FilmFrame film={film} t={current.highlight.start} fit={{ width: stageW, height: STAGE_MAX_H }} className="preview-frame" />
                )}
              </div>
              <h2 className="preview-title">{current.highlight.title}</h2>
              <p className="preview-meta">
                <span>{t("pick.length", { len: formatLength(current.highlight.end - current.highlight.start) })}</span>
                <span>{t("pick.score", { n: Math.round(current.highlight.score) })}</span>
              </p>
            </>
          )}
        </section>
      </div>
    </Screen>
  );
}
