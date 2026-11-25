import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";

// The website build against the mock engine: uploads go to a presigned PUT URL on this origin (/__mock-r2/...), which
// each test answers with page.route, so it can hold a request open, count it, or let it finish.

const SAMPLE = readFileSync("public/mock/sample.mp4");
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
// a 2 second WebM: the test browser has no H.264, so it could not read the length of an MP4
const SHORT = readFileSync("tests/e2e/fixtures/short.webm");
const video = (name: string) => ({ name, mimeType: "video/webm", buffer: SHORT });
const NAME_ONLY = { cookies: [], origins: [{ origin: "http://localhost:5198", localStorage: [{ name: "vedit.signin.skipped", value: "true" }] }] };

/** Answers every upload; returns the list of PUT requests seen. */
async function acceptUploads(page: Page, hold?: Promise<void>) {
  const puts: { url: string; type: string | undefined }[] = [];
  await page.route("**/__mock-r2/uploads/**", async (route: Route) => {
    const req = route.request();
    if (req.method() !== "PUT") return route.fulfill({ status: 404 });
    puts.push({ url: req.url(), type: req.headers()["content-type"] });
    if (hold) await hold;
    await route.fulfill({ status: 200, body: "" });
  });
  return puts;
}

test.describe("import", () => {
  test("uploads the video with its type, shows progress, then analyses it", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    const puts = await acceptUploads(page, held);
    await page.goto("/#/p/new/import");
    await page.locator('input[type="file"]').setInputFiles(video("episode-7.webm"));
    await expect(page.getByText("Your video is uploaded to your Vedit account", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Start" }).click();

    const bar = page.getByRole("progressbar", { name: "Upload progress" });
    await expect(bar).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel" })).toBeVisible();
    await expect.poll(() => puts.length).toBe(1);
    expect(puts[0].type).toBe("video/webm");
    release();

    await expect(page.getByText("Making a preview copy")).toBeVisible();
    await expect(page).toHaveURL(/#\/p\/[^/]+\/pick/);
    await expect(page.getByRole("heading", { name: "12 clips found, 8 kept" })).toBeVisible();
  });

  test("refuses a video longer than the minutes left, before uploading anything", async ({ page }) => {
    await page.addInitScript(() => ((globalThis as { __veditMockUsedSeconds?: number }).__veditMockUsedSeconds = 3599));
    const puts = await acceptUploads(page);
    await page.goto("/#/p/new/import");
    await page.locator('input[type="file"]').setInputFiles(video("episode-7.webm"));
    await page.getByRole("button", { name: "Start" }).click();
    await expect(page.getByRole("alert")).toHaveText("This video is 1 min; you have 0 min left this month");
    expect(puts).toHaveLength(0);
    await expect(page.getByRole("progressbar", { name: "Upload progress" })).toHaveCount(0);
  });

  test("Cancel stops the upload and leaves no project behind", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    const puts = await acceptUploads(page, held);
    const failed: string[] = [];
    page.on("requestfailed", (r) => r.url().includes("__mock-r2") && failed.push(r.url()));
    await page.goto("/#/p/new/import");
    await page.locator('input[type="file"]').setInputFiles(video("cancel-me.webm"));
    await page.getByRole("button", { name: "Start" }).click();
    await expect.poll(() => puts.length).toBe(1);
    await page.getByRole("button", { name: "Cancel" }).click();

    await expect(page.getByRole("progressbar", { name: "Upload progress" })).toHaveCount(0);
    await expect(page.getByRole("status").filter({ hasText: "Upload cancelled" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Start" })).toBeEnabled();
    await expect.poll(() => failed.length).toBe(1);
    release();

    await page.goto("/#/");
    await expect(page.getByRole("link", { name: /podcast-ep12\.mp4/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /cancel-me/ })).toHaveCount(0);
  });

  test("leaving Import mid-upload stops it: no project, no analysis, no pull back", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    let put = false;
    await page.route("**/__mock-r2/uploads/**", async (route) => {
      put = true;
      await held;
      await route.fulfill({ status: 200, body: "" }).catch(() => undefined); // the page has already aborted it
    });
    await page.goto("/#/p/new/import");
    await page.locator('input[type="file"]').setInputFiles(video("leave.webm"));
    await page.getByRole("button", { name: "Start" }).click();
    await expect.poll(() => put).toBe(true);
    await page.getByRole("link", { name: "Vedit home" }).click();
    await expect(page).toHaveURL(/#\/$/);
    release();
    await page.waitForTimeout(1500);
    await expect(page).toHaveURL(/#\/$/); // not pulled back to a project
    await expect(page.getByRole("link", { name: /leave/ })).toHaveCount(0);
  });

  test("an upload the storage rejects is explained", async ({ page }) => {
    await page.route("**/__mock-r2/uploads/**", (route) => route.fulfill({ status: 403, body: "" }));
    await page.goto("/#/p/new/import");
    await page.locator('input[type="file"]').setInputFiles(video("episode-7.webm"));
    await page.getByRole("button", { name: "Start" }).click();
    await expect(page.getByRole("alert")).toHaveText("The upload didn't finish. Check your connection and try again.");
    await expect(page.getByRole("button", { name: "Start" })).toBeEnabled();
  });

  test.describe("signed out", () => {
    test.use({ storageState: NAME_ONLY });
    test("Start sends you to the sign-in screen", async ({ page }) => {
      const puts = await acceptUploads(page);
      await page.goto("/#/p/new/import");
      await page.locator('input[type="file"]').setInputFiles(video("episode-7.webm"));
      await page.getByRole("button", { name: "Start" }).click();
      await expect(page.getByRole("heading", { name: "Sign in to Vedit" })).toBeVisible();
      expect(puts).toHaveLength(0);
    });
  });
});

test.describe("export", () => {
  async function exportOne(page: Page) {
    await page.goto("/#/p/mock-1/export");
    const boxes = page.getByRole("list").filter({ has: page.getByRole("checkbox") }).first().getByRole("checkbox");
    for (const box of await boxes.all()) {
      const name = (await box.evaluate((el) => el.closest("label")?.textContent ?? "")).trim();
      await box.setChecked(name === "Nobody tells you this when you start", { force: true });
    }
    await page.getByRole("button", { name: "Export 1 clip" }).click();
  }

  test("has no folder settings, and lists Download links when the clip is done", async ({ page }) => {
    await page.goto("/#/p/mock-1/export");
    await expect(page.getByRole("heading", { name: "Export", exact: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Save to" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Change folder" })).toHaveCount(0);
    await expect(page.getByTestId("export-folder")).toHaveCount(0);

    await exportOne(page);
    await expect(page.getByRole("heading", { name: "1 clip exported" })).toBeVisible({ timeout: 15_000 });
    const links = page.getByRole("link", { name: /^Download/ });
    await expect(links.first()).toBeVisible();
    await expect(links.first()).toHaveAttribute("href", /__mock-r2\/exports\/mock-1\/01%20Nobody%20tells%20you%20this%20when%20you%20start\.mp4/);
    await expect(page.getByRole("button", { name: "Show in folder" })).toHaveCount(0);
    await expect(page.getByText("Showing files in a folder works in the installed app.")).toHaveCount(0);
  });

  test("a link older than a day is re-issued before it is followed", async ({ page }) => {
    const hits: string[] = [];
    await page.route("**/__mock-r2/exports/**", (route) => {
      hits.push(route.request().url());
      return route.fulfill({ status: 200, headers: { "Content-Disposition": "attachment; filename=clip.mp4" }, body: "mp4" });
    });
    await exportOne(page);
    const link = page.getByRole("link", { name: /^Download/ }).first();
    await expect(link).toBeVisible({ timeout: 15_000 });
    const before = await link.getAttribute("href");

    // a fresh link is followed as it is
    let [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
    expect(download.url()).toBe(before);

    await page.evaluate(() => {
      const real = Date.now;
      Date.now = () => real() + 24 * 3600 * 1000;
    });
    [download] = await Promise.all([page.waitForEvent("download"), link.click()]);
    expect(download.url()).not.toBe(before);
    expect(download.url()).toContain("/__mock-r2/exports/mock-1/");
    await expect(link).not.toHaveAttribute("href", before!);
  });
});

test.describe("folders", () => {
  test("Settings has no output-folder choice", async ({ page }) => {
    await page.goto("/#/settings");
    await expect(page.getByRole("heading", { name: "Settings", exact: true }).first()).toBeVisible();
    await expect(page.getByText("Save finished clips")).toHaveCount(0);
    await expect(page.getByRole("radio", { name: "Choose folder…" })).toHaveCount(0);
  });

  test("b-roll: a photo is uploaded and placed, and suggestions come from chosen files, not a folder", async ({ page }) => {
    const puts = await acceptUploads(page);
    await page.goto("/#/p/mock-1/edit/h01");
    await expect(page.locator('.tx [data-w="0"]')).toBeVisible();
    await page.getByRole("tab", { name: "B-roll", exact: true }).click();
    const tool = page.getByRole("tabpanel", { name: "B-roll" });

    await expect(tool.getByRole("textbox", { name: "Folder of photos and videos" })).toHaveCount(0);
    await expect(tool.getByRole("button", { name: "Choose…" })).toHaveCount(0);

    // add one photo as a layer
    const chooser = page.waitForEvent("filechooser");
    await tool.getByRole("button", { name: "Add a photo or video" }).click();
    await (await chooser).setFiles({ name: "office.png", mimeType: "image/png", buffer: PNG });
    await expect(tool.locator(".ly-item")).toHaveCount(1);
    await expect(tool.locator(".ly-item")).toContainText("office.png");
    expect(puts.at(-1)?.type).toBe("image/png");

    // suggestions from chosen files
    const suggest = tool.getByRole("button", { name: "Suggest b-roll" });
    await expect(suggest).toBeDisabled();
    const chooser2 = page.waitForEvent("filechooser");
    await tool.getByRole("button", { name: "Choose files" }).click();
    await (await chooser2).setFiles([
      { name: "first-gig.jpg", mimeType: "image/jpeg", buffer: PNG },
      { name: "new_year party.mp4", mimeType: "video/mp4", buffer: SAMPLE },
    ]);
    await expect(tool.getByText("2 files chosen")).toBeVisible();
    await suggest.click();
    const list = tool.getByRole("list", { name: "B-roll suggestions" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list).toContainText("first-gig.jpg");
    await expect(list).toContainText("new_year party.mp4");
  });
});
