import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EngineError } from "../engine/types";
import type { CloudApi, CloudBundle, CloudProject, Job, LocalSyncApi, ProjectBundle, SyncState } from "../engine/types";
import { bootSyncWith, openCloudProject, setSyncEnabled, setWebVideo, startSync, syncEnabled, syncStore } from "./sync";

const H1 = "a".repeat(64);
const H2 = "b".repeat(64);
const H3 = "c".repeat(64);

const state = (over: Partial<SyncState> = {}): SyncState => ({
  id: "p1", title: "Talk", cloud_id: "c1", cloud_rev: 2, cloud_hash: H1, cloud_owner: "uA", hash: H1, web_video: false,
  has_source: true, source_name: "talk.mp4", size: 1234, content_type: "video/mp4", ...over,
});

const bundleOf = (title = "Talk"): ProjectBundle => ({
  format: 1, id: null, rev: 0, title, project: {}, files: {}, brand: null, has_video_in_cloud: false,
});

const cloudRow = (over: Partial<CloudProject> = {}): CloudProject => ({
  id: "c1", title: "Talk", rev: 2, updated: "2026-11-26T00:00:00Z", has_video_in_cloud: false, expired: false, ...over,
});

const doneJob = (id: string): Job => ({ id, project_id: "p1", kind: "sync", state: "done", stage: "", pct: 100, message: "", error: null, result: null });

function fakes(states: SyncState[], list: CloudProject[]) {
  const local = {
    syncState: vi.fn(async () => states.map((s) => ({ ...s }))),
    projectBundle: vi.fn(async () => bundleOf()),
    importBundle: vi.fn(async (_body: { bundle: ProjectBundle; video_url?: string; expect_hash?: string }) => ({ project: { id: "p9" } as never })),
    publishVideo: vi.fn(async () => ({ ...doneJob("j1"), state: "running" as const })),
    patchSyncSettings: vi.fn(async (pid: string, patch: Record<string, unknown>) => {
      const s = states.find((x) => x.id === pid)!;
      Object.assign(s, patch);
      if ("web_video" in patch) s.hash = H3; // web_video is part of the bundle
      return { ...s };
    }),
    getJob: vi.fn(async (id: string) => doneJob(id)),
  } satisfies LocalSyncApi;
  const cloud = {
    listSynced: vi.fn(async () => list.map((c) => ({ ...c }))),
    getSynced: vi.fn(async (id: string): Promise<CloudBundle> => ({ ...bundleOf(), id, rev: 3, video_url: "https://r2.example/v.mp4?sig" })),
    pushSynced: vi.fn(async (id: string, baseRev: number) => ({ id, rev: baseRev + 1 })),
    pushNew: vi.fn(async () => ({ id: "cnew", rev: 1 })),
    attachVideo: vi.fn(async (id: string) => cloudRow({ id, rev: 3, has_video_in_cloud: true })),
    deleteVideo: vi.fn(async (id: string) => cloudRow({ id, rev: 3 })),
    createUpload: vi.fn(async () => ({ upload_key: "uploads/k1", url: "https://r2.example/put?sig" })),
  } satisfies CloudApi;
  /** The cloud client of one unit of work, bound to its token: every request is recorded with the token it used. */
  const tokens: [string, string][] = [];
  const cloudFor = (token: string): CloudApi => Object.fromEntries(Object.entries(cloud).map(([name, fn]) => [
    name, (...args: unknown[]) => {
      tokens.push([name, token]);
      return (fn as (...a: unknown[]) => unknown)(...args);
    },
  ])) as unknown as CloudApi;
  return { local, cloud, cloudFor, tokens };
}

const calls = (o: Record<string, { mock: { calls: unknown[] } }>): number =>
  Object.values(o).reduce((n, f) => n + f.mock.calls.length, 0);

let stop: (() => void) | null = null;
const signedIn = async () => (account ? { token: `tok-${account}`, uid: account } : null);
let account: string | null = "uA"; // the signed-in user's id
const me = (): string | null => account;

beforeEach(() => {
  vi.useFakeTimers();
  account = "uA";
});

afterEach(() => {
  stop?.();
  stop = null;
  vi.useRealTimers();
});

describe("startSync", () => {
  it("without a cloud URL is a no-op", async () => {
    const { local } = fakes([state()], []);
    stop = startSync(local, null, signedIn, me);
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(calls(local)).toBe(0);
  });

  it("signed out makes no requests, and starts on sign-in", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow()]);
    let token: string | null = null;
    let signIn: () => void = () => undefined;
    stop = startSync(local, cloudFor, async () => (token ? { token, uid: "uA" } : null), me, { signIns: (fn) => ((signIn = fn), () => undefined) });
    await vi.advanceTimersByTimeAsync(3 * 60_000);
    expect(calls(local) + calls(cloud)).toBe(0);
    token = "tok";
    signIn();
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.listSynced).toHaveBeenCalledTimes(1);
  });

  it("pulls the list on start and every 60 s", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow()]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.listSynced).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(59_000);
    expect(cloud.listSynced).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1_000);
    expect(cloud.listSynced).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cloud.listSynced).toHaveBeenCalledTimes(3);
    stop();
    stop = null;
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(cloud.listSynced).toHaveBeenCalledTimes(3);
  });

  it("does not push a project whose bundle hash is unchanged", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow()]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(3 * 60_000);
    expect(cloud.pushSynced).not.toHaveBeenCalled();
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(local.projectBundle).not.toHaveBeenCalled();
  });

  it("pushes an edited project 5 s after its last edit, once", async () => {
    const states = [state()];
    const { local, cloud, cloudFor } = fakes(states, [cloudRow()]);
    let edit: (pid: string) => void = () => undefined;
    stop = startSync(local, cloudFor, signedIn, me, { edits: (fn) => ((edit = fn), () => undefined) });
    await vi.advanceTimersByTimeAsync(1_000); // the start cycle: nothing to do
    states[0].hash = H2;
    edit("p1");
    await vi.advanceTimersByTimeAsync(3_000);
    edit("p1");
    await vi.advanceTimersByTimeAsync(3_000);
    edit("p1");
    await vi.advanceTimersByTimeAsync(4_900);
    expect(cloud.pushSynced).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(100);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
    expect(cloud.pushSynced).toHaveBeenCalledWith("c1", 2, bundleOf());
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_rev: 3, cloud_hash: H2 });
    await vi.advanceTimersByTimeAsync(60_000); // the next cycle sees it clean
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
  });

  it("the 60 s cycle pushes what an edit signal missed, and a new project gets a cloud id", async () => {
    const states = [state({ id: "p2", cloud_id: null, cloud_rev: 0, cloud_hash: null, hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, []);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p2", { cloud_id: "cnew", cloud_rev: 1, cloud_hash: H2, cloud_owner: "uA" });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
  });

  it("imports a newer cloud revision in place when the local project is unchanged", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow({ rev: 3 })]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.getSynced).toHaveBeenCalledWith("c1");
    const body = local.importBundle.mock.calls[0][0] as { bundle: CloudBundle; video_url?: string };
    expect(body.video_url).toBeUndefined(); // the local video is there: nothing is downloaded
    expect(body.bundle.id).toBe("c1");
    expect(body).toMatchObject({ expect_hash: H1, cloud_owner: "uA" }); // refused by the engine if the project changed meanwhile
    expect("video_url" in body.bundle).toBe(false);
    expect(cloud.pushSynced).not.toHaveBeenCalled();
  });

  it("both sides changed: the push is kept as a copy, the local project points at it, and a toast says so", async () => {
    const states = [state({ hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, [cloudRow({ rev: 3 })]);
    cloud.pushSynced.mockResolvedValueOnce({ id: "c2", rev: 1, conflict: true, original: "c1" } as never);
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).toHaveBeenCalledWith("c1", 2, bundleOf());
    // linked to the copy at revision 0, so the copy itself is pulled in place (its "(copy)" title included)
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_id: "c2", cloud_rev: 0, cloud_hash: H2, cloud_owner: "uA" });
    expect(cloud.getSynced).toHaveBeenCalledWith("c2");
    expect(local.importBundle).toHaveBeenCalledWith(expect.objectContaining({ expect_hash: H2 }));
    expect(toast).toHaveBeenCalledWith("Saved a copy: Talk (copy)");
    // the original is now a cloud-only project
    expect(syncStore.getSnapshot().cloudOnly.map((c) => c.id)).toEqual(["c1"]);
  });

  it("after a conflict the local project takes the copy's title, and later pushes keep it", async () => {
    const states = [state({ hash: H2 })];
    const list = [cloudRow({ rev: 3 })];
    const { local, cloud, cloudFor } = fakes(states, list);
    local.projectBundle.mockImplementation(async () => bundleOf(states[0].title));
    cloud.pushSynced.mockResolvedValueOnce({ id: "c2", rev: 1, conflict: true, original: "c1" } as never);
    cloud.getSynced.mockImplementation(async (id: string) => ({ ...bundleOf(id === "c2" ? "Talk (copy)" : "Talk"), id, rev: id === "c2" ? 1 : 3 }));
    local.importBundle.mockImplementation(async (body) => {
      if (body.expect_hash !== states[0].hash) throw new EngineError(409, "bad_request", "changed");
      Object.assign(states[0], { title: body.bundle.title, cloud_rev: body.bundle.rev, hash: H3, cloud_hash: H3 });
      return { project: { id: "p1" } as never };
    });
    let edit: (pid: string) => void = () => undefined;
    stop = startSync(local, cloudFor, signedIn, me, { edits: (fn) => ((edit = fn), () => undefined) });
    await vi.advanceTimersByTimeAsync(0);
    expect(states[0]).toMatchObject({ cloud_id: "c2", title: "Talk (copy)", cloud_rev: 1, hash: H3, cloud_hash: H3 });
    list.push(cloudRow({ id: "c2", title: "Talk (copy)", rev: 1 }));
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1); // in step: nothing to push
    states[0].hash = H1; // the next local edit
    edit("p1");
    await vi.advanceTimersByTimeAsync(5_000);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(2);
    expect(cloud.pushSynced).toHaveBeenLastCalledWith("c2", 1, bundleOf("Talk (copy)"));
  });

  it("an edit landing between the snapshot and the import is not overwritten: it goes down the conflict path", async () => {
    const states = [state()];
    const { local, cloud, cloudFor } = fakes(states, [cloudRow({ rev: 3 })]);
    cloud.getSynced.mockImplementationOnce(async (id: string) => {
      states[0].hash = H2; // edited here while the bundle was on its way
      return { ...bundleOf(), id, rev: 3 };
    });
    local.importBundle.mockImplementation(async (body) => {
      if (body.expect_hash !== states[0].hash) throw new EngineError(409, "bad_request", "changed");
      return { project: { id: "p1" } as never };
    });
    cloud.pushSynced.mockResolvedValueOnce({ id: "c2", rev: 1, conflict: true, original: "c1" } as never);
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(local.importBundle).toHaveBeenCalledTimes(1);
    expect(states[0]).toMatchObject({ hash: H2, cloud_hash: H1, cloud_rev: 2 }); // refused: the edit stands
    expect(cloud.pushSynced).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cloud.pushSynced).toHaveBeenCalledWith("c1", 2, bundleOf());
    expect(toast).toHaveBeenCalledWith("Saved a copy: Talk (copy)");
  });

  it("a project removed on the website is uploaded again as a new one, once", async () => {
    const states = [state({ hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, []);
    cloud.pushSynced.mockRejectedValueOnce(new EngineError(404, "not_found", "Not found"));
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
    expect(cloud.pushNew).toHaveBeenCalledWith(bundleOf());
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_id: "cnew", cloud_rev: 1, cloud_hash: H2, cloud_owner: "uA" });
    expect(toast).toHaveBeenCalledWith("Talk was removed from the web, so it was uploaded again");
    await vi.advanceTimersByTimeAsync(3 * 60_000);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
  });

  it("a toast goes to the sync store when no handler is given", async () => {
    const { local, cloud, cloudFor } = fakes([state({ hash: H2 })], [cloudRow({ rev: 3 })]);
    cloud.pushSynced.mockResolvedValueOnce({ id: "c2", rev: 1, conflict: true, original: "c1" } as never);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    const toasts = syncStore.getSnapshot().toasts;
    expect(toasts.map((t) => t.message)).toEqual(["Saved a copy: Talk (copy)"]);
    syncStore.dismissToast(toasts[0].id);
    expect(syncStore.getSnapshot().toasts).toEqual([]);
  });

  it("lists cloud projects that are not on this computer, without downloading them", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow(), cloudRow({ id: "w1", title: "Web only", rev: 5 })]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(syncStore.getSnapshot().cloudOnly.map((c) => c.id)).toEqual(["w1"]);
    expect(cloud.getSynced).not.toHaveBeenCalled();
    expect(local.importBundle).not.toHaveBeenCalled();
  });

  it("a busy project (409) stays dirty and is retried on the next cycle, without a toast", async () => {
    const states = [state({ hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, [cloudRow()]);
    cloud.pushSynced.mockRejectedValueOnce(new EngineError(409, "bad_request", "This project is busy on the website. Try again in a moment."));
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
    expect(local.patchSyncSettings).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(2);
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_rev: 3, cloud_hash: H2 });
    expect(toast).not.toHaveBeenCalled();
  });

  it("network errors are retried quietly with backoff", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow()]);
    cloud.listSynced.mockRejectedValueOnce(new EngineError(0, "network", "could not reach the engine"));
    cloud.listSynced.mockRejectedValueOnce(new EngineError(0, "network", "could not reach the engine"));
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.listSynced).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5_000); // first retry well before the 60 s cycle
    expect(cloud.listSynced).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(cloud.listSynced).toHaveBeenCalledTimes(2); // the second waits longer
    await vi.advanceTimersByTimeAsync(5_000);
    expect(cloud.listSynced).toHaveBeenCalledTimes(3);
    expect(toast).not.toHaveBeenCalled();
  });
});

describe("accounts", () => {
  it("a project linked under another account is left alone: no request for it, no toast", async () => {
    account = "uB";
    const states = [state({ hash: H2 })]; // edited, linked by uA
    const { local, cloud, cloudFor } = fakes(states, []); // uB's cloud does not have it (a 404 if asked)
    cloud.pushSynced.mockRejectedValue(new EngineError(404, "not_found", "Not found"));
    const toast = vi.fn();
    let edit: (pid: string) => void = () => undefined;
    let signIn: () => void = () => undefined;
    stop = startSync(local, cloudFor, signedIn, me, {
      toast, edits: (fn) => ((edit = fn), () => undefined), signIns: (fn) => ((signIn = fn), () => undefined),
    });
    await vi.advanceTimersByTimeAsync(0);
    edit("p1");
    await vi.advanceTimersByTimeAsync(3 * 60_000);
    expect(local.projectBundle).not.toHaveBeenCalled();
    expect(cloud.pushSynced).not.toHaveBeenCalled();
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(cloud.getSynced).not.toHaveBeenCalled();
    expect(local.importBundle).not.toHaveBeenCalled();
    expect(local.patchSyncSettings).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
    expect(states[0]).toMatchObject({ cloud_id: "c1", cloud_owner: "uA" }); // the link to uA's project stays
    // back as uA: syncs as before
    cloud.pushSynced.mockReset();
    cloud.pushSynced.mockImplementation(async (id: string, baseRev: number) => ({ id, rev: baseRev + 1 }));
    account = "uA";
    signIn();
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).toHaveBeenCalledWith("c1", 2, bundleOf());
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_rev: 3, cloud_hash: H2 });
  });

  it("an unlinked project goes into the signed-in account", async () => {
    account = "uB";
    const { local, cloudFor } = fakes([state({ id: "p2", cloud_id: null, cloud_rev: 0, cloud_hash: null, cloud_owner: null, hash: H2 })], []);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p2", { cloud_id: "cnew", cloud_rev: 1, cloud_hash: H2, cloud_owner: "uB" });
  });

  it("a link made before owners were recorded is adopted by the signed-in account on its next push or pull", async () => {
    const states = [state({ cloud_owner: null, hash: H2 }), state({ id: "p3", cloud_id: "c3", cloud_owner: null })];
    const { local, cloudFor } = fakes(states, [cloudRow(), cloudRow({ id: "c3", rev: 3 })]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_rev: 3, cloud_hash: H2, cloud_owner: "uA" });
    expect(local.importBundle).toHaveBeenCalledWith(expect.objectContaining({ expect_hash: H1, cloud_owner: "uA" }));
  });

  it("a 404 for a link with no recorded owner is not taken as removed: no re-upload, no toast", async () => {
    const { local, cloud, cloudFor } = fakes([state({ cloud_owner: null, hash: H2 })], []);
    cloud.pushSynced.mockRejectedValue(new EngineError(404, "not_found", "Not found"));
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });

  it("with a session but no account id, nothing is requested", async () => {
    account = null;
    const { local, cloud, cloudFor } = fakes([state({ hash: H2 })], [cloudRow()]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(3 * 60_000);
    expect(calls(local) + calls(cloud)).toBe(0);
  });

  it("openCloudProject and setWebVideo use the signed-in account", async () => {
    const { local, cloud, cloudFor } = fakes([state({ cloud_owner: "uZ" })], [cloudRow({ id: "w1", rev: 5 })]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    await openCloudProject("w1");
    expect(local.importBundle).toHaveBeenCalledWith(expect.objectContaining({ cloud_owner: "uA" }));
    await expect(setWebVideo("p1", true)).rejects.toThrow("another account");
    expect(cloud.createUpload).not.toHaveBeenCalled();
  });
});

describe("switching accounts during a unit of work", () => {
  it("a switch between the snapshot and a push of a linked project writes nothing", async () => {
    const states = [state({ hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, []);
    cloud.pushSynced.mockRejectedValue(new EngineError(404, "not_found", "Not found"));
    local.projectBundle.mockImplementation(async () => {
      account = "uB"; // uA signs out and uB signs in while the bundle is read
      return bundleOf();
    });
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).not.toHaveBeenCalled();
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(local.patchSyncSettings).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
    expect(states[0]).toMatchObject({ cloud_id: "c1", cloud_owner: "uA", cloud_hash: H1 });
    // the next cycle runs as uB, which leaves uA's link alone
    await vi.advanceTimersByTimeAsync(2 * 60_000);
    expect(cloud.pushSynced).not.toHaveBeenCalled();
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(states[0]).toMatchObject({ cloud_id: "c1", cloud_owner: "uA" });
  });

  it("a switch after a 404 never re-uploads into the new account", async () => {
    const states = [state({ hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, []);
    cloud.pushSynced.mockImplementation(async () => {
      account = "uB";
      throw new EngineError(404, "not_found", "Not found");
    });
    const toast = vi.fn();
    stop = startSync(local, cloudFor, signedIn, me, { toast });
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushSynced).toHaveBeenCalledTimes(1);
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(local.patchSyncSettings).not.toHaveBeenCalled();
    expect(toast).not.toHaveBeenCalled();
  });

  it("a switch before pushNew of an unlinked project pushes nothing and records no owner", async () => {
    const states = [state({ id: "p2", cloud_id: null, cloud_rev: 0, cloud_hash: null, cloud_owner: null, hash: H2 })];
    const { local, cloud, cloudFor } = fakes(states, []);
    local.projectBundle.mockImplementationOnce(async () => {
      account = "uB";
      return bundleOf();
    });
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushNew).not.toHaveBeenCalled();
    expect(local.patchSyncSettings).not.toHaveBeenCalled();
    // the next cycle, as uB, pushes it into uB and says so
    await vi.advanceTimersByTimeAsync(60_000);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p2", { cloud_id: "cnew", cloud_rev: 1, cloud_hash: H2, cloud_owner: "uB" });
  });

  it("a switch during a pull imports nothing", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow({ rev: 3 })]);
    cloud.getSynced.mockImplementationOnce(async (id: string) => {
      account = "uB";
      return { ...bundleOf(), id, rev: 3 };
    });
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.getSynced).toHaveBeenCalledTimes(1);
    expect(local.importBundle).not.toHaveBeenCalled();
  });

  it("a switch during openCloudProject imports nothing", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow(), cloudRow({ id: "w1", rev: 5 })]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    cloud.getSynced.mockImplementationOnce(async (id: string) => {
      account = "uB";
      return { ...bundleOf(), id, rev: 5 };
    });
    await expect(openCloudProject("w1")).rejects.toThrow();
    expect(local.importBundle).not.toHaveBeenCalled();
  });

  it("every request of one unit uses the token read at its start", async () => {
    const states = [state({ hash: H2 })];
    const { local, cloud, cloudFor, tokens } = fakes(states, [cloudRow({ rev: 3 })]);
    cloud.pushSynced.mockResolvedValueOnce({ id: "c2", rev: 1, conflict: true, original: "c1" } as never);
    let token = "tok-1";
    const session = vi.fn(async () => ({ token, uid: "uA" }));
    local.projectBundle.mockImplementation(async () => {
      token = "tok-2"; // refreshed (or another account's) while the unit runs
      return bundleOf();
    });
    stop = startSync(local, cloudFor, session, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(session).toHaveBeenCalledTimes(1);
    expect(tokens.map(([name]) => name)).toEqual(["listSynced", "pushSynced", "getSynced"]);
    expect(tokens.every(([, t]) => t === "tok-1")).toBe(true);
    await vi.advanceTimersByTimeAsync(60_000); // the next unit reads the session again
    expect(tokens.slice(3).every(([, t]) => t === "tok-2")).toBe(true);
    expect(tokens.length).toBeGreaterThan(3);
  });
});

describe("cloud projects and the web video", () => {
  it("openCloudProject downloads the video once through the local engine", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow(), cloudRow({ id: "w1", rev: 5 })]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    local.importBundle.mockResolvedValueOnce({ job: { ...doneJob("j7"), state: "running" } } as never);
    const r = await openCloudProject("w1");
    expect(r.job?.id).toBe("j7");
    expect(cloud.getSynced).toHaveBeenCalledWith("w1");
    const body = local.importBundle.mock.calls[0][0] as { bundle: CloudBundle; video_url?: string };
    expect(body.video_url).toBe("https://r2.example/v.mp4?sig");
    expect("video_url" in body.bundle).toBe(false);
  });

  it("setWebVideo(on) uploads through the local engine, attaches it and records it", async () => {
    const states = [state()];
    const { local, cloud, cloudFor } = fakes(states, [cloudRow()]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    const p = setWebVideo("p1", true);
    await vi.advanceTimersByTimeAsync(2_000);
    await p;
    expect(cloud.createUpload).toHaveBeenCalledWith({ kind: "video", name: "talk.mp4", size: 1234, content_type: "video/mp4" });
    expect(local.publishVideo).toHaveBeenCalledWith("p1", "https://r2.example/put?sig");
    expect(cloud.attachVideo).toHaveBeenCalledWith("c1", "uploads/k1");
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { web_video: true, cloud_rev: 3 });
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { cloud_hash: H3 }); // in step: nothing to push
    expect(states[0]).toMatchObject({ web_video: true, cloud_rev: 3, cloud_hash: H3, hash: H3 });
  });

  it("setWebVideo(on) surfaces the cloud's refusal", async () => {
    const { local, cloud, cloudFor } = fakes([state()], [cloudRow()]);
    cloud.attachVideo.mockRejectedValueOnce(new EngineError(400, "bad_request", "Videos can be up to 60 minutes"));
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    const p = setWebVideo("p1", true);
    const seen = expect(p).rejects.toThrow("Videos can be up to 60 minutes");
    await vi.advanceTimersByTimeAsync(2_000);
    await seen;
    expect(local.patchSyncSettings).not.toHaveBeenCalled();
  });

  it("setWebVideo(off) deletes the cloud video only", async () => {
    const states = [state({ web_video: true })];
    const { local, cloud, cloudFor } = fakes(states, [cloudRow()]);
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    await setWebVideo("p1", false);
    expect(cloud.deleteVideo).toHaveBeenCalledWith("c1");
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", { web_video: false, cloud_rev: 3 });
    expect(local.publishVideo).not.toHaveBeenCalled();
  });
});

describe("what the UI reads", () => {
  it("after a cycle: every project's row and the cloud's ids; a push in flight is busy", async () => {
    const { local, cloud, cloudFor } = fakes([state({ cloud_id: null, cloud_hash: null })], [cloudRow({ id: "c7" })]);
    let release!: () => void;
    cloud.pushNew.mockImplementationOnce(() => new Promise((r) => (release = () => r({ id: "cnew", rev: 1 }))));
    stop = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(syncStore.getSnapshot().running).toBe(true);
    expect(syncStore.getSnapshot().busy).toEqual(["p1"]);
    release();
    await vi.advanceTimersByTimeAsync(0);
    const snap = syncStore.getSnapshot();
    expect(snap.busy).toEqual([]);
    expect(snap.states.map((x) => [x.id, x.cloud_id])).toEqual([["p1", "cnew"]]);
    expect(snap.cloudIds.sort()).toEqual(["c7", "cnew"]);
    stop();
    stop = null;
    expect(syncStore.getSnapshot()).toMatchObject({ running: false, states: [], cloudIds: [], busy: [] });
  });

  it("the web video upload reports the job's progress", async () => {
    const { local, cloudFor } = fakes([state()], [cloudRow()]);
    local.getJob.mockImplementationOnce(async (id: string) => ({ ...doneJob(id), state: "running" as const, pct: 40 }));
    stop = startSync(local, cloudFor, signedIn, me, { jobPollMs: 10 });
    await vi.advanceTimersByTimeAsync(0);
    const seen: number[] = [];
    const done = setWebVideo("p1", true, (n) => seen.push(n));
    await vi.advanceTimersByTimeAsync(100);
    await done;
    expect(seen).toEqual([100, 40, 100]);
  });
});

describe("Sync projects with your account", () => {
  afterEach(() => localStorage.removeItem("vedit.sync.enabled"));

  it("is on by default; off stops sync and on starts it again, remembered on this computer", async () => {
    expect(syncEnabled()).toBe(true);
    const stops = vi.fn();
    const start = vi.fn(() => stops);
    const off = bootSyncWith(start);
    expect(start).toHaveBeenCalledTimes(1);
    expect(syncStore.getSnapshot().available).toBe(true);
    setSyncEnabled(false);
    expect(stops).toHaveBeenCalledTimes(1);
    expect(syncEnabled()).toBe(false);
    setSyncEnabled(true);
    await vi.advanceTimersByTimeAsync(0); // once the stopped one has finished its step
    expect(start).toHaveBeenCalledTimes(2);
    off();
    expect(syncStore.getSnapshot().available).toBe(false);
  });

  it("switched off before the app starts, sync does not start", () => {
    setSyncEnabled(false);
    const start = vi.fn(() => () => undefined);
    const off = bootSyncWith(start);
    expect(start).not.toHaveBeenCalled();
    off();
  });
});

describe("stopping", () => {
  afterEach(() => localStorage.removeItem("vedit.sync.enabled"));

  it("stopped during a pending push: the link is still recorded, then nothing else is sent", async () => {
    const { local, cloud, cloudFor } = fakes(
      [state({ id: "p1", cloud_id: null, cloud_hash: null }), state({ id: "p2", cloud_id: null, cloud_hash: null })], []);
    let release!: () => void;
    cloud.pushNew.mockImplementationOnce(() => new Promise((r) => (release = () => r({ id: "cnew", rev: 1 }))));
    const halt = startSync(local, cloudFor, signedIn, me);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
    const before = calls(cloud);
    const drained = halt();
    release();
    await vi.advanceTimersByTimeAsync(0);
    await drained;
    // the cloud made the project: this computer records the link, or the next start would make it again
    expect(local.patchSyncSettings).toHaveBeenCalledWith("p1", expect.objectContaining({ cloud_id: "cnew" }));
    await vi.advanceTimersByTimeAsync(5 * 60_000);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1); // p2 is never pushed
    expect(calls(cloud)).toBe(before);
  });

  it("a quick off and on runs one sync at a time: an unlinked project is pushed once", async () => {
    const { local, cloud, cloudFor } = fakes([state({ cloud_id: null, cloud_hash: null })], []);
    let release!: () => void;
    cloud.pushNew.mockImplementationOnce(() => new Promise((r) => (release = () => r({ id: "cnew", rev: 1 }))));
    const off = bootSyncWith(() => startSync(local, cloudFor, signedIn, me));
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
    setSyncEnabled(false);
    setSyncEnabled(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(cloud.listSynced).toHaveBeenCalledTimes(1); // the new sync waits for the old one's push
    release();
    await vi.advanceTimersByTimeAsync(3 * 60_000);
    expect(cloud.pushNew).toHaveBeenCalledTimes(1);
    expect(cloud.listSynced.mock.calls.length).toBeGreaterThan(1); // and then runs
    off();
  });
});
