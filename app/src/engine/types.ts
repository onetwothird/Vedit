// Types mirroring the local engine server (scripts/studio_server.py and the dataclasses behind it).

export type Language = "en" | "tl" | "taglish";
export type Platform = "reels" | "shorts" | "tiktok" | "youtube";
/** clip: many highlights from a long video. vlog / reel: the whole video as one edit. */
export type ProjectType = "clip" | "vlog" | "reel";
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

export type JobKind = "analyze" | "export" | "model" | "story";
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
  /** The video finished but something extra did not (a thumbnail). */
  warnings?: { hid: string; aspect: string; kind: "thumbnail" }[];
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
  type: ProjectType;
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

/** Text or b-roll the editor places on its timeline (scripts/edl_ops.py _overlay). `at` is source time on the main
    video, so an overlay stays on its moment when words before it are cut; `duration` is seconds of output. */
export type Overlay =
  | { id?: string; type: "text"; at: { src: string; t: number }; duration: number; text: string; position: "top" | "center" | "bottom"; size?: number }
  | {
      id?: string; type: "image" | "video"; at: { src: string; t: number }; duration: number; path: string; mode: "full" | "pip";
      position?: "top-right" | "top-left" | "bottom-left" | "bottom-right" | "center"; src_in?: number;
    };

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
  /** Mock engine only: seconds its short sample video is shifted back from real source time. */
  sample_offset?: number;
  /** The zoom each clip is drawn at in the export: automatic punch-ins are capped where the picture would soften. */
  zooms?: number[];
  /** How far a zoom can go on this video before the picture is enlarged past its own pixels (1 = not at all). */
  sharp_zoom?: number;
}

export type EditSection = "captions" | "audio" | "color" | "framing" | "output" | "overlays" | "transition";

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
  /** Defaults to "clip". */
  type?: ProjectType;
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
  /** Absolute folder for the files; the engine's default is "<name> - Vedit" beside the video. */
  out_dir?: string;
  /** Also write the .srt subtitle file (default true). */
  srt?: boolean;
  /** Also save a .jpg frame beside each video (default false). */
  thumbnail?: boolean;
  /** The number the first clip's file name starts at (default 1); a retried clip keeps its own. */
  first_number?: number;
}

export type ModelStatus = Record<string, { installed: boolean; size_mb: number }>;

/** The brand kit saved by Settings; an empty string means not set. Colours are #rrggbb. */
export interface Brand {
  font: string;
  caption_color: string;
  highlight: string;
  logo: string;
  intro: string;
  outro: string;
  music: string;
}

/** POST /projects/{pid}/clips/{hid}/broll: suggest b-roll from the photos and videos in `folder` (scripts/broll.py). */
export interface BrollBody {
  folder: string;
  ai?: AnalyzeBody["ai"];
}

/** A moment the clip names a thing or place, and the file that shows it. `t` is source time on the main video. */
export interface BrollSuggestion {
  path: string;
  name: string;
  type: "image" | "video";
  t: number;
  duration: number;
  /** The words that matched (by file name) or that the AI picked. */
  word: string;
  source: "name" | "ai";
  why: string;
}

export interface BrollSuggestions {
  suggestions: BrollSuggestion[];
  /** Photos and videos found in the folder. */
  files: number;
  /** null when no AI key was sent; "ai_failed" keeps the file-name matches. */
  ai: null | "ok" | "ai_failed";
}

export interface AiTestBody {
  provider: "anthropic" | "openai";
  api_key: string;
}

export type AiTestResult = { ok: true } | { ok: false; error: string };

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
export type StoryAspect = "9:16" | "16:9" | "1:1";
export type StoryCaptions = "pop" | "box" | "clean" | "none";
export type StoryMotion = "auto" | "in" | "out" | "left" | "right" | "none";

export interface StoryScene {
  /** Full path of the photo or clip. */
  media: string;
  kind: "image" | "video";
  /** What the narrator says: one caption per line. */
  text: string;
  motion: StoryMotion;
  /** A clip that keeps its own sound (and is captioned from it) instead of narration. */
  keep_audio: boolean;
}

export interface Story {
  id: string;
  title: string;
  aspect: StoryAspect;
  voice: string;
  captions: StoryCaptions;
  music: string | null;
  music_volume: number;
  scenes: StoryScene[];
  created: string;
  updated: string;
  /** The last video made from it. */
  output: string | null;
  last_job: Job | null;
}

/** What PUT /stories/{id} takes; a scene's kind is worked out from its file. */
export type StoryBody = Partial<Pick<Story, "title" | "aspect" | "voice" | "captions" | "music" | "music_volume">> & {
  scenes?: Pick<StoryScene, "media" | "text" | "motion" | "keep_audio">[];
};

export interface StoryVoice {
  id: string;
  name: string;
}

export interface StoryResult {
  files: string[];
}

export interface BuildStoryBody {
  out_dir?: string;
  quality?: "normal" | "high";
}

export interface BatchBody extends AnalyzeBody {
  sources: string[];
  type: ProjectType;
  language: Language;
  platform: Platform;
  export: boolean;
  formats: Aspect[];
  quality: "normal" | "high";
  out_dir?: string;
  srt: boolean;
  thumbnail: boolean;
}

export interface BatchItem {
  /** Null when the project was removed since. */
  project: Project | null;
  source: string;
  analyze: string;
  export: string | null;
  error: string | null;
}

export interface Batch {
  id: string;
  created: string;
  type: ProjectType;
  export: boolean;
  items: BatchItem[];
}

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
  suggestBroll(pid: string, hid: string, body: BrollBody): Promise<BrollSuggestions>;
  exportClips(pid: string, body: ExportBody): Promise<Job>;
  getJob(id: string): Promise<Job>;
  cancelJob(id: string): Promise<Job>;
  /** Every job still queued or running, optionally of one project and kind (a reloaded app asks for these). */
  activeJobs(project?: string, kind?: JobKind): Promise<Job[]>;
  models(): Promise<ModelStatus>;
  downloadModel(name: string): Promise<Job>;
  deleteModel(name: string): Promise<ModelStatus>;
  testAi(body: AiTestBody): Promise<AiTestResult>;
  brand(): Promise<Brand>;
  saveBrand(brand: Brand): Promise<Brand>;
  stories(): Promise<Story[]>;
  storyVoices(): Promise<StoryVoice[]>;
  createStory(title?: string): Promise<Story>;
  getStory(sid: string): Promise<Story>;
  saveStory(sid: string, body: StoryBody): Promise<Story>;
  buildStory(sid: string, body?: BuildStoryBody): Promise<Job>;
  createBatch(body: BatchBody): Promise<Batch>;
  getBatch(bid: string): Promise<Batch>;
}
