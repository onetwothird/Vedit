// Screenshots for the visual check against the mockup. Run with SCREENSHOTS=<dir> npx playwright test screens.
import { expect, test, type Page } from "@playwright/test";
import path from "node:path";

const dir = process.env.SCREENSHOTS;
test.skip(!dir, "set SCREENSHOTS to a folder to save screenshots");

const shot = (page: Page, name: string, task = "t15") => page.screenshot({ path: path.join(dir!, `${task}-${name}.png`) });
const file = (name: string) => ({ name, mimeType: "video/mp4", buffer: Buffer.from("x") });

test("screenshots", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto("/#/");
  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.reload();
  await expect(page.getByRole("link", { name: /podcast-ep12/ })).toBeVisible();
  await shot(page, "home-dark");

  await page.evaluate(() => localStorage.setItem("vedit.theme", "light"));
  await page.reload();
  await expect(page.getByRole("link", { name: /podcast-ep12/ })).toBeVisible();
  await page.evaluate(() => document.documentElement.classList.add("light"));
  await shot(page, "home-light");

  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.goto("/#/p/new/import");
  await page.reload();
  await shot(page, "import-empty-dark");
  await page.locator('input[type="file"]').setInputFiles(file("podcast_ep13.mp4"));
  await shot(page, "import-idle");

  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("Listening to the audio")).toBeVisible();
  await page.waitForTimeout(500);
  await shot(page, "import-analyzing");

  await page.goto("/#/p/new/import");
  await page.reload();
  await page.locator('input[type="file"]').setInputFiles(file("will-fail.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  await shot(page, "import-failed");
});

test("pick screenshots", async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const heading = page.getByRole("heading", { name: "12 clips found · 8 kept" });

  await page.goto("/#/");
  await page.evaluate(() => localStorage.setItem("vedit.theme", "dark"));
  await page.goto("/#/p/mock-1/pick");
  await page.reload();
  await expect(heading).toBeVisible();
  await page.locator(".clip-main").nth(1).click();
  await shot(page, "pick-dark", "t16");

  await page.evaluate(() => document.documentElement.classList.add("light"));
  await shot(page, "pick-light", "t16");

  await page.setViewportSize({ width: 900, height: 700 });
  await shot(page, "pick-narrow-light", "t16");
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.getByRole("button", { name: "Find more clips" }).click();
  await expect(page.getByRole("button", { name: /Finding more clips/ })).toBeVisible();
  await shot(page, "pick-finding-light", "t16");
  await expect(page.getByRole("heading", { name: "18 clips found · 14 kept" })).toBeVisible({ timeout: 15_000 });

  await page.evaluate(() => document.documentElement.classList.remove("light"));
  await page.goto("/#/p/new/import");
  await page.locator('input[type="file"]').setInputFiles(file("silent.mp4"));
  await page.getByRole("button", { name: "Start" }).click();
  await expect(page.getByText("No speech found in this video.")).toBeVisible({ timeout: 20_000 });
  await shot(page, "pick-empty-dark", "t16");
  await page.evaluate(() => document.documentElement.classList.add("light"));
  await shot(page, "pick-empty-light", "t16");
});
