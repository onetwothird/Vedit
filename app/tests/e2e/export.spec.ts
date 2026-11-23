import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const URL = "/#/p/mock-1/export";

/** Leaves only the named kept clips switched on. */
async function only(page: Page, titles: string[]) {
  const all = page.getByRole("list").filter({ has: page.getByRole("checkbox") }).first().getByRole("checkbox");
  for (const box of await all.all()) {
    const name = (await box.evaluate((el) => el.closest("label")?.textContent ?? "")).trim();
    await box.setChecked(titles.includes(name), { force: true });
  }
}

test("starts with the kept clips on, 9:16, Normal, and both extras saved next to the video", async ({ page }) => {
  await page.goto(URL);
  await expect(page.getByRole("heading", { name: "Export", exact: true })).toBeVisible();
  const clips = page.getByRole("checkbox", { name: /^(Nobody tells you|The ₱500|Why I almost|My boss|Hot take|How I find|The one tool|Taglish works)/ });
  await expect(clips).toHaveCount(8);
  for (const box of await clips.all()) await expect(box).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Long story about my laptop" })).toHaveCount(0); // not kept

  await expect(page.getByRole("checkbox", { name: "Vertical 9:16" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Square 1:1" })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Wide 16:9" })).not.toBeChecked();
  await expect(page.getByRole("radio", { name: "Normal" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "High" })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Subtitles (.srt)" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Thumbnail (.jpg)" })).toBeChecked();

  await expect(page.getByTestId("export-folder")).toHaveText("C:/Videos/podcast-ep12 - Vedit");
  await expect(page.getByRole("button", { name: "Change folder" })).toBeDisabled(); // browser: the folder dialog is desktop only
  await expect(page.getByRole("button", { name: "Export 8 clips" })).toBeEnabled();

  await only(page, ["Nobody tells you this when you start"]);
  await expect(page.getByRole("button", { name: "Export 1 clip" })).toBeEnabled();
  await page.getByRole("checkbox", { name: "Vertical 9:16" }).uncheck({ force: true });
  await expect(page.getByRole("button", { name: "Export 1 clip" })).toBeDisabled();
});

test("exports each clip with its own progress, then lists the finished files", async ({ page }) => {
  await page.goto(URL);
  await only(page, ["Nobody tells you this when you start", "The ₱500 mistake on my first gig"]);
  await page.getByRole("button", { name: "Export 2 clips" }).click();

  await expect(page.getByRole("heading", { name: /Exporting \d of 2/ })).toBeVisible();
  const bar = page.getByRole("progressbar", { name: /Exporting / }).first();
  await expect(bar).toBeVisible();
  await expect(bar).toHaveAttribute("aria-valuenow", /\d+/);

  await expect(page.getByRole("heading", { name: "2 clips exported" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("01 Nobody tells you this when you start.mp4")).toBeVisible();
  await expect(page.getByText("02 The ₱500 mistake on my first gig.mp4")).toBeVisible();
  const show = page.getByRole("button", { name: "Show in folder" });
  await expect(show).toHaveCount(2);
  await expect(show.first()).toBeDisabled(); // browser: the desktop app opens Explorer
});

test("shows a chosen folder with accented and non-Latin names intact", async ({ page }) => {
  const folder = "D:\\Mga Bidyo\\Ñandú 日本語";
  await page.goto("/");
  await page.evaluate((f) => localStorage.setItem("vedit.settings", JSON.stringify({ outputMode: "folder", outputFolder: f })), folder);
  await page.goto(URL);
  await expect(page.getByTestId("export-folder")).toHaveText(folder);
  await only(page, ["Nobody tells you this when you start"]);
  await page.getByRole("button", { name: "Export 1 clip" }).click();
  await expect(page.getByRole("heading", { name: "1 clip exported" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(folder, { exact: true })).toBeVisible();
  await expect(page.getByText("01 Nobody tells you this when you start.mp4")).toHaveAttribute("title", `${folder}\\01 Nobody tells you this when you start.mp4`);
});

test("one failed clip does not stop the others, and Try again re-runs only that clip", async ({ page }) => {
  await page.goto(URL);
  await only(page, ["Nobody tells you this when you start", "The ₱500 mistake on my first gig", "Why I almost quit after a year"]);
  await page.evaluate(() => { (window as unknown as { __veditMockFail: string[] }).__veditMockFail = ["h02"]; });
  await page.getByRole("button", { name: "Export 3 clips" }).click();

  await expect(page.getByRole("heading", { name: "2 exported, 1 failed" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("01 Nobody tells you this when you start.mp4")).toBeVisible();
  await expect(page.getByText("03 Why I almost quit after a year.mp4")).toBeVisible();
  const failed = page.getByRole("listitem").filter({ hasText: "The ₱500 mistake on my first gig" }).first();
  await expect(failed.getByRole("alert")).toContainText("The render stopped");
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(1);

  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("heading", { name: "3 clips exported" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("02 The ₱500 mistake on my first gig.mp4")).toBeVisible(); // keeps its number
  await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
});

test("fits a 375px phone without sideways scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto(URL);
  await expect(page.getByRole("button", { name: "Export 8 clips" })).toBeVisible();
  const over = () => page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth
    || [...document.querySelectorAll(".screen")].some((e) => e.scrollWidth > e.clientWidth));
  expect(await over()).toBe(false);
  await page.getByRole("button", { name: "Export 8 clips" }).click();
  await expect(page.getByRole("heading", { name: "8 clips exported" })).toBeVisible({ timeout: 30_000 });
  expect(await over()).toBe(false);
});

for (const scheme of ["dark", "light"] as const) {
  test(`no serious accessibility problems in ${scheme} theme`, async ({ page }) => {
    await page.goto("/");
    await page.evaluate((v) => localStorage.setItem("vedit.theme", v), scheme);
    await page.goto(URL);
    await page.reload();
    await expect(page.getByRole("button", { name: "Export 8 clips" })).toBeVisible();
    const check = async () => {
      const res = await new AxeBuilder({ page }).analyze();
      const bad = res.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
    };
    await check();
    await only(page, ["Nobody tells you this when you start"]);
    await page.getByRole("button", { name: "Export 1 clip" }).click();
    await expect(page.getByRole("heading", { name: "1 clip exported" })).toBeVisible({ timeout: 20_000 });
    await check();
  });
}

test("Cancel stops the clips still waiting; finished ones stay and can be retried one by one", async ({ page }) => {
  await page.goto(URL);
  await only(page, ["Nobody tells you this when you start", "The ₱500 mistake on my first gig", "Why I almost quit after a year"]);
  await page.getByRole("button", { name: "Export 3 clips" }).click();
  await page.getByRole("button", { name: "Cancel export" }).click();
  await expect(page.getByRole("heading", { name: /\d exported, \d failed/ })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Cancelled").first()).toBeVisible();
  const retry = page.getByRole("button", { name: "Try again" });
  await retry.first().dblclick(); // a double click starts one job, not two
  await expect(page.getByRole("button", { name: "Change options" })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("01 Nobody tells you this when you start.mp4")).toHaveCount(1);
});

test("coming back to a running export waits for it instead of starting a duplicate", async ({ page }) => {
  await page.goto(URL);
  await page.getByRole("checkbox", { name: "Square 1:1" }).check({ force: true });
  await page.getByRole("checkbox", { name: "Wide 16:9" }).check({ force: true });
  await only(page, ["Nobody tells you this when you start", "The ₱500 mistake on my first gig"]);
  await page.getByRole("button", { name: "Export 2 clips" }).click();
  await page.evaluate(() => { location.hash = "#/p/mock-1/pick"; }); // leave without a reload: the engine keeps working
  await expect(page.getByRole("heading", { name: /clips found/ })).toBeVisible();
  await page.evaluate(() => { location.hash = "#/p/mock-1/export"; });
  const go = page.getByRole("button", { name: /^Export \d+ clips?$/ });
  await expect(page.getByText("An export of this video is still running.")).toBeVisible();
  await expect(go).toBeDisabled();
  await expect(page.getByText("An export of this video is still running.")).toBeHidden({ timeout: 20_000 });
  await expect(go).toBeEnabled();
});
