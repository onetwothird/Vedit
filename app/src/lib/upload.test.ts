import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EngineError } from "../engine/types";

const api = vi.hoisted(() => ({
  createUpload: vi.fn(),
  commitAsset: vi.fn(),
}));
vi.mock("../engine/client", () => ({ engine: api }));

import { commitAsset, importProblem, uploadFile, videoDuration } from "./upload";

class FakeXHR {
  static last: FakeXHR | null = null;
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: unknown = null;
  status = 0;
  aborted = false;
  upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    FakeXHR.last = this;
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(body: unknown) {
    this.body = body;
  }
  abort() {
    this.aborted = true;
    this.onabort?.();
  }
}

const file = (name: string, type: string, size = 10): File => {
  const f = new File(["x"], name, { type });
  Object.defineProperty(f, "size", { value: size });
  return f;
};

beforeEach(() => {
  FakeXHR.last = null;
  api.createUpload.mockReset().mockResolvedValue({ upload_key: "u/abc", url: "https://r2.example/put?sig=1" });
  api.commitAsset.mockReset();
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

const started = () => vi.waitFor(() => expect(FakeXHR.last?.body).not.toBeNull());

describe("uploadFile", () => {
  it("asks for a presigned URL, PUTs the file with its type, and reports progress", async () => {
    const f = file("talk.mp4", "video/mp4", 100);
    const seen: number[] = [];
    const p = uploadFile(f, "video", (n) => seen.push(n));
    await started();
    expect(api.createUpload).toHaveBeenCalledWith({ kind: "video", name: "talk.mp4", size: 100, content_type: "video/mp4" });
    const x = FakeXHR.last!;
    expect(x.method).toBe("PUT");
    expect(x.url).toBe("https://r2.example/put?sig=1");
    expect(x.headers).toEqual({ "Content-Type": "video/mp4" });
    expect(x.body).toBe(f);
    x.upload.onprogress?.({ lengthComputable: true, loaded: 25, total: 100 });
    x.upload.onprogress?.({ lengthComputable: true, loaded: 100, total: 100 });
    x.status = 200;
    x.onload?.();
    await expect(p).resolves.toEqual({ upload_key: "u/abc" });
    expect(seen).toEqual([25, 100]);
  });

  it("falls back to a type from the file name when the browser gives none", async () => {
    const p = uploadFile(file("clip.mkv", ""), "video", () => undefined);
    await started();
    expect(api.createUpload.mock.calls[0][0].content_type).toBe("video/x-matroska");
    FakeXHR.last!.status = 200;
    FakeXHR.last!.onload?.();
    await p;
  });

  it("aborts the request when the signal fires and rejects as aborted", async () => {
    const ctl = new AbortController();
    const p = uploadFile(file("talk.mp4", "video/mp4"), "video", () => undefined, ctl.signal);
    await started();
    ctl.abort();
    expect(FakeXHR.last!.aborted).toBe(true);
    await expect(p).rejects.toMatchObject({ name: "UploadError", code: "aborted" });
  });

  it("does not start when the signal is already aborted", async () => {
    const ctl = new AbortController();
    ctl.abort();
    await expect(uploadFile(file("a.mp4", "video/mp4"), "video", () => undefined, ctl.signal)).rejects.toMatchObject({ code: "aborted" });
    expect(FakeXHR.last).toBeNull();
  });

  it("refuses a file over its kind's limit before asking for a URL", async () => {
    const big = file("photo.png", "image/png", 21 * 1024 * 1024);
    await expect(uploadFile(big, "image", () => undefined)).rejects.toMatchObject({
      code: "too_large",
      message: "An image can be up to 20 MB",
    });
    expect(api.createUpload).not.toHaveBeenCalled();
    await expect(uploadFile(file("v.mp4", "video/mp4", 3 * 1024 ** 3 + 1), "video", () => undefined)).rejects.toMatchObject({
      message: "A video can be up to 3 GB",
    });
  });

  it("rejects when the storage answers with an error status", async () => {
    const p = uploadFile(file("a.mp4", "video/mp4"), "video", () => undefined);
    await started();
    FakeXHR.last!.status = 403;
    FakeXHR.last!.onload?.();
    await expect(p).rejects.toMatchObject({ code: "failed" });
  });

  it("rejects when the network drops", async () => {
    const p = uploadFile(file("a.mp4", "video/mp4"), "video", () => undefined);
    await started();
    FakeXHR.last!.onerror?.();
    await expect(p).rejects.toMatchObject({ code: "failed" });
  });

  it("passes the engine's refusal of the request through", async () => {
    api.createUpload.mockRejectedValue(new EngineError(400, "bad_request", "Not enough storage"));
    await expect(uploadFile(file("a.mp4", "video/mp4"), "video", () => undefined)).rejects.toMatchObject({ message: "Not enough storage" });
  });
});

describe("commitAsset", () => {
  it("returns the asset", async () => {
    api.commitAsset.mockResolvedValue({ asset_id: "a1", kind: "image", name: "x.png", size: 5 });
    await expect(commitAsset("u/abc")).resolves.toMatchObject({ asset_id: "a1" });
    expect(api.commitAsset).toHaveBeenCalledWith("u/abc");
  });

  it("tries again when the same key is being committed (409)", async () => {
    vi.useFakeTimers();
    api.commitAsset
      .mockRejectedValueOnce(new EngineError(409, "conflict", "in flight"))
      .mockResolvedValue({ asset_id: "a1", kind: "image", name: "x.png", size: 5 });
    const p = commitAsset("u/abc");
    await vi.advanceTimersByTimeAsync(1000);
    await expect(p).resolves.toMatchObject({ asset_id: "a1" });
    expect(api.commitAsset).toHaveBeenCalledTimes(2);
  });
});

describe("importProblem", () => {
  const usage = (used: number) => ({ seconds_used: used, seconds_limit: 3600, storage_bytes: 0, storage_limit: 10 * 1024 ** 3 });
  it("is null when the video fits", () => {
    expect(importProblem({ duration: 600, size: 1000 }, usage(0))).toBeNull();
    expect(importProblem({ duration: 600, size: 1000 }, usage(3000))).toBeNull();
  });
  it("words the minutes the way the engine counts them", () => {
    // 10 min 1 s counts as 11; 3000 s used leaves floor(600 / 60) = 10
    expect(importProblem({ duration: 601, size: 1000 }, usage(3000))).toBe("This video is 11 min; you have 10 min left this month");
    // 59 s left is 0 whole minutes
    expect(importProblem({ duration: 30, size: 1000 }, usage(3541))).toBe("This video is 1 min; you have 0 min left this month");
  });
  it("refuses over 60 minutes and over 3 GB first", () => {
    expect(importProblem({ duration: 3601, size: 1000 }, usage(0))).toBe("Videos can be up to 60 minutes");
    expect(importProblem({ duration: 60, size: 3 * 1024 ** 3 + 1 }, usage(0))).toBe("Videos can be up to 3 GB");
  });
  it("skips the minutes check when the duration is unknown", () => {
    expect(importProblem({ duration: null, size: 1000 }, usage(3599))).toBeNull();
  });
});

describe("videoDuration", () => {
  function fakeVideo() {
    const v: Record<string, unknown> = { duration: 12.5, removeAttribute: vi.fn() };
    v.load = vi.fn(() => (v.duration = NaN)); // load() resets a real element, so its length must be read first
    const real = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tag: string) => (tag === "video" ? v : real(tag))) as typeof document.createElement);
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    return v;
  }
  afterEach(() => vi.restoreAllMocks());

  it("reads the length from the file's metadata and frees the object URL", async () => {
    const v = fakeVideo();
    const p = videoDuration(file("a.mp4", "video/mp4"));
    expect(v.src).toBe("blob:x");
    (v.onloadedmetadata as () => void)();
    await expect(p).resolves.toBe(12.5);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
  });

  it("rejects when the browser cannot read the file", async () => {
    const v = fakeVideo();
    const p = videoDuration(file("a.avi", "video/x-msvideo"));
    (v.onerror as () => void)();
    await expect(p).rejects.toBeInstanceOf(Error);
  });
});
