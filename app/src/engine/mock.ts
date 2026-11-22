// In-memory engine with the same interface as the real client. Seeded with the mockup's 12 clips and transcript;
// analyze and export run as timed jobs with progress events. Edit ops use simple word-range logic (Task 17 refines).

import {
  EngineError,
  type AnalyzeBody, type CaptionTrack, type ClipOp, type ClipView, type Doctor, type Edl, type EdlClip, type Engine,
  type ExportBody, type Health, type Highlight, type Job, type ModelStatus, type NewProjectBody, type Project,
  type ProjectClip, type RenderPlanEntry, type Transcript, type TranscriptWord,
} from "./types";

const CLIPS: [title: string, score: number, kept: boolean][] = [
  ["Nobody tells you this when you start", 94, true], ["The ₱500 mistake on my first gig", 88, true],
  ["Why I almost quit after a year", 84, true], ["My boss laughed at my portfolio", 81, true],
  ["Hot take: AI won't replace editors", 77, true], ["How I find clients in Cavite", 74, true],
  ["The one tool I pay for", 70, true], ["Taglish works better online", 66, true],
  ["Long story about my laptop", 52, false], ["Rambling about coffee", 41, false],
  ["Mic check and intro", 22, false], ["Outro and thank yous", 18, false],
];

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
  CLIPS.forEach((_, c) => {
    PASSAGE.forEach((text, i) => {
      const start = c * CLIP_SPAN_SEC + i * WORD_SEC;
      words.push({
        text, start: round(start), end: round(start + WORD_SEC - 0.04),
        filler: HARD_FILLERS.has(i) ? "hard" : SOFT_FILLERS.has(i) ? "soft" : null,
        speaker: i < SPEAKER_BREAK ? "S1" : "S2",
      });
    });
  });
  return { words, duration: CLIPS.length * CLIP_SPAN_SEC };
}

const round = (n: number): number => Math.round(n * 1000) / 1000;
const copy = <T>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

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
    case "fix_word": {
      const captions = (edl.captions ?? {}) as { replace?: Record<string, string> };
      return { ...edl, captions: { ...captions, replace: { ...captions.replace, [op.wrong]: op.right } } };
    }
    case "patch":
      return { ...edl, [op.section]: { ...((edl[op.section] as object | undefined) ?? {}), ...op.patch } };
    default:
      throw new EngineError(400, "bad_request", `unknown op ${(op as { op?: unknown }).op}`);
  }
}

function clipView(edl: Edl, tr: Transcript): ClipView {
  const ranges = wordRanges(edl);
  const replace = ((edl.captions ?? {}) as { replace?: Record<string, string> }).replace ?? {};
  const kept_ranges = ranges.map(([a, b]) => [tr.words[a]?.start ?? 0, tr.words[b]?.end ?? 0] as [number, number]);
  const spoken: { text: string; start: number; end: number; speaker: string | null }[] = [];
  let t = 0;
  for (const [a, b] of ranges) {
    for (let i = a; i <= b && i < tr.words.length; i++) {
      const w = tr.words[i];
      const dur = w.end - w.start;
      if (w.filler !== "hard") {
        spoken.push({ text: replace[w.text] ?? w.text, start: round(t), end: round(t + dur), speaker: w.speaker ?? null });
        t += dur;
      }
    }
  }
  const events = [];
  for (let i = 0; i < spoken.length; i += 3) {
    const g = spoken.slice(i, i + 3);
    const end = g[g.length - 1].end + 0.15;
    events.push({
      start: g[0].start, end,
      words: g.map((w, k) => ({
        ...w, hl_start: k === 0 ? g[0].start : w.start, hl_end: k + 1 < g.length ? g[k + 1].start : end,
      })),
    });
  }
  const captions: CaptionTrack = {
    style: "pop", font: "Arial", size_frac: 0.0479, baseline_frac: 0.24, uppercase: true, color: "#FFFFFF",
    highlight: "#FFD400", outline_frac: 0.0031, active_scale: 108, outline_color: "#000000", shadow: 1,
    back_color: null, back_alpha: null, speaker_colors: {}, events,
  };
  return { edl: copy(edl), kept_ranges, captions };
}

// ---- the engine -----------------------------------------------------------------------------------------------

export interface MockEngine extends Engine {
  /** Every job change, as the real engine's WebSocket would deliver it. Returns the unsubscribe. */
  onJob(listener: (job: Job) => void): () => void;
}

export function createMockEngine(opts: { tickMs?: number } = {}): MockEngine {
  const tickMs = opts.tickMs ?? 120;
  const TICKS = 4; // progress steps per stage
  const transcript = seedTranscript();
  const edls = new Map<string, Edl>(); // `${pid}/${hid}`
  const projects = new Map<string, Project>();
  const jobs = new Map<string, Job>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const listeners = new Set<(job: Job) => void>();
  const models: ModelStatus = { small: { installed: true, size_mb: 484 }, "large-v3": { installed: false, size_mb: 3090 } };
  let seq = 0;

  const notFound = (what: string) => new EngineError(404, "not_found", `not found: ${what}`);

  function seedClips(pid: string): ProjectClip[] {
    return CLIPS.map(([title, score, kept], i) => {
      const w0 = i * PASSAGE_LEN, w1 = w0 + PASSAGE_LEN - 1;
      const id = `h${String(i + 1).padStart(2, "0")}`;
      const highlight: Highlight = {
        id, start: transcript.words[w0].start, end: transcript.words[w1].end, w0, w1, score, title,
        features: { hook: score / 100 },
      };
      let edl: Edl = { clips: [{ src: "main", words: [w0, w1] }], output: { aspect: "9:16", fps: 30 }, captions: { style: "pop" } };
      for (const f of HARD_FILLERS) edl = applyOp(edl, { op: "cut", w0: w0 + f, w1: w0 + f });
      edls.set(`${pid}/${id}`, edl);
      return { highlight, kept, edl_file: `clip-${id}.edl.json` };
    });
  }

  function newProject(source: string, language: Project["language"], platform: Project["platform"], withClips: boolean): Project {
    const id = `mock-${++seq}`;
    const p: Project = {
      id, type: "clip", source, language, platform, created: new Date().toISOString(), clips: [], settings: {}, last_job: null,
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
    for (const l of [...listeners]) l(snapshot(job));
  }

  function run(job: Job, stages: [name: string, share: number][], finish: (job: Job) => void): Job {
    emit(job);
    let s = 0, tick = 0, done = 0;
    const step = (): void => {
      const cur = jobs.get(job.id)!;
      if (cur.state === "cancelled") return;
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

  return {
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
      return snapshot(newProject(body.source, body.language, body.platform, false));
    },
    async getProject(pid) { return snapshot(project(pid)); },
    async analyze(pid, _body?: AnalyzeBody) {
      const p = project(pid); // the AI key in the body is deliberately never read or stored
      const stages: [string, number][] = [["proxy", 0.1], ["analyze", 0.6], ["highlights", 0.15], ["framing", 0.15]];
      return run(newJob(pid, "analyze"), stages, (job) => {
        const had = p.clips.length;
        if (!had) {
          p.clips = seedClips(pid);
          p.settings = { workdir: "C:/mock/work", proxy: p.source, filmstrip: "C:/mock/work/filmstrip.json" };
        }
        job.result = { clips: p.clips.length, new: p.clips.length - had, reason: null };
      });
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
      const plan: { stage: string; hid: string; aspect: string; path: string }[] = [];
      const n = body.clips.length * formats.length;
      body.clips.forEach((hid, i) => formats.forEach((aspect) => {
        const title = p.clips.find((c) => c.highlight.id === hid)!.highlight.title;
        const name = `${String(i + 1).padStart(2, "0")} ${title} (${aspect.replace(":", "x")}).mp4`;
        plan.push({ stage: `render ${plan.length + 1}/${n}`, hid, aspect, path: `C:/Videos/vedit exports/${name}` });
      }));
      const renders: RenderPlanEntry[] = plan.map((e) => ({ ...e }));
      const job = newJob(pid, "export");
      job.result = { renders, files: [] };
      return run(job, plan.map((e) => [e.stage, 1 / n] as [string, number]), (j) => {
        j.result = { renders, files: renders.map((r) => r.path) };
      });
    },
    async getJob(id) {
      const j = jobs.get(id);
      if (!j) throw notFound(`job ${id}`);
      return snapshot(j);
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
  };
}

export const mockEngine: MockEngine = createMockEngine();
