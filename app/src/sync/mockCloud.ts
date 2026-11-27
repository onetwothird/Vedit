// The desktop mock's stand-in for the website's sync routes, so e2e tests can run sync end to end in the browser.
// Off unless a test sets window.__veditMockCloud before the app loads; the real app's gate (bootSync: the Tauri app
// with VITE_CLOUD_URL) is not touched. The local side is the mock engine's sync hooks; the cloud is in memory here.
//
// window.__veditMockCloud = {
//   projects?: [{ id, title, rev?, has_video_in_cloud?, expired?, video_bytes? }],   // already in the cloud
//   attachError?: string,      // attaching a video is refused with this message (as the cloud's 400)
//   pushDelayMs?: number,      // each push takes this long
//   localOwners?: { [pid]: uid }, // local projects linked by another account
//   intervalMs?: number,       // the sync cycle (default 300 ms)
// }
// and the fake writes back `requests` (one name per cloud request) and keeps `projects` current.

import { useAuth } from "../auth/auth";
import { isMock } from "../engine/client";
import { isWeb } from "../engine/flavor";
import { mockEngine } from "../engine/mock";
import { EngineError } from "../engine/types";
import type { CloudApi, CloudBundle, CloudProject, Job, LocalSyncApi, ProjectBundle } from "../engine/types";
import { authSignIns, bootSyncWith, startSync } from "./sync";

interface FakeProject extends CloudProject {
  bundle: ProjectBundle;
}

interface FakeCloud {
  projects: FakeProject[];
  requests: string[];
  attachError?: string;
  pushDelayMs?: number;
  localOwners?: Record<string, string>;
  intervalMs?: number;
}

const TICK_MS = 80;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function fakeCloud(state: FakeCloud): (token: string) => CloudApi {
  let seq = 0;
  const now = () => new Date().toISOString();
  const find = (id: string): FakeProject => {
    const p = state.projects.find((x) => x.id === id);
    if (!p) throw new EngineError(404, "not_found", "Not found");
    return p;
  };
  const summary = ({ bundle: _b, ...row }: FakeProject): CloudProject => ({ ...row });
  const make = (bundle: ProjectBundle, title = bundle.title): FakeProject => {
    const p: FakeProject = {
      id: `cloud-${++seq}`, title, rev: 1, updated: now(), has_video_in_cloud: false, expired: false, video_bytes: 0,
      bundle: { ...bundle, title },
    };
    state.projects.push(p);
    return p;
  };
  const asked = <T>(name: string, task: () => T | Promise<T>): Promise<T> => {
    state.requests.push(name);
    return Promise.resolve().then(task);
  };
  return () => ({
    listSynced: () => asked("list", () => state.projects.map(summary)),
    getSynced: (id) => asked("get", (): CloudBundle => {
      const p = find(id);
      return { ...p.bundle, id: p.id, rev: p.rev, title: p.title, has_video_in_cloud: p.has_video_in_cloud,
        ...(p.has_video_in_cloud ? { video_url: `https://r2.mock/${p.id}.mp4` } : {}) };
    }),
    pushNew: (bundle) => asked("push", async () => {
      await sleep(state.pushDelayMs ?? 0);
      const p = make(bundle);
      return { id: p.id, rev: p.rev };
    }),
    pushSynced: (id, baseRev, bundle) => asked("push", async () => {
      await sleep(state.pushDelayMs ?? 0);
      const p = find(id);
      if (baseRev !== p.rev) {
        const c = make(bundle, `${bundle.title} (copy)`);
        return { id: c.id, rev: c.rev, conflict: true, original: id };
      }
      Object.assign(p, { rev: p.rev + 1, updated: now(), title: bundle.title, bundle });
      return { id, rev: p.rev };
    }),
    createUpload: (body) => asked("upload", () => ({ upload_key: `up-${++seq}:${body.size}`, url: `https://r2.mock/put/${seq}` })),
    attachVideo: (id, key) => asked("attach", () => {
      if (state.attachError) throw new EngineError(400, "bad_request", state.attachError);
      const p = find(id);
      Object.assign(p, { rev: p.rev + 1, has_video_in_cloud: true, expired: false, video_bytes: Number(key.split(":")[1]) || 0 });
      return summary(p);
    }),
    deleteVideo: (id) => asked("delete-video", () => {
      const p = find(id);
      Object.assign(p, { rev: p.rev + 1, has_video_in_cloud: false, video_bytes: 0 });
      return summary(p);
    }),
  });
}

/** The local engine's sync routes over the mock engine; video transfers are timed jobs of kind "sync". */
function fakeLocal(): LocalSyncApi {
  const hooks = mockEngine.sync;
  const jobs = new Map<string, Job>();
  let seq = 0;
  const run = (project: string, finish: () => Record<string, unknown> | null): Job => {
    const job: Job = { id: `sync-${++seq}`, project_id: project, kind: "sync", state: "running", stage: "transfer", pct: 0,
      message: "", error: null, result: null };
    jobs.set(job.id, job);
    const step = () => {
      job.pct = Math.min(100, job.pct + 20);
      if (job.pct < 100) setTimeout(step, TICK_MS);
      else Object.assign(job, { state: "done", result: finish() });
    };
    setTimeout(step, TICK_MS);
    return { ...job };
  };
  return {
    syncState: async () => hooks.rows(),
    projectBundle: async (pid) => hooks.bundle(pid),
    importBundle: async ({ bundle, video_url, cloud_owner }) => {
      if (!video_url) return { project: hooks.importBundle(bundle, cloud_owner) };
      return { job: run("sync", () => ({ project: hooks.importBundle(bundle, cloud_owner).id })) };
    },
    publishVideo: async (pid) => run(pid, () => null),
    patchSyncSettings: async (pid, patch) => hooks.patch(pid, patch),
    getJob: async (id) => {
      const j = jobs.get(id);
      return j ? { ...j } : mockEngine.getJob(id);
    },
  };
}

/** Starts sync against the fake cloud when a test asked for it (desktop mock only). */
export function bootMockSync(): void {
  const g = globalThis as { __veditMockCloud?: Partial<FakeCloud> & { projects?: Partial<FakeProject>[] } };
  const seed = g.__veditMockCloud;
  if (!isMock() || isWeb() || !seed) return;
  const state: FakeCloud = {
    ...seed,
    requests: [],
    projects: (seed.projects ?? []).map((p) => ({
      id: p.id ?? "cloud-x", title: p.title ?? "Untitled", rev: p.rev ?? 1, updated: new Date().toISOString(),
      has_video_in_cloud: p.has_video_in_cloud ?? false, expired: p.expired ?? false, video_bytes: p.video_bytes ?? 0,
      bundle: { format: 1, id: p.id ?? null, rev: p.rev ?? 1, title: p.title ?? "Untitled", project: {}, files: {}, brand: null,
        has_video_in_cloud: p.has_video_in_cloud ?? false },
    })),
  };
  g.__veditMockCloud = state;
  for (const [pid, owner] of Object.entries(state.localOwners ?? {})) {
    mockEngine.sync.patch(pid, { cloud_id: `elsewhere-${pid}`, cloud_owner: owner });
  }
  const account = () => useAuth.getState().account?.id ?? null;
  bootSyncWith(() => startSync(fakeLocal(), fakeCloud(state), async () => {
    const uid = account();
    return uid ? { token: "mock-token", uid } : null;
  }, account, { signIns: authSignIns, intervalMs: state.intervalMs ?? 300, debounceMs: 200, jobPollMs: TICK_MS }));
}
