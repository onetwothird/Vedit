import { expect, test, type Page } from "@playwright/test";

// Sync in the UI. The desktop tests run the mock desktop build against a fake cloud (src/sync/mockCloud.ts), switched
// on by window.__veditMockCloud before the app loads; the website test runs the website build (project "web").

const MB = 1024 ** 2;

interface CloudSeed {
  projects?: { id: string; title: string; rev?: number; has_video_in_cloud?: boolean; expired?: boolean; video_bytes?: number }[];
  /** The answer the cloud gives to attaching a video (a refusal), if any. */
  attachError?: string;
  /** How long each cloud push takes, so "Syncing…" can be seen. */
  pushDelayMs?: number;
  /** Local projects linked by another account: pid → owner. */
  localOwners?: Record<string, string>;
}

const ACCOUNT = { id: "demo-google", name: "Demo Creator", email: "demo.creator@gmail.com", provider: "google", plan: "free" };

async function desktop(page: Page, seed: CloudSeed, signedIn = true) {
  await page.addInitScript(([s, account]) => {
    (window as unknown as { __veditMockCloud: unknown }).__veditMockCloud = { intervalMs: 300, ...(s as object) };
    if (account) localStorage.setItem("vedit.account", JSON.stringify(account));
    else localStorage.removeItem("vedit.account");
  }, [seed, signedIn ? ACCOUNT : null] as const);
}

const requests = (page: Page) =>
  page.evaluate(() => (window as unknown as { __veditMockCloud: { requests: string[] } }).__veditMockCloud.requests.length);

/** The describe's tests run only in this Playwright project (the other build skips them). */
const only = (project: "chromium" | "web") =>
  // eslint-disable-next-line no-empty-pattern
  test.beforeEach(({}, info) => test.skip(info.project.name !== project, `${project} build only`));

test.describe("desktop", () => {
  only("chromium");

  test("recent projects say where each lives and whether it is synced", async ({ page }) => {
    await desktop(page, {
      pushDelayMs: 1500,
      projects: [{ id: "c-beach", title: "Beach day", has_video_in_cloud: true, video_bytes: 120 * MB }],
    });
    await page.goto("/#/");
    const local = page.getByRole("listitem").filter({ hasText: "podcast-ep12" });
    await expect(local.getByText("Syncing…")).toBeVisible();
    await expect(local.getByText("Both")).toBeVisible();
    await expect(local.getByText("Synced")).toBeVisible();
    const cloud = page.getByRole("listitem").filter({ hasText: "Beach day" });
    await expect(cloud.getByText("Cloud", { exact: true })).toBeVisible();
  });

  test("signed out, projects stay on this computer and nothing is sent", async ({ page }) => {
    await desktop(page, { projects: [{ id: "c-beach", title: "Beach day" }] }, false);
    await page.goto("/#/");
    const local = page.getByRole("listitem").filter({ hasText: "podcast-ep12" });
    await expect(local.getByText("This computer")).toBeVisible();
    await expect(local.getByText("Not synced (signed out)")).toBeVisible();
    await expect(page.getByText("Beach day")).toHaveCount(0);
    expect(await requests(page)).toBe(0);
  });

  test("opening a cloud-only project downloads its video, then opens it", async ({ page }) => {
    await desktop(page, { projects: [{ id: "c-beach", title: "Beach day", has_video_in_cloud: true, video_bytes: 120 * MB }] });
    await page.goto("/#/");
    await page.getByRole("button", { name: /Beach day/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("Download the video (120 MB) to edit here");
    await dialog.getByRole("button", { name: "Download" }).click();
    await expect(page.getByRole("progressbar", { name: "Download progress" })).toBeVisible();
    await expect(page).toHaveURL(/#\/p\/mock-\d+\/pick$/);
    await page.goto("/#/");
    const row = page.getByRole("listitem").filter({ hasText: "Beach day" });
    await expect(row.getByText("Both")).toBeVisible();
  });

  test("an expired cloud video cannot be downloaded", async ({ page }) => {
    await desktop(page, { projects: [{ id: "c-old", title: "Old trip", has_video_in_cloud: false, expired: true }] });
    await page.goto("/#/");
    await page.getByRole("button", { name: /Old trip/ }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("The video expired on the web");
    await expect(dialog.getByRole("button", { name: "Download" })).toHaveCount(0);
  });

  test("Available on the web uploads the video with progress, and turning it off asks first", async ({ page }) => {
    await desktop(page, {});
    await page.goto("/#/p/mock-1/pick");
    const sw = page.getByRole("switch", { name: "Available on the web" });
    await expect(sw).not.toBeChecked();
    await sw.click();
    await expect(page.getByRole("progressbar", { name: "Upload progress" })).toBeVisible();
    await expect(sw).toBeChecked();
    await expect(page.getByText("Uses 250 MB of your storage")).toBeVisible();

    await sw.click();
    const confirm = page.getByRole("dialog");
    await expect(confirm).toContainText("Remove the video from the web? Your copy on this computer stays.");
    await confirm.getByRole("button", { name: "Keep it" }).click();
    await expect(sw).toBeChecked();

    await sw.click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove" }).click();
    await expect(sw).not.toBeChecked();
    expect(await page.evaluate(() => (window as unknown as { __veditMockCloud: { projects: { has_video_in_cloud: boolean }[] } })
      .__veditMockCloud.projects.every((p) => !p.has_video_in_cloud))).toBe(true);
  });

  test("a refused upload shows the server's message and the switch goes back off", async ({ page }) => {
    await desktop(page, { attachError: "Videos can be up to 60 minutes" });
    await page.goto("/#/p/mock-1/pick");
    const sw = page.getByRole("switch", { name: "Available on the web" });
    await sw.click();
    await expect(page.getByRole("alert").filter({ hasText: "Videos can be up to 60 minutes" })).toBeVisible();
    await expect(sw).not.toBeChecked();
  });

  test("another account's project cannot be put on the web from here", async ({ page }) => {
    await desktop(page, { localOwners: { "mock-1": "someone-else" } });
    await page.goto("/#/p/mock-1/pick");
    await expect(page.getByRole("switch", { name: "Available on the web" })).toBeDisabled();
    await expect(page.getByText("Synced with another account")).toBeVisible();
  });

  test("on a narrow window the server's message and the other-account reason still show", async ({ page }) => {
    await page.setViewportSize({ width: 700, height: 900 });
    await desktop(page, { attachError: "Videos can be up to 60 minutes" });
    await page.goto("/#/p/mock-1/pick");
    await page.getByRole("switch", { name: "Available on the web" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Videos can be up to 60 minutes" })).toBeVisible();

    const other = await page.context().newPage();
    await other.setViewportSize({ width: 700, height: 900 });
    await desktop(other, { localOwners: { "mock-1": "someone-else" } });
    await other.goto("/#/p/mock-1/pick");
    await expect(other.getByText("Synced with another account")).toBeVisible();
  });

  test("the sync setting is on when signed in, and off stops every sync request", async ({ page }) => {
    await desktop(page, { projects: [{ id: "c-beach", title: "Beach day" }] });
    await page.goto("/#/settings");
    const sw = page.getByRole("switch", { name: "Sync projects with your account" });
    await expect(sw).toBeChecked();
    await sw.click();
    await expect(sw).not.toBeChecked();
    await page.reload();
    await expect(page.getByRole("switch", { name: "Sync projects with your account" })).not.toBeChecked();
    await page.waitForTimeout(1000); // several 300 ms cycles would have run
    expect(await requests(page)).toBe(0);
    await page.goto("/#/");
    await expect(page.getByRole("listitem").filter({ hasText: "podcast-ep12" }).getByText("Not synced (signed out)")).toBeVisible();

    await page.goto("/#/settings");
    await page.getByRole("switch", { name: "Sync projects with your account" }).click();
    await expect.poll(() => requests(page)).toBeGreaterThan(0);
  });

  test("the sync setting is hidden when signed out", async ({ page }) => {
    await desktop(page, {}, false);
    await page.goto("/#/settings");
    await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "Sync projects with your account" })).toHaveCount(0);
  });
});

test.describe("website", () => {
  only("web");

  test("a synced project without its video says to open it in the desktop app", async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __veditMockNoVideo: string[] }).__veditMockNoVideo = ["mock-1"];
    });
    await page.goto("/#/p/mock-1/pick");
    await expect(page.getByText("Open in the desktop app to use the video")).toBeVisible();
    await expect(page.locator("video")).toHaveCount(0);
    await expect(page.getByRole("switch", { name: "Available on the web" })).toHaveCount(0);
    await page.goto("/#/p/mock-1/edit/h01");
    await expect(page.getByText("Open in the desktop app to use the video")).toBeVisible();
    await expect(page.locator("video")).toHaveCount(0);
  });

  test("recent projects without a cloud video say to open them in the desktop app", async ({ page }) => {
    await page.addInitScript(() => {
      (window as unknown as { __veditMockNoVideo: string[] }).__veditMockNoVideo = ["mock-1"];
    });
    await page.goto("/#/");
    const row = page.getByRole("listitem").filter({ hasText: "podcast-ep12" });
    await expect(row.getByText("Open in the desktop app to use the video")).toBeVisible();
  });

  test("recent projects with their video in the cloud have no desktop note", async ({ page }) => {
    await page.goto("/#/");
    await expect(page.getByRole("listitem").filter({ hasText: "podcast-ep12" })).toBeVisible();
    await expect(page.getByText("Open in the desktop app to use the video")).toHaveCount(0);
  });

  test("website projects have no sync setting", async ({ page }) => {
    await page.goto("/#/settings");
    await expect(page.getByRole("heading", { name: "Account" })).toBeVisible();
    await expect(page.getByRole("switch", { name: "Sync projects with your account" })).toHaveCount(0);
  });
});
