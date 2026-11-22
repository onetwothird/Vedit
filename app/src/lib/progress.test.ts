import { describe, expect, it } from "vitest";
import { secondsLeft, stageRows, stageShares } from "./progress";

describe("stageShares", () => {
  it("has four stages summing to 1 without a model step", () => {
    const s = stageShares(false);
    expect(s.map((x) => x.key)).toEqual(["proxy", "analyze", "highlights", "framing"]);
    expect(s.reduce((a, x) => a + x.share, 0)).toBeCloseTo(1);
  });
  it("puts the model first at 0.3 and scales the rest by 0.7", () => {
    const s = stageShares(true);
    expect(s[0]).toEqual({ key: "model", share: 0.3 });
    expect(s[2].share).toBeCloseTo(0.42);
    expect(s.reduce((a, x) => a + x.share, 0)).toBeCloseTo(1);
  });
});

describe("stageRows", () => {
  it("marks earlier stages done, the current one with its own pct, later ones pending", () => {
    // proxy 0-10, analyze 10-70: overall 40 is halfway through analyze
    const rows = stageRows("analyze", 40, false);
    expect(rows.map((r) => r.status)).toEqual(["done", "current", "pending", "pending"]);
    expect(rows[1].pct).toBe(50);
  });
  it("uses the scaled table when a model step is present", () => {
    const rows = stageRows("model", 15, true);
    expect(rows[0]).toMatchObject({ key: "model", status: "current", pct: 50 });
  });
  it("is all pending before the first stage and all done when finished", () => {
    expect(stageRows("", 0, false).every((r) => r.status === "pending")).toBe(true);
    expect(stageRows("framing", 100, false, true).every((r) => r.status === "done")).toBe(true);
  });
  it("clamps the current pct to 0-100", () => {
    expect(stageRows("proxy", 12, false)[0].pct).toBe(100);
  });
});

describe("secondsLeft", () => {
  it("waits until pct is above 3", () => {
    expect(secondsLeft(5000, 3)).toBeNull();
    expect(secondsLeft(5000, 0)).toBeNull();
  });
  it("is elapsed / pct x remaining pct", () => {
    expect(secondsLeft(10_000, 25)).toBe(30);
  });
});
