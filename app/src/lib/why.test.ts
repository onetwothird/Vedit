import { describe, expect, it } from "vitest";
import { whyPicked } from "./why";
import { formatClock } from "./time";

describe("whyPicked", () => {
  it("keeps strong known features, strongest weighted first", () => {
    expect(whyPicked({ energy: 0.9, hook: 0.7, laughter: 0.6, face: 0.9, filler_density: 0.8 })).toEqual(["hook", "energy", "laughter"]);
  });
  it("drops weak features and caps the list", () => {
    expect(whyPicked({ hook: 0.2, energy: 0.5 })).toEqual([]);
    expect(whyPicked({ hook: 1, energy: 1, complete: 1, laughter: 1 }, 2)).toEqual(["hook", "energy"]);
  });
});

describe("formatClock", () => {
  it("writes minutes, or hours past the hour", () => {
    expect(formatClock(372.6)).toBe("6:12");
    expect(formatClock(4349)).toBe("1:12:29");
    expect(formatClock(-3)).toBe("0:00");
  });
});
