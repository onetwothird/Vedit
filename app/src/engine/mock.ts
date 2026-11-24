// In-memory engine with the same interface as the real client. Seeded with the mockup's 12 clips and transcript;
// analyze and export run as timed jobs with progress events. Edit ops use simple word-range logic (Task 17 refines).

import {
  EngineError,
  type AnalyzeBody, type CaptionTrack, type ClipOp, type ClipView, type Doctor, type Edl, type EdlClip, type Engine,
  type AiTestResult, type Batch, type BatchBody, type Brand, type ExportBody, type Story, type StoryBody, type StoryScene, type FilmstripInfo, type Health, type Highlight, type Job, type ModelStatus, type NewProjectBody, type Project, type ProjectType,
  type ProjectClip, type RenderPlanEntry, type Transcript, type TranscriptWord,
} from "./types";
import { spelledAs } from "../edit/spelling";
import { fixWord, patchSection } from "../edit/timeline";
import { defaultOutputFolder, joinPath } from "../lib/outputFolder";

const CLIPS: [title: string, score: number, kept: boolean][] = [
  ["Nobody tells you this when you start", 94, true], ["The ₱500 mistake on my first gig", 88, true],
  ["Why I almost quit after a year", 84, true], ["My boss laughed at my portfolio", 81, true],
  ["Hot take: AI won't replace editors", 77, true], ["How I find clients in Cavite", 74, true],
  ["The one tool I pay for", 70, true], ["Taglish works better online", 66, true],
  ["Long story about my laptop", 52, false], ["Rambling about coffee", 41, false],
  ["Mic check and intro", 22, false], ["Outro and thank yous", 18, false],
];

// What "Find more clips" adds: the next six, found when analyze runs again with a larger max_clips.
const EXTRA_CLIPS: [title: string, score: number, kept: boolean][] = [
  ["What I charge now versus then", 64, true], ["The client who ghosted me", 61, true],
  ["Three mistakes in my first edit", 58, true], ["Why I film in the morning", 55, true],
  ["A tip about audio nobody uses", 49, true], ["Closing thoughts on freelancing", 44, true],
];
const ALL_CLIPS = [...CLIPS, ...EXTRA_CLIPS];

// The mockup's passage; each clip gets its own copy so every clip has words of its own.
const PASSAGE = ("Nobody um tells you this when you start. The first year is uh, like, brutal. "
  + "I lost ₱500 on my first gig and you know I almost quit. But I kept going.").split(" ");
const PASSAGE_LEN = PASSAGE.length; // 32
const SPEAKER_BREAK = 15; // "I lost ..." is the second speaker
const WORD_SEC = 0.42;
const CLIP_SPAN_SEC = 30;
const HARD_FILLERS = new Set([1, 12]); // um, uh
const SOFT_FILLERS = new Set([13, 23, 24]); // like, you know

function seedTranscript(): Transcript {
  const words: TranscriptWord[] = [];
  ALL_CLIPS.forEach((_, c) => {
    PASSAGE.forEach((text, i) => {
      const start = c * CLIP_SPAN_SEC + i * WORD_SEC;
      words.push({
        text, start: round(start), end: round(start + WORD_SEC - 0.04),
        filler: HARD_FILLERS.has(i) ? "hard" : SOFT_FILLERS.has(i) ? "soft" : null,
        speaker: i < SPEAKER_BREAK ? "S1" : "S2",
      });
    });
  });
  return { words, duration: ALL_CLIPS.length * CLIP_SPAN_SEC };
}

const round = (n: number): number => Math.round(n * 1000) / 1000;
const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

// Real thumbnails: public/mock/filmstrip.jpg is 120 frames (192x108, 20x6 grid, one per 5 s) cut from the
// Pexels podcast clip, one camera angle per 30 s clip (wide, then each speaker close up).
function mockFilmstrip(): FilmstripInfo {
  return { every: 5, width: 192, height: 108, count: 120, cols: 20, rows: 6, image: "/mock/filmstrip.jpg" };
}

// ---- word-range edit logic ------------------------------------------------------------------------------------

type Range = [number, number];

const wordRanges = (edl: Edl): Range[] => edl.clips.filter((c) => c.words).map((c) => [...c.words!] as Range);
const otherClips = (edl: Edl): EdlClip[] => edl.clips.filter((c) => !c.words);

function withRanges(edl: Edl, ranges: Range[]): Edl {
  const sorted = ranges.filter(([a, b]) => a <= b).sort((x, y) => x[0] - y[0]);
  const merged: Range[] = [];
  for (const r of sorted) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1] + 1) last[1] = Math.max(last[1], r[1]);
    else merged.push([r[0], r[1]]);
  }
  return { ...edl, clips: [...otherClips(edl), ...merged.map((words) => ({ src: "main", words }))] };
}

function checkRange(a: number, b: number): void {
  if (!Number.isInteger(a) || !Number.isInteger(b) || a < 0 || b < a) {
    throw new EngineError(400, "bad_request", "invalid word range");
  }
}

function applyOp(edl: Edl, op: ClipOp): Edl {
  switch (op.op) {
    case "cut": {
      checkRange(op.w0, op.w1);
      const out: Range[] = [];
      for (const [a, b] of wordRanges(edl)) {
        if (b < op.w0 || a > op.w1) out.push([a, b]);
        else {
          if (a < op.w0) out.push([a, op.w0 - 1]);
          if (b > op.w1) out.push([op.w1 + 1, b]);
        }
      }
      return withRanges(edl, out);
    }
    case "restore":
      checkRange(op.w0, op.w1);
      return withRanges(edl, [...wordRanges(edl), [op.w0, op.w1]]);
    case "trim": {
      checkRange(op.w_first, op.w_last);
      return withRanges(edl, wordRanges(edl).map(([a, b]) => [Math.max(a, op.w_first), Math.min(b, op.w_last)] as Range));
    }
    case "fix_word":
      return fixWord(edl, op.wrong, op.right); // right === wrong removes the replacement
    case "patch":
      try {
        return patchSection(edl, op.section, op.patch); // a null value removes the key
      } catch (e) {
        throw new EngineError(400, "bad_request", e instanceof Error ? e.message : "bad patch");
      }
    default:
      throw new EngineError(400, "bad_request", `unknown op ${(op as { op?: unknown }).op}`);
  }
}

const SAMPLE_SEC = 12; // public/mock/sample.mp4 is this long; every mock clip plays from its start

/** The caption look and grouping the edl asks for, as scripts/captions.py _plan reads edl.captions. */
function captionTrack(edl: Edl, spoken: { text: string; start: number; end: number; speaker: string | null }[]): CaptionTrack {
  const cfg: Record<string, unknown> = { style: "pop", hide_fillers: true, ...((edl.captions ?? {}) as Record<string, unknown>) };
  const style = String(cfg.style);
  const clean = style === "clean";
  const box = style === "box";
  const aspect = String((edl.output as { aspect?: string } | undefined)?.aspect ?? "9:16");
  const vertical = aspect === "9:16" || aspect === "4:5";
  const maxWords = Number(cfg.max_words ?? (clean ? 10 : 3));
  const pos = cfg.position ?? (clean ? "bottom" : "lower");
  const baseline = typeof pos === "number" ? pos : ({ bottom: 0.07, lower: vertical ? 0.24 : 0.14, middle: 0.45 } as Record<string, number>)[String(pos)] ?? 0.14;
  const events = [];
  for (let i = 0; i < spoken.length; i += maxWords) {
    const g = spoken.slice(i, i + maxWords);
    const end = g[g.length - 1].end + 0.15;
    events.push({
      start: g[0].start, end,
      words: g.map((w, k) => ({
        ...w, hl_start: k === 0 ? g[0].start : w.start, hl_end: k + 1 < g.length ? g[k + 1].start : end,
      })),
    });
  }
  return {
    style, font: "Arial", size_frac: clean ? 0.0327 : 0.0479, baseline_frac: baseline,
    uppercase: typeof cfg.uppercase === "boolean" ? cfg.uppercase : !clean, color: String(cfg.color ?? "#FFFFFF"),
    highlight: String(cfg.highlight ?? "#FFD400"), outline_frac: 0.0031, active_scale: 108, outline_color: "#000000",
    shadow: box ? 0 : 1, back_color: box ? "#000000" : null, back_alpha: box ? 0.5608 : null, speaker_colors: {}, events,
  };
}

function clipView(edl: Edl, tr: Transcript): ClipView {
  const ranges = wordRanges(edl);
  // the mock source is one short sample, so a clip's times are made relative to its first word and held inside it
  const base = ranges.length ? Math.floor(ranges[0][0] / PASSAGE_LEN) * CLIP_SPAN_SEC : 0;
  const replace = ((edl.captions ?? {}) as { replace?: Record<string, string> }).replace;
  const hideFillers = ((edl.captions ?? {}) as { hide_fillers?: boolean }).hide_fillers !== false;
  const mapped = ranges
    .map(([a, b]) => [(tr.words[a]?.start ?? 0) - base, Math.min((tr.words[b]?.end ?? 0) - base, SAMPLE_SEC)] as [number, number]);
  const kept_ranges = mapped.filter(([a, b]) => b > a);
  // caption times follow the output: each range starts where the one before it ended
  const spoken: { text: string; start: number; end: number; speaker: string | null }[] = [];
  let t = 0;
  ranges.forEach(([a, b], r) => {
    const kr = mapped[r];
    if (kr[1] <= kr[0]) return; // past the end of the sample
    const s0 = kr[0] + base;
    for (let i = a; i <= b && i < tr.words.length; i++) {
      const w = tr.words[i];
      if (w.start - base >= SAMPLE_SEC) break;
      if (hideFillers && w.filler === "hard") continue;
      spoken.push({ text: spelledAs(w.text, replace), start: round(t + w.start - s0), end: round(t + w.end - s0), speaker: w.speaker ?? null });
    }
    t += kr[1] - kr[0];
  });
  return { edl: copy(edl), kept_ranges, captions: captionTrack(edl, spoken), sample_offset: base };
}

// ---- the engine -----------------------------------------------------------------------------------------------

export interface MockEngine extends Engine {
  /** Every job change, as the real engine's WebSocket would deliver it. Returns the unsubscribe. */
  onJob(listener: (job: Job) => void): () => void;
}

export function createMockEngine(opts: { tickMs?: number; persist?: Pick<Storage, "getItem" | "setItem"> } = {}): MockEngine {
  const tickMs = opts.tickMs ?? 120;
  // clip edits outlive a page reload, as the real engine's edit files do (the app's mock passes sessionStorage)
  const SAVED = "vedit.mock.edls";
  const saved = (): Record<string, Edl> => {
    try {
      return JSON.parse(opts.persist?.getItem(SAVED) ?? "{}") as Record<string, Edl>;
    } catch {
      return {};
    }
  };
  const save = (key: string, edl: Edl): void => {
    try {
      opts.persist?.setItem(SAVED, JSON.stringify({ ...saved(), [key]: edl }));
    } catch {
      /* storage full or blocked: edits last until the reload */
    }
  };
  const TICKS = 4; // progress steps per stage
  const transcript = seedTranscript();
  const edls = new Map<string, Edl>(); // `${pid}/${hid}`
  const projects = new Map<string, Project>();
  const jobs = new Map<string, Job>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const listeners = new Set<(job: Job) => void>();
  const models: ModelStatus = { small: { installed: true, size_mb: 484 }, "large-v3": { installed: false, size_mb: 3090 } };
  let brand: Brand = { font: "", caption_color: "", highlight: "", logo: "", intro: "", outro: "", music: "" };
  const failedOnce = new Set<string>(); // projects whose first analyze already failed on purpose
  // stories outlive a reload too, as the real engine's story files do
  const STORIES = "vedit.mock.stories";
  const stories = new Map<string, Story>((() => {
    try {
      return Object.entries(JSON.parse(opts.persist?.getItem(STORIES) ?? "{}") as Record<string, Story>);
    } catch {
      return [];
    }
  })());
  const keepStories = (): void => {
    try {
      opts.persist?.setItem(STORIES, JSON.stringify(Object.fromEntries(stories)));
    } catch {
      /* storage full or blocked */
    }
  };
  const batches = new Map<string, { id: string; created: string; type: ProjectType; export: boolean; items: { project: string; source: string; analyze: string }[] }>();
  const autoExport = new Map<string, { body: Omit<ExportBody, "clips">; job?: string; error?: string }>(); // batch projects
  let seq = 0;

  const notFound = (what: string) => new EngineError(404, "not_found", `not found: ${what}`);

  /** Steady, made-up scores per clip, shaped like the engine's (0..1 each), so Pick clips has reasons to show. */
  const mockFeatures = (i: number, score: number): Record<string, number> => {
    const f = (k: number) => Math.round(((Math.sin(i * 2.3 + k) + 1) / 2) * 100) / 100;
    return { hook: score / 100, energy: f(1), laughter: f(2), direct: f(3), complete: f(4), face: 0.5 };
  };

  /** Clips `from` up to (not including) `to` of the full list, with an edit file each. */
  function seedClips(pid: string, from = 0, to = CLIPS.length): ProjectClip[] {
    return ALL_CLIPS.slice(from, to).map(([title, score, kept], k) => {
      const i = from + k;
      const w0 = i * PASSAGE_LEN, w1 = w0 + PASSAGE_LEN - 1;
      const id = `h${String(i + 1).padStart(2, "0")}`;
      const highlight: Highlight = {
        id, start: transcript.words[w0].start, end: transcript.words[w1].end, w0, w1, score, title,
        features: mockFeatures(i, score),
      };
      let edl: Edl = { clips: [{ src: "main", words: [w0, w1] }], output: { aspect: "9:16", fps: 30 }, captions: { style: "pop" } };
      for (const f of HARD_FILLERS) edl = applyOp(edl, { op: "cut", w0: w0 + f, w1: w0 + f });
      edls.set(`${pid}/${id}`, saved()[`${pid}/${id}`] ?? edl);
      return { highlight, kept, edl_file: `clip-${id}.edl.json` };
    });
  }

  /** A vlog or Quick Reel project: the whole sample as one clip, cut with the preset (16:9 or vertical). */
  function wholeClip(pid: string, type: "vlog" | "reel", source: string): ProjectClip {
    const w1 = transcript.words.length - 1;
    const highlight: Highlight = {
      id: "h01", start: transcript.words[0].start, end: transcript.words[w1].end, w0: 0, w1, score: 100,
      title: (source.split(/[\\/]/).pop() ?? "").replace(/\.[^.]+$/, "") || "Video", features: {},
    };
    let edl: Edl = {
      clips: [{ src: "main", words: [0, w1] }],
      output: { aspect: type === "vlog" ? "16:9" : "9:16", fps: 30 },
      captions: { style: type === "vlog" ? "clean" : "pop" },
    };
    for (let i = 0; i <= w1; i++) if (HARD_FILLERS.has(i % PASSAGE_LEN)) edl = applyOp(edl, { op: "cut", w0: i, w1: i });
    edls.set(`${pid}/h01`, saved()[`${pid}/h01`] ?? edl);
    return { highlight, kept: true, edl_file: `${type}-h01.edl.json` };
  }

  function newProject(source: string, language: Project["language"], platform: Project["platform"], withClips: boolean,
                      type: ProjectType = "clip"): Project {
    const id = `mock-${++seq}`;
    const p: Project = {
      id, type, source, language, platform, created: new Date().toISOString(), clips: [], settings: {}, last_job: null,
    };
    projects.set(id, p);
    if (withClips) {
      p.clips = seedClips(id);
      p.settings = { workdir: "C:/mock/work", proxy: source, filmstrip: "C:/mock/work/filmstrip.json" };
    }
    return p;
  }

  newProject("C:/Videos/podcast-ep12.mp4", "taglish", "reels", true);

  const project = (pid: string): Project => projects.get(pid) ?? (() => { throw notFound(`project ${pid}`); })();
  const snapshot = <T>(x: T): T => copy(x);

  function emit(job: Job): void {
    jobs.set(job.id, job);
    const p = projects.get(job.project_id);
    if (p) p.last_job = snapshot(job);
    const st = stories.get(job.project_id);
    if (st) {
      st.last_job = snapshot(job);
      keepStories();
    }
    for (const l of [...listeners]) l(snapshot(job));
  }

  function run(
    job: Job, stages: [name: string, share: number][], finish: (job: Job) => void,
    failure?: { afterTicks: number; error: string; result?: Job["result"] },
  ): Job {
    emit(job);
    let s = 0, tick = 0, done = 0, total = 0;
    const step = (): void => {
      const cur = jobs.get(job.id)!;
      if (cur.state === "cancelled") return;
      if (failure && total >= failure.afterTicks) {
        emit({ ...cur, state: "failed", error: failure.error, message: "", ...(failure.result ? { result: failure.result } : {}) });
        timers.delete(job.id);
        return;
      }
      total++;
      if (s >= stages.length) {
        finish(cur);
        emit({ ...cur, state: "done", pct: 100, message: "" });
        timers.delete(job.id);
        return;
      }
      const [name, share] = stages[s];
      tick++;
      emit({ ...cur, state: "running", stage: name, pct: round((done + (share * tick) / TICKS) * 100), message: name });
      if (tick >= TICKS) { done += share; s++; tick = 0; }
      timers.set(job.id, setTimeout(step, tickMs));
    };
    timers.set(job.id, setTimeout(step, tickMs));
    return snapshot(job);
  }

  const newJob = (project_id: string, kind: Job["kind"]): Job =>
    ({ id: `job-${++seq}`, project_id, kind, state: "queued", stage: "", pct: 0, message: "", error: null, result: null });

  const active = (): boolean => [...jobs.values()].some((j) => j.state === "queued" || j.state === "running");

  const VOICES = [
    { id: "en-US-AndrewNeural", name: "Andrew (US English)" }, { id: "en-US-AvaNeural", name: "Ava (US English)" },
    { id: "en-GB-RyanNeural", name: "Ryan (UK English)" }, { id: "en-GB-SoniaNeural", name: "Sonia (UK English)" },
    { id: "fil-PH-AngeloNeural", name: "Angelo (Filipino)" }, { id: "fil-PH-BlessicaNeural", name: "Blessica (Filipino)" },
  ];
  const IMAGE = ["jpg", "jpeg", "png", "webp", "bmp"];
  const VIDEO = ["mp4", "mov", "m4v", "mkv", "avi", "webm", "mts", "3gp"];
  const ext = (path: string) => path.split(".").pop()?.toLowerCase() ?? "";
  const fileName = (path: string) => path.split(/[\\/]/).pop() ?? path;
  const bad = (detail: string) => new EngineError(400, "bad_request", detail);
  const story = (sid: string): Story => stories.get(sid) ?? (() => { throw notFound(`story ${sid}`); })();

  function cleanStory(old: Story, body: StoryBody): Story {
    const next = { ...old, ...body, scenes: old.scenes } as Story;
    if (next.title.length > 80) throw bad("the title can be at most 80 characters");
    if (!["9:16", "16:9", "1:1"].includes(next.aspect)) throw bad(`unsupported shape: ${next.aspect}`);
    if (!VOICES.some((v) => v.id === next.voice)) throw bad(`unsupported voice: ${next.voice}`);
    if (!["pop", "box", "clean", "none"].includes(next.captions)) throw bad(`unsupported caption style: ${next.captions}`);
    if (next.music !== null && !["mp3", "m4a", "aac", "wav", "flac", "ogg", "opus"].includes(ext(next.music))) throw bad("music must be an audio file");
    if (body.scenes) {
      next.scenes = body.scenes.map((sc, i): StoryScene => {
        const kind = IMAGE.includes(ext(sc.media)) ? "image" : VIDEO.includes(ext(sc.media)) ? "video" : null;
        if (!kind) throw bad(`scene ${i + 1}: ${fileName(sc.media)} is not a photo or a video`);
        return { media: sc.media, kind, text: sc.text ?? "", motion: sc.motion ?? "auto", keep_audio: !!sc.keep_audio && kind === "video" };
      });
    }
    return { ...next, updated: new Date().toISOString() };
  }

  // a batch video exports its kept clips once its analysis is done
  listeners.add((job) => {
    const auto = autoExport.get(job.project_id);
    if (!auto || auto.job || auto.error || job.kind !== "analyze" || job.state !== "done") return;
    const kept = project(job.project_id).clips.filter((c) => c.kept).map((c) => c.highlight.id);
    if (!kept.length) {
      auto.error = "no clips were found in this video";
      return;
    }
    auto.job = "pending";
    void api.exportClips(job.project_id, { ...auto.body, clips: kept }).then((j) => { auto.job = j.id; });
  });

  function batchView(bid: string): Batch {
    const b = batches.get(bid);
    if (!b) throw notFound(`batch ${bid}`);
    return snapshot({
      ...b, items: b.items.map((it) => {
        const auto = autoExport.get(it.project);
        const job = !auto?.job || auto.job === "pending" ? null : auto.job;
        return { ...it, project: snapshot(project(it.project)), export: job, error: auto?.error ?? null };
      }),
    });
  }

  const api: MockEngine = {
    async stories() { return [...stories.values()].sort((a, b) => b.updated.localeCompare(a.updated)).map(snapshot); },
    async storyVoices() { return snapshot(VOICES); },
    async createStory(title = "") {
      const now = new Date().toISOString();
      const st: Story = {
        id: `s${String(Date.now() % 1e12).padStart(12, "0")}`, title: title.trim(), aspect: "9:16", voice: "en-US-AndrewNeural", captions: "pop",
        music: null, music_volume: 0.15, scenes: [], created: now, updated: now, output: null, last_job: null,
      };
      stories.set(st.id, st);
      keepStories();
      return snapshot(st);
    },
    async getStory(sid) { return snapshot(story(sid)); },
    async saveStory(sid, body) {
      const next = cleanStory(story(sid), body);
      stories.set(sid, next);
      keepStories();
      return snapshot(next);
    },
    async buildStory(sid, body) {
      const st = story(sid);
      if (!st.scenes.length) throw bad("add at least one photo or clip first");
      // test trigger: a scene file name containing "missing" was moved or deleted
      const gone = st.scenes.findIndex((sc) => /missing/i.test(sc.media));
      if (gone >= 0) throw bad(`scene ${gone + 1}: ${fileName(st.scenes[gone].media)} was moved or deleted`);
      const name = `${st.title.replace(/[<>:"/\\|?*]/g, "").trim() || "Story"}${st.aspect === "9:16" ? "" : ` (${st.aspect.replace(":", "x")})`}.mp4`;
      const out = joinPath(body?.out_dir ?? "C:/Users/you/Videos/Vedit", name);
      // test trigger: a title containing "offline" fails as a voice with no internet would
      const failure = /offline/i.test(st.title)
        ? { afterTicks: 3, error: "Couldn't reach the voice service. Check your internet connection and try again.", result: { files: [] } }
        : undefined;
      return run(newJob(sid, "story"), [["voices", 0.25], ["render", 0.75]], (j) => {
        j.result = { files: [out] };
        st.output = out;
        keepStories();
      }, failure);
    },
    async createBatch(body: BatchBody) {
      if (!body.sources.length) throw new EngineError(422, "invalid_body", "pick at least one video");
      if (new Set(body.sources).size !== body.sources.length) throw bad("the same video is in the batch twice");
      if (body.export && !body.formats.length) throw bad("pick at least one format");
      const bid = `b${String(++seq).padStart(12, "0")}`;
      const items = [];
      for (const source of body.sources) {
        const p = await api.createProject({ source, language: body.language, platform: body.platform, type: body.type });
        if (body.export) {
          autoExport.set(p.id, { body: { formats: body.formats, quality: body.quality, out_dir: body.out_dir, srt: body.srt, thumbnail: body.thumbnail } });
        }
        const job = await api.analyze(p.id, { max_clips: body.max_clips });
        items.push({ project: p.id, source, analyze: job.id });
      }
      batches.set(bid, { id: bid, created: new Date().toISOString(), type: body.type, export: body.export, items });
      return batchView(bid);
    },
    async getBatch(bid) { return batchView(bid); },
    onJob(listener) {
      listeners.add(listener);
      for (const j of jobs.values()) if (j.state === "queued" || j.state === "running") listener(snapshot(j));
      return () => void listeners.delete(listener);
    },
    async health(): Promise<Health> { return { ok: true, version: "mock" }; },
    async doctor(): Promise<Doctor> { return { ffmpeg: true, libass: true, models: snapshot(models) }; },
    async recent() { return [...projects.values()].map(snapshot); },
    async createProject(body: NewProjectBody) {
      if (!body.source) throw new EngineError(400, "bad_request", `source video not found: ${body.source}`);
      const type = body.type ?? "clip";
      const allowed: Record<ProjectType, Project["platform"][]> = {
        clip: ["reels", "shorts", "tiktok"], reel: ["reels", "shorts", "tiktok"], vlog: ["youtube"],
      };
      if (!allowed[type].includes(body.platform)) {
        throw new EngineError(400, "bad_request", `unsupported platform for a ${type} project: ${body.platform}`);
      }
      return snapshot(newProject(body.source, body.language, body.platform, false, type));
    },
    async getProject(pid) { return snapshot(project(pid)); },
    async filmstrip(pid) {
      const p = project(pid);
      if (!p.clips.length) throw new EngineError(404, "no_filmstrip", "this project has no thumbnails yet");
      return mockFilmstrip();
    },
    async analyze(pid, body?: AnalyzeBody) {
      const p = project(pid); // the AI key in the body is deliberately never read or stored
      const whole = p.type !== "clip";
      const stages: [string, number][] = whole
        ? [["proxy", 0.15], ["analyze", 0.85]]
        : [["proxy", 0.1], ["analyze", 0.6], ["highlights", 0.15], ["framing", 0.15]];
      // test trigger: a source file name containing "fail" makes the first analyze of that project fail halfway
      const failing = /fail/i.test(p.source) && !failedOnce.has(pid);
      if (failing) failedOnce.add(pid);
      return run(newJob(pid, "analyze"), stages, (job) => {
        const had = p.clips.length;
        // test trigger: a source file name containing "silent" has no speech to find clips in
        if (!had && /silent/i.test(p.source)) {
          job.result = { clips: 0, new: 0, reason: "no_speech" };
          return;
        }
        if (whole) {
          if (!had) p.clips = [wholeClip(pid, p.type as "vlog" | "reel", p.source)];
          p.settings = { workdir: "C:/mock/work", proxy: p.source, filmstrip: "C:/mock/work/filmstrip.json" };
          job.result = { clips: 1, new: had ? 0 : 1, reason: null };
          return;
        }
        if (!had) {
          p.clips = seedClips(pid);
          p.settings = { workdir: "C:/mock/work", proxy: p.source, filmstrip: "C:/mock/work/filmstrip.json" };
        } else if (body?.max_clips !== undefined && body.max_clips > had && had < ALL_CLIPS.length) {
          // a larger max_clips finds six more; the existing clips and their edits stay
          p.clips.push(...seedClips(pid, had, Math.min(had + 6, ALL_CLIPS.length)));
        }
        job.result = { clips: p.clips.length, new: p.clips.length - had, reason: null };
      }, failing ? { afterTicks: 8, error: "Couldn't read the audio track in this file." } : undefined);
    },
    async transcript(pid) {
      const p = project(pid);
      if (!p.clips.length) throw notFound("transcript");
      return snapshot(transcript);
    },
    async clip(pid, hid) {
      project(pid);
      const edl = edls.get(`${pid}/${hid}`);
      if (!edl) throw notFound(`clip ${hid}`);
      return clipView(edl, transcript);
    },
    async patchClip(pid, hid, op) {
      project(pid);
      const key = `${pid}/${hid}`;
      const edl = edls.get(key);
      if (!edl) throw notFound(`clip ${hid}`);
      const next = applyOp(edl, op);
      if (!wordRanges(next).length && !otherClips(next).length) {
        throw new EngineError(400, "bad_request", "the edit would have no speech left");
      }
      edls.set(key, next);
      save(key, next);
      return clipView(next, transcript);
    },
    async setKept(pid, hid, kept) {
      const p = project(pid);
      const c = p.clips.find((x) => x.highlight.id === hid);
      if (!c) throw notFound(`clip ${hid}`);
      c.kept = kept;
      return snapshot(p);
    },
    async exportClips(pid, body: ExportBody) {
      const p = project(pid);
      const formats = body.formats ?? ["9:16"];
      if (!body.clips.length || !formats.length) throw new EngineError(400, "bad_request", "export needs at least one clip and one format");
      for (const hid of body.clips) if (!p.clips.some((c) => c.highlight.id === hid)) throw notFound(`clip ${hid}`);
      const plan: RenderPlanEntry[] = [];
      const n = body.clips.length * formats.length;
      const dir = body.out_dir ?? defaultOutputFolder(p.source);
      const first = body.first_number ?? 1;
      body.clips.forEach((hid, i) => formats.forEach((aspect) => {
        const title = p.clips.find((c) => c.highlight.id === hid)!.highlight.title;
        const name = `${String(first + i).padStart(2, "0")} ${title}${aspect === "9:16" ? "" : ` (${aspect.replace(":", "x")})`}.mp4`;
        plan.push({ stage: `render ${plan.length + 1}/${n}`, hid, aspect, path: joinPath(dir, name) });
      }));
      const renders: RenderPlanEntry[] = plan.map((e) => ({ ...e }));
      const withExtras = (list: RenderPlanEntry[]): string[] => list.flatMap((r) => [
        r.path, ...(body.srt === false ? [] : [r.path.replace(/\.mp4$/, ".srt")]), ...(body.thumbnail ? [r.path.replace(/\.mp4$/, ".jpg")] : []),
      ]);
      const job = newJob(pid, "export");
      job.result = { renders, files: [] };
      // test trigger: window.__veditMockFail = ["h02"] makes the next export of that clip fail once, as a bad render would
      const doomed = import.meta.env.VITE_ENGINE === "mock" ? (globalThis as { __veditMockFail?: string[] }).__veditMockFail : undefined;
      const bad = doomed?.find((h) => body.clips.includes(h));
      if (bad) doomed!.splice(doomed!.indexOf(bad), 1);
      const failure = bad
        ? { afterTicks: 2, error: "The render stopped: ffmpeg could not read this part of the video.", result: { renders, files: [], failed: { hid: bad, aspect: formats[0] } } }
        : undefined;
      return run(job, plan.map((e) => [e.stage, 1 / n] as [string, number]), (j) => {
        j.result = { renders, files: withExtras(renders) };
      }, failure);
    },
    async getJob(id) {
      const j = jobs.get(id);
      if (!j) throw notFound(`job ${id}`);
      return snapshot(j);
    },
    async activeJobs(project, kind) {
      return [...jobs.values()]
        .filter((j) => (j.state === "queued" || j.state === "running") && (!project || j.project_id === project) && (!kind || j.kind === kind))
        .map(snapshot);
    },
    async cancelJob(id) {
      const j = jobs.get(id);
      if (!j) throw notFound(`job ${id}`);
      if (j.state === "queued" || j.state === "running") {
        clearTimeout(timers.get(id));
        timers.delete(id);
        emit({ ...j, state: "cancelled", message: "" });
      }
      return snapshot(jobs.get(id)!);
    },
    async models() { return snapshot(models); },
    async downloadModel(name) {
      if (!(name in models)) throw notFound(`model ${name}`);
      return run(newJob("", "model"), [["model", 1]], () => { models[name].installed = true; });
    },
    async deleteModel(name) {
      if (!(name in models)) throw notFound(`model ${name}`);
      if (active()) throw new EngineError(409, "busy", "a job is running; try again when it finishes");
      models[name].installed = false;
      return snapshot(models);
    },
    // the mock accepts any key that starts with "sk-"
    async testAi(body): Promise<AiTestResult> {
      return body.api_key.startsWith("sk-") ? { ok: true } : { ok: false, error: "rejected" };
    },
    async brand() { return { ...brand }; },
    async saveBrand(next) { brand = { ...next }; return { ...brand }; },
  };
  return api;
}

const sessionStore = (): Storage | undefined => {
  try {
    return typeof sessionStorage === "undefined" ? undefined : sessionStorage;
  } catch {
    return undefined;
  }
};

export const mockEngine: MockEngine = createMockEngine({ persist: sessionStore() });
