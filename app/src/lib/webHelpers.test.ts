import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ listAssets: vi.fn() }));
vi.mock("../engine/client", () => ({ engine: api, isMock: () => false, isTauri: () => false }));

import { assetName, loadAssetNames, rememberAssetInfo, resetAssetInfo } from "./assetInfo";
import { baseName } from "./native";
import { brollType } from "./overlayMedia";
import { estimateStorySeconds, STORY_MAX_SECONDS } from "./storyLength";
import { batchProblem, runPool } from "./upload";

describe("asset names", () => {
  beforeEach(() => {
    resetAssetInfo();
    api.listAssets.mockReset();
  });

  it("shows the name a file was uploaded with, never the id", async () => {
    api.listAssets.mockResolvedValue([{ asset_id: "a000000000001", kind: "image", name: "beach.png", size: 3 }]);
    expect(baseName("asset:a000000000001")).toBe("Uploaded file");
    await loadAssetNames();
    expect(baseName("asset:a000000000001")).toBe("beach.png");
    expect(brollType("asset:a000000000001")).toBe("image");
    expect(baseName("C:/clips/x.mp4")).toBe("x.mp4");
  });

  it("asks the engine once, however many names are wanted", async () => {
    api.listAssets.mockResolvedValue([]);
    await Promise.all([loadAssetNames(), loadAssetNames(), loadAssetNames()]);
    expect(api.listAssets).toHaveBeenCalledTimes(1);
  });

  it("asks again after a failure", async () => {
    api.listAssets.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce([
      { asset_id: "a000000000002", kind: "video", name: "trip.mp4", size: 3 },
    ]);
    await loadAssetNames();
    await loadAssetNames();
    expect(assetName("asset:a000000000002")).toBe("trip.mp4");
  });

  it("knows files uploaded in this session without asking", () => {
    rememberAssetInfo("asset:a000000000003", "logo.png", "image");
    expect(baseName("asset:a000000000003")).toBe("logo.png");
    expect(api.listAssets).not.toHaveBeenCalled();
  });
});

describe("story length", () => {
  const scene = (kind: "image" | "video", text = "", media = "asset:a1", keep_audio = false) =>
    ({ media, kind, text, motion: "auto" as const, keep_audio });

  it("counts narration at 150 words a minute, a silent photo 3 s, a clip its own length (as the engine does)", () => {
    const durations = new Map([["asset:v", 12]]);
    const r = estimateStorySeconds([
      scene("image", "one two three four five"), // 2 s
      scene("image"), // 3 s
      scene("video", "", "asset:v"), // its 12 s
      scene("video", "said over it", "asset:v", true), // its own sound: 12 s
      scene("video", "a b c d e f g h i j", "asset:v"), // narrated: 4 s
    ], durations);
    expect(r).toEqual({ seconds: 33, unknown: 0 });
  });

  it("ignores blank lines and extra spaces", () => {
    expect(estimateStorySeconds([scene("image", "  one   two\n\n three  four five ")], new Map()).seconds).toBe(2);
  });

  it("counts clips whose length is not known", () => {
    expect(estimateStorySeconds([scene("video", "", "asset:x")], new Map())).toEqual({ seconds: 0, unknown: 1 });
  });

  it("caps at three minutes", () => {
    expect(STORY_MAX_SECONDS).toBe(180);
  });
});

describe("batchProblem", () => {
  const usage = { seconds_used: 0, seconds_limit: 3600, storage_bytes: 0, storage_limit: 1 };
  const v = (name: string, duration: number | null, size = 10) => ({ name, duration, size });

  it("is null when the videos fit", () => {
    expect(batchProblem([v("a.mp4", 600), v("b.mp4", 1200)], usage)).toBeNull();
  });

  it("adds the lengths up and words them the engine's way", () => {
    expect(batchProblem([v("a.mp4", 1), v("b.mp4", 1)], { ...usage, seconds_used: 3599 }))
      .toBe("These videos are 1 min; you have 0 min left this month");
    expect(batchProblem([v("a.mp4", 1900), v("b.mp4", 1900)], usage)).toBe("These videos are 64 min; you have 60 min left this month");
  });

  it("names a video that is too long or too big", () => {
    expect(batchProblem([v("a.mp4", 10), v("long.mp4", 3700)], usage)).toBe("long.mp4: Videos can be up to 60 minutes");
    expect(batchProblem([v("big.mp4", 10, 4 * 1024 ** 3)], usage)).toBe("big.mp4: Videos can be up to 3 GB");
  });

  it("leaves a video of unknown length to the engine", () => {
    expect(batchProblem([v("a.avi", null)], { ...usage, seconds_used: 3600 })).toBeNull();
  });
});

describe("runPool", () => {
  it("runs at most `size` tasks at once, all of them, in order of start", async () => {
    let running = 0;
    let most = 0;
    const started: number[] = [];
    const gates = Array.from({ length: 5 }, () => {
      let open!: () => void;
      const p = new Promise<void>((r) => (open = r));
      return { p, open };
    });
    const done = runPool([0, 1, 2, 3, 4], 3, async (i) => {
      started.push(i);
      running++;
      most = Math.max(most, running);
      await gates[i].p;
      running--;
    });
    await Promise.resolve();
    expect(started).toEqual([0, 1, 2]);
    gates[1].open();
    await new Promise((r) => setTimeout(r, 0));
    expect(started).toEqual([0, 1, 2, 3]);
    gates.forEach((g) => g.open());
    await done;
    expect(started).toEqual([0, 1, 2, 3, 4]);
    expect(most).toBe(3);
  });
});
