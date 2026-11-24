import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import Screen from "../components/Screen";
import { engine, engineMediaUrl, isMock, isTauri } from "../engine/client";
import { useJob } from "../engine/events";
import {
  EngineError, type Story as StoryDoc, type StoryAspect, type StoryBody, type StoryCaptions, type StoryMotion,
  type StoryResult, type StoryScene, type StoryVoice,
} from "../engine/types";
import { t } from "../i18n/t";
import {
  AUDIO_ACCEPT, AUDIO_EXTENSIONS, PHOTO_EXTENSIONS, STORY_ACCEPT, VIDEO_EXTENSIONS, baseName, isStoryMediaName,
  listenNativeDrop, pickAudioNative, pickManyNative, showInFolder,
} from "../lib/native";
import { pickedUrl, rememberPicked } from "../lib/overlayMedia";
import { getSettings } from "../lib/prefs";
import "./story.css";

const NO_STEPS = { steps: false };

const ASPECTS: StoryAspect[] = ["9:16", "16:9", "1:1"];
const CAPTIONS: StoryCaptions[] = ["pop", "box", "clean", "none"];
const MOTIONS: StoryMotion[] = ["auto", "in", "out", "left", "right", "none"];
const SAVE_DELAY_MS = 500;

type SaveState = "idle" | "saving" | "saved" | "error";

const kindOf = (path: string): StoryScene["kind"] =>
  PHOTO_EXTENSIONS.includes(path.split(".").pop()?.toLowerCase() ?? "") ? "image" : "video";

const bodyOf = (s: StoryDoc): StoryBody => ({
  title: s.title, aspect: s.aspect, voice: s.voice, captions: s.captions, music: s.music, music_volume: s.music_volume,
  scenes: s.scenes.map(({ media, text, motion, keep_audio }) => ({ media, text, motion, keep_audio })),
});

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
            <span>{t(`${prefix}.${k.replace(":", "")}`)}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

/** What a scene shows in the list: the photo itself, or the clip's first frame. */
function SceneThumb({ sid, scene, version }: { sid: string; scene: StoryScene; version: string }) {
  const [broken, setBroken] = useState(false);
  const url = pickedUrl(scene.media) ?? (isMock() ? null : engineMediaUrl(`/stories/${sid}/file`, { path: scene.media, v: version }));
  useEffect(() => setBroken(false), [url]);
  if (!url || broken) {
    return <span className="sc-thumb empty" aria-hidden="true">{scene.kind === "image" ? <PhotoIcon /> : <ClipIcon />}</span>;
  }
  return (
    <span className="sc-thumb" aria-hidden="true">
      {scene.kind === "image"
        ? <img src={url} alt="" onError={() => setBroken(true)} />
        : <video src={url} muted preload="metadata" onError={() => setBroken(true)} />}
    </span>
  );
}

export default function Story() {
  const { sid } = useParams();
  const navigate = useNavigate();
  const desktop = isTauri();
  const [story, setStory] = useState<StoryDoc | null>(null);
  const [missing, setMissing] = useState(false);
  const [voices, setVoices] = useState<StoryVoice[]>([]);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | undefined>();
  const [starting, setStarting] = useState(false);
  const [over, setOver] = useState(false);
  const [showError, setShowError] = useState(false);
  const creating = useRef(false);
  const latest = useRef<StoryDoc | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saving = useRef<Promise<boolean>>(Promise.resolve(true));
  const mediaInput = useRef<HTMLInputElement>(null);
  const musicInput = useRef<HTMLInputElement>(null);
  const job = useJob(jobId);
  const running = job?.state === "queued" || job?.state === "running";

  useEffect(() => {
    engine.storyVoices().then(setVoices, () => setVoices([]));
  }, []);

  useEffect(() => {
    if (!sid) return;
    if (sid === "new") {
      if (creating.current) return;
      creating.current = true;
      engine.createStory().then(
        (s) => navigate(`/s/${s.id}`, { replace: true }),
        () => setMissing(true),
      );
      return;
    }
    let live = true;
    setMissing(false);
    engine.getStory(sid).then(
      (s) => {
        if (!live) return;
        latest.current = s;
        setStory(s);
        const last = s.last_job;
        if (last && (last.state === "queued" || last.state === "running")) {
          setJobId(last.id);
          engine.getJob(last.id).catch(() => undefined);
        }
      },
      () => live && setMissing(true),
    );
    return () => {
      live = false;
    };
  }, [sid, navigate]);

  const save = useCallback((): Promise<boolean> => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const doc = latest.current;
    if (!doc) return Promise.resolve(true);
    setSaveState("saving");
    saving.current = saving.current.then(() => engine.saveStory(doc.id, bodyOf(doc)).then(
      (saved) => {
        if (latest.current === doc) {
          latest.current = saved;
          setStory(saved);
        }
        setSaveState("saved");
        setSaveError(null);
        return true;
      },
      (e) => {
        setSaveState("error");
        setSaveError(message(e, t("story.save.failed")));
        return false;
      },
    ));
    return saving.current;
  }, []);

  // a change waiting to be saved is saved when the screen closes
  useEffect(() => () => {
    if (timer.current) void save();
  }, [save]);

  function change(fn: (s: StoryDoc) => StoryDoc) {
    const cur = latest.current;
    if (!cur) return;
    const next = fn(cur);
    latest.current = next;
    setStory(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(), SAVE_DELAY_MS);
  }

  const setScene = (i: number, patch: Partial<StoryScene>) =>
    change((s) => ({ ...s, scenes: s.scenes.map((sc, k) => (k === i ? { ...sc, ...patch } : sc)) }));

  function move(i: number, by: -1 | 1) {
    change((s) => {
      const scenes = [...s.scenes];
      const [sc] = scenes.splice(i, 1);
      scenes.splice(i + by, 0, sc);
      return { ...s, scenes };
    });
  }

  function addMedia(paths: string[]) {
    const ok = paths.filter((p) => isStoryMediaName(baseName(p)));
    const skipped = paths.length - ok.length;
    setNotice(skipped ? t(skipped === 1 ? "story.add.skipped.one" : "story.add.skipped.other", { n: skipped }) : null);
    if (!ok.length) return;
    change((s) => ({
      ...s,
      scenes: [...s.scenes, ...ok.map((media): StoryScene => ({ media, kind: kindOf(media), text: "", motion: "auto", keep_audio: false }))],
    }));
  }

  const addRef = useRef(addMedia);
  addRef.current = addMedia;
  useEffect(() => listenNativeDrop({ onOver: setOver, onDrop: (paths) => addRef.current(paths) }), []);

  async function chooseMedia() {
    if (!desktop) {
      mediaInput.current?.click();
      return;
    }
    try {
      addMedia(await pickManyNative(t("story.add.dialog"), [...PHOTO_EXTENSIONS, ...VIDEO_EXTENSIONS]));
    } catch {
      setError(t("story.add.failed"));
    }
  }

  function onMediaInput(e: React.ChangeEvent<HTMLInputElement>) {
    const files = [...(e.target.files ?? [])];
    e.target.value = "";
    addMedia(files.map(rememberPicked));
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setOver(false);
    if (!desktop) addMedia([...e.dataTransfer.files].map(rememberPicked));
  }

  async function chooseMusic() {
    if (!desktop) {
      musicInput.current?.click();
      return;
    }
    try {
      const path = await pickAudioNative();
      if (path) change((s) => ({ ...s, music: path }));
    } catch {
      setError(t("story.add.failed"));
    }
  }

  function onMusicInput(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f && AUDIO_EXTENSIONS.includes(f.name.split(".").pop()?.toLowerCase() ?? "")) change((s) => ({ ...s, music: rememberPicked(f) }));
  }

  async function make() {
    if (!story || starting || running) return;
    setStarting(true);
    setError(null);
    setShowError(false);
    try {
      if (!(await save())) return;
      const settings = getSettings();
      const out_dir = settings.outputMode === "folder" ? settings.outputFolder : undefined;
      const started = await engine.buildStory(story.id, out_dir ? { out_dir } : {});
      setJobId(started.id);
    } catch (e) {
      setError(message(e, t("story.make.failed")));
    } finally {
      setStarting(false);
    }
  }

  async function cancel() {
    if (!jobId) return;
    try {
      await engine.cancelJob(jobId);
    } catch (e) {
      setError(message(e, t("story.make.failed")));
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

  // a finished video becomes the story's latest one
  useEffect(() => {
    if (job?.state !== "done" || !story) return;
    const file = (job.result as StoryResult | null)?.files?.[0];
    if (file && story.output !== file) {
      const next = { ...story, output: file };
      latest.current = next;
      setStory(next);
    }
  }, [job?.state, job?.result, story]);

  if (missing) {
    return (
      <Screen bar={NO_STEPS} narrow>
        <h1 className="title">{t("story.title")}</h1>
        <p className="note-text" role="alert">{t("story.missing")}</p>
        <Link className="secondary inline" to="/">{t("story.home")}</Link>
      </Screen>
    );
  }

  if (!story) {
    return (
      <Screen bar={NO_STEPS} narrow>
        <h1 className="title">{t("story.title")}</h1>
        <p className="note-text">{t("story.loading")}</p>
      </Screen>
    );
  }

  const pct = Math.round(job?.pct ?? 0);
  const n = story.scenes.length;
  const output = story.output;
  const failed = job?.state === "failed";

  return (
    <Screen bar={NO_STEPS} narrow>
      <div className="story-head">
        <h1 className="title">{t("story.title")}</h1>
        <span className="story-saved" role="status">
          {saveState === "saving" && t("story.save.saving")}
          {saveState === "saved" && t("story.save.saved")}
        </span>
      </div>
      {saveError && <p className="error" role="alert">{saveError}</p>}

      <div className="story">
        <label className="story-name">
          <span>{t("story.name")}</span>
          <input
            className="ed-input"
            value={story.title}
            maxLength={80}
            placeholder={t("story.name.placeholder")}
            onChange={(e) => change((s) => ({ ...s, title: e.target.value }))}
          />
        </label>

        <section className="story-scenes" aria-labelledby="scenes-h">
          <h2 id="scenes-h">{t("story.scenes")}</h2>
          {n === 0 ? null : (
            <ol className="sc-list">
              {story.scenes.map((sc, i) => {
                const name = baseName(sc.media);
                return (
                  <li key={`${sc.media}-${i}`} className="sc">
                    <SceneThumb sid={story.id} scene={sc} version={story.updated} />
                    <div className="sc-body">
                      <div className="sc-top">
                        <span className="sc-num mono">{i + 1}</span>
                        <span className="sc-name mono" title={sc.media}>{name}</span>
                        <span className="sc-tools">
                          <button type="button" className="icon-btn" aria-label={t("story.scene.up", { n: i + 1 })} disabled={i === 0} onClick={() => move(i, -1)}>
                            <ArrowIcon up />
                          </button>
                          <button type="button" className="icon-btn" aria-label={t("story.scene.down", { n: i + 1 })} disabled={i === n - 1} onClick={() => move(i, 1)}>
                            <ArrowIcon />
                          </button>
                          <button type="button" className="icon-btn" aria-label={t("story.scene.remove", { n: i + 1 })}
                            onClick={() => change((s) => ({ ...s, scenes: s.scenes.filter((_, k) => k !== i) }))}>
                            <RemoveIcon />
                          </button>
                        </span>
                      </div>
                      {sc.keep_audio ? (
                        <p className="set-note">{t("story.scene.ownsound.note")}</p>
                      ) : (
                        <label className="sc-text">
                          <span className="sr-only">{t("story.scene.say", { n: i + 1 })}</span>
                          <textarea
                            rows={3}
                            maxLength={2000}
                            value={sc.text}
                            placeholder={t(sc.kind === "image" ? "story.scene.say.placeholder" : "story.scene.say.placeholder.clip")}
                            onChange={(e) => setScene(i, { text: e.target.value })}
                          />
                        </label>
                      )}
                      <div className="sc-opts">
                        {sc.kind === "image" ? (
                          <label className="sc-motion">
                            <span>{t("story.scene.motion")}</span>
                            <select value={sc.motion} onChange={(e) => setScene(i, { motion: e.target.value as StoryMotion })}>
                              {MOTIONS.map((m) => <option key={m} value={m}>{t(`story.motion.${m}`)}</option>)}
                            </select>
                          </label>
                        ) : (
                          <label className="sc-own">
                            <input type="checkbox" checked={sc.keep_audio} onChange={(e) => setScene(i, { keep_audio: e.target.checked })} />
                            <span>{t("story.scene.ownsound")}</span>
                          </label>
                        )}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
          <div
            className={`sc-drop${over ? " over" : ""}`}
            role="group"
            aria-label={t("story.add.label")}
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
          >
            <span>{t(n === 0 ? "story.add.empty" : "story.add.more")}</span>
            <button type="button" className="secondary" onClick={() => void chooseMedia()}>{t("story.add")}</button>
            <input ref={mediaInput} type="file" accept={STORY_ACCEPT} multiple hidden onChange={onMediaInput} />
          </div>
          {notice && <p className="set-note" role="status">{notice}</p>}
        </section>

        <section className="story-opts" aria-labelledby="look-h">
          <h2 id="look-h">{t("story.look")}</h2>
          <div className="fields">
            <Seg legend={t("story.shape")} name="aspect" keys={ASPECTS} prefix="story.shape" value={story.aspect}
              onChange={(aspect) => change((s) => ({ ...s, aspect }))} />
            <Seg legend={t("story.captions")} name="captions" keys={CAPTIONS} prefix="story.captions" value={story.captions}
              onChange={(captions) => change((s) => ({ ...s, captions }))} />
            <label className="field story-voice">
              <span className="legend">{t("story.voice")}</span>
              <select value={story.voice} onChange={(e) => change((s) => ({ ...s, voice: e.target.value }))}>
                {(voices.length ? voices : [{ id: story.voice, name: story.voice }]).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
              </select>
              <span className="set-note">{t("story.voice.note")}</span>
            </label>
            <div className="field story-music" role="group" aria-labelledby="music-l">
              <span className="legend" id="music-l">{t("story.music")}</span>
              {story.music ? (
                <>
                  <span className="sc-name mono" title={story.music}>{baseName(story.music)}</span>
                  <label className="story-volume">
                    <span>{t("story.music.volume")}</span>
                    <input type="range" min={0} max={0.5} step={0.01} value={story.music_volume}
                      onChange={(e) => change((s) => ({ ...s, music_volume: Number(e.target.value) }))} />
                  </label>
                  <div className="row">
                    <button type="button" className="secondary" onClick={() => void chooseMusic()}>{t("story.music.change")}</button>
                    <button type="button" className="secondary" onClick={() => change((s) => ({ ...s, music: null }))}>{t("story.music.remove")}</button>
                  </div>
                </>
              ) : (
                <div className="row">
                  <button type="button" className="secondary" onClick={() => void chooseMusic()}>{t("story.music.add")}</button>
                </div>
              )}
              <input ref={musicInput} type="file" accept={AUDIO_ACCEPT} hidden onChange={onMusicInput} />
            </div>
          </div>
        </section>

        <section className="story-make" aria-labelledby="make-h">
          <h2 id="make-h" className="sr-only">{t("story.make")}</h2>
          {running ? (
            <div className="run">
              <div className="run-head">
                <span className="eta">{t(job?.stage === "render" ? "story.stage.render" : "story.stage.voices")}</span>
                <span className="mono total">{pct}%</span>
              </div>
              <div className="meter" role="progressbar" aria-label={t("story.progress")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
                <i style={{ width: `${pct}%` }} />
              </div>
              <button type="button" className="secondary" onClick={() => void cancel()}>{t("story.cancel")}</button>
            </div>
          ) : (
            <>
              {failed && <p className="error" role="alert">{job?.error || t("story.make.failed")}</p>}
              {job?.state === "cancelled" && <p className="note-text" role="status">{t("story.cancelled")}</p>}
              {error && <p className="error" role="alert">{error}</p>}
              <div className="row">
                <button type="button" className="primary" disabled={n === 0 || starting} onClick={() => void make()}>
                  {starting ? t("story.make.starting") : t(output ? "story.make.again" : "story.make")}
                </button>
                {n === 0 && <span className="set-note">{t("story.make.needs")}</span>}
              </div>
              {output && (
                <div className="story-out" role="status">
                  <span>{t(job?.state === "done" ? "story.done" : "story.last")}</span>
                  <span className="sc-name mono" title={output}>{baseName(output)}</span>
                  <button type="button" className="secondary" disabled={!desktop} onClick={() => void show(output)}>{t("story.show")}</button>
                </div>
              )}
              {showError && <p className="error" role="alert">{t("story.show.error")}</p>}
            </>
          )}
        </section>
      </div>
    </Screen>
  );
}

const ICON = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round" } as const;

function ArrowIcon({ up }: { up?: boolean }) {
  return <svg {...ICON} aria-hidden="true"><path d={up ? "M12 19V5m-6 6 6-6 6 6" : "M12 5v14m6-6-6 6-6-6"} /></svg>;
}

function RemoveIcon() {
  return <svg {...ICON} aria-hidden="true"><path d="M5 7h14M10 11v6m4-6v6M9 7l1-3h4l1 3m-9 0 1 13h10l1-13" /></svg>;
}

function PhotoIcon() {
  return <svg {...ICON}><rect x="3" y="5" width="18" height="14" rx="2.5" /><circle cx="9" cy="10" r="1.6" /><path d="m4 17 5-5 4 4 3-3 4 4" /></svg>;
}

function ClipIcon() {
  return <svg {...ICON}><rect x="3" y="5" width="18" height="14" rx="2.5" /><path d="m10 9.5 4.5 2.5-4.5 2.5z" /></svg>;
}
