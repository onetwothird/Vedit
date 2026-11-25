import { expect, test, type Page, type Route } from "@playwright/test";
import { readFileSync } from "node:fs";

// The website build against the mock engine: uploads go to a presigned PUT URL on this origin (/__mock-r2/...), which
// each test answers with page.route, so it can hold a request open, count it, or let it finish.

const SAMPLE = readFileSync("public/mock/sample.mp4");
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64");
// a 2 second WebM: the test browser has no H.264, so it could not read the length of an MP4
const SHORT = readFileSync("tests/e2e/fixtures/short.webm");
const video = (name: string) => ({ name, mimeType: "video/webm", buffer: SHORT });
// no account saved: the website treats this as a visitor (the skip flag does not matter on the web)
const SIGNED_OUT = { cookies: [], origins: [] };

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

test.describe("formats", () => {
  test("Import offers and names only the video types the engine takes", async ({ page }) => {
    await page.goto("/#/p/new/import");
    await expect(page.locator('input[type="file"]')).toHaveAttribute("accept", ".mp4,.mov,.m4v,.mkv,.webm");
    await expect(page.getByText("MP4, MOV, M4V, MKV or WebM")).toBeVisible();
    await expect(page.getByText("AVI", { exact: false })).toHaveCount(0);
  });
});

test.describe("story", () => {
  const words = (n: number) => Array(n).fill("go").join(" "); // short words: a scene's script is at most 2000 characters

  test("photos upload with progress, keep their names after a reload, and the 3-minute cap is shown", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    const puts = await acceptUploads(page, held);
    await page.goto("/#/s/new");
    await expect(page).toHaveURL(/#\/s\/s\d+/);
    const media = page.locator('input[type="file"][multiple]');
    await expect(media).toHaveAttribute("accept", ".jpg,.jpeg,.png,.webp,.mp4,.mov,.m4v,.mkv,.webm");
    await media.setInputFiles([
      { name: "beach.png", mimeType: "image/png", buffer: PNG },
      { name: "boat.png", mimeType: "image/png", buffer: PNG },
    ]);
    await expect(page.getByRole("progressbar", { name: "Uploading beach.png" })).toBeVisible();
    await expect.poll(() => puts.length).toBe(1);
    expect(puts[0].type).toBe("image/png");
    release();
    await expect(page.getByRole("progressbar", { name: /^Uploading/ })).toHaveCount(0);
    const scenes = page.locator(".sc-list .sc-name");
    await expect(scenes).toHaveText(["beach.png", "boat.png"]);
    await expect(page.getByText("Stories can be up to 3 minutes", { exact: false }).first()).toBeVisible();

    await page.getByRole("textbox", { name: "What the narrator says over scene 1" }).fill(words(460));
    await expect(page.getByRole("alert")).toContainText("This story is about 3:07 long. Stories can be up to 3 minutes");
    await expect(page.getByRole("button", { name: "Make video" })).toBeDisabled();
    await page.getByRole("textbox", { name: "What the narrator says over scene 1" }).fill(words(10));
    await expect(page.getByRole("button", { name: "Make video" })).toBeEnabled();
    await expect(page.getByText("Saved")).toBeVisible();

    await page.reload();
    await expect(page.locator(".sc-list .sc-name")).toHaveText(["beach.png", "boat.png"]);
    await expect(page.getByText(/asset:/)).toHaveCount(0);
  });

  test("the finished video is a Download link, re-issued once it is a day old", async ({ page }) => {
    await acceptUploads(page);
    await page.route("**/__mock-r2/exports/**", (route) =>
      route.fulfill({ status: 200, headers: { "Content-Disposition": "attachment; filename=story.mp4" }, body: "mp4" }));
    await page.goto("/#/s/new");
    await expect(page).toHaveURL(/#\/s\/s\d+/);
    await page.locator('input[type="file"][multiple]').setInputFiles({ name: "beach.png", mimeType: "image/png", buffer: PNG });
    await expect(page.locator(".sc-list .sc-name")).toHaveText(["beach.png"]);
    await page.getByRole("textbox", { name: "What the narrator says over scene 1" }).fill("A day at the beach");
    await page.getByRole("button", { name: "Make video" }).click();
    const link = page.getByRole("link", { name: "Download Story.mp4" });
    await expect(link).toBeVisible({ timeout: 15_000 });
    await expect(link).toHaveAttribute("href", /__mock-r2\/exports\/s\d+\/Story\.mp4/);
    await expect(page.getByRole("button", { name: "Show in folder" })).toHaveCount(0);

    await page.reload();
    const again = page.getByRole("link", { name: "Download Story.mp4" });
    await expect(again).toBeVisible();
    const before = await again.getAttribute("href");
    await page.evaluate(() => {
      const real = Date.now;
      Date.now = () => real() + 24 * 3600 * 1000;
    });
    const [download] = await Promise.all([page.waitForEvent("download"), again.click()]);
    expect(download.url()).not.toBe(before);
    expect(download.url()).toContain("/__mock-r2/exports/");
  });
});

test.describe("batch", () => {
  const four = ["a.webm", "b.webm", "c.webm", "d.webm"].map(video);

  test("refuses videos longer than the minutes left, before uploading anything", async ({ page }) => {
    await page.addInitScript(() => ((globalThis as { __veditMockUsedSeconds?: number }).__veditMockUsedSeconds = 3599));
    const puts = await acceptUploads(page);
    await page.goto("/#/batch");
    const input = page.locator('input[type="file"]');
    await expect(input).toHaveAttribute("accept", ".mp4,.mov,.m4v,.mkv,.webm");
    await input.setInputFiles(four.slice(0, 2));
    await page.getByRole("button", { name: "Start 2 videos" }).click();
    await expect(page.getByRole("alert")).toHaveText("These videos are 1 min; you have 0 min left this month");
    expect(puts).toHaveLength(0);
  });

  test("uploads three at a time, a file can be cancelled, then links and Download all", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((r) => (release = r));
    const puts: string[] = [];
    await page.route("**/__mock-r2/**", async (route) => {
      const url = route.request().url();
      if (!url.includes("/uploads/")) return route.fulfill({ status: 200, body: "file" });
      puts.push(url);
      await held;
      await route.fulfill({ status: 200, body: "" }).catch(() => undefined);
    });
    await page.goto("/#/batch");
    await expect(page.getByRole("button", { name: /folder/i })).toHaveCount(0);
    await page.locator('input[type="file"]').setInputFiles(four);
    await page.getByRole("button", { name: "Start 4 videos" }).click();

    for (const n of ["a.webm", "b.webm", "c.webm", "d.webm"]) {
      await expect(page.getByRole("progressbar", { name: `Uploading ${n}` })).toBeVisible();
    }
    await expect.poll(() => puts.length).toBe(3);
    await page.waitForTimeout(300);
    expect(puts).toHaveLength(3); // never more than three at once
    await page.getByRole("button", { name: "Cancel upload of b.webm" }).click();
    await expect.poll(() => puts.length).toBe(4); // the fourth starts in its place
    await expect(page.getByRole("progressbar", { name: "Uploading b.webm" })).toHaveCount(0);
    release();

    await expect(page).toHaveURL(/#\/batch\/b\d+/, { timeout: 15_000 });
    await expect(page.locator(".q-title")).toHaveText(["a.webm", "c.webm", "d.webm"]);
    await expect(page.getByText("All done.")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("link", { name: /^Download 01 / }).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Show in folder" })).toHaveCount(0);

    await page.getByRole("button", { name: "Download all" }).click();
    const zip = page.getByRole("link", { name: "Download all (zip)" });
    await expect(zip).toBeVisible({ timeout: 15_000 });
    await expect(zip).toHaveAttribute("href", /__mock-r2\/zips\/b\d+\.zip/);
  });

  test("a finished batch opened again shows freshly signed links, not the ones stored with the job", async ({ page }) => {
    await acceptUploads(page);
    await page.goto("/#/batch");
    await page.locator('input[type="file"]').setInputFiles(video("a.webm"));
    await page.getByRole("button", { name: "Start 1 video" }).click();
    await expect(page).toHaveURL(/#\/batch\/b\d+/, { timeout: 15_000 });
    const url = page.url();
    await expect(page.getByText("All done.")).toBeVisible({ timeout: 30_000 });
    const link = page.getByRole("link", { name: /^Download 01 / }).first();
    await expect(link).toBeVisible();
    const before = await link.getAttribute("href");

    // come back later: the job's stored result is the same, so only a new projectExports call can change the link
    await page.getByRole("link", { name: "Vedit home" }).click();
    await expect(page.getByRole("link", { name: /podcast-ep12\.mp4/ })).toBeVisible();
    await expect(page.getByText("All done.")).toHaveCount(0);
    await page.goto(url);
    await expect(page.getByText("All done.")).toBeVisible({ timeout: 15_000 });
    await expect(link).toHaveAttribute("href", /__mock-r2\/exports\//);
    await expect(link).not.toHaveAttribute("href", before!);
  });
});

test.describe("brand kit", () => {
  test("logo, LUT and a font upload to the library, keep their names, and can be removed", async ({ page }) => {
    const puts = await acceptUploads(page);
    await page.goto("/#/settings");
    const kit = page.locator("#brand");
    await expect(kit.getByRole("heading", { name: "Brand kit" })).toBeVisible();

    const logo = kit.getByRole("group", { name: "Logo" });
    let chooser = page.waitForEvent("filechooser");
    await logo.getByRole("button", { name: "Choose…" }).click();
    await (await chooser).setFiles({ name: "logo.png", mimeType: "image/png", buffer: PNG });
    await expect(logo).toContainText("logo.png");
    expect(puts.at(-1)?.type).toBe("image/png");

    const lut = kit.getByRole("group", { name: "Color look (LUT)" });
    chooser = page.waitForEvent("filechooser");
    await lut.getByRole("button", { name: "Choose…" }).click();
    await (await chooser).setFiles({ name: "warm.cube", mimeType: "", buffer: Buffer.from("LUT_3D_SIZE 2\n") });
    await expect(lut).toContainText("warm.cube");

    const font = kit.getByRole("group", { name: "Title font (extra bold)" });
    chooser = page.waitForEvent("filechooser");
    await font.getByRole("button", { name: "Choose…" }).click();
    await (await chooser).setFiles({ name: "Brand-Black.ttf", mimeType: "font/ttf", buffer: Buffer.from("ttf") });
    await expect(font).toContainText("Brand-Black.ttf");

    await kit.getByRole("button", { name: "Save brand kit" }).click();
    await expect(kit.getByText("Brand kit saved.")).toBeVisible();

    await page.reload();
    const again = page.locator("#brand");
    await expect(again.getByRole("group", { name: "Logo" })).toContainText("logo.png");
    await expect(again.getByRole("group", { name: "Color look (LUT)" })).toContainText("warm.cube");
    await expect(again.getByRole("group", { name: "Title font (extra bold)" })).toContainText("Brand-Black.ttf");
    await expect(again.getByText(/asset:/)).toHaveCount(0);

    await again.getByRole("group", { name: "Logo" }).getByRole("button", { name: "Clear" }).click();
    await expect(again.getByRole("group", { name: "Logo" })).toContainText("Not set");
  });
});

test.describe("sign-in gate", () => {
  test.use({ storageState: SIGNED_OUT });

  for (const route of ["/#/p/new/import", "/#/settings", "/#/batch", "/#/s/new", "/#/p/mock-1/export"]) {
    test(`${route} sends a visitor to sign in`, async ({ page }) => {
      await page.goto(route);
      await expect(page).toHaveURL(/#\/signin$/);
      await expect(page.getByRole("heading", { name: "Sign in to Vedit" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Skip for now" })).toHaveCount(0);
    });
  }

  test("signing in returns to the page that was asked for", async ({ page }) => {
    await page.goto("/#/p/new/import");
    await page.getByRole("button", { name: "Continue with Google" }).click();
    await expect(page).toHaveURL(/#\/p\/new\/import$/);
    await expect(page.getByRole("button", { name: "Start" })).toBeVisible();
  });

  test("Home is open and asks the visitor to sign in", async ({ page }) => {
    await page.goto("/#/");
    await expect(page.getByRole("heading", { name: "Sign in to start cutting" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in or create an account" })).toBeVisible();
    await expect(page).toHaveURL(/#\/$/);
  });

  for (const [route, title] of [["/#/terms", "Terms of use"], ["/#/privacy", "Privacy policy"]] as const) {
    test(`${route} is open, marked as a draft, and shows the contact placeholder`, async ({ page }) => {
      await page.goto(route);
      await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
      await expect(page.getByRole("note")).toContainText("Draft — review before launch");
      await expect(page.getByText("contact email not set")).toBeVisible();
      await expect(page).toHaveURL(new RegExp(`${route.slice(2)}$`));
    });
  }
});

test.describe("usage meter", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      Object.assign(window, { __veditMockUsedSeconds: 23 * 60, __veditMockStorageBytes: 1.2 * 1024 ** 3 });
    });
  });

  test("Import shows minutes and storage as named meters", async ({ page }) => {
    await page.goto("/#/p/new/import");
    await expect(page.getByText("23 of 60 minutes used this month")).toBeVisible();
    await expect(page.getByText("1.2 of 10 GB of storage used")).toBeVisible();
    await expect(page.getByRole("meter", { name: "Analysis time this month" })).toHaveAttribute("aria-valuenow", String(23 * 60));
    await expect(page.getByRole("meter", { name: "Storage used" })).toBeVisible();
  });

  test("Settings shows the same meters, no speech-model section and no output folder", async ({ page }) => {
    await page.goto("/#/settings");
    await expect(page.getByRole("heading", { name: "Usage", level: 2 })).toBeVisible();
    await expect(page.getByText("23 of 60 minutes used this month")).toBeVisible();
    await expect(page.getByText("1.2 of 10 GB of storage used")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Language models" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Language models" })).toHaveCount(0);
    await expect(page.getByText("Save finished clips")).toHaveCount(0);
  });
});
