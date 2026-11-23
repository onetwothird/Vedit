import { beforeEach, describe, expect, it } from "vitest";
import { getSettings, saveSettings, setAiProvider, setLanguage, getAiProvider, getLanguage } from "./prefs";

beforeEach(() => localStorage.clear());

describe("settings prefs", () => {
  it("defaults to saving next to the video", () => {
    expect(getSettings()).toEqual({ outputMode: "next", outputFolder: "" });
  });

  it("round-trips a chosen folder in vedit.settings", () => {
    saveSettings({ outputMode: "folder", outputFolder: "D:\Clips" });
    expect(getSettings()).toEqual({ outputMode: "folder", outputFolder: "D:\Clips" });
    expect(localStorage.getItem("vedit.settings")).toContain("D:");
  });

  it("falls back to next-to-the-video when the folder mode has no folder or the JSON is damaged", () => {
    localStorage.setItem("vedit.settings", JSON.stringify({ outputMode: "folder", outputFolder: "" }));
    expect(getSettings().outputMode).toBe("next");
    localStorage.setItem("vedit.settings", "{oops");
    expect(getSettings()).toEqual({ outputMode: "next", outputFolder: "" });
  });

  it("writes the per-key defaults Import reads", () => {
    setLanguage("tl");
    setAiProvider("openai");
    expect(getLanguage()).toBe("tl");
    expect(getAiProvider()).toBe("openai");
  });
});
