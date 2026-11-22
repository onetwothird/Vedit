// Typed HTTP client for the local engine, plus the choice of which engine the app talks to.
// The token and any AI key are never logged and never put into an error message.

import type {
  AnalyzeBody, ClipOp, ClipView, Doctor, Engine, EngineInfo, ErrorBody, ExportBody, FilmstripInfo, Health, Job, ModelStatus,
  NewProjectBody, Project, Transcript,
} from "./types";
import { mockEngine } from "./mock";
import { EngineError } from "./types";

export { EngineError };

export type InfoSource = () => Promise<EngineInfo>;

export interface ClientOptions {
  /** Asks for the engine's current port and token. Called again after a network failure or a 401. */
  getInfo: InfoSource;
  fetchImpl?: typeof fetch;
  /** Called with every Job a route returns, so live-job stores can pick it up before the first event. */
  onJob?: (job: Job) => void;
}

const enc = encodeURIComponent;

function isErrorBody(x: unknown): x is ErrorBody {
  return typeof x === "object" && x !== null && typeof (x as ErrorBody).detail === "string"
    && typeof (x as ErrorBody).code === "string";
}

export function createClient(opts: ClientOptions): Engine {
  const doFetch: typeof fetch = opts.fetchImpl ?? ((input, init) => globalThis.fetch(input, init));
  let current: Promise<EngineInfo> | null = null;

  function info(): Promise<EngineInfo> {
    if (!current) {
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
      } catch {
        throw new EngineError(0, "engine_unavailable", "the engine is not running");
      }
      let res: Response;
      try {
        res = await doFetch(`http://127.0.0.1:${conn.port}${path}`, {
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
    patchClip: (pid, hid, op: ClipOp) => request<ClipView>("PATCH", `/projects/${enc(pid)}/clips/${enc(hid)}`, op),
    setKept: (pid, hid, kept) => request<Project>("PUT", `/projects/${enc(pid)}/clips/${enc(hid)}/kept`, { kept }),
    exportClips: (pid, body: ExportBody) => job(request<Job>("POST", `/projects/${enc(pid)}/export`, body)),
    getJob: (id) => job(request<Job>("GET", `/jobs/${enc(id)}`)),
    cancelJob: (id) => job(request<Job>("POST", `/jobs/${enc(id)}/cancel`)),
    models: () => request<ModelStatus>("GET", "/models"),
    downloadModel: (name) => job(request<Job>("POST", `/models/${enc(name)}/download`)),
    deleteModel: (name) => request<ModelStatus>("DELETE", `/models/${enc(name)}`),
  };
}

// ---- which engine -------------------------------------------------------------------------------------------

export const isTauri = (): boolean => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

export const isMock = (): boolean => !isTauri() && import.meta.env.VITE_ENGINE === "mock";

/** Port and token of the running engine: the Tauri command, else the VITE_ENGINE_URL/VITE_ENGINE_TOKEN dev pair. */
export async function fetchEngineInfo(): Promise<EngineInfo> {
  if (isTauri()) {
    const { invoke } = await import("@tauri-apps/api/core");
    return invoke<EngineInfo>("engine_info");
  }
  const url = import.meta.env.VITE_ENGINE_URL as string | undefined;
  const token = import.meta.env.VITE_ENGINE_TOKEN as string | undefined;
  if (!url || !token) throw new Error("no engine: set VITE_ENGINE_URL and VITE_ENGINE_TOKEN, or run dev:mock");
  const port = Number(new URL(url).port);
  if (!Number.isInteger(port) || port <= 0) throw new Error("VITE_ENGINE_URL needs an explicit port");
  return { port, token };
}

const jobListeners = new Set<(job: Job) => void>();

/** Subscribe to every Job any default-engine route returns (used by events.ts to seed the live store). */
export function onJobResult(fn: (job: Job) => void): () => void {
  jobListeners.add(fn);
  return () => void jobListeners.delete(fn);
}

export const engine: Engine = isMock()
  ? mockEngine
  : createClient({ getInfo: fetchEngineInfo, onJob: (j) => jobListeners.forEach((fn) => fn(j)) });
