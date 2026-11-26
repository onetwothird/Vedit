// Typed HTTP client for the local engine, plus the choice of which engine the app talks to.
// The token and any AI key are never logged and never put into an error message.

import type {
  AiTestBody, AiTestResult, AnalyzeBody, Asset, Batch, BrollBody, BrollSuggestions, BatchBody, Brand, BuildStoryBody, Story, StoryBody, StoryVoice, ClipOp, ClipView, CloudApi, CloudBundle, CloudProject, Doctor, Engine, EngineInfo, ErrorBody, ExportBody, ExportLink, FilmstripInfo, Health, ImportBundleResult, Job, LocalSyncApi, ModelStatus,
  NewProjectBody, Project, ProjectBundle, PushResult, SyncState, Transcript, UploadTicket, Usage,
} from "./types";
import { getAccessToken } from "../auth/accessToken";
import { isCloud, isTauri } from "./flavor";
import { setMediaRefresher, setMediaToken } from "./mediaToken";
import { mockEngine } from "./mock";
import { EngineError } from "./types";

export { EngineError };

export type InfoSource = () => Promise<EngineInfo>;

export interface ClientOptions {
  /** Asks for the engine's current port and token. Called again after a network failure or a 401. */
  getInfo: InfoSource;
  fetchImpl?: typeof fetch;
  /** Keep the first answer of getInfo until a failure or a 401. Default: yes on desktop, no in the cloud, where the
      access token is refreshed hourly and every request must carry the latest one. */
  cacheInfo?: boolean;
  /** Called with every Job a route returns, so live-job stores can pick it up before the first event. */
  onJob?: (job: Job) => void;
  /** Called after a route changed what a project's sync bundle holds (a clip edit), with the project id. */
  onEdit?: (pid: string) => void;
}

/** Every route of an engine client: the app's own, the local engine's sync routes and the cloud's sync routes
    (each engine answers only its own; the others are 404 there). */
export type Client = Engine & LocalSyncApi & CloudApi;

const enc = encodeURIComponent;

function isErrorBody(x: unknown): x is ErrorBody {
  return typeof x === "object" && x !== null && typeof (x as ErrorBody).detail === "string"
    && typeof (x as ErrorBody).code === "string";
}

export function createClient(opts: ClientOptions): Client {
  const doFetch: typeof fetch = opts.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  let current: Promise<EngineInfo> | null = null;

  function info(): Promise<EngineInfo> {
    if (!current || !(opts.cacheInfo ?? !isCloud())) {
      const p = opts.getInfo();
      current = p;
      p.catch(() => {
        if (current === p) current = null; // never cache a failure
      });
    }
    return current;
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      const used = info();
      let conn: EngineInfo;
      try {
        conn = await used;
      } catch (e) {
        if (e instanceof EngineError && e.code === "signin") throw e; // cloud build, nobody signed in
        throw new EngineError(0, "engine_unavailable", "the engine is not running");
      }
      let res: Response;
      try {
        res = await doFetch(`${conn.base ?? `http://127.0.0.1:${conn.port}`}${path}`, {
          method,
          headers: {
            Authorization: `Bearer ${conn.token}`,
            ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          },
          body: body !== undefined ? JSON.stringify(body) : undefined,
        });
      } catch {
        if (current === used) current = null; // the engine may have restarted on a new port
        // only a GET is retried: a POST/PATCH/PUT/DELETE may have reached the server, and must not run twice
        if (attempt === 0 && method === "GET") continue;
        throw new EngineError(0, "network", "could not reach the engine");
      }
      if (res.status === 401 && attempt === 0) {
        if (current === used) current = null; // a restarted engine has a new token
        continue;
      }
      let data: unknown = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      if (!res.ok) {
        if (isErrorBody(data)) throw new EngineError(res.status, data.code, data.detail);
        throw new EngineError(res.status, res.status >= 500 ? "internal" : "bad_request", `engine error ${res.status}`);
      }
      return data as T;
    }
  }

  const job = async (p: Promise<Job>): Promise<Job> => {
    const j = await p;
    opts.onJob?.(j);
    return j;
  };

  const edited = async <T>(pid: string, p: Promise<T>): Promise<T> => {
    const r = await p;
    opts.onEdit?.(pid);
    return r;
  };

  return {
    health: () => request<Health>("GET", "/health"),
    doctor: () => request<Doctor>("GET", "/doctor"),
    recent: () => request<Project[]>("GET", "/projects/recent"),
    createProject: (body: NewProjectBody) => request<Project>("POST", "/projects", body),
    getProject: (pid) => request<Project>("GET", `/projects/${enc(pid)}`),
    analyze: (pid, body?: AnalyzeBody) => job(request<Job>("POST", `/projects/${enc(pid)}/analyze`, body ?? {})),
    transcript: (pid) => request<Transcript>("GET", `/projects/${enc(pid)}/transcript`),
    filmstrip: (pid) => request<FilmstripInfo>("GET", `/projects/${enc(pid)}/filmstrip`),
    clip: (pid, hid) => request<ClipView>("GET", `/projects/${enc(pid)}/clips/${enc(hid)}`),
    patchClip: (pid, hid, op: ClipOp) => edited(pid, request<ClipView>("PATCH", `/projects/${enc(pid)}/clips/${enc(hid)}`, op)),
    setKept: (pid, hid, kept) => edited(pid, request<Project>("PUT", `/projects/${enc(pid)}/clips/${enc(hid)}/kept`, { kept })),
    suggestBroll: (pid, hid, body: BrollBody) => request<BrollSuggestions>("POST", `/projects/${enc(pid)}/clips/${enc(hid)}/broll`, body),
    exportClips: (pid, body: ExportBody) => job(request<Job>("POST", `/projects/${enc(pid)}/export`, body)),
    getJob: (id) => job(request<Job>("GET", `/jobs/${enc(id)}`)),
    activeJobs: async (project, kind) => {
      const q = new URLSearchParams();
      if (project) q.set("project", project);
      if (kind) q.set("kind", kind);
      const list = await request<Job[]>("GET", `/jobs${q.size ? `?${q}` : ""}`);
      list.forEach((j) => opts.onJob?.(j));
      return list;
    },
    cancelJob: (id) => job(request<Job>("POST", `/jobs/${enc(id)}/cancel`)),
    models: () => request<ModelStatus>("GET", "/models"),
    downloadModel: (name) => job(request<Job>("POST", `/models/${enc(name)}/download`)),
    deleteModel: (name) => request<ModelStatus>("DELETE", `/models/${enc(name)}`),
    testAi: (body: AiTestBody) => request<AiTestResult>("POST", "/ai/test", body),
    brand: () => request<Brand>("GET", "/brand"),
    saveBrand: (brand: Brand) => request<Brand>("PUT", "/brand", brand),
    stories: () => request<Story[]>("GET", "/stories"),
    storyVoices: () => request<StoryVoice[]>("GET", "/stories/voices"),
    createStory: (title = "") => request<Story>("POST", "/stories", { title }),
    getStory: (sid) => request<Story>("GET", `/stories/${enc(sid)}`),
    saveStory: (sid, body: StoryBody) => request<Story>("PUT", `/stories/${enc(sid)}`, body),
    buildStory: (sid, body?: BuildStoryBody) => job(request<Job>("POST", `/stories/${enc(sid)}/build`, body ?? {})),
    createBatch: (body: BatchBody) => request<Batch>("POST", "/batches", body),
    getBatch: (bid) => request<Batch>("GET", `/batches/${enc(bid)}`),
    createUpload: (body) => request<UploadTicket>("POST", "/uploads", body),
    commitAsset: (uploadKey) => request<Asset>("POST", "/assets", { upload_key: uploadKey }),
    usage: () => request<Usage>("GET", "/usage"),
    projectExports: (pid) => request<ExportLink[]>("GET", `/projects/${enc(pid)}/exports`),
    listAssets: (kind) => request<Asset[]>("GET", `/assets${kind ? `?kind=${enc(kind)}` : ""}`),
    storyExports: (sid) => request<ExportLink[]>("GET", `/stories/${enc(sid)}/exports`),
    zipBatch: (bid) => job(request<Job>("POST", `/batches/${enc(bid)}/zip`)),
    // local engine (desktop): sync
    syncState: () => request<SyncState[]>("GET", "/projects/sync-state"),
    projectBundle: (pid) => request<ProjectBundle>("GET", `/projects/${enc(pid)}/bundle`),
    importBundle: async (body) => {
      const r = await request<ImportBundleResult>("POST", "/projects/import-bundle", body);
      if (r.job) opts.onJob?.(r.job);
      return r;
    },
    publishVideo: (pid, url) => job(request<Job>("POST", `/projects/${enc(pid)}/publish`, { url })),
    patchSyncSettings: (pid, patch) => request<SyncState>("PATCH", `/projects/${enc(pid)}/settings`, patch),
    // cloud engine: sync
    listSynced: () => request<CloudProject[]>("GET", "/sync/projects"),
    getSynced: (id) => request<CloudBundle>("GET", `/sync/projects/${enc(id)}`),
    pushSynced: (id, baseRev, bundle) => request<PushResult>("PUT", `/sync/projects/${enc(id)}`, { base_rev: baseRev, bundle }),
    pushNew: (bundle) => request<PushResult>("PUT", "/sync/projects/new", { bundle }),
    attachVideo: (id, uploadKey) => request<CloudProject>("POST", `/sync/projects/${enc(id)}/video`, { upload_key: uploadKey }),
    deleteVideo: (id) => request<CloudProject>("DELETE", `/sync/projects/${enc(id)}/video`),
  };
}

// ---- which engine -------------------------------------------------------------------------------------------

export { isCloud, isTauri };

export const isMock = (): boolean => !isTauri() && import.meta.env.VITE_ENGINE === "mock";

let lastInfo: EngineInfo | null = null; // the engine the app last reached, for media URLs
/** Remembers the engine the app last reached; media elements watching the token hear when it changes. */
function reached(info: EngineInfo): EngineInfo {
  const changed = lastInfo?.token !== info.token || lastInfo?.base !== info.base;
  lastInfo = info;
  setMediaToken(info.token, changed);
  return info;
}

// media URLs of the website expire with the access token: useMediaUrl asks for a fresh one through this
setMediaRefresher(async () => {
  if (isCloud()) await fetchEngineInfo();
});

/** Port and token of the running engine: the Tauri command, else the VITE_ENGINE_URL/VITE_ENGINE_TOKEN dev pair. */
export async function fetchEngineInfo(): Promise<EngineInfo> {
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return reached(await invoke<EngineInfo>("engine_info"));
  }
  if (isCloud()) {
    const base = ((import.meta.env.VITE_ENGINE_URL as string | undefined) ?? "").trim().replace(/\/+$/, "");
    if (!base) throw new Error("no engine: set VITE_ENGINE_URL to the cloud API address");
    const token = await getAccessToken(); // fetched per request: it is refreshed while the app is open
    if (!token) throw new EngineError(401, "signin", "sign in to use Vedit");
    return reached({ base, token, port: 0 });
  }
  const url = import.meta.env.VITE_ENGINE_URL as string | undefined;
  const token = import.meta.env.VITE_ENGINE_TOKEN as string | undefined;
  if (!url || !token) throw new Error("no engine: set VITE_ENGINE_URL and VITE_ENGINE_TOKEN, or run dev:mock");
  const port = Number(new URL(url).port);
  if (!Number.isInteger(port) || port <= 0) throw new Error("VITE_ENGINE_URL needs an explicit port");
  return reached({ port, token });
}

/** A URL a <video> or <img> can load from the engine (which streams the file, with seeking): the token rides in
    the query because media elements cannot send headers. Null until the app has reached the engine once. */
export function engineMediaUrl(route: string, params: Record<string, string> = {}): string | null {
  if (!lastInfo) return null;
  return `${lastInfo.base ?? `http://127.0.0.1:${lastInfo.port}`}${route}?${new URLSearchParams({ ...params, token: lastInfo.token })}`;
}

const jobListeners = new Set<(job: Job) => void>();

/** Subscribe to every Job any default-engine route returns (used by events.ts to seed the live store). */
export function onJobResult(fn: (job: Job) => void): () => void {
  jobListeners.add(fn);
  return () => void jobListeners.delete(fn);
}

const editListeners = new Set<(pid: string) => void>();

/** Subscribe to clip edits made through the default engine (the sync client pushes the project shortly after). */
export function onProjectEdited(fn: (pid: string) => void): () => void {
  editListeners.add(fn);
  return () => void editListeners.delete(fn);
}

const realEngine: Client | null = isMock()
  ? null
  : createClient({
    getInfo: fetchEngineInfo,
    onJob: (j) => jobListeners.forEach((fn) => fn(j)),
    onEdit: (pid) => editListeners.forEach((fn) => fn(pid)),
  });

export const engine: Engine = realEngine ?? mockEngine;

/** The local engine's sync routes; null with the mock engine. */
export const localSync: LocalSyncApi | null = realEngine;

/** The cloud engine's sync routes for the desktop app, at VITE_CLOUD_URL: `cloudFor(token)` is a client that sends
    that one access token with every request, so a unit of sync work acts as one account from start to end. Null when
    VITE_CLOUD_URL is not set: then there is no sync at all. */
export function createCloudApi(fetchImpl?: typeof fetch): ((token: string) => CloudApi) | null {
  const base = ((import.meta.env.VITE_CLOUD_URL as string | undefined) ?? "").trim().replace(/\/+$/, "");
  if (!base) return null;
  return (token: string) => createClient({ cacheInfo: false, fetchImpl, getInfo: async () => ({ base, token, port: 0 }) });
}
