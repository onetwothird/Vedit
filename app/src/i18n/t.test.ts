import { afterEach, describe, expect, it, vi } from "vitest";
import { t } from "./t";

describe("t", () => {
  afterEach(() => vi.restoreAllMocks());

  it("interpolates counts", () => {
    expect(t("export.button", { n: 8 })).toBe("Export 8 clips");
  });

  it("returns the key and warns in dev when missing", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(t("no.such.key")).toBe("no.such.key");
    expect(warn).toHaveBeenCalledOnce();
  });

  it("leaves unknown placeholders intact", () => {
    expect(t("export.button")).toBe("Export {n} clips");
  });
});
