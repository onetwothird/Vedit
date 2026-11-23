import { describe, expect, it } from "vitest";
import { cueAt, popAt } from "./HomeArt";

describe("hero preview captions", () => {
  it("shows the line being said at each moment", () => {
    expect(cueAt(0)).toBe("Bakit? Bawal ba?");
    expect(cueAt(2.2)).toBe("Akala po niya, sabi niya po sa akin kanina parang,");
    expect(cueAt(5)).toBe("si Jy po, di ba, 1983 po pinanganak?");
    expect(cueAt(9.9)).toBe("Sir, may narinig ka.");
  });

  it("pops up to three words at a time, in order", () => {
    expect(popAt(0)).toEqual(["Bakit?", "Bawal", "ba?"]);
    expect(popAt(4.4)).toEqual(["si", "Jy", "po,"]);
    expect(popAt(7.2)).toEqual(["po", "pinanganak?"]);
  });
});
