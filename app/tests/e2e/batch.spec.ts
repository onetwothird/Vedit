import { expect, test } from "@playwright/test";

/* Batch & automate: many videos at once, each analysed and then exported without anyone pressing a button. */

const file = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("not really a video") });

test("a batch of two videos is analysed and exported, one row each", async ({ page }) => {
  await page.goto("/#/");
  await page.getByRole("link", { name: /Batch & automate/ }).click();
  await expect(page.getByRole("heading", { name: "Batch & automate" })).toBeVisible();
  await expect(page.getByRole("button", { name: /^Start/ })).toBeDisabled();

  await page.locator('input[type="file"][multiple]').setInputFiles([file("ep1.mp4"), file("ep2.mp4"), file("ep3.mp4")]);
  await page.getByRole("button", { name: "Remove ep3.mp4" }).click();
  await page.getByRole("radio", { name: "A Quick Reel" }).check();
  await page.getByRole("radio", { name: "TikTok" }).check();
  await page.getByRole("checkbox", { name: "Vertical 9:16" }).uncheck();
  await expect(page.getByText("Pick at least one format.")).toBeVisible();
  await page.getByRole("checkbox", { name: "Square 1:1" }).check();

  await page.getByRole("button", { name: "Start 2 videos" }).click();
  await expect(page).toHaveURL(/#\/batch\/b\d{12}$/);
  await expect(page.getByRole("heading", { name: "Working through your videos" })).toBeVisible();
  const rows = page.locator(".q-row");
  await expect(rows).toHaveCount(2);
  await expect(page.getByText("All done.")).toBeVisible({ timeout: 30_000 });
  await expect(rows.nth(0)).toContainText("Done");
  await expect(rows.nth(0).getByText(/\(1x1\)\.mp4$/)).toBeVisible();
  await rows.nth(1).getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(/\/edit\/h01$/);
});

test("a batch without auto export leaves each video ready to review", async ({ page }) => {
  await page.goto("/#/batch");
  await page.locator('input[type="file"][multiple]').setInputFiles([file("talk.mp4")]);
  await page.getByRole("checkbox", { name: "Export its clips right away" }).uncheck();
  await expect(page.getByText("Each video waits in Recent projects")).toBeVisible();
  await page.getByRole("button", { name: "Start 1 video" }).click();
  await expect(page.getByText("All done.")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "Open" }).click();
  await expect(page).toHaveURL(/\/pick$/);
});
