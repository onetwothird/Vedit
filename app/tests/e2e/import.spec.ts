import { expect, test, type Page } from "@playwright/test";

const video = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("not really a video") });

async function chooseVideo(page: Page, name: string) {
  await page.locator('input[type="file"]').setInputFiles(video(name));
}

test("the whole Clip card opens Import, and its sound button plays sound without leaving Home", async ({ page }) => {
  await page.goto("/#/");
  const sound = page.locator(".mode-hero").getByRole("button", { name: "Sound" });
  if (await sound.count()) {
    // only the local preview footage has an audio track; the bundled clip has none, so CI has no button
    await sound.click();
    await expect(sound).toHaveAttribute("aria-pressed", "true");
    await expect(page).toHaveURL(/#\/$/);
    expect(await page.locator(".mode-hero .src-frame video").evaluate((v: HTMLVideoElement) => v.muted)).toBe(false);
  }
  await page.locator(".mode-hero .cuts").click({ force: true, position: { x: 10, y: 10 } });
  await expect(page).toHaveURL(/\/p\/new\/import$/);
});

test("home shows five tiles and only one is active", async ({ page }) => {
  await page.goto("/#/");
  await expect(page.getByRole("heading", { name: "What are you making?" })).toBeVisible();
  await expect(page.locator("[data-tile]")).toHaveCount(5);
  await expect(page.locator("[data-tile][aria-disabled='true']")).toHaveCount(4);
  await expect(page.getByRole("link", { name: /Clip a long video/ })).toBeVisible();
  await expect(page.getByRole("button", { name: /Edit a vlog/ })).toHaveCount(0);
  await expect(page.getByText("Coming soon")).toHaveCount(4);
  // recent projects come from the seeded mock engine
  await expect(page.getByRole("link", { name: /podcast-ep12\.mp4/ })).toContainText("12 clips");
});

test("picking a file runs the stages and lands on Pick clips", async ({ page }) => {
  await page.goto("/#/");
  await page.getByRole("link", { name: /Clip a long video/ }).click();
  await expect(page).toHaveURL(/#\/p\/new\/import/);
  await chooseVideo(page, "episode-7.mp4");
  await expect(page.getByRole("group", { name: "Video file" }).getByText("episode-7.mp4")).toBeVisible();
  await page.getByRole("button", { name: "Start" }).click();

  await expect(page.getByText("Making a preview copy")).toBeVisible();
  await expect(page.getByText("Listening to the audio")).toBeVisible();
  await expect(page.getByRole("progressbar", { name: "Overall progress" })).toBeVisible();
  await expect(page.getByText("Downloading the language model")).toHaveCount(0);

  await expect(page).toHaveURL(/#\/p\/[^/]+\/pick/);
  await expect(page.getByRole("heading", { name: "12 clips found, 8 kept" })).toBeVisible();
});

test("cancel returns to Import with Cancelled", async ({ page }) => {
  await page.goto("/#/p/new/import");
  await chooseVideo(page, "long-talk.mov");
  await page.getByRole("button", { name: "Start" }).click();
  await page.getByRole("button", { name: "Cancel" }).click();

  await expect(page.getByText("Cancelled", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start" })).toBeEnabled();
  await expect(page).toHaveURL(/#\/p\/[^/]+\/import/);
});

test("a failed analysis shows the error and Try again recovers", async ({ page }) => {
  await page.goto("/#/p/new/import");
  await chooseVideo(page, "will-fail.mp4");
  await page.getByRole("button", { name: "Start" }).click();

  await expect(page.getByRole("alert")).toHaveText("Couldn't read the audio track in this file.");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(page).toHaveURL(/#\/p\/[^/]+\/pick/);
});

test("an unsupported file is rejected with a plain message", async ({ page }) => {
  await page.goto("/#/p/new/import");
  await chooseVideo(page, "notes.txt");
  await expect(page.getByRole("alert")).toHaveText("This file isn't a video Vedit can open. Try an MP4 or MOV file.");
  await expect(page.getByRole("button", { name: "Start" })).toBeDisabled();
});

test("the whole flow works with the keyboard alone", async ({ page }) => {
  await page.goto("/#/");

  const tabTo = async (name: string) => {
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press("Tab");
      const label = await page.evaluate(() => {
        const el = document.activeElement as HTMLInputElement | null;
        return (el?.getAttribute("aria-label") ?? el?.labels?.[0]?.textContent ?? el?.textContent ?? "").trim();
      });
      if (label.startsWith(name)) return;
    }
    throw new Error(`never reached ${name} with Tab`);
  };

  await tabTo("Clip a long video");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/p\/new\/import/);

  await tabTo("Choose a video");
  await expect(page.getByRole("button", { name: "Choose a video" })).toBeFocused();
  const chooser = page.waitForEvent("filechooser");
  await page.evaluate(() => undefined); // let the file-chooser interception settle before the key press
  await page.keyboard.press("Enter");
  await (await chooser).setFiles(video("keyboard.mp4"));
  await expect(page.getByRole("group", { name: "Video file" }).getByText("keyboard.mp4")).toBeVisible();

  // arrow keys move within a radio group
  await tabTo("English");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("radio", { name: "Filipino" })).toBeChecked();

  await tabTo("Start");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/p\/[^/]+\/pick/);
});
