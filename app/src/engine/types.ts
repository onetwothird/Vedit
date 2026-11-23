// Types mirroring the local engine server (scripts/studio_server.py and the dataclasses behind it).

export type Language = "en" | "tl" | "taglish";
export type Platform = "reels" | "shorts" | "tiktok";
export type Aspect = "9:16" | "1:1" | "4:5" | "16:9";

/** Thrown by every engine method. status 0 means the request never got an answer (code "network"). */
export class EngineError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "EngineError";
    this.status = status;
    this.code = code;
  }
}

export interface EngineInfo {
  port: number;
  token: string;
}

/** Every non-2xx body the server sends. */
export interface ErrorBody {
  detail: string;
  code: string;
}

export interface Highlight {
  id: string;
  start: number;
  end: number;
  w0: number;
  w1: number;
  score: number;
  title: string;
  features: Record<string, number>;
}

export interface ProjectClip {
  highlight: Highlight;
  kept: boolean;
  /** Relative to the workdir, e.g. "clip-h01.edl.json". */
  edl_file: string;
}

export interface ProjectSettings {
  workdir?: string;
  proxy?: string;
  filmstrip?: string;
}

export type JobKind = "analyze" | "export" | "model";
export type JobState = "queued" | "running" | "done" | "failed" | "cancelled";

export interface AnalyzeResult {
  clips: number;
  new: number;
  reason?: string | null;
}

export interface RenderPlanEntry {
  stage: string;
  hid: string;
  aspect: string;
  path: string;
}

export interface ExportResult {
  files: string[];
  failed?: { hid: string; aspect: string };
  renders: RenderPlanEntry[];
}

export interface Job {
  id: string;
  project_id: string;
  kind: JobKind;
  state: JobState;
  stage: string;
  pct: number;
  message: string;
  error: string | null;
  result: AnalyzeResult | ExportResult | Record<string, unknown> | null;
}

export interface Project {
  id: string;
  type: "clip";
  source: string;
  language: Language;
  platform: Platform;
  created: string;
  clips: ProjectClip[];
  settings: ProjectSettings;
  last_job: Job | null;
}

export interface TranscriptWord {
  text: string;
  start: number;
  end: number;
  filler?: string | null;
  speaker?: string | null;
}

export interface Transcript {
  words: TranscriptWord[];
  duration?: number;
}

/** A clip of the edit: a word range of a source, or a plain time range. */
export interface EdlClip {
  src?: string;
  words?: [number, number];
  in?: number | string;
  out?: number | string;
  speed?: number;
  crop_x?: number;
  [key: string]: unknown;
}

export interface Edl {
  clips: EdlClip[];
  output?: Record<string, unknown>;
  captions?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface CaptionWord {
  text: string;
  start: number;
  end: number;
  hl_start: number;
  hl_end: number;
  speaker: string | null;
}

export interface CaptionEvent {
  start: number;
  end: number;
  words: CaptionWord[];
}

export interface CaptionTrack {
  style: string;
  font: string;
  size_frac: number;
  baseline_frac: number;
  uppercase: boolean;
  color: string;
  highlight: string;
  outline_frac: number;
  active_scale: number;
  outline_color: string;
  shadow: number;
  back_color: string | null;
  back_alpha: number | null;
  speaker_colors: Record<string, string>;
  /** Seconds of intro bumper before the first edited clip; caption times include it, the preview skips it. */
  lead_in?: number;
  events: CaptionEvent[];
}

export interface ClipView {
  edl: Edl;
  /** Source-time [start, end] of each clip in play order. */
  kept_ranges: [number, number][];
  captions: CaptionTrack;
}

export type EditSection = "captions" | "audio" | "color" | "framing" | "output";

export type ClipOp =
  | { op: "cut"; w0: number; w1: number }
  | { op: "restore"; w0: number; w1: number }
  | { op: "trim"; w_first: number; w_last: number }
  | { op: "fix_word"; wrong: string; right: string }
  | { op: "patch"; section: EditSection; patch: Record<string, unknown> };

export interface NewProjectBody {
  source: string;
  language: Language;
  platform: Platform;
}

export interface AnalyzeBody {
  /** Most highlights to find (1 to 60). Re-analyzing with a bigger number finds more; existing clips stay. */
  max_clips?: number;
  ai?: { provider: "anthropic" | "openai"; api_key: string; model?: string };
}

/** The thumbnail sprite: one frame of `width` x `height` per `every` seconds, laid out `cols` x `rows`. */
export interface FilmstripInfo {
  every: number;
  height: number;
  width: number;
  count: number;
  cols: number;
  rows: number;
  /** Absolute file path from the engine; a URL the webview can load in the mock. */
  image: string;
}

export interface ExportBody {
  clips: string[];
  formats?: Aspect[];
  quality?: "normal" | "high";
}

export type ModelStatus = Record<string, { installed: boolean; size_mb: number }>;

export interface Health {
  ok: boolean;
  version: string;
}

export interface Doctor {
  ffmpeg: boolean;
  libass: boolean;
  models: ModelStatus;
}

export interface JobEvent {
  type: "job";
  job: Job;
}

/** One async method per server route. The real client and the in-memory mock both implement it. */
export interface Engine {
  health(): Promise<Health>;
  doctor(): Promise<Doctor>;
  recent(): Promise<Project[]>;
  createProject(body: NewProjectBody): Promise<Project>;
  getProject(pid: string): Promise<Project>;
  analyze(pid: string, body?: AnalyzeBody): Promise<Job>;
  transcript(pid: string): Promise<Transcript>;
  filmstrip(pid: string): Promise<FilmstripInfo>;
  clip(pid: string, hid: string): Promise<ClipView>;
  patchClip(pid: string, hid: string, op: ClipOp): Promise<ClipView>;
  setKept(pid: string, hid: string, kept: boolean): Promise<Project>;
  exportClips(pid: string, body: ExportBody): Promise<Job>;
  getJob(id: string): Promise<Job>;
  cancelJob(id: string): Promise<Job>;
  models(): Promise<ModelStatus>;
  downloadModel(name: string): Promise<Job>;
  deleteModel(name: string): Promise<ModelStatus>;
}
