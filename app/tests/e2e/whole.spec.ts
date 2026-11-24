import { expect, test } from "@playwright/test";

/* Vlog and Quick Reel projects: the whole video as one edit, so Import goes straight to Edit (no Pick clips). */

const file = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("not really a video") });

test("Edit a vlog: Import without a platform, then straight to Edit with no clip list, exporting 16:9", async ({ page }) => {
  await page.goto("/#/");
  await page.getByRole("link", { name: /Edit a vlog/ }).click();
  await expect(page).toHaveURL(/#\/p\/new\/import\?type=vlog$/);
  await expect(page.getByRole("heading", { name: "Edit a vlog" })).toBeVisible();
  await expect(page.getByRole("group", { name: "Where you'll post" })).toHaveCount(0);
  await expect(page.getByText("It cuts dead air, filler words and retakes")).toBeVisible();
  const steps = page.getByRole("navigation", { name: "Steps" });
  await expect(steps.getByText("Pick clips")).toHaveCount(0);

  await page.locator('input[type="file"]').setInputFiles(file("my-vlog.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("Finding the best moments")).toHaveCount(0);
  await expect(page).toHaveURL(/\/edit\/h01$/, { timeout: 20_000 });
  await expect(page.getByRole("heading", { name: "my-vlog" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Clips" })).toHaveCount(0);
  await expect(page.getByRole("radio", { name: "16:9" })).toBeChecked();

  await page.getByRole("button", { name: "Export video" }).click();
  await expect(page).toHaveURL(/\/export$/);
  await expect(page.getByRole("checkbox", { name: "Wide 16:9" })).toBeChecked();
  await expect(page.getByRole("checkbox", { name: "Vertical 9:16" })).not.toBeChecked();
  await expect(steps.getByText("Pick clips")).toHaveCount(0);
});

test("Quick Reel: a vertical edit of the whole video, and reopening it from Home goes back to Edit", async ({ page }) => {
  await page.goto("/#/");
  await page.getByRole("link", { name: /Quick Reel/ }).click();
  await expect(page.getByRole("heading", { name: "Make a Quick Reel" })).toBeVisible();
  await page.getByRole("radio", { name: "TikTok" }).check();
  await page.locator('input[type="file"]').setInputFiles(file("phone clip.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page).toHaveURL(/\/edit\/h01$/, { timeout: 20_000 });
  await expect(page.getByRole("radio", { name: "9:16" })).toBeChecked();
  const url = page.url();
  const pid = /\/p\/([^/]+)\//.exec(url)![1];

  // Pick clips is not a step of this project: its address leads back to Edit
  await page.goto(`/#/p/${pid}/pick`);
  await expect(page).toHaveURL(/\/edit\/h01$/);

  await page.goto("/#/");
  const recent = page.getByRole("link", { name: /phone clip\.mp4/ });
  await expect(recent).toContainText("Quick Reel");
  await recent.click();
  await expect(page).toHaveURL(new RegExp(`/p/${pid}/edit/h01$`));
});
