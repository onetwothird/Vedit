import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "./types";

const session = vi.hoisted(() => ({ token: "tok-1" as string | null, calls: 0 }));
vi.mock("../auth/accessToken", () => ({
  getAccessToken: async () => {
    session.calls++;
    return session.token;
  },
}));

const BASE = "https://api.vedit.example";

function cloudEnv(): void {
  vi.stubEnv("VITE_ENGINE", "cloud");
  vi.stubEnv("VITE_ENGINE_URL", BASE);
  vi.stubEnv("VITE_ENGINE_TOKEN", "");
}

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  session.token = "tok-1";
  session.calls = 0;
  cloudEnv();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.resetModules();
  vi.useRealTimers();
});

const authOf = (fetchImpl: { mock: { calls: unknown[][] } }, i: number): string =>
  ((fetchImpl.mock.calls[i] as [string, RequestInit])[1].headers as Record<string, string>).Authorization;

describe("cloud flavor", () => {
  it("isCloud is true only for VITE_ENGINE=cloud outside Tauri", async () => {
    const { isCloud } = await import("./client");
    expect(isCloud()).toBe(true);
    vi.stubEnv("VITE_ENGINE", "");
    expect(isCloud()).toBe(false);
    vi.stubEnv("VITE_ENGINE", "cloud");
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    try {
      expect(isCloud()).toBe(false);
    } finally {
      delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    }
  });

  it("fetchEngineInfo returns the cloud base and the current access token", async () => {
    const { fetchEngineInfo } = await import("./client");
    expect(await fetchEngineInfo()).toEqual({ base: BASE, token: "tok-1", port: 0 });
  });

  it("no session is EngineError signin", async () => {
    session.token = null;
    const { fetchEngineInfo, EngineError } = await import("./client");
    const err = await fetchEngineInfo().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(EngineError);
    expect((err as InstanceType<typeof EngineError>).code).toBe("signin");
  });

  it("requests go to the base with the bearer token", async () => {
    const { createClient, fetchEngineInfo } = await import("./client");
    const fetchImpl = vi.fn(async (_u: string, _i: RequestInit) => json({ ok: true }));
    const c = createClient({ getInfo: fetchEngineInfo, fetchImpl: fetchImpl as unknown as typeof fetch });
    await c.health();
    expect(fetchImpl.mock.calls[0][0]).toBe(`${BASE}/health`);
    expect(authOf(fetchImpl, 0)).toBe("Bearer tok-1");
  });

  it("a token refreshed between two calls is used by the second", async () => {
    const { createClient, fetchEngineInfo } = await import("./client");
    const fetchImpl = vi.fn(async (_u: string, _i: RequestInit) => json({}));
    const c = createClient({ getInfo: fetchEngineInfo, fetchImpl: fetchImpl as unknown as typeof fetch });
    await c.doctor();
    session.token = "tok-2";
    await c.doctor();
    expect(authOf(fetchImpl, 0)).toBe("Bearer tok-1");
    expect(authOf(fetchImpl, 1)).toBe("Bearer tok-2");
  });

  it("a request with no session fails with signin and never hits the network", async () => {
    session.token = null;
    const { createClient, fetchEngineInfo } = await import("./client");
    const fetchImpl = vi.fn(async (_u: string, _i: RequestInit) => json({}));
    const c = createClient({ getInfo: fetchEngineInfo, fetchImpl: fetchImpl as unknown as typeof fetch });
    await expect(c.doctor()).rejects.toMatchObject({ code: "signin" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("a 401 is retried once with a freshly fetched token", async () => {
    const { createClient, fetchEngineInfo } = await import("./client");
    const fetchImpl = vi.fn(async (_u: string, init: RequestInit) => {
      const t = (init.headers as Record<string, string>).Authorization;
      if (t === "Bearer tok-2") return json({ ok: 1 });
      session.token = "tok-2"; // the session refreshed while the first call was in flight
      return json({ detail: "bad token", code: "unauthorized" }, 401);
    });
    const c = createClient({ getInfo: fetchEngineInfo, fetchImpl: fetchImpl as unknown as typeof fetch });
    await expect(c.doctor()).resolves.toEqual({ ok: 1 });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("media URLs use the base and carry the latest token in the query", async () => {
    const { engineMediaUrl, fetchEngineInfo } = await import("./client");
    expect(engineMediaUrl("/projects/p1/media")).toBeNull();
    await fetchEngineInfo();
    expect(engineMediaUrl("/projects/p1/media")).toBe(`${BASE}/projects/p1/media?token=tok-1`);
    session.token = "tok-2";
    await fetchEngineInfo();
    expect(engineMediaUrl("/projects/p1/media")).toBe(`${BASE}/projects/p1/media?token=tok-2`);
  });
});

// ---- the events websocket ------------------------------------------------------------------------------------

class FakeSocket {
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(public url: string) {}
  close(): void {}
}

const job = (id: string, state = "running"): Job => ({ id, state, pct: 10 }) as unknown as Job;
const tick = (ms = 0) => vi.advanceTimersByTimeAsync(ms);

async function setup(resyncAll?: () => Promise<Job[]>) {
  vi.useFakeTimers();
  const { createJobStore, CLOUD_BACKOFF_MS } = await import("./events");
  const { fetchEngineInfo } = await import("./client");
  const sockets: FakeSocket[] = [];
  const store = createJobStore({
    getInfo: () => fetchEngineInfo(),
    createSocket: (url) => {
      const s = new FakeSocket(url);
      sockets.push(s);
      return s;
    },
    backoff: CLOUD_BACKOFF_MS,
    resyncAll,
  });
  return { store, sockets };
}

describe("cloud events", () => {
  it("builds a wss URL from the base with the token in the query", async () => {
    const { eventsUrl } = await import("./events");
    expect(eventsUrl({ base: "https://api.vedit.example", token: "a b", port: 0 })).toBe("wss://api.vedit.example/events?token=a%20b");
    expect(eventsUrl({ base: "http://localhost:9000/", token: "t", port: 0 })).toBe("ws://localhost:9000/events?token=t");
    expect(eventsUrl({ port: 8765, token: "t" })).toBe("ws://127.0.0.1:8765/events?token=t");
  });

  it("reconnects with backoff 1,2,4,8,15,15 s and resets after a successful open", async () => {
    const { store, sockets } = await setup();
    store.subscribe(() => undefined);
    await tick();
    expect(sockets).toHaveLength(1);
    for (const [i, wait] of [1000, 2000, 4000, 8000, 15000, 15000].entries()) {
      sockets[i].onclose?.({});
      await tick(wait - 1);
      expect(sockets).toHaveLength(i + 1);
      await tick(1);
      expect(sockets).toHaveLength(i + 2);
    }
    const last = sockets[sockets.length - 1];
    last.onopen?.({});
    last.onclose?.({});
    await tick(999);
    expect(sockets).toHaveLength(7);
    await tick(1);
    expect(sockets).toHaveLength(8);
  });

  it("each reconnect uses a freshly fetched token", async () => {
    const { store, sockets } = await setup();
    store.subscribe(() => undefined);
    await tick();
    expect(sockets[0].url).toBe(`wss://api.vedit.example/events?token=tok-1`);
    session.token = "tok-2";
    sockets[0].onclose?.({});
    await tick(1000);
    expect(sockets[1].url).toBe(`wss://api.vedit.example/events?token=tok-2`);
  });

  it("after a reconnect it re-reads GET /jobs and feeds the active jobs to listeners", async () => {
    const resyncAll = vi.fn(async () => [job("j1"), job("j2", "queued")]);
    const { store, sockets } = await setup(resyncAll);
    store.subscribe(() => undefined);
    await tick();
    sockets[0].onopen?.({});
    await tick();
    expect(resyncAll).not.toHaveBeenCalled(); // the server sends the snapshots on a first connect
    sockets[0].onclose?.({});
    await tick(1000);
    sockets[1].onopen?.({});
    await tick();
    expect(resyncAll).toHaveBeenCalledTimes(1);
    expect([...store.getJobs().keys()].sort()).toEqual(["j1", "j2"]);
  });

  it("a failing /jobs read does not break the socket", async () => {
    const resyncAll = vi.fn(async (): Promise<Job[]> => {
      throw new Error("down");
    });
    const { store, sockets } = await setup(resyncAll);
    store.subscribe(() => undefined);
    await tick();
    sockets[0].onclose?.({});
    await tick(1000);
    sockets[1].onopen?.({});
    await tick();
    sockets[1].onmessage?.({ data: JSON.stringify({ type: "job", job: job("j9") }) });
    expect(store.getJob("j9")?.id).toBe("j9");
  });

  it("no session while reconnecting keeps retrying instead of giving up", async () => {
    const { store, sockets } = await setup();
    store.subscribe(() => undefined);
    await tick();
    session.token = null;
    sockets[0].onclose?.({});
    await tick(1000);
    expect(sockets).toHaveLength(1);
    session.token = "tok-3";
    await tick(2000);
    expect(sockets).toHaveLength(2);
    expect(sockets[1].url).toContain("token=tok-3");
  });
});

describe("desktop sync clients", () => {
  it("there is no cloud API without VITE_CLOUD_URL", async () => {
    vi.stubEnv("VITE_CLOUD_URL", "");
    const { createCloudApi } = await import("./client");
    expect(createCloudApi()).toBeNull();
  });

  it("the cloud API reaches VITE_CLOUD_URL with the current access token on every request", async () => {
    vi.stubEnv("VITE_ENGINE", "");
    vi.stubEnv("VITE_CLOUD_URL", "https://me--vedit.modal.run/");
    const fetchImpl = vi.fn(async () => json({ id: "c1", rev: 2 }));
    const { createCloudApi } = await import("./client");
    const cloud = createCloudApi(fetchImpl as unknown as typeof fetch)!;
    await cloud.pushSynced("c1", 1, { format: 1 } as never);
    session.token = "tok-2";
    await cloud.listSynced();
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("https://me--vedit.modal.run/sync/projects/c1");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ base_rev: 1, bundle: { format: 1 } });
    expect(authOf(fetchImpl, 0)).toBe("Bearer tok-1");
    expect(authOf(fetchImpl, 1)).toBe("Bearer tok-2");
    session.token = null;
    await expect(cloud.listSynced()).rejects.toMatchObject({ code: "signin" });
    expect(fetchImpl).toHaveBeenCalledTimes(2); // signed out: no request
  });

  it("a clip edit through the client is reported with its project id", async () => {
    const fetchImpl = vi.fn(async () => json({}));
    const onEdit = vi.fn();
    const { createClient } = await import("./client");
    const c = createClient({ getInfo: async () => ({ port: 1, token: "t" }), fetchImpl: fetchImpl as unknown as typeof fetch, onEdit });
    await c.patchClip("p1", "h01", { op: "trim", start: 0, end: 1 } as never);
    await c.setKept("p1", "h01", false);
    await c.getProject("p1");
    expect(onEdit.mock.calls).toEqual([["p1"], ["p1"]]);
  });
});
