import { beforeEach, describe, expect, it } from "vitest";
import { getTheme, setTheme } from "./theme";

describe("theme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.className = "";
    document.head.innerHTML = '<meta name="color-scheme" content="dark">';
  });

  it("defaults to dark", () => {
    expect(getTheme()).toBe("dark");
  });

  it("setTheme light adds class, persists and updates color-scheme", () => {
    setTheme("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem("vedit.theme")).toBe("light");
    expect(getTheme()).toBe("light");
    expect(document.querySelector('meta[name="color-scheme"]')?.getAttribute("content")).toBe("light");
  });

  it("setTheme dark removes class", () => {
    setTheme("light");
    setTheme("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
    expect(getTheme()).toBe("dark");
  });
});
