import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient, EngineError } from "./client";
import { BACKOFF_MS, createJobStore, type SocketLike } from "./events";
import { createMockEngine } from "./mock";
import type { Job } from "./types";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const INFO = { port: 4111, token: "tok-one" };

describe("engine client", () => {
  it("sends the bearer header to the engine port", async () => {
    const f = vi.fn(async () => json(200, { ok: true, version: "1" }));
    const c = createClient({ getInfo: async () => INFO, fetchImpl: f as unknown as typeof fetch });
    await c.health();
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:4111/health");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-one");
  });

  it("maps a 401 to EngineError unauthorized", async () => {
    const f = vi.fn(async () => json(401, { detail: "missing or wrong token", code: "unauthorized" }));
    const c = createClient({ getInfo: async () => INFO, fetchImpl: f as unknown as typeof fetch });
    const err = await c.health().catch((e) => e);
    expect(err).toBeInstanceOf(EngineError);
    expect(err).toMatchObject({ status: 401, code: "unauthorized", message: "missing or wrong token" });
  });

  it("re-fetches engine info and retries once on a 401", async () => {
    const getInfo = vi.fn().mockResolvedValueOnce(INFO).mockResolvedValueOnce({ port: 4222, token: "tok-two" });
    const f = vi.fn()
      .mockResolvedValueOnce(json(401, { detail: "x", code: "unauthorized" }))
      .mockResolvedValueOnce(json(200, { ok: true, version: "1" }));
    const c = createClient({ getInfo, fetchImpl: f as unknown as typeof fetch });
    await expect(c.health()).resolves.toEqual({ ok: true, version: "1" });
    expect(getInfo).toHaveBeenCalledTimes(2);
    const [url, init] = f.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:4222/health");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok-two");
  });

  it("re-fetches and retries once on a network failure, then reports code network", async () => {
    const getInfo = vi.fn().mockResolvedValue(INFO);
    const f = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const c = createClient({ getInfo, fetchImpl: f as unknown as typeof fetch });
    await expect(c.health()).rejects.toMatchObject({ status: 0, code: "network" });
    expect(f).toHaveBeenCalledTimes(2);
    expect(getInfo).toHaveBeenCalledTimes(2);
  });

  it("never retries a POST/PATCH/PUT/DELETE after a network error", async () => {
    const getInfo = vi.fn().mockResolvedValue(INFO);
    const f = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    const c = createClient({ getInfo, fetchImpl: f as unknown as typeof fetch });
    await expect(c.analyze("p1")).rejects.toMatchObject({ status: 0, code: "network" });
    await expect(c.setKept("p1", "h01", true)).rejects.toMatchObject({ code: "network" });
    await expect(c.deleteModel("small")).rejects.toMatchObject({ code: "network" });
    expect(f).toHaveBeenCalledTimes(3);
  });

  it("sends patchClip bodies with the server's field names", async () => {
    const f = vi.fn(async () => json(200, {}));
    const c = createClient({ getInfo: async () => INFO, fetchImpl: f as unknown as typeof fetch });
    await c.patchClip("p1", "h01", { op: "cut", w0: 1, w1: 1 });
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:4111/projects/p1/clips/h01");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ op: "cut", w0: 1, w1: 1 });
    await c.patchClip("p1", "h01", { op: "trim", w_first: 2, w_last: 9 });
    const second = f.mock.calls[1] as unknown as [string, RequestInit];
    expect(JSON.parse(second[1].body as string)).toEqual({ op: "trim", w_first: 2, w_last: 9 });
  });
});

class FakeSocket implements SocketLike {
  static all: FakeSocket[] = [];
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  closed = false;
  url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  close() { this.closed = true; }
  open() { this.onopen?.({}); }
  drop() { this.onclose?.({}); }
  send(msg: unknown) { this.onmessage?.({ data: JSON.stringify(msg) }); }
}

const job = (over: Partial<Job> = {}): Job => ({
  id: "j1", project_id: "p1", kind: "analyze", state: "running", stage: "analyze", pct: 10, message: "", error: null,
  result: null, ...over,
});

const flush = async () => { await vi.advanceTimersByTimeAsync(0); };

describe("job store", () => {
  beforeEach(() => { vi.useFakeTimers(); FakeSocket.all = []; });
  afterEach(() => vi.useRealTimers());

  function make() {
    const getInfo = vi.fn(async (_fresh: boolean) => INFO);
    const store = createJobStore({ getInfo, createSocket: (u) => new FakeSocket(u) });
    return { store, getInfo };
  }

  it("connects once, delivers snapshot and events, and closes when the last subscriber leaves", async () => {
    const { store } = make();
    const a = vi.fn(), b = vi.fn();
    const offA = store.subscribe(a), offB = store.subscribe(b);
    await flush();
    expect(FakeSocket.all).toHaveLength(1);
    expect(FakeSocket.all[0].url).toBe("ws://127.0.0.1:4111/events?token=tok-one");
    FakeSocket.all[0].open();
    FakeSocket.all[0].send({ type: "job", job: job() });
    expect(store.getJob("j1")?.pct).toBe(10);
    expect(a).toHaveBeenCalledTimes(1);
    FakeSocket.all[0].send({ type: "job", job: job() }); // unchanged: no notification
    expect(a).toHaveBeenCalledTimes(1);
    offA();
    expect(FakeSocket.all[0].closed).toBe(false);
    offB();
    expect(FakeSocket.all[0].closed).toBe(true);
  });

  it("keeps the same job object for unrelated updates (useJob re-renders only for its own job)", async () => {
    const { store } = make();
    store.subscribe(() => undefined);
    await flush();
    FakeSocket.all[0].open();
    FakeSocket.all[0].send({ type: "job", job: job() });
    const before = store.getJob("j1");
    FakeSocket.all[0].send({ type: "job", job: job({ id: "j2" }) });
    expect(store.getJob("j1")).toBe(before);
    expect(store.getJobs().size).toBe(2);
  });

  it("reconnects after the socket closes, with backoff that resets after a successful open", async () => {
    const { store, getInfo } = make();
    store.subscribe(() => undefined);
    await flush();
    expect(FakeSocket.all).toHaveLength(1);
    FakeSocket.all[0].drop(); // never opened: 1 s, then 2 s
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0] - 1);
    expect(FakeSocket.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all).toHaveLength(2);
    expect(getInfo).toHaveBeenLastCalledWith(true); // reconnect re-fetches engine_info
    FakeSocket.all[1].drop();
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[1] - 1);
    expect(FakeSocket.all).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeSocket.all).toHaveLength(3);
    FakeSocket.all[2].open(); // success resets the backoff
    FakeSocket.all[2].drop();
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0]);
    expect(FakeSocket.all).toHaveLength(4);
  });

  it("caps the backoff at 10 s", async () => {
    const { store } = make();
    store.subscribe(() => undefined);
    await flush();
    for (let i = 0; i < 6; i++) {
      FakeSocket.all[FakeSocket.all.length - 1].drop();
      await vi.advanceTimersByTimeAsync(10000);
    }
    const n = FakeSocket.all.length;
    FakeSocket.all[n - 1].drop();
    await vi.advanceTimersByTimeAsync(10000);
    expect(FakeSocket.all).toHaveLength(n + 1);
  });

  it("never moves a job backwards when stale snapshots arrive", () => {
    const { store } = make();
    store.upsert(job({ pct: 50, stage: "analyze" }));
    store.upsert(job({ pct: 20, stage: "proxy" })); // lower pct running
    expect(store.getJob("j1")).toMatchObject({ pct: 50, stage: "analyze" });
    store.upsert(job({ state: "queued", pct: 0, stage: "" })); // queued after running
    expect(store.getJob("j1")?.state).toBe("running");
    store.upsert(job({ pct: 60, stage: "highlights" }));
    expect(store.getJob("j1")).toMatchObject({ pct: 60, stage: "highlights" });
    store.upsert(job({ state: "done", pct: 100 }));
    store.upsert(job({ pct: 70 })); // running after done
    store.upsert(job({ state: "failed", pct: 100 }), "http"); // HTTP terminal over terminal
    expect(store.getJob("j1")?.state).toBe("done");
  });

  it("lets a done snapshot gain its result once", () => {
    const { store } = make();
    store.upsert(job({ state: "done", pct: 100, result: null }));
    store.upsert(job({ state: "done", pct: 100, result: { clips: 3 } }));
    expect(store.getJob("j1")?.result).toEqual({ clips: 3 });
    store.upsert(job({ state: "done", pct: 100, result: { clips: 9 } }), "http");
    expect(store.getJob("j1")?.result).toEqual({ clips: 3 });
  });

  it("lets a WS terminal replace an HTTP terminal (export with files [] then filled; done then failed)", () => {
    const { store } = make();
    store.upsert(job({ kind: "export", state: "done", pct: 100, result: { renders: [], files: [] } }), "http");
    store.upsert(job({ kind: "export", state: "done", pct: 100, result: { renders: [], files: ["a.mp4"] } }), "ws");
    expect((store.getJob("j1")?.result as { files: string[] }).files).toEqual(["a.mp4"]);
    store.upsert(job({ id: "j2", state: "done", pct: 100 }), "http");
    store.upsert(job({ id: "j2", state: "failed", pct: 100, error: "post-processing failed: x" }), "ws");
    expect(store.getJob("j2")).toMatchObject({ state: "failed", error: "post-processing failed: x" });
  });

  it("marks a running job failed/interrupted when resync gets a 404 after an engine restart", async () => {
    const resync = vi.fn(async (_id: string): Promise<Job> => { throw new EngineError(404, "not_found", "gone"); });
    const store = createJobStore({ getInfo: async () => INFO, createSocket: (u) => new FakeSocket(u), resync });
    store.subscribe(() => undefined);
    await flush();
    FakeSocket.all[0].open();
    FakeSocket.all[0].send({ type: "job", job: job() });
    FakeSocket.all[0].drop();
    await vi.advanceTimersByTimeAsync(BACKOFF_MS[0]);
    FakeSocket.all[1].open();
    await flush();
    expect(resync).toHaveBeenCalledWith("j1");
    expect(store.getJob("j1")).toMatchObject({ state: "failed", error: "interrupted" });
  });

  it("does not reconnect after the last subscriber left", async () => {
    const { store } = make();
    const off = store.subscribe(() => undefined);
    await flush();
    const ws = FakeSocket.all[0];
    off();
    ws.drop();
    await vi.advanceTimersByTimeAsync(20000);
    expect(FakeSocket.all).toHaveLength(1);
  });
});

describe("mock engine", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("is seeded with 12 clips and runs an analyze job with progress events", async () => {
    const m = createMockEngine({ tickMs: 10 });
    expect((await m.recent())[0].clips).toHaveLength(12);
    const seen: Job[] = [];
    m.onJob((j) => seen.push(j));
    const p = await m.createProject({ source: "C:/a.mp4", language: "en", platform: "reels" });
    expect(p.clips).toHaveLength(0);
    const j = await m.analyze(p.id);
    await vi.advanceTimersByTimeAsync(1000);
    expect(seen.at(-1)).toMatchObject({ id: j.id, state: "done", pct: 100 });
    expect(seen.some((e) => e.state === "running" && e.pct > 0 && e.pct < 100)).toBe(true);
    expect((await m.getProject(p.id)).clips).toHaveLength(12);
  });

  it("cuts, restores and trims word ranges", async () => {
    const m = createMockEngine();
    const [p] = await m.recent();
    const words = (v: { edl: { clips: { words?: [number, number] }[] } }) => v.edl.clips.map((c) => c.words);
    expect(words(await m.clip(p.id, "h01"))).toEqual([[0, 0], [2, 11], [13, 31]]);
    expect(words(await m.patchClip(p.id, "h01", { op: "cut", w0: 5, w1: 6 })))
      .toEqual([[0, 0], [2, 4], [7, 11], [13, 31]]);
    expect(words(await m.patchClip(p.id, "h01", { op: "restore", w0: 5, w1: 6 })))
      .toEqual([[0, 0], [2, 11], [13, 31]]);
    expect(words(await m.patchClip(p.id, "h01", { op: "trim", w_first: 2, w_last: 8 }))).toEqual([[2, 8]]);
    await expect(m.patchClip(p.id, "h01", { op: "cut", w0: 4, w1: 2 })).rejects.toMatchObject({ code: "bad_request" });
  });

  it("runs an export job to done with files", async () => {
    const m = createMockEngine({ tickMs: 10 });
    const [p] = await m.recent();
    const j = await m.exportClips(p.id, { clips: ["h01", "h02"], formats: ["9:16"] });
    await vi.advanceTimersByTimeAsync(2000);
    const done = await m.getJob(j.id);
    expect(done.state).toBe("done");
    expect((done.result as { files: string[] }).files).toHaveLength(4); // each video and its .srt
  });
});
