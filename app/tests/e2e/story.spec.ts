import { expect, test } from "@playwright/test";

/* Photos + script → Reel: scenes in order, a line per caption, then one button that makes the narrated video. */

const photo = (name: string) => ({ name, mimeType: "image/jpeg", buffer: Buffer.from([0xff, 0xd8, 0xff, 0xd9]) });
const clip = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("not really a video") });

test("make a story: add scenes, write lines, reorder, and make the video", async ({ page }) => {
  await page.goto("/#/");
  await page.getByRole("link", { name: /Photos \+ script/ }).click();
  await expect(page).toHaveURL(/#\/s\/s\d{12}$/);
  await expect(page.getByRole("heading", { name: "Photos + script → Reel" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Make video" })).toBeDisabled();

  await page.getByLabel("Name").fill("Lost wallet");
  await page.locator('input[type="file"][multiple]').setInputFiles([photo("street.jpg"), clip("cafe.mp4"), photo("notes.jpg")]);
  const scenes = page.locator(".sc");
  await expect(scenes).toHaveCount(3);
  await expect(scenes.nth(1).getByText("cafe.mp4")).toBeVisible();

  await page.getByLabel("What the narrator says over scene 1").fill("Maria was already late.\nWhere is my wallet?");
  await page.getByRole("button", { name: "Remove scene 3" }).click();
  await expect(scenes).toHaveCount(2);
  await page.getByRole("button", { name: "Move scene 2 up" }).click();
  await expect(scenes.nth(0).getByText("cafe.mp4")).toBeVisible();
  await page.getByLabel("Use the clip's own sound and captions instead of narration").check();
  await expect(page.getByText("This clip keeps its own sound.")).toBeVisible();
  await page.getByRole("radio", { name: "Wide 16:9" }).check();
  await page.getByLabel("Narrator's voice").selectOption("fil-PH-BlessicaNeural");
  await expect(page.getByRole("status").filter({ hasText: "Saved" })).toBeVisible();

  // everything was saved: a reload shows the same story
  await page.reload();
  await expect(page.getByLabel("Name")).toHaveValue("Lost wallet");
  await expect(scenes).toHaveCount(2);
  await expect(page.getByLabel("Narrator's voice")).toHaveValue("fil-PH-BlessicaNeural");
  await expect(page.getByRole("radio", { name: "Wide 16:9" })).toBeChecked();

  await page.getByRole("button", { name: "Make video" }).click();
  await expect(page.getByRole("progressbar", { name: "Making the video" })).toBeVisible();
  await expect(page.getByText("Your video is ready:")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Lost wallet (16x9).mp4")).toBeVisible();
  await expect(page.getByRole("button", { name: "Make video again" })).toBeEnabled();

  await page.goto("/#/");
  const recent = page.getByRole("link", { name: /Lost wallet/ });
  await expect(recent).toContainText("Story · 2 scenes");
});

test("a story that can't be made says why, and can be tried again", async ({ page }) => {
  await page.goto("/#/s/new");
  await page.getByLabel("Name").fill("Offline test");
  await page.locator('input[type="file"][multiple]').setInputFiles([photo("a.jpg"), { ...photo("readme.txt"), mimeType: "text/plain" }]);
  await expect(page.getByText("1 file was skipped because it isn't a photo or a video.")).toBeVisible();
  await page.getByRole("button", { name: "Make video" }).click();
  await expect(page.getByRole("alert")).toContainText("Couldn't reach the voice service", { timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Make video" })).toBeEnabled();
});

test("an unknown story says so", async ({ page }) => {
  await page.goto("/#/s/s999999999999");
  await expect(page.getByText("This story could not be found.")).toBeVisible();
  await page.getByRole("link", { name: "Back to Home" }).click();
  await expect(page).toHaveURL(/#\/$/);
});
